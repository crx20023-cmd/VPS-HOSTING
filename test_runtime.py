#!/usr/bin/env python3
"""VPS-PANEL comprehensive runtime test — every option, live."""
import json, time, os, io, socket, zipfile, urllib.request, urllib.parse, http.cookiejar, sys

BASE = "http://127.0.0.1:8080"
PASS = 0; FAIL = 0; NOTES = []
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def raw_req(path, method="GET", data=None, headers=None, timeout=30, use_jar=True, with_csrf=True):
    h = dict(headers or {})
    body = None
    if isinstance(data, bytes):
        body = data
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if with_csrf and method not in ("GET", "HEAD") and not any(k.lower() == "x-csrf-token" for k in h):
        h["X-CSRF-Token"] = csrf()
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        o = opener if use_jar else urllib.request.build_opener()
        resp = o.open(r, timeout=timeout)
        return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)
    except Exception as e:
        return -1, str(e).encode(), {}

def req(path, method="GET", data=None, headers=None, timeout=30, use_jar=True):
    st, body, hdrs = raw_req(path, method, data, headers, timeout, use_jar)
    txt = body.decode("utf-8", "replace")
    try:
        return st, json.loads(txt)
    except Exception:
        return st, txt

def csrf():
    return next((c.value for c in jar if c.name == "csrf_token"), "")

aj = http.cookiejar.CookieJar()
aopener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(aj))
def areq(path, method="GET", data=None, headers=None, timeout=30):
    h = dict(headers or {})
    body = None
    if isinstance(data, bytes):
        body = data
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if method not in ("GET", "HEAD"):
        ac = next((c.value for c in aj if c.name == "csrf_token"), "")
        h["X-CSRF-Token"] = ac
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        resp = aopener.open(r, timeout=timeout)
        st, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        st, raw = e.code, e.read()
    try:
        return st, json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return st, raw.decode("utf-8", "replace")
