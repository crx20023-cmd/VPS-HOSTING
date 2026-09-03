"""VPS-PANEL — multi-language VPS-style hosting panel (FastAPI)."""
import os
import io
import re
import json
import time
import uuid
import shlex
import secrets
import zipfile
import tempfile
import shutil
import asyncio
import datetime
import urllib.parse
import subprocess
import hashlib
import threading
from typing import Dict, Optional, List

from fastapi import (FastAPI, Request, Depends, HTTPException, status, Body,
                     Form, File, UploadFile, WebSocket, WebSocketDisconnect, Cookie, Header)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import httpx
import psutil
import websockets

import db
from runtime.base import get_driver

app = FastAPI(title="VPS-PANEL")
driver = get_driver()

os.makedirs("data/apps", exist_ok=True)
os.makedirs("data/logs", exist_ok=True)
db.init_db()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- config (env-overridable) ---
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
SESSION_IDLE = int(os.environ.get("SESSION_IDLE_SECONDS", str(24 * 3600)))
LOGIN_MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
LOGIN_LOCKOUT = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "600"))
REGISTER_MAX_PER_HOUR = int(os.environ.get("REGISTER_MAX_PER_HOUR", "10"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
MAX_PASTE_CHARS = int(os.environ.get("MAX_PASTE_CHARS", "500000"))
MAX_ZIP_ENTRIES = int(os.environ.get("MAX_ZIP_ENTRIES", "5000"))
MAX_ZIP_UNCOMPRESSED_MB = int(os.environ.get("MAX_ZIP_UNCOMPRESSED_MB", "500"))

# session_id -> {username, csrf, created, last_active}
active_sessions: Dict[str, dict] = {}
login_attempts: Dict[str, dict] = {}
register_attempts: Dict[str, list] = {}


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.strip()
    return request.client.host if request.client else "unknown"


def get_current_user(session_id: Optional[str] = Cookie(None),
                     x_api_token: Optional[str] = Header(None, alias="X-API-Token")) -> dict:
    if x_api_token:
        row = db.get_api_token_by_hash(hashlib.sha256(x_api_token.encode()).hexdigest())
        if not row:
            raise HTTPException(401, "Invalid API token")
        db.touch_api_token(row["id"])
        user = db.get_user(row["user"])
        if not user or user["status"] != "approved":
            raise HTTPException(403, "User not active")
        return dict(user)
    if not session_id or session_id not in active_sessions:
        raise HTTPException(401, "Not authenticated")
    sess = active_sessions[session_id]
    now = time.time()
    if now - sess.get("created", now) > SESSION_MAX_AGE or now - sess.get("last_active", now) > SESSION_IDLE:
        del active_sessions[session_id]
        raise HTTPException(401, "Session expired. Please log in again.")
    sess["last_active"] = now
    user = db.get_user(sess["username"])
    if not user:
        raise HTTPException(401, "Session user not found")
    if user["status"] == "banned":
        raise HTTPException(403, "Your account has been banned.")
    if user["status"] == "pending":
        raise HTTPException(403, "Your account is pending admin approval.")
    return dict(user)


def verify_admin(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required.")
    return user


def verify_ownership(app_id: str, user: dict) -> dict:
    app_row = db.get_app(app_id)
    if not app_row:
        raise HTTPException(404, "App not found")
    app = dict(app_row)
    if user["role"] != "admin" and app["owner"] != user["username"]:
        raise HTTPException(403, "Permission denied.")
    return app


@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if request.url.path in ("/login", "/register"):
            return await call_next(request)
        if request.headers.get("X-API-Token"):
            return await call_next(request)
        token = request.headers.get("X-CSRF-Token", "")
        sess = active_sessions.get(request.cookies.get("session_id"))
        if not sess or not token or not secrets.compare_digest(token, sess.get("csrf", "")):
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed."})
    return await call_next(request)


def check_login_lock(ip: str) -> bool:
    rec = login_attempts.get(ip)
    return bool(rec and rec.get("locked_until", 0) > time.time())


def record_login_fail(ip: str):
    rec = login_attempts.setdefault(ip, {"fails": 0, "locked_until": 0, "last_fail": time.time()})
    rec["fails"] += 1
    rec["last_fail"] = time.time()
    if rec["fails"] >= LOGIN_MAX_FAILS:
        rec["locked_until"] = time.time() + LOGIN_LOCKOUT
        rec["fails"] = 0


def record_login_success(ip: str):
    login_attempts.pop(ip, None)


def check_register_limit(ip: str) -> bool:
    stamps = [t for t in register_attempts.get(ip, []) if time.time() - t < 3600]
    register_attempts[ip] = stamps
    return len(stamps) >= REGISTER_MAX_PER_HOUR


# ---------- monitor loop ----------
async def monitor_loop():
    tick_n = 0
    while True:
        try:
            driver.monitor()
            now = time.time()
            if tick_n % 15 == 0:
                _flush_stats()
                try:
                    _cron_tick()
                except Exception as e:
                    print(f"cron tick error: {e}")
            tick_n += 1
            try:
                _dev_watch()
            except Exception as e:
                print(f"dev watch error: {e}")
            for sid in list(active_sessions.keys()):
                sess = active_sessions[sid]
                if now - sess.get("created", now) > SESSION_MAX_AGE or now - sess.get("last_active", now) > SESSION_IDLE:
                    del active_sessions[sid]
            for ip in list(login_attempts.keys()):
                rec = login_attempts[ip]
                if rec.get("locked_until", 0) < now and now - rec.get("last_fail", now) > 3600:
                    del login_attempts[ip]
            for ip in list(register_attempts.keys()):
                register_attempts[ip] = [t for t in register_attempts[ip] if now - t < 3600]
                if not register_attempts[ip]:
                    del register_attempts[ip]
        except Exception as e:
            print(f"monitor error: {e}")
        await asyncio.sleep(2)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_loop())


class DevModeRequest(BaseModel):
    enabled: bool


@app.post("/api/apps/{app_id}/devmode")
async def set_devmode(app_id: str, req: DevModeRequest = Body(...), user: dict = Depends(get_current_user)):
    """Toggle dev mode: auto-restart the app when its files change."""
    verify_ownership(app_id, user)
    db.update_app(app_id, dev_mode=1 if req.enabled else 0)
    if not req.enabled:
        _dev_snapshots.pop(app_id, None)
    return {"status": "success", "dev_mode": bool(req.enabled)}


_dev_snapshots = {}  # app_id -> {relpath: (mtime, size)}


def _dev_watch():
    """Dev mode: restart running apps whose files changed (skips venv/node_modules/.git/__pycache__)."""
    running = db.get_running_apps()
    for app in running:
        if not app["dev_mode"]:
            _dev_snapshots.pop(app["id"], None)
            continue
        app_dir = os.path.abspath(os.path.join("data", "apps", app["id"]))
        if not os.path.isdir(app_dir):
            continue
        snap = {}
        skip = {"venv", "node_modules", ".git", "__pycache__", ".pytest_cache"}
        try:
            for root, dirs, names in os.walk(app_dir):
                dirs[:] = [d for d in dirs if d not in skip]
                for n in names:
                    if n == "app.log":
                        continue
                    fp = os.path.join(root, n)
                    try:
                        st = os.stat(fp)
                        snap[os.path.relpath(fp, app_dir)] = (st.st_mtime, st.st_size)
                    except OSError:
                        continue
        except OSError:
            continue
        prev = _dev_snapshots.get(app["id"])
        _dev_snapshots[app["id"]] = snap
        if prev is None:
            continue  # first scan = baseline
        changed = [k for k in snap if snap[k] != prev.get(k)] + [k for k in prev if k not in snap]
        if changed:
            del _dev_snapshots[app["id"]]
            driver.write_log(app["id"], f"\n[DEV] file change detected ({changed[0]}) — restarting\n")
            driver.restart(dict(app))


# ---------- auth routes ----------
@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = get_client_ip(request)
    if check_login_lock(ip):
        raise HTTPException(429, f"Too many failed attempts. Try again in {LOGIN_LOCKOUT // 60} minutes.")
    username = username.strip().lower()
    user = db.get_user(username)
    if not user or not db.verify_password(password, user["password_hash"]):
        record_login_fail(ip)
        raise HTTPException(400, "Incorrect username or password")
    if user["status"] == "banned":
        raise HTTPException(403, "Your account has been banned.")
    if user["status"] == "pending":
        raise HTTPException(403, "Your account is pending admin approval.")
    record_login_success(ip)

    session_id = str(uuid.uuid4())
    csrf_token = secrets.token_urlsafe(32)
    active_sessions[session_id] = {"username": username, "csrf": csrf_token,
                                   "created": time.time(), "last_active": time.time()}
    response = JSONResponse(content={"status": "success", "role": user["role"]})
    response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="lax")
    return response


@app.post("/register")
async def register_post(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = get_client_ip(request)
    if check_register_limit(ip):
        raise HTTPException(429, f"Registration rate limit reached ({REGISTER_MAX_PER_HOUR}/hour).")
    username = username.strip().lower()
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if not re.match(r"^[a-z0-9_.-]{3,32}$", username):
        raise HTTPException(400, "Username must be 3-32 chars: a-z, 0-9, . _ -")
    auto_approve = db.get_setting("auto_approve", "0") == "1"
    user = db.create_user(
        username=username, password_plain=password, role="user",
        status="approved" if auto_approve else "pending",
        ip=get_client_ip(request), device=request.headers.get("User-Agent", "Unknown"),
    )
    if not user:
        raise HTTPException(400, "Username already taken.")
    register_attempts.setdefault(ip, []).append(time.time())
    return {"status": "success", "approval_required": not auto_approve}


@app.get("/logout")
async def logout(session_id: Optional[str] = Cookie(None)):
    active_sessions.pop(session_id, None)
    response = RedirectResponse(url="/login")
    response.delete_cookie("session_id")
    response.delete_cookie("csrf_token")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session_id: Optional[str] = Cookie(None)):
    if not session_id or session_id not in active_sessions:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request=request, name="dashboard.html")


