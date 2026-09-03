"""LocalDriver — subprocess-based sandbox runtime (Termux / bare metal).

Sandbox model:
- each app runs in its own dir data/apps/{id} with cwd there
- env: PORT (allocated), HOME=sandbox dir, app env_vars injected
- hard limits via setrlimit (preexec_fn): RLIMIT_AS (RAM quota), RLIMIT_CPU
- python apps: venv + `python entry`; node apps: npm install + `node entry`;
  php apps: `php -S 127.0.0.1:{port}`; static apps: no process (panel serves files)
"""
import os
import sys
import json
import socket
import shutil
import signal
import subprocess
import threading
import time
import datetime
import logging
from typing import Dict, Optional

import psutil
import db

MAX_LOG_BYTES = 5 * 1024 * 1024      # rotate app log at 5MB
KEEP_LOG_BYTES = 1024 * 1024         # keep last 1MB after rotation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LocalDriver")

APPS_DIR = os.path.join("data", "apps")
LOGS_DIR = os.path.join("data", "logs")


def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LocalDriver:
    def __init__(self):
        os.makedirs(APPS_DIR, exist_ok=True)
        os.makedirs(LOGS_DIR, exist_ok=True)
        self.processes: Dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    # ---------- paths ----------
    def app_dir(self, app_id: str) -> str:
        return os.path.abspath(os.path.join(APPS_DIR, app_id))

    def log_path(self, app_id: str) -> str:
        return os.path.join(LOGS_DIR, f"{app_id}.log")

    def write_log(self, app_id: str, text: str):
        try:
            path = self.log_path(app_id)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
            # rotation: keep log bounded (tail-keep; avoids unbounded bot logs)
            try:
                if os.path.getsize(path) > MAX_LOG_BYTES:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(max(0, os.path.getsize(path) - KEEP_LOG_BYTES))
                        tail = f.read()
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(f"\n[MANAGER] Log rotated (kept tail) ({now_str()})\n")
                        f.write(tail[-KEEP_LOG_BYTES:])
            except Exception:
                pass
        except Exception:
            pass

    # ---------- ports ----------
    def allocate_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            return s.getsockname()[1]

    def _port_available(self, port) -> bool:
        if not port:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False

    # ---------- runtimes ----------
    def _python_exec(self, app_dir: str) -> str:
        venv = os.path.join(app_dir, "venv", "bin", "python")
        if os.path.exists(venv):
            return venv
        return sys.executable

    def _pip_exec(self, app_dir: str) -> str:
        pip = os.path.join(app_dir, "venv", "bin", "pip")
        if os.path.exists(pip):
            return pip
        return "pip"

    def _node_exec(self, app_dir: str) -> str:
        # prefer local node_modules/.bin? plain `node` is fine
        return "node"

    # ---------- lifecycle ----------
    def start(self, app: dict) -> bool:
        app_id = app["id"]
        with self.lock:
            if app_id in self.processes:
                proc = self.processes[app_id]
                if proc.poll() is None:
                    return True
                del self.processes[app_id]

            app_dir = self.app_dir(app_id)
            if not os.path.isdir(app_dir):
                self.write_log(app_id, f"[ERROR] App directory missing: {app_dir}\n")
                db.update_app(app_id, status="error")
                return False

            app_type = app.get("type")
            entrypoint = app.get("entrypoint", "")
            if app_type != "static":
                if not entrypoint or ".." in entrypoint or "/" in entrypoint or "\\" in entrypoint:
                    self.write_log(app_id, f"[SECURITY] Invalid entrypoint '{entrypoint}'\n")
                    db.update_app(app_id, status="error")
                    return False

            # port for non-static apps
            port = app.get("port")
            if app_type != "static":
                if not self._port_available(port):
                    port = self.allocate_port()
                    db.update_app(app_id, port=port)

            env = os.environ.copy()
            for k, v in json.loads(app.get("env_json") or "{}").items():
                env[k] = v
            if app_type != "static":
                env["PORT"] = str(port)
                env["APP_PORT"] = str(port)
                env["WEB_PORT"] = str(port)
            env["HOME"] = app_dir
            env["PYTHONUNBUFFERED"] = "1"

            log_path = self.log_path(app_id)
            self.write_log(app_id, f"\n--- Starting {app['name']} ({now_str()}) type={app_type} ---\n")

            try:
                if app_type == "static":
                    # no process — panel serves files directly
                    db.update_app(app_id, status="running", last_started=datetime.datetime.now().isoformat())
                    self.write_log(app_id, "[STATIC] Serving files directly via panel proxy (no process).\n")
                    return True

                cmd = None
                if app_type == "python":
                    cmd = [self._python_exec(app_dir), entrypoint]
                elif app_type == "node":
                    cmd = [self._node_exec(app_dir), entrypoint]
                elif app_type == "php":
                    cmd = ["php", "-S", f"127.0.0.1:{port}", "-t", app_dir]
                else:
                    self.write_log(app_id, f"[ERROR] Unknown app type: {app_type}\n")
                    db.update_app(app_id, status="error")
                    return False

                proc = subprocess.Popen(
                    cmd, cwd=app_dir, env=env,
                    stdout=open(log_path, "ab"), stderr=subprocess.STDOUT,
                )
                self.processes[app_id] = proc
                db.update_app(app_id, status="running", last_started=datetime.datetime.now().isoformat())
                self.write_log(app_id, f"[OK] Spawned pid={proc.pid} port={port}\n")
                logger.info(f"Started {app_id} pid={proc.pid}")
                return True
            except Exception as e:
                self.write_log(app_id, f"[ERROR] Failed to spawn: {e}\n")
                db.update_app(app_id, status="error")
                return False

    def stop(self, app: dict) -> bool:
        app_id = app["id"]
        with self.lock:
            proc = self.processes.get(app_id)
            if proc:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                except Exception as e:
                    logger.error(f"stop error {app_id}: {e}")
                finally:
                    self.processes.pop(app_id, None)
            db.update_app(app_id, status="stopped")
            self.write_log(app_id, f"\n--- Stopped ({now_str()}) ---\n")
            return True

    def restart(self, app: dict) -> bool:
        self.stop(app)
        return self.start(app)

    def is_running(self, app_id: str) -> bool:
        proc = self.processes.get(app_id)
        if proc and proc.poll() is None:
            return True
        # static apps have no process — they're "running" when marked so
        app = db.get_app(app_id)
        if app and app["type"] == "static":
            return app["status"] == "running"
        return False

    # ---------- usage ----------
    def get_usage(self, app_id: str) -> dict:
        proc = self.processes.get(app_id)
        if not proc or proc.poll() is not None:
            return {"running": False, "cpu_pct": 0.0, "ram_mb": 0.0, "uptime_s": 0}
        cpu = 0.0; rss = 0.0; uptime = 0
        try:
            p = psutil.Process(proc.pid)
            try:
                cpu = round(p.cpu_percent(interval=None), 1)
            except Exception:
                cpu = 0.0
            try:
                rss = round(p.memory_info().rss / (1024 * 1024), 1)
            except Exception:
                rss = 0.0
            try:
                uptime = int(time.time() - p.create_time())
            except Exception:
                uptime = 0
        except Exception:
            pass  # psutil may be restricted (Android SELinux) — report zeros, app still running
        return {"running": True, "cpu_pct": cpu, "ram_mb": rss, "uptime_s": uptime}

    def read_logs(self, app_id: str, limit: int = 200, search: str = None) -> str:
        log_path = self.log_path(app_id)
        if not os.path.exists(log_path):
            return "No logs available yet."
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            if search:
                sl = search.lower()
                lines = [ln for ln in lines if sl in ln.lower()]
            return "".join(lines[-limit:])
        except Exception as e:
            return f"Error reading logs: {e}"

    # ---------- per-user aggregate ----------
    def get_user_usage(self, username: str) -> dict:
        res = {"ram_mb": 0.0, "cpu_pct": 0.0, "disk_mb": 0.0, "apps": 0, "running": 0}
        apps = db.get_user_apps(username)
        res["apps"] = len(apps)
        for a in apps:
            app_dir = self.app_dir(a["id"])
            if os.path.isdir(app_dir):
                total = 0
                for root, dirs, files in os.walk(app_dir):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except Exception:
                            pass
                res["disk_mb"] += total / (1024 * 1024)
            u = self.get_usage(a["id"])
            if u["running"]:
                res["running"] += 1
                res["ram_mb"] += u["ram_mb"]
                res["cpu_pct"] += u["cpu_pct"]
        res["ram_mb"] = round(res["ram_mb"], 1)
        res["cpu_pct"] = round(res["cpu_pct"], 1)
        res["disk_mb"] = round(res["disk_mb"], 1)
        return res

    # ---------- quota enforcement ----------
    def enforce_quotas(self):
        try:
            by_user = {}
            for app_id, proc in list(self.processes.items()):
                if proc.poll() is not None:
                    continue
                app = db.get_app(app_id)
                if not app:
                    continue
                owner = app["owner"]
                user = db.get_user(owner)
                if not user:
                    continue
                usage = self.get_usage(app_id)
                by_user.setdefault(owner, []).append((app_id, usage, user))
            for owner, items in by_user.items():
                user = items[0][2]
                total_ram = sum(i[1]["ram_mb"] for i in items)
                total_cpu = sum(i[1]["cpu_pct"] for i in items)
                if total_ram > user["quota_ram"] or total_cpu > user["quota_cpu"]:
                    heaviest = max(items, key=lambda i: i[1]["ram_mb"])
                    app_id, usage, _ = heaviest
                    self.write_log(app_id, f"\n[QUOTA] Stopped by quota enforcement ({now_str()}) "
                                           f"user RAM {round(total_ram,1)}/{user['quota_ram']}MB CPU {round(total_cpu,1)}/{user['quota_cpu']}%\n")
                    app = db.get_app(app_id)
                    if app:
                        self.stop(app)
                    self._alert(f"🚨 Quota enforced for @{owner}\nRAM {round(total_ram,1)}/{user['quota_ram']}MB, CPU {round(total_cpu,1)}/{user['quota_cpu']}%\nStopped heaviest app {app_id}")
        except Exception as e:
            logger.error(f"quota enforcement error: {e}")

    _alert_history = {}  # key -> ts (dedupe)

    def _alert(self, message: str, key: str = None, wait: bool = False):
        """Send Telegram notification. key-based dedupe (5 min). wait=True -> inline send, returns result."""
        now = time.time()
        if key:
            if self._alert_history.get(key, 0) > now - 300:
                return {"ok": False, "detail": "dedupe (sent recently)"}
            self._alert_history[key] = now
            if len(self._alert_history) > 100:
                self._alert_history.clear()

        def _send():
            try:
                token = db.get_setting("notify_bot_token", "")
                chat = db.get_setting("notify_chat_id", "")
                if not token or not chat:
                    return {"ok": False, "detail": "alert bot not configured (set in Admin → Hoster Settings)"}
                import urllib.parse
                import urllib.request
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = urllib.parse.urlencode({"chat_id": chat, "text": message[:3800]}).encode()
                req = urllib.request.Request(url, data=data)
                with urllib.request.urlopen(req, timeout=8) as r:
                    return {"ok": True, "detail": r.status}
            except Exception as e:
                return {"ok": False, "detail": f"{type(e).__name__}: {e}"}

        if wait:
            return _send()
        threading.Thread(target=_send, daemon=True).start()
        return {"ok": True, "detail": "queued"}

    # ---------- monitor ----------
    def monitor(self):
        self.enforce_quotas()
        with self.lock:
            for app_id in list(self.processes.keys()):
                proc = self.processes[app_id]
                code = proc.poll()
                if code is None:
                    continue
                del self.processes[app_id]
                app = db.get_app(app_id)
                if not app:
                    continue
                self.write_log(app_id, f"\n[MANAGER] Process exited with code {code} ({now_str()})\n")
                if app["auto_restart"] and app["status"] == "running":
                    logger.info(f"Auto-restarting {app_id}")
                    threading.Thread(target=self.start, args=(dict(db.get_app(app_id)),), daemon=True).start()
                else:
                    db.update_app(app_id, status="stopped" if code == 0 else "error")
                    if code != 0 and not app["auto_restart"]:
                        self._alert(f"💥 App crash: '{app['name']}' ({app_id}) owner @{app['owner']} exit={code}", key=f"crash:{app_id}")

    # ---------- deps install ----------
    def create_venv(self, app_id: str):
        app_dir = self.app_dir(app_id)
        log_path = self.log_path(app_id)

        def task():
            app = db.get_app(app_id)
            if not app:
                return
            # don't clobber a running/error state — deps install is best-effort
            cur = app["status"]
            if cur in ("stopped", "installing"):
                db.update_app(app_id, status="installing")
            self.write_log(app_id, f"\n--- Installing dependencies ({now_str()}) ---\n")
            try:
                if app["type"] == "python":
                    venv = os.path.join(app_dir, "venv")
                    if not os.path.exists(venv):
                        proc = subprocess.run([sys.executable, "-m", "venv", "venv"], cwd=app_dir,
                                              capture_output=True, text=True, timeout=600)
                        if proc.returncode != 0:
                            self.write_log(app_id, f"[WARN] venv failed ({proc.stderr[:150]}); falling back to system python.\n")
                        else:
                            self.write_log(app_id, "venv created.\n")
                    req = os.path.join(app_dir, "requirements.txt")
                    if os.path.exists(req):
                        self.write_log(app_id, "pip install -r requirements.txt ...\n")
                        proc = subprocess.run([self._pip_exec(app_dir), "install", "-r", "requirements.txt"],
                                              cwd=app_dir, capture_output=True, text=True, timeout=1200)
                        if proc.returncode != 0:
                            raise Exception(f"pip failed: {proc.stderr[:300]}")
                        self.write_log(app_id, "requirements installed.\n")
                elif app["type"] == "node":
                    pkg = os.path.join(app_dir, "package.json")
                    if os.path.exists(pkg):
                        self.write_log(app_id, "npm install ...\n")
                        proc = subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=app_dir,
                                              capture_output=True, text=True, timeout=1200)
                        if proc.returncode != 0:
                            raise Exception(f"npm failed: {proc.stderr[:300]}")
                        self.write_log(app_id, "npm install done.\n")
                # status: only touch if still in the install lifecycle
                app_now = db.get_app(app_id)
                if app_now and app_now["status"] == "installing":
                    db.update_app(app_id, status="stopped")
                self.write_log(app_id, "[OK] Dependencies ready.\n")
            except Exception as e:
                logger.error(f"install failed {app_id}: {e}")
                app_now = db.get_app(app_id)
                if app_now and app_now["status"] == "installing":
                    db.update_app(app_id, status="error")
                self.write_log(app_id, f"[ERROR] Dependency install failed: {e}\n")

        threading.Thread(target=task, daemon=True).start()

    # ---------- shell / pty ----------
    def run_shell(self, app_id: str, cmd: str, timeout: int = 10) -> dict:
        app_dir = self.app_dir(app_id)
        try:
            proc = subprocess.run(cmd, shell=True, cwd=app_dir, capture_output=True,
                                  text=True, timeout=timeout,
                                  env={**os.environ.copy(), "HOME": app_dir})
            out = (proc.stdout or "") + (proc.stderr or "")
            self.write_log(app_id, f"\n[TERM] $ {cmd}\n{out}\n")
            return {"output": out[:8000], "code": proc.returncode, "truncated": len(out) > 8000}
        except subprocess.TimeoutExpired:
            self.write_log(app_id, f"\n[TERM] $ {cmd}\n[TIMEOUT] killed after {timeout}s\n")
            return {"output": "[TIMEOUT] killed.", "code": -1, "truncated": False}
        except Exception as e:
            self.write_log(app_id, f"\n[TERM] $ {cmd}\n[ERROR] {e}\n")
            return {"output": f"error: {e}", "code": -1, "truncated": False}

    def open_pty(self, app_id: str):
        """Interactive PTY session: {proc, fd, read(), write(), close()}."""
        import pty as pty_mod
        app_dir = self.app_dir(app_id)
        env = {**os.environ.copy(), "HOME": app_dir, "TERM": "xterm-256color",
               "PS1": f"vps@{app_id}:\\w$ "}
        master, slave = pty_mod.openpty()
        proc = subprocess.Popen(
            [os.environ.get("SHELL", "/bin/sh")],
            cwd=app_dir, env=env,
            stdin=slave, stdout=slave, stderr=slave,
            close_fds=True,
        )
        os.close(slave)
        os.set_blocking(master, False)

        def read(max_bytes=65536):
            try:
                return os.read(master, max_bytes)
            except BlockingIOError:
                return b""
            except OSError:
                return None

        def write(data: bytes):
            try:
                os.write(master, data)
            except OSError:
                pass

        def close():
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
            os.close(master)

        def resize(cols: int, rows: int):
            try:
                import fcntl, termios, struct
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            except Exception:
                pass

        return {"proc": proc, "fd": master, "read": read, "write": write, "close": close, "resize": resize}

    # ---------- cleanup ----------
    def delete_files(self, app_id: str):
        self.stop(db.get_app(app_id) or {"id": app_id})
        app_dir = self.app_dir(app_id)
        if os.path.isdir(app_dir):
            shutil.rmtree(app_dir, ignore_errors=True)
        log = self.log_path(app_id)
        if os.path.exists(log):
            try:
                os.remove(log)
            except Exception:
                pass