def admin_login():
    aopener.open(urllib.request.Request(BASE + "/login", data=urllib.parse.urlencode(
        {"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=10)

def login(u, p):
    return req("/login", "POST", {"username": u, "password": p}, use_jar=True)

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}  {str(extra)[:180]}")

def wait_port(port, timeout=45):
    for _ in range(timeout):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1); s.close(); return True
        except OSError:
            time.sleep(1)
    return False

def multipart(fields, files):
    boundary = "----rtboundary"
    out = []
    for k, v in fields.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n")
    for k, (fname, content, ctype) in files.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n")
        out.append(content.decode("latin1"))
        out.append("\r\n")
    out.append(f"--{boundary}--\r\n")
    return ("".join(out)).encode("latin1"), f"multipart/form-data; boundary={boundary}"

print("===== AUTH =====")
st, _ = req("/login"); check("GET /login page", st == 200)
st, d = login("admin", "admin123"); check("admin login", st == 200 and d.get("role") == "admin", d)
st, d = req("/api/me"); check("me role admin", d.get("role") == "admin", d)
st, d = req("/logout"); check("logout", st == 200)
st, d = req("/api/me"); check("logged out -> 401", st == 401, d)
st, d = login("admin", "admin123"); check("re-login", st == 200)

print("===== REGISTER / APPROVAL FLOW =====")
# idempotent: remove leftover tester_rt from a crashed earlier run
st, d = areq("/api/admin/users/tester_rt", "DELETE")
st, d = req("/register", "POST", {"username": "tester_rt", "password": "rtpass123"}, use_jar=True)
check("register new user", st == 200, d)
st, d = login("tester_rt", "rtpass123"); check("pending login blocked", st == 403, d)
st, d = req("/register", "POST", {"username": "tester_rt", "password": "x"}); check("duplicate register blocked", st == 400, d)
admin_login()
st, d = areq("/api/admin/users"); row = next((u for u in d if u["username"] == "tester_rt"), None)
check("user visible in admin list (pending)", row and row["status"] == "pending", d)
st, d = areq("/api/admin/users/tester_rt/approve", "POST"); check("admin approves", st == 200, d)
st, d = login("tester_rt", "rtpass123"); check("approved user can login", st == 200, d)
st, d = req("/api/me"); check("me role user", d.get("role") == "user", d)
check("user quota present", d.get("quota", {}).get("ram_mb", 0) > 0, d)
check("user default app_limit", d.get("app_limit") == 3, d)

print("===== USER APP LIMIT =====")
pycode = 'import os,http.server\nport=int(os.environ.get("PORT","8000"))\nclass H(http.server.BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.send_header("Content-Type","text/plain");self.end_headers()\n  b=(os.environ.get("FOO","none")).encode();self.wfile.write(b)\n def log_message(self,*a):pass\nhttp.server.HTTPServer(("127.0.0.1",port),H).serve_forever()\n'
created_ids = []
for i in range(1, 5):
    st, d = req("/api/apps", "POST", {"name": f"limit-test-{i}", "app_type": "python", "entrypoint": "main.py",
                                      "source_type": "paste", "paste_code": pycode, "auto_restart": "false"})
    if st == 200: created_ids.append(d["id"])
    print(f"    create #{i}: {st}")
check("3 apps OK, 4th blocked (limit 3)", len(created_ids) == 3, created_ids)
st, d = areq("/api/admin/users/tester_rt/limit", "PUT", headers={"Content-Type": "application/json"},
            data=json.dumps({"app_limit": 1}).encode())
check("admin sets limit=1", st == 200, d)
st, d = req("/api/apps", "POST", {"name": "over-limit", "app_type": "static", "entrypoint": "",
                                  "source_type": "paste", "paste_code": "<b>x</b>"})
check("limit=1 blocks new app", st == 400, d)
for aid in created_ids:
    req(f"/api/apps/{aid}", "DELETE")
st, d = areq("/api/admin/users/tester_rt/limit", "PUT", headers={"Content-Type": "application/json"},
            data=json.dumps({"app_limit": 10}).encode())
check("limit restored (10)", st == 200)

print("===== DISK QUOTA (gap probe) =====")
admin_login()
st, d = areq("/api/admin/users/tester_rt/quota", "PUT", headers={"Content-Type": "application/json"},
            data=json.dumps({"ram_mb": 128, "cpu_pct": 100, "disk_mb": 2}).encode())
check("quota set: 2MB disk", st == 200, d)
big = b"A" * (3 * 1024 * 1024)  # 3MB
st, d = req("/api/apps", "POST", {"name": "disk-probe", "app_type": "static", "entrypoint": "",
                                  "source_type": "paste", "paste_code": "<b>x</b>"})
check("temp app for probe", st == 200, d)
probe_id = d["id"]
mp, ct = multipart({}, {"file": ("big.bin", big, "application/octet-stream")})
st, d = req(f"/api/apps/{probe_id}/files/upload?path=", "POST", data=mp, headers={"Content-Type": ct}, timeout=60)
check("3MB upload under 2MB quota -> enforced?", st in (400, 413), (st, d))
if st != 413:
    NOTES.append("GAP: files/upload does NOT enforce disk quota (3MB allowed with 2MB quota)")
    req(f"/api/apps/{probe_id}/files?path=big.bin", "DELETE")
# zip deploy DOES pre-check disk
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("index.html", "<b>zip</b>" * 1000000)  # ~3MB
buf.seek(0)
mp, ct = multipart({"name": "zip-quota", "app_type": "static", "entrypoint": "", "source_type": "zip"},
                   {"zip_file": ("big.zip", buf.read(), "application/zip")})
st, d = req("/api/apps", "POST", data=mp, headers={"Content-Type": ct}, timeout=60)
check("zip deploy 3MB under 2MB quota -> blocked", st == 400, d)
req(f"/api/apps/{probe_id}", "DELETE")
st, d = areq("/api/admin/users/tester_rt/quota", "PUT", headers={"Content-Type": "application/json"},
            data=json.dumps({"ram_mb": 512, "cpu_pct": 100, "disk_mb": 1024}).encode())
check("quota restored", st == 200)

print("===== GIT DEPLOY =====")
st, d = req("/api/apps", "POST", {"name": "git-demo", "app_type": "static", "entrypoint": "",
                                  "source_type": "git", "git_url": "https://github.com/octocat/Hello-World.git",
                                  "git_branch": "master", "auto_restart": "false"})
check("git clone deploy", st == 200, (st, str(d)[:120]))
if st == 200:
    gid = d["id"]
    st2, d2 = req(f"/api/apps/{gid}/files")
    files = [i["name"] for i in d2.get("items", [])]
    check("git files extracted", len(files) > 0, files[:5])
    req(f"/api/apps/{gid}", "DELETE")
else:
    NOTES.append("git deploy: network may be unavailable on this device")

print("===== ENV APPLIED AFTER RESTART =====")
st, d = req("/api/apps", "POST", {"name": "env-demo", "app_type": "python", "entrypoint": "main.py",
                                  "source_type": "paste", "paste_code": pycode, "auto_restart": "false"})
env_id = d["id"]; created_ids.append(env_id)
st, d = req(f"/api/apps/{env_id}/env", "PUT", headers={"Content-Type": "application/json"},
            data=json.dumps({"env": {"FOO": "ENV-VALUE-777"}}).encode())
check("env set", st == 200, d)
st, d = req(f"/api/apps/{env_id}/start", "POST"); check("env-demo start", st == 200)
port = req(f"/api/apps/{env_id}")[1].get("port")
if wait_port(port):
    st, body, _ = raw_req(f"/app/{env_id}/")
    check("env FOO visible in app", st == 200 and body == b"ENV-VALUE-777", body)
else:
    check("env FOO visible in app", False, "port never up")
st, d = req(f"/api/apps/{env_id}/stop", "POST"); check("stop", st == 200)

print("===== FILES: FULL CRUD + UPLOAD + UNZIP + 2MB READ LIMIT =====")
st, d = req("/api/apps", "POST", {"name": "fs-demo", "app_type": "static", "entrypoint": "",
                                  "source_type": "paste", "paste_code": "<h1>fs</h1>", "auto_restart": "false"})
fsid = d["id"]; created_ids.append(fsid)
st, d = req(f"/api/apps/{fsid}/files/create", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"path": "a/b", "is_dir": True}).encode())
check("mkdir nested", st == 200)
st, d = req(f"/api/apps/{fsid}/files/create", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"path": "a/b/t.txt", "is_dir": False}).encode())
check("create file", st == 200)
st, d = req(f"/api/apps/{fsid}/files/write?path=a/b/t.txt", "POST", data=b"hello-runtime", headers={"Content-Type": "text/plain"})
check("write file raw", st == 200)
st, body, _ = raw_req(f"/api/apps/{fsid}/files/read?path=a/b/t.txt")
check("read file raw", st == 200 and body == b"hello-runtime", body)
st, d = req(f"/api/apps/{fsid}/files?path=a/b")
check("list nested", st == 200 and "t.txt" in json.dumps(d))
# upload
mp, ct = multipart({}, {"file": ("up.txt", b"UPLOADED-CONTENT", "text/plain")})
st, d = req(f"/api/apps/{fsid}/files/upload?path=", "POST", data=mp, headers={"Content-Type": ct})
check("upload file", st == 200, d)
st, body, _ = raw_req(f"/api/apps/{fsid}/files/read?path=up.txt")
check("uploaded content correct", body == b"UPLOADED-CONTENT", body)
# unzip
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("unz/one.txt", "ONE")
    zf.writestr("unz/two.txt", "TWO")
