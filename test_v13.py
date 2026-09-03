import urllib.request, http.cookiejar, json, urllib.parse, time, os, socket, subprocess, glob
BASE = "http://127.0.0.1:8080"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def multipart(filename, content, ctype="application/octet-stream"):
    b = os.urandom(16).hex().encode()
    head = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode()
    return head + content + f"\r\n--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"

def csrf():
    return next((c.value for c in jar if c.name == "csrf_token"), "")

def call(path, method="GET", data=None, headers=None):
    h = dict(headers or {}); body = None
    if isinstance(data, bytes): body = data
    elif data is not None:
        body = urllib.parse.urlencode(data).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    if method not in ("GET", "HEAD") and not any(k.lower() == "x-csrf-token" for k in h):
        h["X-CSRF-Token"] = csrf()
    try:
        r = op.open(urllib.request.Request(BASE + path, data=body, headers=h, method=method), timeout=40)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

call("/login", "POST", {"username": "admin", "password": "admin123"})
P = []; F = []
def chk(n, ok, x=""):
    (P if ok else F).append(n)
    print(("  PASS " if ok else "  FAIL ") + n, str(x)[:110])

# ============ PTB template end-to-end ============
st, raw = call("/api/templates")
tpl = json.loads(raw)
chk("templates list (ptb/pyrogram/telethon/grammy)", st == 200 and len(tpl) == 4, [t["id"] for t in tpl])
st, raw = call("/api/apps", "POST", {"name": "tpl-ptb", "app_type": "python", "entrypoint": "main.py",
                                     "source_type": "template", "template_id": "ptb", "auto_restart": "false"})
aid = json.loads(raw)["id"]; chk("ptb app created", st == 200, raw[:60])
st, raw = call(f"/api/apps/{aid}/files/read?path=main.py")
chk("ptb main.py deployed", st == 200 and b"python-telegram-bot" in raw.lower(), st)
for _ in range(90):
    st, raw = call(f"/api/apps/{aid}")
    d = json.loads(raw)
    if d.get("status") not in ("installing", "new"): break
    time.sleep(2)
st, raw = call(f"/api/apps/{aid}/logs"); txt = raw.decode("utf-8", "replace")
chk("ptb deps installed", "Dependencies ready" in txt, txt[-90:])
st, raw = call(f"/api/apps/{aid}/start", "POST")
time.sleep(6)
st, raw = call(f"/api/apps/{aid}/logs"); txt = raw.decode("utf-8", "replace")
chk("ptb friendly BOT_TOKEN msg", "BOT_TOKEN is not set" in txt, txt[-110:])
call(f"/api/apps/{aid}", "DELETE")

# ============ dev mode ============
code = '''import os,time,http.server
port=int(os.environ.get("PORT","8000"))
class H(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b"dev")
 def log_message(self,*a):pass
http.server.HTTPServer(("127.0.0.1",port),H).serve_forever()
'''
st, raw = call("/api/apps", "POST", {"name": "dev-probe", "app_type": "python", "entrypoint": "main.py",
                                     "source_type": "paste", "paste_code": code, "auto_restart": "false"})
aid = json.loads(raw)["id"]
call(f"/api/apps/{aid}/devmode", "POST", data=json.dumps({"enabled": True}).encode(), headers={"Content-Type": "application/json"})
call(f"/api/apps/{aid}/start", "POST")
for _ in range(45):
    st, raw = call(f"/api/apps/{aid}")
    port = json.loads(raw).get("port")
    if port:
        try:
            s = socket.create_connection(("127.0.0.1", port), 1); s.close(); break
        except OSError: pass
    time.sleep(1)
def app_pid():
    appdir = os.path.realpath(f"data/apps/{aid}")
    for pid in os.listdir("/proc"):
        if not pid.isdigit(): continue
        try:
            if os.path.realpath(f"/proc/{pid}/cwd") != appdir: continue
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")
            if b"main.py" in cmd: return int(pid)
        except OSError: pass
    return None
pid1 = app_pid()
time.sleep(4)  # let watcher baseline
st, raw = call(f"/api/apps/{aid}/files/write?path=main.py", "POST", data=("# changed\n" + code).encode(), headers={"Content-Type": "text/plain"})
chk("dev write ok", st == 200, st)
changed = False; pid2 = pid1
for _ in range(8):
    time.sleep(2)
    st, raw = call(f"/api/apps/{aid}/logs"); txt = raw.decode("utf-8", "replace")
    if "[DEV] file change detected" in txt: changed = True
    pid2 = app_pid()
    if changed and pid2 and pid2 != pid1: break
chk("dev mode restarted on file save", changed and pid2 and pid2 != pid1, f"pid1={pid1} pid2={pid2}")
call(f"/api/apps/{aid}", "DELETE")

