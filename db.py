"""SQLite layer for VPS-PANEL — users, apps, settings. stdlib only."""
import os
import json
import sqlite3
import hashlib
import datetime
import threading

DB_PATH = os.environ.get("VPSPANEL_DB", os.path.join("data", "panel.db"))
_lock = threading.RLock()  # RLock: some db functions call other db functions while holding the lock


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    db_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"{salt.hex()}:{db_hash.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        salt_hex, hash_hex = hashed.split(":")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        db_hash = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, 100000)
        return db_hash == expected
    except Exception:
        return False


def _conn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username      TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|banned
            ip_address    TEXT DEFAULT 'unknown',
            device_name   TEXT DEFAULT 'unknown',
            registered_at TEXT,
            app_limit     INTEGER DEFAULT 3,
            quota_ram     INTEGER DEFAULT 512,   -- MB
            quota_cpu     INTEGER DEFAULT 100,   -- percent
            quota_disk    INTEGER DEFAULT 1024   -- MB
        );
        CREATE TABLE IF NOT EXISTS apps (
            id           TEXT PRIMARY KEY,
            owner        TEXT NOT NULL,
            name         TEXT NOT NULL,
            type         TEXT NOT NULL,           -- static|python|node|php
            entrypoint   TEXT DEFAULT '',
            env_json     TEXT DEFAULT '{}',
            auto_restart INTEGER DEFAULT 1,
            status       TEXT DEFAULT 'stopped',  -- stopped|running|error|installing
            port         INTEGER,
            alias        TEXT UNIQUE,
            created_at   TEXT,
            last_started TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id         TEXT PRIMARY KEY,
            app_id     TEXT NOT NULL,
            owner      TEXT NOT NULL,
            name       TEXT NOT NULL,
            schedule   TEXT NOT NULL,   -- cron-ish "min hour dom mon dow" (supports */n)
            command    TEXT NOT NULL,
            enabled    INTEGER DEFAULT 1,
            last_run   TEXT,
            next_run   TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id         TEXT PRIMARY KEY,
            app_id     TEXT NOT NULL,
            owner      TEXT NOT NULL,
            name       TEXT NOT NULL,
            file       TEXT NOT NULL,
            size       INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            id         TEXT PRIMARY KEY,
            user       TEXT NOT NULL,
            label      TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            created_at TEXT,
            last_used  TEXT
        );
        """)
        # Ensure default admin exists (ADMIN_PASSWORD env or 'admin123')
        row = conn.execute("SELECT 1 FROM users WHERE username='admin'").fetchone()
        if not row:
            default_password = os.environ.get("ADMIN_PASSWORD", "admin123")
            conn.execute(
                "INSERT INTO users (username,password_hash,role,status,ip_address,device_name,registered_at,app_limit,quota_ram,quota_cpu,quota_disk)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("admin", hash_password(default_password), "admin", "approved",
                 "127.0.0.1", "Local Server", datetime.datetime.now().isoformat(),
                 999, 4096, 400, 8192),
            )
        # Default settings
        defaults = {
            "auto_approve": "0",
            "default_ram": "512",
            "default_cpu": "100",
            "default_disk": "1024",
            "notify_bot_token": "",
            "notify_chat_id": "",
            "panel_name": "VPS-PANEL",
        }
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
        # apps stats columns (added in v1.2) — additive migration
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(apps)").fetchall()}
        if "requests" not in cols:
            conn.execute("ALTER TABLE apps ADD COLUMN requests INTEGER DEFAULT 0")
        if "bytes_served" not in cols:
            conn.execute("ALTER TABLE apps ADD COLUMN bytes_served INTEGER DEFAULT 0")
        if "dev_mode" not in cols:
            conn.execute("ALTER TABLE apps ADD COLUMN dev_mode INTEGER DEFAULT 0")


# ---------- users ----------
def get_user(username: str):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def get_all_users():
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY registered_at").fetchall()


def create_user(username, password_plain, role="user", status="pending",
                ip="unknown", device="unknown", app_limit=3):
    with _lock, _conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return None
        ram = int(get_setting("default_ram", "512"))
        cpu = int(get_setting("default_cpu", "100"))
        disk = int(get_setting("default_disk", "1024"))
        conn.execute(
            "INSERT INTO users (username,password_hash,role,status,ip_address,device_name,registered_at,app_limit,quota_ram,quota_cpu,quota_disk)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (username, hash_password(password_plain), role, status, ip, device,
             datetime.datetime.now().isoformat(),
             999 if role == "admin" else app_limit, ram, cpu, disk),
        )
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def update_user_status(username, status):
    with _lock, _conn() as conn:
        cur = conn.execute("UPDATE users SET status=? WHERE username=? AND username!='admin'", (status, username))
        return cur.rowcount > 0


def update_user_app_limit(username, app_limit):
    with _lock, _conn() as conn:
        cur = conn.execute("UPDATE users SET app_limit=? WHERE username=?", (app_limit, username))
        return cur.rowcount > 0


def update_user_quota(username, ram, cpu, disk):
    with _lock, _conn() as conn:
        cur = conn.execute("UPDATE users SET quota_ram=?, quota_cpu=?, quota_disk=? WHERE username=?",
                           (ram, cpu, disk, username))
        return cur.rowcount > 0


def update_password(username, new_password):
    with _lock, _conn() as conn:
        cur = conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                           (hash_password(new_password), username))
        return cur.rowcount > 0


def delete_user(username):
    with _lock, _conn() as conn:
        if username == "admin":
            return False
        conn.execute("DELETE FROM apps WHERE owner=?", (username,))
        cur = conn.execute("DELETE FROM users WHERE username=?", (username,))
        return cur.rowcount > 0


# ---------- apps ----------
def create_app(app_id, owner, name, app_type, entrypoint="", env_vars=None,
               auto_restart=True, alias=None):
    alias = alias or None  # "" -> NULL so UNIQUE(alias) doesn't collide on empty
    with _lock, _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO apps (id,owner,name,type,entrypoint,env_json,auto_restart,status,alias,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (app_id, owner, name, app_type, entrypoint,
                 json.dumps(env_vars or {}), 1 if auto_restart else 0, "stopped",
                 alias, datetime.datetime.now().isoformat()),
            )
            row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
            return dict(row) if row else None
        except sqlite3.IntegrityError:
            return None  # alias conflict or duplicate id


def get_app(app_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()


def get_app_by_alias(alias):
    if not alias:
        return None
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM apps WHERE alias=?", (alias,)).fetchone()


def get_all_apps():
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM apps ORDER BY created_at").fetchall()


def get_user_apps(username):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM apps WHERE owner=? ORDER BY created_at", (username,)).fetchall()


def get_running_apps():
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM apps WHERE status='running'").fetchall()


def update_app(app_id, **updates):
    allowed = {"name", "entrypoint", "env_json", "auto_restart", "status", "port", "alias", "last_started", "dev_mode"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return None
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as conn:
        try:
            conn.execute(f"UPDATE apps SET {sets} WHERE id=?", (*fields.values(), app_id))
        except sqlite3.IntegrityError:
            return None
        return get_app(app_id)


def delete_app(app_id):
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM apps WHERE id=?", (app_id,))
        conn.execute("DELETE FROM cron_jobs WHERE app_id=?", (app_id,))
        conn.execute("DELETE FROM snapshots WHERE app_id=?", (app_id,))
        return cur.rowcount > 0


# ---------- settings ----------
def get_setting(key, default=None):
    with _lock, _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with _lock, _conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, str(value)))


# ---------- app stats (requests / bytes) ----------
def add_app_stats(app_id, requests=0, bytes_served=0):
    if not requests and not bytes_served:
        return
    with _lock, _conn() as conn:
        conn.execute("UPDATE apps SET requests=requests+?, bytes_served=bytes_served+? WHERE id=?",
                     (requests, bytes_served, app_id))


# ---------- cron jobs ----------
def create_cron(app_id, owner, name, schedule, command):
    cid = "cron_" + os.urandom(8).hex()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO cron_jobs (id,app_id,owner,name,schedule,command,enabled,created_at)"
            " VALUES (?,?,?,?,?,?,1,?)",
            (cid, app_id, owner, name, schedule, command, datetime.datetime.now().isoformat()),
        )
        row = conn.execute("SELECT * FROM cron_jobs WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None


def get_cron_jobs(app_id=None, owner=None):
    q = "SELECT * FROM cron_jobs"
    args = []
    if app_id:
        q += " WHERE app_id=?"
        args.append(app_id)
    if owner:
        q += " AND owner=?" if app_id else " WHERE owner=?"
        args.append(owner)
    q += " ORDER BY created_at"
    with _lock, _conn() as conn:
        return conn.execute(q, args).fetchall()


def get_cron_job(job_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM cron_jobs WHERE id=?", (job_id,)).fetchone()


def delete_cron(job_id):
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM cron_jobs WHERE id=?", (job_id,))
        return cur.rowcount > 0


def update_cron(job_id, **updates):
    allowed = {"enabled", "last_run", "next_run", "schedule", "command", "name"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return None
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE cron_jobs SET {sets} WHERE id=?", (*fields.values(), job_id))
        row = conn.execute("SELECT * FROM cron_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


# ---------- snapshots ----------
def create_snapshot(app_id, owner, name, file, size):
    sid = "snap_" + os.urandom(8).hex()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO snapshots (id,app_id,owner,name,file,size,created_at) VALUES (?,?,?,?,?,?,?)",
            (sid, app_id, owner, name, file, size, datetime.datetime.now().isoformat()),
        )
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None


def get_snapshots(app_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM snapshots WHERE app_id=? ORDER BY created_at DESC", (app_id,)).fetchall()


def get_snapshot(snap_id):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()


def update_snapshot(snap_id, **updates):
    allowed = {"name", "file", "size"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return None
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as conn:
        conn.execute(f"UPDATE snapshots SET {sets} WHERE id=?", (*fields.values(), snap_id))
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        return dict(row) if row else None


def delete_snapshot(snap_id):
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM snapshots WHERE id=?", (snap_id,))
        return cur.rowcount > 0


# ---------- api tokens ----------
def create_api_token(user, label, token_hash):
    tid = "tok_" + os.urandom(8).hex()
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO api_tokens (id,user,label,token_hash,created_at) VALUES (?,?,?,?,?)",
            (tid, user, label, token_hash, datetime.datetime.now().isoformat()),
        )
        row = conn.execute("SELECT * FROM api_tokens WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None


def get_api_tokens(user=None):
    q = "SELECT * FROM api_tokens"
    args = []
    if user:
        q += " WHERE user=?"
        args.append(user)
    q += " ORDER BY created_at"
    with _lock, _conn() as conn:
        return conn.execute(q, args).fetchall()


def get_api_token_by_hash(token_hash):
    with _lock, _conn() as conn:
        return conn.execute("SELECT * FROM api_tokens WHERE token_hash=?", (token_hash,)).fetchone()


def touch_api_token(tid):
    with _lock, _conn() as conn:
        conn.execute("UPDATE api_tokens SET last_used=? WHERE id=?",
                     (datetime.datetime.now().isoformat(), tid))


def delete_api_token(tid):
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM api_tokens WHERE id=?", (tid,))
        return cur.rowcount > 0