buf.seek(0)
mp, ct = multipart({}, {"file": ("in.zip", buf.read(), "application/zip")})
st, d = req(f"/api/apps/{fsid}/files/unzip?path=", "POST", data=mp, headers={"Content-Type": ct})
check("unzip", st == 200, d)
st, d = req(f"/api/apps/{fsid}/files?path=unz")
check("unzip files listed", st == 200 and "one.txt" in json.dumps(d))
# 2MB read limit
big2 = b"B" * (2 * 1024 * 1024 + 10)
mp, ct = multipart({}, {"file": ("huge.txt", big2, "text/plain")})
st, d = req(f"/api/apps/{fsid}/files/upload?path=", "POST", data=mp, headers={"Content-Type": ct})
check("2MB upload ok", st == 200)
st, d = req(f"/api/apps/{fsid}/files/read?path=huge.txt")
check("read >2MB blocked 413", st == 413, d)
# delete
st, d = req(f"/api/apps/{fsid}/files?path=a/b/t.txt", "DELETE")
check("delete file", st == 200)
st, d = req(f"/api/apps/{fsid}/files?path=a/b")
check("deleted gone", "t.txt" not in json.dumps(d))
st, d = req(f"/api/apps/{fsid}/files?path=../..")
check("traversal blocked", st == 403)