# ============ quota enforcement (non-admin) ============
st, raw = call("/register", "POST", {"username": "quota_rt", "password": "quota123"})
call("/logout", "POST"); call("/login", "POST", {"username": "admin", "password": "admin123"})
call("/api/admin/users/quota_rt/approve", "POST")
call("/api/admin/users/quota_rt/quota", "PUT", data=json.dumps({"ram_mb": 128, "cpu_pct": 50, "disk_mb": 1}).encode(), headers={"Content-Type": "application/json"})
jar2 = http.cookiejar.CookieJar()
op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar2))
def call2(path, method="GET", data=None, headers=None):
    h = dict(headers or {}); body = None
    if isinstance(data, bytes): body = data
    elif data is not None:
        body = urllib.parse.urlencode(data).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    tok = next((c.value for c in jar2 if c.name == "csrf_token"), "")
    if method not in ("GET", "HEAD"): h["X-CSRF-Token"] = tok
    try:
        r = op2.open(urllib.request.Request(BASE + path, data=body, headers=h, method=method), timeout=40)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
call2("/login", "POST", {"username": "quota_rt", "password": "quota123"})
st, raw = call2("/api/apps", "POST", {"name": "q-app", "app_type": "python", "entrypoint": "main.py",
                                      "source_type": "paste", "paste_code": code, "auto_restart": "false"})
qid = json.loads(raw)["id"]
big = os.urandom(2 * 1024 * 1024)
mbody, mct = multipart("big.bin", big)
st, raw = call2(f"/api/apps/{qid}/files/upload", "POST", data=mbody, headers={"Content-Type": mct})
chk("upload blocked over quota (400)", st == 400, st)
left = [f for f in glob.glob(f"data/apps/{qid}/**", recursive=True) if os.path.isfile(f)]
chk("no leftover partial upload", not left or max(os.path.getsize(f) for f in left) < 1_000_000, len(left))
st, raw = call2(f"/api/apps/{qid}/files/write?path=main.py", "POST", data=b"y" * 2_000_000, headers={"Content-Type": "text/plain"})
chk("write blocked over quota (400)", st == 400, st)
call2(f"/api/apps/{qid}", "DELETE")
call2("/logout", "POST")

# ============ alert test pipeline ============
call("/login", "POST", {"username": "admin", "password": "admin123"})
st, raw = call("/api/admin/alert-test", "POST")
chk("alert-test no-token -> friendly error", st == 200 and b"not configured" in raw, raw[:100])
full = {"auto_approve": False, "default_ram": 512, "default_cpu": 100, "default_disk": 1024,
        "notify_bot_token": "123:FAKE", "notify_chat_id": "999", "panel_name": "VPS-PANEL"}
st, raw = call("/api/admin/settings", "PUT", data=json.dumps(full).encode(), headers={"Content-Type": "application/json"})
chk("settings save fake token", st == 200, st)
st, raw = call("/api/admin/alert-test", "POST")
chk("alert-test fake token -> tg error shown", st == 200 and b"error" in raw, raw[:120])
full["notify_bot_token"] = ""; full["notify_chat_id"] = ""
call("/api/admin/settings", "PUT", data=json.dumps(full).encode(), headers={"Content-Type": "application/json"})

# ============ WS pass-through ============
ws_code = '''import asyncio, os, websockets
async def handler(ws, path):
    async for msg in ws:
        await ws.send("echo:" + msg)
async def main():
    port = int(os.environ.get("PORT", "8000"))
    async with websockets.serve(handler, "127.0.0.1", port):
        await asyncio.Future()
asyncio.run(main())
'''
st, raw = call("/api/apps", "POST", {"name": "ws-app", "app_type": "python", "entrypoint": "main.py",
                                     "source_type": "paste", "paste_code": ws_code, "auto_restart": "false"})
wid = json.loads(raw)["id"]
call(f"/api/apps/{wid}/start", "POST")
for _ in range(45):
    st, raw = call(f"/api/apps/{wid}")
    port = json.loads(raw).get("port")
    if port:
        try:
            s = socket.create_connection(("127.0.0.1", port), 1); s.close(); break
        except OSError: pass
    time.sleep(1)
import websockets, asyncio
async def ws_test():
    async with websockets.connect(f"ws://127.0.0.1:8080/app/{wid}/ws/sock") as ws:
        await ws.send("hello")
        out = await asyncio.wait_for(ws.recv(), timeout=5)
        return out
out = asyncio.run(ws_test())
chk("WS via /app/{id}/ws/ echo", out == "echo:hello", out)
call(f"/api/apps/{wid}/files/write?path=x.txt", "POST", data=b"x", headers={"Content-Type": "text/plain"})  # trigger nothing
call(f"/api/apps/{wid}", "DELETE")

# ============ log rotation ============
rot_code = '''import os, time
for i in range(120):
    print("PADDING" * 8000)
    time.sleep(0.05)
'''
st, raw = call("/api/apps", "POST", {"name": "rot-app", "app_type": "python", "entrypoint": "main.py",
                                     "source_type": "paste", "paste_code": rot_code, "auto_restart": "false"})
rid = json.loads(raw)["id"]
call(f"/api/apps/{rid}/start", "POST")
time.sleep(16)
logf = f"data/logs/{rid}.log"
size = os.path.getsize(logf) if os.path.exists(logf) else 0
chk("log rotated below 5MB", size < 5 * 1024 * 1024, size)
txt = open(logf, encoding="utf-8", errors="replace").read()
chk("rotation marker present", "Log rotated" in txt, txt[-80:])
call(f"/api/apps/{rid}", "DELETE")

print(f"\n===== NEW-FEATURES v2: {len(P)} passed, {len(F)} failed =====")
for f in F: print("FAILED:", f)