# ---------- me / metrics ----------
@app.get("/csrf")
async def get_csrf(request: Request):
    token = request.cookies.get("csrf_token", "")
    if not token:
        raise HTTPException(401, "Not authenticated")
    return {"csrf": token}


@app.get("/api/me")
async def api_me(user: dict = Depends(get_current_user)):
    return {
        "username": user["username"], "role": user["role"],
        "app_limit": user["app_limit"],
        "quota": {"ram_mb": user["quota_ram"], "cpu_pct": user["quota_cpu"], "disk_mb": user["quota_disk"]},
        "usage": driver.get_user_usage(user["username"]),
    }


@app.get("/api/metrics")
async def api_metrics(user: dict = Depends(get_current_user)):
    if user["role"] == "admin":
        all_apps = db.get_all_apps()
        total = len(all_apps)
        running = sum(1 for a in all_apps if driver.is_running(a["id"]))
    else:
        apps = db.get_user_apps(user["username"])
        total = len(apps)
        running = sum(1 for a in apps if driver.is_running(a["id"]))
    try:
        host_cpu = psutil.cpu_percent(interval=None)
    except Exception:
        host_cpu = 0.0  # /proc/stat may be restricted (Android SELinux)
    try:
        vm = psutil.virtual_memory()
        host_ram = vm.percent
        host_ram_mb = round(vm.used / (1024 * 1024), 0)
    except Exception:
        host_ram, host_ram_mb = 0.0, 0
    return {
        "total_apps": total, "running_apps": running,
        "host_cpu": host_cpu,
        "host_ram": host_ram,
        "host_ram_mb": host_ram_mb,
    }


# ---------- apps CRUD ----------
@app.get("/api/apps")
async def list_apps(user: dict = Depends(get_current_user)):
    if user["role"] == "admin":
        rows = db.get_all_apps()
    else:
        rows = db.get_user_apps(user["username"])
    out = []
    for r in rows:
        a = dict(r)
        a["env_vars"] = json.loads(a.pop("env_json") or "{}")
        a["running"] = driver.is_running(a["id"])
        out.append(a)
    return out