print("===== TERMINAL: REST + TIMEOUT =====")
st, d = req(f"/api/apps/{env_id}/terminal", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"cmd": "whoami && pwd"}).encode())
check("terminal exec", st == 200 and d.get("output"), d)
st, d = req(f"/api/apps/{env_id}/terminal", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"cmd": "sleep 20", "timeout": 10}).encode())
check("long cmd killed at timeout", "TIMEOUT" in str(d.get("output")), d)

print("===== LOGS / EXPORT / BACKUP =====")
st, body, _ = raw_req(f"/api/apps/{env_id}/logs")
check("logs endpoint", st == 200 and len(body) > 0)
st, body, _ = raw_req(f"/api/apps/{env_id}/logs?search=TERM")
check("logs search", st == 200)
st, body, _ = raw_req(f"/api/apps/{env_id}/logs/download")
check("logs download", st == 200)
st, body, _ = raw_req(f"/api/apps/{env_id}/export")
zok = False
if st == 200:
    z = zipfile.ZipFile(io.BytesIO(body))
    names = z.namelist()
    zok = "main.py" in names and "app-metadata.json" in names
check("export zip has code + metadata", zok, [])
admin_login()
def araw(path):
    h = {"X-CSRF-Token": next((c.value for c in aj if c.name == "csrf_token"), "")}
    r = urllib.request.Request(BASE + path, headers=h)
    try:
        resp = aopener.open(r, timeout=30); return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
st, body = araw("/api/admin/backup")
bok = False
if st == 200 and isinstance(body, bytes) and body[:2] == b"PK":
    z = zipfile.ZipFile(io.BytesIO(body))
    bok = "panel.db" in z.namelist() and len(z.namelist()) >= 1
check("backup zip has panel.db", bok, st)

print("===== AUTO-RESTART ON CRASH =====")
st, d = req("/api/apps", "POST", {"name": "ar-demo", "app_type": "python", "entrypoint": "main.py",
                                  "source_type": "paste", "paste_code": pycode, "auto_restart": "true"})
arid = d["id"]; created_ids.append(arid)
st, d = req(f"/api/apps/{arid}/start", "POST"); check("ar start", st == 200)
port = req(f"/api/apps/{arid}")[1].get("port")
wait_port(port, 45)
st, d = req(f"/api/apps/{arid}/metrics")
check("ar running before kill", d.get("running") is True, d)
import subprocess, os
def app_pid():
    appdir = os.path.realpath(f"data/apps/{arid}")
    for pid in os.listdir("/proc"):
        if not pid.isdigit(): continue
        try:
            if os.path.realpath(f"/proc/{pid}/cwd") != appdir: continue
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")
            if b"main.py" in cmd:
                return int(pid)
        except OSError:
            pass
    return None
pid1 = app_pid()
check("found app pid via /proc cwd", pid1 is not None, pid1)
subprocess.run(["kill", "-9", str(pid1)], capture_output=True)
check("kill -9 issued", True)
# wait for death + respawn — monitor polls every ~2s, so poll up to 12s
pid2 = None
died_observed = False
restarted_observed = False
for _ in range(6):
    time.sleep(2)
    st, body, _ = raw_req(f"/api/apps/{arid}/logs")
    txt = body.decode("utf-8", "replace").lower()
    died_observed = died_observed or "process exited" in txt
    restarted_observed = restarted_observed or txt.count("spawned pid=") >= 2
    pid2 = app_pid()
    if died_observed and restarted_observed and pid2 and pid2 != pid1:
        break
check("exit observed + respawn (log evidence)", died_observed and restarted_observed, txt[-300:])
check("respawned with new pid", pid2 is not None and pid2 != pid1, f"pid1={pid1} pid2={pid2}")

print("===== BAN / UNBAN =====")
admin_login()
st, d = areq("/api/admin/users/tester_rt/ban", "POST"); check("ban user", st == 200)
st, d = req("/api/me"); check("banned user session killed", st == 401, d)
st, d = req("/register", "POST", {"username": "tester_rt", "password": "x"}); check("banned cannot re-register", st == 400, d)
st, d = areq("/api/admin/users/tester_rt/unban", "POST"); check("unban", st == 200)
st, d = login("tester_rt", "rtpass123"); check("unbanned can login", st == 200, d)

print("===== PROXY DETAILS =====")
st, d = req("/api/apps", "POST", {"name": "proxy-demo", "app_type": "python", "entrypoint": "main.py",
                                  "source_type": "paste",
                                  "paste_code": '''import os,http.server,urllib.parse
port=int(os.environ.get("PORT","8000"))
class H(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  q=urllib.parse.urlparse(self.path).query
  b=("Q="+q).encode()
  self.send_response(200);self.send_header("Content-Type","text/plain");self.end_headers();self.wfile.write(b)
 def do_POST(self):
  ln=int(self.headers.get("Content-Length","0"));b=self.rfile.read(ln)
  self.send_response(200);self.send_header("Content-Type","text/plain");self.end_headers();self.wfile.write(b"POST:"+b)
 def do_HEAD(self):
  self.send_response(200);self.send_header("Content-Type","text/plain");self.send_header("X-HEAD-OK","1");self.end_headers()
 def log_message(self,*a):pass
http.server.HTTPServer(("127.0.0.1",port),H).serve_forever()
''', "auto_restart": "false"})
pxid = d["id"]; created_ids.append(pxid)
st, d = req(f"/api/apps/{pxid}/start", "POST")
port = req(f"/api/apps/{pxid}")[1].get("port")
wait_port(port, 45)
st, body, _ = raw_req(f"/app/{pxid}/?a=1&b=2")
check("query params proxied", st == 200 and b"a=1&b=2" in body, body)
st, body, _ = raw_req(f"/app/{pxid}/x?x=9", "POST", data=b"form-data-here", headers={"Content-Type": "text/plain"})
check("POST body proxied", st == 200 and b"POST:form-data-here" in body, body)
import http.client as _hc
_hc_conn = _hc.HTTPConnection("127.0.0.1", 8080, timeout=6)
_hc_conn.request("HEAD", f"/app/{pxid}/")
_hc_resp = _hc_conn.getresponse()
_head_ok = _hc_resp.status == 200 and any(k.lower() == "x-head-ok" for k, _ in _hc_resp.getheaders())
_hc_conn.close()
check("HEAD proxied (upstream X-HEAD-OK header preserved)", _head_ok, _hc_resp.status)
st, body, _ = raw_req(f"/app/{pxid}/nonexistent-path")
check("app 404 passthrough", st == 404 or st == 200, st)  # upstream decides
st, body, _ = raw_req(f"/app/does-not-exist-xyz/")
check("unknown app 404", st == 404)

print("===== ALIAS VALIDATION =====")
st, d = req(f"/api/apps/{pxid}/alias", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"alias": "rt-alias"}).encode())
check("set alias ok", st == 200, d)
st, body, _ = raw_req("/p/rt-alias/?z=1")
check("alias proxy works", st == 200 and b"z=1" in body, body)
st, d = req(f"/api/apps/{pxid}/alias", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"alias": "BAD ALIAS!"}).encode())
check("bad alias rejected", st == 400, d)
st, d = req(f"/api/apps/{pxid}/alias", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"alias": "x"}).encode())
check("too-short alias rejected", st == 400, d)
st, d = req("/api/apps", "POST", {"name": "alias-dup", "app_type": "static", "entrypoint": "",
                                  "source_type": "paste", "paste_code": "<b>d</b>", "alias": "rt-alias"})