@app.get("/api/apps/{app_id}")
async def get_app(app_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    app["env_vars"] = json.loads(app.pop("env_json") or "{}")
    app["running"] = driver.is_running(app_id)
    return app


@app.post("/api/apps")
async def create_app(
    name: str = Form(...),
    app_type: str = Form(...),
    entrypoint: str = Form(""),
    source_type: str = Form("paste"),  # paste|zip|git|template
    zip_file: Optional[UploadFile] = File(None),
    git_url: Optional[str] = Form(None),
    git_branch: Optional[str] = Form("main"),
    paste_code: Optional[str] = Form(None),
    template_id: Optional[str] = Form(None),
    env_vars_json: Optional[str] = Form("{}"),
    auto_restart: bool = Form(True),
    alias: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    if app_type not in ("static", "python", "node", "php"):
        raise HTTPException(400, "Invalid app type. Choose static, python, node or php.")

    owner = user["username"]
    owner_user = db.get_user(owner)
    if owner_user["role"] != "admin":
        limit = owner_user["app_limit"]
        if len(db.get_user_apps(owner)) >= limit:
            raise HTTPException(400, f"App limit reached ({limit}). Contact admin.")

    # disk quota pre-check
    usage = driver.get_user_usage(owner)

    if app_type != "static":
        if not entrypoint or not re.match(r"^[A-Za-z0-9_.-]+\.(py|js|php)$", entrypoint):
            raise HTTPException(400, "Entrypoint must be a single .py, .js or .php file name.")
    if alias:
        alias = alias.strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9-_]{1,31}$", alias):
            raise HTTPException(400, "Alias must be 2-32 chars: a-z, 0-9, - _ (start with letter/digit).")
        if db.get_app_by_alias(alias):
            raise HTTPException(409, f"Alias '{alias}' is already taken.")

    env_vars = {}
    try:
        env_vars = json.loads(env_vars_json or "{}")
        if not isinstance(env_vars, dict):
            raise ValueError
    except Exception:
        raise HTTPException(400, "env_vars_json must be a JSON object.")

    app_id = f"app_{int(time.time() * 1000)}"
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    log_path = os.path.join("data", "logs", f"{app_id}.log")
    os.makedirs(app_dir, exist_ok=True)

    try:
        if source_type == "zip":
            if not zip_file:
                raise HTTPException(400, "ZIP file required")
            max_bytes = MAX_UPLOAD_MB * 1024 * 1024
            size = 0
            while True:
                chunk = zip_file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"Upload too large (max {MAX_UPLOAD_MB} MB).")
            zip_file.file.seek(0)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                shutil.copyfileobj(zip_file.file, tmp)
                tmp_path = tmp.name
            try:
                with zipfile.ZipFile(tmp_path, "r") as zf:
                    infos = zf.infolist()
                    if len(infos) > MAX_ZIP_ENTRIES:
                        raise HTTPException(400, f"Too many entries (max {MAX_ZIP_ENTRIES}).")
                    total_un = sum(i.file_size for i in infos)
                    if total_un > MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024:
                        raise HTTPException(400, f"Uncompressed size too large (max {MAX_ZIP_UNCOMPRESSED_MB} MB).")
                    projected = usage["disk_mb"] + total_un / (1024 * 1024)
                    if owner_user["role"] != "admin" and projected > owner_user["quota_disk"]:
                        raise HTTPException(400, f"Disk quota exceeded ({round(projected,1)}MB > {owner_user['quota_disk']}MB).")
                    for info in infos:
                        target = os.path.normpath(os.path.join(app_dir, info.filename))
                        if not target.startswith(os.path.abspath(app_dir) + os.sep) and target != os.path.abspath(app_dir):
                            raise HTTPException(400, f"Unsafe path in ZIP: {info.filename}")
                        if info.is_dir():
                            os.makedirs(target, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(info) as src, open(target, "wb") as dst:
                                shutil.copyfileobj(src, dst)
            finally:
                os.remove(tmp_path)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"[MANAGER] Extracted ZIP for {app_id}.\n")

        elif source_type == "git":
            if not git_url:
                raise HTTPException(400, "Git URL required")
            if not re.match(r"^(https?|git|ssh)://", git_url):
                raise HTTPException(400, "Invalid git URL protocol.")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"[MANAGER] Cloning {git_url} ({git_branch})...\n")
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    subprocess.run(["git", "clone", "--depth", "1", "-b", git_branch, git_url, "."],
                                   cwd=app_dir, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=300)
            except Exception as e:
                shutil.rmtree(app_dir, ignore_errors=True)
                raise HTTPException(400, f"Git clone failed: {e}")

        elif source_type == "paste":
            if not paste_code:
                raise HTTPException(400, "Pasted code required")
            if len(paste_code) > MAX_PASTE_CHARS:
                raise HTTPException(413, f"Pasted code too large (max {MAX_PASTE_CHARS}).")
            if app_type == "static":
                entrypoint = "index.html"
                with open(os.path.join(app_dir, "index.html"), "w", encoding="utf-8") as f:
                    f.write(paste_code)
            else:
                with open(os.path.join(app_dir, entrypoint), "w", encoding="utf-8") as f:
                    f.write(paste_code)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"[MANAGER] Created {entrypoint} via paste.\n")

        elif source_type == "template":
            if not template_id:
                raise HTTPException(400, "Template required")
            tpl_dir = os.path.join("templates", template_id)
            if not os.path.isdir(tpl_dir):
                raise HTTPException(400, f"Unknown template '{template_id}'")
            tpl_total = 0
            for root, dirs, names in os.walk(tpl_dir):
                for n in names:
                    tpl_total += os.path.getsize(os.path.join(root, n))
            projected = usage["disk_mb"] + tpl_total / (1024 * 1024)
            if owner_user["role"] != "admin" and projected > owner_user["quota_disk"]:
                raise HTTPException(400, f"Disk quota exceeded ({round(projected,1)}MB > {owner_user['quota_disk']}MB).")
            if not re.match(r"^[A-Za-z0-9_.-]+\.(py|js)$", entrypoint):
                raise HTTPException(400, "Template entrypoint must be a .py or .js file name.")
            copied = 0
            for root, dirs, names in os.walk(tpl_dir):
                rel = os.path.relpath(root, tpl_dir)
                for n in names:
                    src = os.path.join(root, n)
                    dst_dir = app_dir if rel == "." else os.path.join(app_dir, rel)
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copy2(src, os.path.join(dst_dir, n))
                    copied += 1
            tpl_note = ""
            try:
                with open("templates/manifest.json", "r", encoding="utf-8") as f:
                    tpl_note = next((t.get("note", "") for t in json.load(f) if t.get("id") == template_id), "")
            except Exception:
                pass
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"[MANAGER] Deployed template '{template_id}' ({copied} files).\n")
                if tpl_note:
                    f.write(f"[MANAGER] Note: {tpl_note}\n")
        else:
            raise HTTPException(400, "Invalid source type")

        created = db.create_app(app_id, owner, name, app_type, entrypoint, env_vars, auto_restart, alias)
        if not created:
            shutil.rmtree(app_dir, ignore_errors=True)
            raise HTTPException(409, "App creation conflict (alias taken?).")

        # dependency install in background (python/node)
        if app_type in ("python", "node"):
            driver.create_venv(app_id)
        return dict(created)
    except HTTPException as he:
        shutil.rmtree(app_dir, ignore_errors=True)
        raise he
    except Exception as e:
        shutil.rmtree(app_dir, ignore_errors=True)
        raise HTTPException(500, f"Internal error: {e}")


@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    driver.delete_files(app_id)
    shutil.rmtree(os.path.join(SNAP_DIR, app_id), ignore_errors=True)
    db.delete_app(app_id)
    return {"status": "success"}