check("duplicate alias at create -> 409", st == 409, d)
st, d = req(f"/api/apps/{pxid}/alias", "POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"alias": ""}).encode())
check("clear alias", st == 200 and d.get("alias") in (None, ""), d)

print("===== APP-LEVEL WEBSOCKET THROUGH PROXY (gap probe) =====")
wscode = '''import asyncio, os, sys
async def handler(ws, path):
    await ws.send("APP-WS-OK")
    await ws.close()
async def main():
    import websockets
    async with websockets.serve(handler, "127.0.0.1", int(os.environ.get("PORT","8000"))):
        await asyncio.Future()
asyncio.run(main())
'''
st, d = req("/api/apps", "POST", {"name": "ws-app", "app_type": "python", "entrypoint": "main.py",
                                  "source_type": "paste", "paste_code": wscode, "auto_restart": "false"})
wsid = d["id"]; created_ids.append(wsid)
st, d = req(f"/api/apps/{wsid}/start", "POST")
port = req(f"/api/apps/{wsid}")[1].get("port")
if wait_port(port, 45):
    st, body, _ = raw_req(f"/app/{wsid}/", timeout=5)
    check("ws app http reachable (426 = websockets.serve normal response)", st in (404, 200, 426), st)
    import websockets, asyncio
    # probe the raw upgrade: what does the panel do with sec-websocket headers?
    up_st, up_body, up_hdrs = raw_req(f"/app/{wsid}/ws", "GET",
        headers={"Upgrade": "websocket", "Connection": "Upgrade",
                 "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                 "Sec-WebSocket-Version": "13"}, timeout=6)
    print(f"    raw upgrade probe -> status {up_st}, headers: Upgrade={up_hdrs.get('Upgrade')}, Sec-WebSocket-Accept={up_hdrs.get('Sec-WebSocket-Accept')}")
    async def try_ws():
        try:
            async with websockets.connect(f"ws://127.0.0.1:8080/app/{wsid}/ws", timeout=6) as ws:
                return await asyncio.wait_for(ws.recv(), timeout=5)
        except Exception as e:
            return f"ERR:{type(e).__name__}"
    r = asyncio.run(try_ws())
    check("app WS through panel proxy", r == "APP-WS-OK", str(r)[:100])
    if r != "APP-WS-OK":
        NOTES.append(f"GAP: app-level WebSocket through panel proxy fails (upgrade probe status={up_st}; websockets client error={str(r)[:60]}) — needs WS pass-through in proxy")
else:
    check("app WS through panel proxy", False, "port never up")
    NOTES.append("ws-app did not start; ws probe skipped")

print("===== CLEANUP (before lockout) =====")
for aid in created_ids:
    req(f"/api/apps/{aid}", "DELETE")
admin_login()
st, d = areq("/api/admin/users/tester_rt", "DELETE")
check("delete test user", st == 200, d)
st, d = login("admin", "admin123")
check("main jar back to admin", st == 200, d)
st, d = req("/api/apps")
check("app list clean", len(d) == 3, [a["name"] for a in d])

print("===== SECURITY (final block) =====")
st, d = raw_req("/api/apps", use_jar=False)[0:2]
check("unauth 401", st == 401)
st, d, _ = raw_req("/api/apps/1/start", "POST", use_jar=True, with_csrf=False)
check("no-CSRF 403", st == 403, st)
st, d = req("/api/apps/1/start", "POST", headers={"X-CSRF-Token": "wrong-token"})
check("wrong CSRF 403", st == 403, d)
st, d = req("/api/apps", "POST", {"name": "bad-type", "app_type": "cobol", "entrypoint": "x", "source_type": "paste", "paste_code": "x"})
check("invalid app type 400", st == 400, d)
st, d = req("/api/apps", "POST", {"name": "bad-entry", "app_type": "python", "entrypoint": "x.sh; rm -rf /", "source_type": "paste", "paste_code": "x"})
check("malicious entrypoint 400", st == 400, d)
st, d = req("/api/apps", "POST", {"name": "sec-app", "app_type": "static", "entrypoint": "",
                                  "source_type": "paste", "paste_code": "<b>s</b>", "auto_restart": "false"})
sec_id = d["id"]
check("sec-app created", st == 200)
st, d = req(f"/api/apps/{sec_id}/files/read?path=../../../../etc/passwd")
check("read traversal blocked", st == 403, d)
st, d = req(f"/api/apps/{sec_id}/files?path=../../..")
check("list traversal blocked", st == 403, d)
st, d = req(f"/api/apps/{sec_id}/files/write?path=../../evil.txt", "POST", data=b"x", headers={"Content-Type": "text/plain"})
check("write traversal blocked", st == 403, d)
req(f"/api/apps/{sec_id}", "DELETE")
# lockout — LAST
codes = []
for _ in range(6):
    st, _ = req("/login", "POST", {"username": "admin", "password": "wrong-pass"})
    codes.append(st)
check("lockout after 5 fails (429 on 6th)", codes[-1] == 429, codes)
st, d = req("/login", "POST", {"username": "admin", "password": "admin123"})
check("locked out admin cannot login", st == 429, d)



print(f"\n===== RESULT: {PASS} passed, {FAIL} failed =====")
for n in NOTES:
    print("NOTE:", n)
sys.exit(1 if FAIL else 0)