@app.get("/api/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    """Bot quick-start templates (aiogram/pyrogram/telethon/grammY)."""
    try:
        with open("templates/manifest.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


@app.post("/api/apps/{app_id}/start")
async def start_app(app_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    if not driver.start(app):
        raise HTTPException(400, "Failed to start. Check console logs.")
    return {"status": "success"}


@app.post("/api/apps/{app_id}/stop")
async def stop_app(app_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    driver.stop(app)
    return {"status": "success"}


@app.post("/api/apps/{app_id}/restart")
async def restart_app(app_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    if not driver.restart(app):
        raise HTTPException(400, "Failed to restart. Check console logs.")
    return {"status": "success"}


# ---------- env / logs ----------
class EnvUpdateRequest(BaseModel):
    env: Dict[str, str]

@app.put("/api/apps/{app_id}/env")
async def update_env(app_id: str, req: EnvUpdateRequest, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    updated = db.update_app(app_id, env_json=json.dumps(req.env))
    if not updated:
        raise HTTPException(404, "App not found")
    return {"status": "success"}


class AliasUpdateRequest(BaseModel):
    alias: Optional[str] = None

@app.post("/api/apps/{app_id}/alias")
async def update_alias(app_id: str, req: AliasUpdateRequest, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    alias = (req.alias or "").strip().lower() or None
    if alias and not re.match(r"^[a-z0-9][a-z0-9-_]{1,31}$", alias):
        raise HTTPException(400, "Alias must be 2-32 chars: a-z, 0-9, - _ (start with letter/digit).")
    if alias:
        existing = db.get_app_by_alias(alias)
        if existing and existing["id"] != app_id:
            raise HTTPException(409, f"Alias '{alias}' is already taken.")
    updated = db.update_app(app_id, alias=alias)
    if not updated:
        raise HTTPException(404, "App not found")
    app = db.get_app(app_id)
    return {"status": "success", "alias": app["alias"]}


@app.get("/api/apps/{app_id}/logs")
async def get_logs(app_id: str, user: dict = Depends(get_current_user),
                   limit: int = 200, search: Optional[str] = None):
    verify_ownership(app_id, user)
    limit = max(1, min(limit, 2000))
    return HTMLResponse(content=driver.read_logs(app_id, limit, search), media_type="text/plain")


@app.get("/api/apps/{app_id}/logs/download")
async def download_logs(app_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    log_path = driver.log_path(app_id)
    if not os.path.exists(log_path):
        raise HTTPException(404, "No logs yet.")
    return FileResponse(log_path, media_type="text/plain", filename=f"{app_id}.log")


@app.get("/api/apps/{app_id}/metrics")
async def app_metrics(app_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    return driver.get_usage(app_id)


@app.get("/api/apps/{app_id}/export")
async def export_app(app_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    if not os.path.isdir(app_dir):
        raise HTTPException(404, "App files not found on disk.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(app_dir):
            dirs[:] = [d for d in dirs if d not in ("venv", ".venv", "__pycache__", ".git", "node_modules")]
            for f in files:
                abs_path = os.path.join(root, f)
                zf.write(abs_path, arcname=os.path.relpath(abs_path, app_dir))
        zf.writestr("app-metadata.json", json.dumps({
            "id": app["id"], "name": app["name"], "owner": app["owner"],
            "type": app["type"], "entrypoint": app["entrypoint"],
            "alias": app["alias"], "env_var_names": list(json.loads(app["env_json"] or "{}").keys()),
            "exported_at": datetime.datetime.now().isoformat(),
        }, indent=2))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{app_id}-export.zip"'})


# ---------- file manager ----------
def safe_join(base: str, path: str) -> str:
    base = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base, path))
    if not target.startswith(base + os.sep) and target != base:
        raise PermissionError("Path escapes app directory")
    return target


@app.get("/api/apps/{app_id}/files")
async def list_files(app_id: str, path: str = "", user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        abs_path = safe_join(app_dir, path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    if not os.path.isdir(abs_path):
        raise HTTPException(404, "Not a directory")
    items = []
    try:
        for entry in sorted(os.scandir(abs_path), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name in ("venv", ".venv", "__pycache__", ".git", "node_modules"):
                continue
            try:
                items.append({"name": entry.name, "is_dir": entry.is_dir(),
                              "size": entry.stat().st_size if entry.is_file() else 0})
            except Exception:
                continue
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    return {"path": path, "items": items}


@app.get("/api/apps/{app_id}/files/read")
async def read_file(app_id: str, path: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        abs_path = safe_join(app_dir, path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    if not os.path.isfile(abs_path):
        raise HTTPException(404, "File not found")
    if os.path.getsize(abs_path) > 2 * 1024 * 1024:
        raise HTTPException(413, "File too large to open in editor (max 2MB).")
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return Response(content=content, media_type="text/plain",
                    headers={"Content-Disposition": "inline"})


@app.post("/api/apps/{app_id}/files/write")
async def write_file(app_id: str, path: str, request: Request,
                     user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        abs_path = safe_join(app_dir, path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    body = (await request.body()).decode("utf-8", "replace")
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    _enforce_disk_quota(app_id, user, len(body))
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(body[:MAX_PASTE_CHARS])
    return {"status": "success"}


class CreateItemRequest(BaseModel):
    path: str
    is_dir: bool = False

@app.post("/api/apps/{app_id}/files/create")
async def create_item(app_id: str, req: CreateItemRequest, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        abs_path = safe_join(app_dir, req.path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    if os.path.exists(abs_path):
        raise HTTPException(400, "Path already exists")
    if req.is_dir:
        os.makedirs(abs_path)
    else:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        open(abs_path, "w").close()
    return {"status": "success"}


@app.delete("/api/apps/{app_id}/files")
async def delete_item(app_id: str, path: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        abs_path = safe_join(app_dir, path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    if not os.path.exists(abs_path):
        raise HTTPException(404, "Not found")
    if os.path.isdir(abs_path):
        shutil.rmtree(abs_path, ignore_errors=True)
    else:
        os.remove(abs_path)
    return {"status": "success"}


@app.post("/api/apps/{app_id}/files/upload")
async def upload_file(app_id: str, path: str = "", file: UploadFile = File(...),
                      user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        target_dir = safe_join(app_dir, path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    os.makedirs(target_dir, exist_ok=True)
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    dest = os.path.join(target_dir, os.path.basename(file.filename or "upload.bin"))
    with open(dest, "wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                out.close()
                os.remove(dest)
                raise HTTPException(413, f"Upload too large (max {MAX_UPLOAD_MB} MB).")
            out.write(chunk)
    try:
        _enforce_disk_quota(app_id, user, size)
    except HTTPException:
        os.remove(dest)
        raise
    return {"status": "success", "size": size}


@app.post("/api/apps/{app_id}/files/unzip")
async def unzip_file(app_id: str, path: str = "", file: UploadFile = File(...),
                     user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = os.path.abspath(os.path.join("data", "apps", app_id))
    try:
        target_dir = safe_join(app_dir, path)
    except PermissionError:
        raise HTTPException(403, "Permission denied.")
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                os.remove(tmp.name)
                raise HTTPException(413, f"Upload too large (max {MAX_UPLOAD_MB} MB).")
            tmp.write(chunk)
        tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise HTTPException(400, f"Too many entries (max {MAX_ZIP_ENTRIES}).")
            if sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024:
                raise HTTPException(400, f"Uncompressed size too large (max {MAX_ZIP_UNCOMPRESSED_MB} MB).")
            _enforce_disk_quota(app_id, user, sum(i.file_size for i in infos))
            for info in infos:
                target = os.path.normpath(os.path.join(target_dir, info.filename))
                if not target.startswith(os.path.abspath(target_dir) + os.sep) and target != os.path.abspath(target_dir):
                    raise HTTPException(400, f"Unsafe path in ZIP: {info.filename}")
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    finally:
        os.remove(tmp_path)
    return {"status": "success"}


def _dir_usage_mb(path: str) -> float:
    total = 0
    try:
        for root, dirs, names in os.walk(path):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                except OSError:
                    continue
    except OSError:
        pass
    return total / (1024 * 1024)


def _enforce_disk_quota(app_id: str, user: dict, extra_bytes: int = 0):
    """Reject the write if it would push the user over quota_disk (admins exempt)."""
    owner_user = db.get_user(user["username"])
    if not owner_user or owner_user["role"] == "admin":
        return
    usage = driver.get_user_usage(user["username"])
    projected = usage["disk_mb"] + extra_bytes / (1024 * 1024)
    if projected > owner_user["quota_disk"]:
        raise HTTPException(400, f"Disk quota exceeded ({round(projected,1)}MB > {owner_user['quota_disk']}MB).")


# ---------- terminal (REST fallback + interactive WS/PTY) ----------
class ShellRequest(BaseModel):
    cmd: str

@app.post("/api/apps/{app_id}/terminal")
async def run_shell(app_id: str, req: ShellRequest, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    if len(req.cmd) > 500:
        raise HTTPException(400, "Command too long (max 500 chars).")
    return driver.run_shell(app_id, req.cmd)


@app.websocket("/api/apps/{app_id}/terminal/ws")
async def terminal_ws(websocket: WebSocket, app_id: str):
    # Auth check via cookie (browser sends cookies on WS)
    session_id = websocket.cookies.get("session_id")
    sess = active_sessions.get(session_id)
    if not sess:
        await websocket.close(code=4401)
        return
    user = db.get_user(sess["username"])
    if not user or user["status"] != "approved":
        await websocket.close(code=4401)
        return
    app = db.get_app(app_id)
    if not app:
        await websocket.close(code=4404)
        return
    if user["role"] != "admin" and app["owner"] != user["username"]:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    session = driver.open_pty(app_id)
    loop = asyncio.get_event_loop()

    async def pump():
        while True:
            data = await loop.run_in_executor(None, session["read"], 65536)
            if data is None:
                await websocket.send_text("__VPS_PTY_CLOSED__")
                break
            if data:
                await websocket.send_text(data.decode("utf-8", "replace"))
            await asyncio.sleep(0.03)

    async def sink():
        while True:
            try:
                msg = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            if msg.startswith("__VPS_RESIZE__"):
                try:
                    _, cols, rows = msg.split(":")
                    session["resize"](int(cols), int(rows))
                except Exception:
                    pass
                continue
            session["write"](msg.encode())

    try:
        pump_task = asyncio.create_task(pump())
        sink_task = asyncio.create_task(sink())
        done, pending = await asyncio.wait([pump_task, sink_task], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    finally:
        session["close"]()


# ---------- advanced: stats / cron / snapshots / api tokens (v1.2) ----------
_stats_mem = {}  # app_id -> {"req": int, "bytes": int} — flushed to DB periodically


def _bump(app_id: str, nbytes: int = 0):
    s = _stats_mem.setdefault(app_id, {"req": 0, "bytes": 0})
    s["req"] += 1
    s["bytes"] += nbytes


def _flush_stats():
    for app_id, s in list(_stats_mem.items()):
        if s["req"] or s["bytes"]:
            db.add_app_stats(app_id, s["req"], s["bytes"])
        _stats_mem[app_id] = {"req": 0, "bytes": 0}


def parse_cron(sched: str):
    """5-field cron: min hour dom mon dow. Supports *, */n, a-b, comma lists."""
    fields = sched.split()
    if len(fields) != 5:
        return None

    def pf(f, lo, hi):
        out = set()
        for part in f.split(","):
            part = part.strip()
            if part == "*":
                out.update(range(lo, hi + 1))
            elif part.startswith("*/"):
                step = int(part[2:])
                if step <= 0:
                    return None
                out.update(range(lo, hi + 1, step))
            elif "-" in part:
                a, b = part.split("-")
                out.update(range(int(a), int(b) + 1))
            else:
                out.add(int(part))
        return out or None

    try:
        mins = pf(fields[0], 0, 59)
        hours = pf(fields[1], 0, 23)
        doms = pf(fields[2], 1, 31)
        mons = pf(fields[3], 1, 12)
        dows = pf(fields[4], 0, 6)
        if None in (mins, hours, doms, mons, dows):
            return None
        return (mins, hours, doms, mons, dows)
    except Exception:
        return None


def next_cron_time(sets, after_ts):
    """Return next datetime >= after_ts matching cron sets, or None."""
    cur = datetime.datetime.fromtimestamp(after_ts).replace(second=0, microsecond=0)
    mins, hours, doms, mons, dows = sets
    for _ in range(2 * 365 * 24 * 60):  # up to 2 years ahead
        if cur.month in mons and cur.day in doms and cur.hour in hours and cur.minute in mins \
                and cur.weekday() in dows:
            return cur
        cur += datetime.timedelta(minutes=1)
    return None


def _run_cron(job: dict):
    app = db.get_app(job["app_id"])
    if not app:
        return
    try:
        app_dir = driver.app_dir(app["id"])
        log_path = driver.log_path(app["id"])
        out = subprocess.run(job["command"], shell=True, cwd=app_dir,
                             capture_output=True, text=True, timeout=120)
        tail = (out.stdout or "")[-800:]
        if out.stderr:
            tail += "\n[stderr] " + out.stderr[-400:]
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n[CRON {job['name']}] ran at {now_str()} exit={out.returncode}\n{tail}\n")
    except subprocess.TimeoutExpired:
        with open(driver.log_path(job["app_id"]), "a", encoding="utf-8") as f:
            f.write(f"\n[CRON {job['name']}] TIMEOUT after 120s\n")
    except Exception as e:
        with open(driver.log_path(job["app_id"]), "a", encoding="utf-8") as f:
            f.write(f"\n[CRON {job['name']}] error: {e}\n")


def _cron_tick():
    now = time.time()
    for row in db.get_cron_jobs():
        job = dict(row)
        if not job["enabled"]:
            continue
        sets = parse_cron(job["schedule"])
        if not sets:
            continue
        nxt = job["next_run"]
        try:
            nxt_ts = datetime.datetime.fromisoformat(nxt).timestamp() if nxt else None
        except Exception:
            nxt_ts = None
        if nxt_ts is None:
            nxt_ts = next_cron_time(sets, now).timestamp() if next_cron_time(sets, now) else None
            if nxt_ts:
                db.update_cron(job["id"], next_run=datetime.datetime.fromtimestamp(nxt_ts).isoformat())
        if nxt_ts and now >= nxt_ts:
            threading.Thread(target=_run_cron, args=(job,), daemon=True).start()
            after = now + 1
            n2 = next_cron_time(sets, after)
            db.update_cron(job["id"], last_run=datetime.datetime.now().isoformat(),
                           next_run=n2.isoformat() if n2 else None)


_proc_samples = {}  # pid -> (ts, jiffies) for delta cpu%


def _parse_etime(etime: str):
    """'MM:SS' | 'HH:MM:SS' | 'D-HH:MM:SS' -> seconds."""
    try:
        if "-" in etime:
            d, rest = etime.split("-", 1)
            return int(d) * 86400 + _parse_etime(rest)
        parts = [int(x) for x in etime.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except Exception:
        return None


def _etime_map():
    """pid -> uptime seconds via toybox ps (Android-safe)."""
    out = {}
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=,etime="], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                et = _parse_etime(parts[1])
                if et is not None:
                    out[int(parts[0])] = et
    except Exception:
        pass
    return out


def proc_info(pid: int, etime_map: dict = None):
    """stdlib /proc reader — cpu% (delta), rss, cmd, cwd, uptime. Android-safe:
    /proc/uptime and /proc/stat are blocked by SELinux, so cpu uses two-sample
    delta and uptime comes from `ps -o etime`."""
    try:
        stat = open(f"/proc/{pid}/stat").read()
        after = stat[stat.rfind(")") + 2:].split()
        utime, stime = int(after[11]), int(after[12])
        jiffies = utime + stime
        clk = os.sysconf("SC_CLK_TCK")
        rss = int(open(f"/proc/{pid}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").strip().decode("utf-8", "replace")[:100]
        cwd = os.path.realpath(f"/proc/{pid}/cwd")
        prev = _proc_samples.get(pid)
        now = time.time()
        if prev:
            dt = max(now - prev[0], 0.01)
            cpu = (jiffies - prev[1]) / clk / dt * 100.0
        else:
            cpu = 0.0
        _proc_samples[pid] = (now, jiffies)
        if len(_proc_samples) > 400:
            _proc_samples.clear()
        uptime_s = (etime_map or {}).get(pid)
        return {"pid": pid, "cpu_pct": round(max(cpu, 0.0), 2), "rss_mb": round(rss, 2),
                "cmd": cmd, "cwd": cwd, "uptime_s": uptime_s}
    except Exception:
        return None


def find_app_processes():
    """Map running app processes to app ids via /proc cwd."""
    apps_dir = os.path.realpath(os.path.join("data", "apps"))
    etime_map = _etime_map()
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cwd = os.path.realpath(f"/proc/{pid}/cwd")
            if not cwd.startswith(apps_dir + os.sep):
                continue
            cmd = open(f"/proc/{pid}/cmdline", "rb").read()
            if b"main.py" not in cmd:
                continue
            app_id = os.path.basename(cwd)
            info = proc_info(int(pid), etime_map) or {}
            info["app_id"] = app_id
            out.append(info)
        except OSError:
            continue
    return out


class CronCreateRequest(BaseModel):
    name: str
    schedule: str
    command: str


class SnapshotCreateRequest(BaseModel):
    name: str = "snapshot"


class TokenCreateRequest(BaseModel):
    label: str




@app.get("/api/apps/{app_id}/stats")
async def app_stats(app_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    app_dir = driver.app_dir(app_id)
    total = 0
    for f in os.listdir(app_dir):
        p = os.path.join(app_dir, f)
        if os.path.isfile(p):
            total += os.path.getsize(p)
    info = None
    for p in find_app_processes():
        if p["app_id"] == app_id:
            info = p
            break
    pend = _stats_mem.get(app_id, {"req": 0, "bytes": 0})
    return {
        "requests": app.get("requests", 0) + pend["req"],
        "bytes_served": app.get("bytes_served", 0) + pend["bytes"],
        "disk_kb": total // 1024,
        "running": driver.is_running(app_id),
        "process": info,
    }


@app.get("/api/admin/stats")
async def admin_stats(admin: dict = Depends(verify_admin)):
    mem = None
    try:
        vm = psutil.virtual_memory()
        mem = {"total_mb": vm.total // 1048576, "used_mb": vm.used // 1048576, "pct": vm.percent}
    except Exception:
        mem = {"total_mb": 0, "used_mb": 0, "pct": 0.0}
    try:
        du = shutil.disk_usage(".")
        disk = {"total_mb": du.total // 1048576, "used_mb": du.used // 1048576, "pct": du.percent}
    except Exception:
        disk = {"total_mb": 0, "used_mb": 0, "pct": 0.0}
    host_cpu = 0.0
    try:
        host_cpu = psutil.cpu_percent(interval=0.5)
    except Exception:
        try:
            host_cpu = psutil.cpu_percent(interval=None)
        except Exception:
            host_cpu = 0.0
    return {"mem": mem, "disk": disk,
            "mem_pct": mem["pct"], "disk_pct": disk["pct"],
            "host_cpu_pct": round(host_cpu, 1),
            "processes": find_app_processes()}


# ---------- cron endpoints ----------
@app.post("/api/apps/{app_id}/cron")
async def create_cron(app_id: str, req: CronCreateRequest, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    if len(req.name) > 40 or not req.name.strip():
        raise HTTPException(400, "Name required (max 40 chars).")
    if len(req.command) > 500:
        raise HTTPException(400, "Command too long (max 500 chars).")
    if not parse_cron(req.schedule):
        raise HTTPException(400, "Invalid schedule. Use 5 fields: min hour dom mon dow (e.g. '*/5 * * * *').")
    job = db.create_cron(app_id, user["username"], req.name.strip(), req.schedule.strip(), req.command.strip())
    sets = parse_cron(job["schedule"])
    n2 = next_cron_time(sets, time.time())
    if n2:
        db.update_cron(job["id"], next_run=n2.isoformat())
    return {"status": "success", "id": job["id"], "next_run": n2.isoformat() if n2 else None}


@app.get("/api/apps/{app_id}/cron")
async def list_cron(app_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    return [dict(r) for r in db.get_cron_jobs(app_id=app_id)]


@app.post("/api/apps/{app_id}/cron/{job_id}/toggle")
async def toggle_cron(app_id: str, job_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    job = db.get_cron_job(job_id)
    if not job or job["app_id"] != app_id:
        raise HTTPException(404, "Cron job not found")
    if user["role"] != "admin" and job["owner"] != user["username"]:
        raise HTTPException(403, "Permission denied.")
    db.update_cron(job_id, enabled=0 if job["enabled"] else 1)
    return {"status": "success"}


@app.delete("/api/apps/{app_id}/cron/{job_id}")
async def delete_cron(app_id: str, job_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    job = db.get_cron_job(job_id)
    if not job or job["app_id"] != app_id:
        raise HTTPException(404, "Cron job not found")
    if user["role"] != "admin" and job["owner"] != user["username"]:
        raise HTTPException(403, "Permission denied.")
    db.delete_cron(job_id)
    return {"status": "success"}


# ---------- snapshot endpoints ----------
SNAP_DIR = os.path.join("data", "snapshots")


def _snapshot_path(app_id, snap_id):
    return os.path.join(SNAP_DIR, app_id, f"{snap_id}.zip")


@app.post("/api/apps/{app_id}/snapshots")
async def create_snapshot(app_id: str, req: SnapshotCreateRequest, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    app_dir = driver.app_dir(app_id)
    if not os.path.isdir(app_dir):
        raise HTTPException(404, "App directory missing")
    snap = db.create_snapshot(app_id, user["username"], req.name.strip() or "snapshot", "", 0)
    snap_dir = os.path.join(SNAP_DIR, app_id)
    os.makedirs(snap_dir, exist_ok=True)
    zpath = _snapshot_path(app_id, snap["id"])
    size = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, names in os.walk(app_dir):
            dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git")]
            for n in names:
                if n in ("app.log",):
                    continue
                fp = os.path.join(root, n)
                arc = os.path.relpath(fp, app_dir)
                zf.write(fp, arc)
                size += os.path.getsize(fp)
    db.update_snapshot(snap["id"], file=zpath, size=size)
    return {"status": "success", "id": snap["id"], "size": size, "files": len(zf.namelist())}


@app.get("/api/apps/{app_id}/snapshots")
async def list_snapshots(app_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    return [dict(r) for r in db.get_snapshots(app_id)]


@app.post("/api/apps/{app_id}/snapshots/{snap_id}/restore")
async def restore_snapshot(app_id: str, snap_id: str, user: dict = Depends(get_current_user)):
    app = verify_ownership(app_id, user)
    snap = db.get_snapshot(snap_id)
    if not snap or snap["app_id"] != app_id:
        raise HTTPException(404, "Snapshot not found")
    if user["role"] != "admin" and snap["owner"] != user["username"]:
        raise HTTPException(403, "Permission denied.")
    zpath = snap["file"]
    if not os.path.isfile(zpath):
        raise HTTPException(404, "Snapshot file missing")
    was_running = driver.is_running(app_id)
    driver.stop(app)
    app_dir = driver.app_dir(app_id)
    # remove source files, keep venv
    for entry in os.listdir(app_dir):
        p = os.path.join(app_dir, entry)
        if entry in ("venv", "__pycache__"):
            continue
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
    with zipfile.ZipFile(zpath) as zf:
        for m in zf.infolist():
            name = m.filename
            if name.startswith("/") or ".." in name.split("/"):
                continue
            target = os.path.join(app_dir, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    if was_running:
        driver.start(dict(db.get_app(app_id)))
    return {"status": "success", "restored": True}


@app.delete("/api/apps/{app_id}/snapshots/{snap_id}")
async def delete_snapshot(app_id: str, snap_id: str, user: dict = Depends(get_current_user)):
    verify_ownership(app_id, user)
    snap = db.get_snapshot(snap_id)
    if not snap or snap["app_id"] != app_id:
        raise HTTPException(404, "Snapshot not found")
    if user["role"] != "admin" and snap["owner"] != user["username"]:
        raise HTTPException(403, "Permission denied.")
    try:
        if snap["file"] and os.path.isfile(snap["file"]):
            os.remove(snap["file"])
    except OSError:
        pass
    db.delete_snapshot(snap_id)
    return {"status": "success"}


# ---------- api token endpoints ----------
@app.get("/api/settings/tokens")
async def list_tokens(user: dict = Depends(get_current_user)):
    return [{"id": r["id"], "label": r["label"], "created_at": r["created_at"], "last_used": r["last_used"]}
            for r in db.get_api_tokens(user=user["username"])]


@app.post("/api/settings/tokens")
async def create_token(req: TokenCreateRequest, user: dict = Depends(get_current_user)):
    if not req.label.strip() or len(req.label) > 40:
        raise HTTPException(400, "Label required (max 40 chars).")
    raw = "vps_" + secrets.token_urlsafe(24)
    db.create_api_token(user["username"], req.label.strip(),
                        hashlib.sha256(raw.encode()).hexdigest())
    return {"status": "success", "token": raw}  # shown once


@app.delete("/api/settings/tokens/{token_id}")
async def delete_token(token_id: str, user: dict = Depends(get_current_user)):
    row = None
    for r in db.get_api_tokens(user=user["username"]):
        if r["id"] == token_id:
            row = r
            break
    if not row:
        raise HTTPException(404, "Token not found")
    db.delete_api_token(token_id)
    return {"status": "success"}


# ---------- reverse proxy ----------
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "content-length",
               "content-encoding", "content-security-policy"}


async def _proxy_to_app(app: dict, full_path: str, request: Request):
    if app["type"] == "static":
        # serve files directly from sandbox
        app_dir = os.path.abspath(os.path.join("data", "apps", app["id"]))
        try:
            rel = "index.html" if full_path in ("", "/") else full_path
            abs_path = safe_join(app_dir, rel)
        except PermissionError:
            raise HTTPException(404, "Not found")
        if os.path.isdir(abs_path):
            abs_path = os.path.join(abs_path, "index.html")
        if not os.path.isfile(abs_path):
            raise HTTPException(404, "Not found")
        try:
            _bump(app["id"], os.path.getsize(abs_path))
        except OSError:
            _bump(app["id"], 0)
        return FileResponse(abs_path)

    port = app.get("port")
    if not port:
        raise HTTPException(503, "No port assigned yet.")
    if not driver.is_running(app["id"]):
        raise HTTPException(404, "App is not running. Start it from the panel first.")

    target_url = f"http://127.0.0.1:{port}/{full_path}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
               and not k.lower().startswith("sec-websocket")}
    headers["Host"] = request.headers.get("host", "")
    body = await request.body()
    params = dict(request.query_params)
    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0, read=60.0))
    try:
        upstream_req = client.build_request(request.method, target_url, headers=headers,
                                            params=params, content=body or None)
        upstream = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        _bump(app["id"], 0)
        return JSONResponse(status_code=502, content={"detail": f"Upstream failed: {e}"})
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    async def stream_body():
        nbytes = 0
        try:
            async for chunk in upstream.aiter_bytes():
                nbytes += len(chunk)
                yield chunk
        finally:
            _bump(app["id"], nbytes)
            await client.aclose()

    return StreamingResponse(stream_body(), status_code=upstream.status_code, headers=resp_headers)


@app.get("/app/{app_id}")
async def proxy_root(app_id: str):
    return RedirectResponse(url=f"/app/{app_id}/")


@app.api_route("/app/{app_id}/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_app(app_id: str, full_path: str, request: Request):
    app = db.get_app(app_id)
    if not app:
        raise HTTPException(404, "App not found")
    return await _proxy_to_app(dict(app), full_path, request)


@app.get("/p/{alias}")
async def alias_root(alias: str):
    return RedirectResponse(url=f"/p/{alias}/")


@app.api_route("/p/{alias}/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_alias(alias: str, full_path: str, request: Request):
    app = db.get_app_by_alias(alias)
    if not app:
        raise HTTPException(404, "App not found")
    return await _proxy_to_app(dict(app), full_path, request)


# ---------- websocket pass-through ----------
_WS_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                  "te", "trailers", "transfer-encoding", "upgrade", "host", "sec-websocket-key",
                  "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}


async def _ws_relay(ws: WebSocket, app: dict, path: str):
    if app["status"] != "running" or not app.get("port"):
        await ws.close(code=4403)
        return
    port = app["port"]
    qs = ws.query_params
    url = f"ws://127.0.0.1:{port}/{path}" + (f"?{qs}" if qs else "")
    headers = {k: v for k, v in ws.headers.items() if k.lower() not in _WS_HOP_BY_HOP}
    try:
        await ws.accept()
        async with websockets.connect(url, extra_headers=headers) if headers else websockets.connect(url) as up:
            async def client_to_up():
                while True:
                    msg = await ws.receive()
                    t = msg["type"]
                    if t == "websocket.disconnect":
                        break
                    if msg.get("text") is not None:
                        await up.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await up.send(msg["bytes"])

            async def up_to_client():
                async for frame in up:
                    if isinstance(frame, str):
                        await ws.send_text(frame)
                    else:
                        await ws.send_bytes(frame)

            await asyncio.gather(client_to_up(), up_to_client())
    except Exception:
        try:
            await ws.close(code=4502)
        except Exception:
            pass


@app.websocket("/app/{app_id}/ws{full_path:path}")
async def ws_proxy_app(ws: WebSocket, app_id: str, full_path: str):
    app = db.get_app(app_id)
    if not app:
        await ws.close(code=4404)
        return
    await _ws_relay(ws, dict(app), full_path)


@app.websocket("/p/{alias}/ws{full_path:path}")
async def ws_proxy_alias(ws: WebSocket, alias: str, full_path: str):
    app = db.get_app_by_alias(alias)
    if not app:
        await ws.close(code=4404)
        return
    await _ws_relay(ws, dict(app), full_path)


# ---------- admin ----------
@app.get("/api/admin/users")
async def admin_users(admin: dict = Depends(verify_admin)):
    out = []
    for u in db.get_all_users():
        d = dict(u)
        d.pop("password_hash", None)
        d["apps_count"] = len(db.get_user_apps(u["username"]))
        out.append(d)
    return out


@app.post("/api/admin/users/{username}/approve")
async def admin_approve(username: str, admin: dict = Depends(verify_admin)):
    if not db.update_user_status(username, "approved"):
        raise HTTPException(400, "Failed to approve.")
    return {"status": "success"}


@app.post("/api/admin/users/{username}/ban")
async def admin_ban(username: str, admin: dict = Depends(verify_admin)):
    if not db.update_user_status(username, "banned"):
        raise HTTPException(400, "Failed to ban.")
    for sid, sess in list(active_sessions.items()):
        if sess.get("username") == username:
            del active_sessions[sid]
    return {"status": "success"}


@app.post("/api/admin/users/{username}/unban")
async def admin_unban(username: str, admin: dict = Depends(verify_admin)):
    if not db.update_user_status(username, "approved"):
        raise HTTPException(400, "Failed to unban.")
    return {"status": "success"}


class LimitRequest(BaseModel):
    app_limit: int

@app.put("/api/admin/users/{username}/limit")
async def admin_limit(username: str, req: LimitRequest, admin: dict = Depends(verify_admin)):
    if req.app_limit < 0:
        raise HTTPException(400, "Limit must be positive.")
    if not db.update_user_app_limit(username, req.app_limit):
        raise HTTPException(400, "Failed to update limit.")
    return {"status": "success"}


class QuotaRequest(BaseModel):
    ram_mb: int
    cpu_pct: int
    disk_mb: int

@app.put("/api/admin/users/{username}/quota")
async def admin_quota(username: str, req: QuotaRequest, admin: dict = Depends(verify_admin)):
    if req.ram_mb < 0 or req.cpu_pct < 0 or req.disk_mb < 0:
        raise HTTPException(400, "Quota values must be positive.")
    if not db.update_user_quota(username, req.ram_mb, req.cpu_pct, req.disk_mb):
        raise HTTPException(400, "Failed to update quota.")
    return {"status": "success"}


@app.get("/api/admin/users/{username}/usage")
async def admin_usage(username: str, admin: dict = Depends(verify_admin)):
    return driver.get_user_usage(username)


@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, admin: dict = Depends(verify_admin)):
    for a in db.get_user_apps(username):
        driver.delete_files(a["id"])
        db.delete_app(a["id"])
    if not db.delete_user(username):
        raise HTTPException(400, "Failed to delete user.")
    for sid, sess in list(active_sessions.items()):
        if sess.get("username") == username:
            del active_sessions[sid]
    return {"status": "success"}


@app.get("/api/admin/settings")
async def admin_get_settings(admin: dict = Depends(verify_admin)):
    return {
        "auto_approve": db.get_setting("auto_approve", "0") == "1",
        "default_ram": int(db.get_setting("default_ram", "512")),
        "default_cpu": int(db.get_setting("default_cpu", "100")),
        "default_disk": int(db.get_setting("default_disk", "1024")),
        "notify_bot_token": db.get_setting("notify_bot_token", ""),
        "notify_chat_id": db.get_setting("notify_chat_id", ""),
        "panel_name": db.get_setting("panel_name", "VPS-PANEL"),
    }


class SettingsUpdate(BaseModel):
    auto_approve: bool
    default_ram: Optional[int] = None
    default_cpu: Optional[int] = None
    default_disk: Optional[int] = None
    notify_bot_token: Optional[str] = ""
    notify_chat_id: Optional[str] = ""
    panel_name: Optional[str] = None

@app.put("/api/admin/settings")
async def admin_update_settings(req: SettingsUpdate, admin: dict = Depends(verify_admin)):
    db.set_setting("auto_approve", "1" if req.auto_approve else "0")
    if req.default_ram is not None:
        db.set_setting("default_ram", max(64, req.default_ram))
    if req.default_cpu is not None:
        db.set_setting("default_cpu", max(1, min(100, req.default_cpu)))
    if req.default_disk is not None:
        db.set_setting("default_disk", max(64, req.default_disk))
    db.set_setting("notify_bot_token", (req.notify_bot_token or "").strip())
    db.set_setting("notify_chat_id", (req.notify_chat_id or "").strip())
    if req.panel_name:
        db.set_setting("panel_name", req.panel_name.strip()[:32])
    return {"status": "success"}


@app.post("/api/admin/alert-test")
async def admin_alert_test(admin: dict = Depends(verify_admin)):
    """Send a live test notification to the configured Telegram alert bot."""
    result = driver._alert("✅ VPS-PANEL test notification — alert pipeline works!", key=None, wait=True)
    return {"status": "success" if result.get("ok") else "error", "detail": result.get("detail")}


class ChangePasswordRequest(BaseModel):
    new_password: str

@app.post("/api/admin/change-password")
async def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user),
                          session_id: Optional[str] = Cookie(None)):
    if len(req.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if not db.update_password(user["username"], req.new_password):
        raise HTTPException(400, "Failed to update password.")
    for sid, sess in list(active_sessions.items()):
        if sess.get("username") == user["username"] and sid != session_id:
            del active_sessions[sid]
    return {"status": "success"}


@app.get("/api/admin/backup")
async def admin_backup(admin: dict = Depends(verify_admin)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db.DB_PATH, arcname="panel.db")
        logs = "data/logs"
        if os.path.isdir(logs):
            for f in sorted(os.listdir(logs)):
                p = os.path.join(logs, f)
                if os.path.isfile(p):
                    zf.write(p, arcname=f"logs/{f}")
        zf.writestr("backup-info.json", json.dumps({
            "exported_at": datetime.datetime.now().isoformat(), "app": "VPS-PANEL"
        }, indent=2))
    buf.seek(0)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="panel-backup-{stamp}.zip"'})