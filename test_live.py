#!/usr/bin/env python3
"""VPS-PANEL live test battery (stdlib only)."""
import json, urllib.request, urllib.parse, http.cookiejar, sys, time, zipfile, io

BASE = "http://127.0.0.1:8080"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
CSRF = None
PASS = 0; FAIL = 0

def req(path, method="GET", data=None, headers=None, raw=None, ctype=None, skip_csrf=False):
    h = dict(headers or {})
    body = None
    if raw is not None:
        body = raw.encode()
        h["Content-Type"] = ctype or "text/plain"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if method not in ("GET", "HEAD") and not skip_csrf and not any(k.lower() == "x-csrf-token" for k in h):
        h["X-CSRF-Token"] = get_csrf()
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        resp = opener.open(r, timeout=25)
        return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)

def get_csrf():
    global CSRF
    if not CSRF:
        _, body, _ = req("/csrf")
        CSRF = json.loads(body)["csrf"]
    return CSRF

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {name}")
    else:
        FAIL += 1; print(f"  FAIL  {name}  {extra[:200]}")

def app_create(name, app_type, entrypoint, code, source="paste", alias="", env="{}", extra=None):
    fields = {"name": name, "app_type": app_type, "entrypoint": entrypoint,
              "source_type": source, "paste_code": code, "env_vars_json": env,
              "auto_restart": "true", "alias": alias}
    if extra: fields.update(extra)
    return req("/api/apps", "POST", fields)

def wait_status(app_id, want, timeout=40):
    for _ in range(timeout):
        st, body, _ = req(f"/api/apps/{app_id}")
        d = json.loads(body)
        if d["status"] == want: return d
        if d["status"] in ("error",): return d
        time.sleep(1)
    return d

def wait_port(app_id, want, timeout=45):
    import socket
    for _ in range(timeout):
        d = wait_status(app_id, want, 2)
        st, body, _ = req(f"/api/apps/{app_id}")
        d = json.loads(body)
        if d.get("port"):
            try:
                s = socket.create_connection(("127.0.0.1", d["port"]), timeout=1)
                s.close()
                return d
            except OSError:
                pass
        time.sleep(1)
    return d

def main():
    print("== 1. AUTH ==")
    st, _, _ = req("/login"); check("GET /login 200", st == 200)
    st, body, _ = req("/login", "POST", {"username": "admin", "password": "admin123"}, skip_csrf=True)
    check("POST /login admin ok", st == 200 and "success" in body, body)
    st, body, _ = req("/csrf"); check("GET /csrf", st == 200)
    st, body, _ = req("/"); check("GET / dashboard", st == 200 and "VPS-PANEL" in body)
    st, body, _ = req("/api/me"); me = json.loads(body)
    check("GET /api/me role=admin", me.get("role") == "admin", body)
    check("me has quota", "quota" in me and "usage" in me)

    print("== 2. STATIC APP ==")
    st, body, _ = app_create("static-demo", "static", "", "<h1>STATIC-OK-12345</h1>")
    check("create static app", st == 200, body)
    sa = json.loads(body); sid = sa["id"]
    st, body, _ = req(f"/api/apps/{sid}/start", "POST")
    check("start static", st == 200 and "success" in body, body)
    st, body, _ = req(f"/app/{sid}/")
    check("static served via /app/", st == 200 and "STATIC-OK-12345" in body, body[:100])
    st, body, _ = req(f"/api/apps/{sid}"); d = json.loads(body)
    check("static has no process", d.get("running") is True)

    print("== 3. PYTHON APP ==")
    pycode = '''
import os, http.server
port = int(os.environ.get("PORT", "8000"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/plain"); self.end_headers()
        self.wfile.write(b"PY-OK-98765")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
'''
    st, body, _ = app_create("py-demo", "python", "main.py", pycode)
    check("create python app", st == 200, body)
    pa = json.loads(body); pid2 = pa["id"]
    st, body, _ = req(f"/api/apps/{pid2}/start", "POST")
    check("start python", st == 200 and "success" in body, body)
    d = wait_status(pid2, "running")
    check("python app stays running", d.get("status") == "running", body)
    st, body, _ = req(f"/app/{pid2}/")
    check("python served", st == 200 and "PY-OK-98765" in body, body[:100])

    print("== 4. NODE APP ==")
    ncode = 'const http=require("http");http.createServer((q,s)=>s.end("NODE-OK-55555")).listen(process.env.PORT||3000);'
    st, body, _ = app_create("node-demo", "node", "app.js", ncode)
    check("create node app", st == 200, body)
    na = json.loads(body); nid = na["id"]
    st, body, _ = req(f"/api/apps/{nid}/start", "POST")
    check("start node", st == 200 and "success" in body, body)
    d = wait_port(nid, "running")
    check("node app stays running", d.get("status") == "running", body)
    st, body, _ = req(f"/app/{nid}/")
    check("node served", st == 200 and "NODE-OK-55555" in body, body[:100])

    print("== 5. PHP APP ==")
    phcode = '<?php echo "PHP-OK-33333";'
    st, body, _ = app_create("php-demo", "php", "index.php", phcode)
    check("create php app", st == 200, body)
    ph = json.loads(body); phid = ph["id"]
    st, body, _ = req(f"/api/apps/{phid}/start", "POST")
    check("start php", st == 200 and "success" in body, body)
    d = wait_port(phid, "running")
    check("php stays running", d.get("status") == "running", body)
    st, body, _ = req(f"/app/{phid}/")
    check("php served", st == 200 and "PHP-OK-33333" in body, body[:100])

    print("== 6. ALIAS + ENV ==")
    st, body, _ = req(f"/api/apps/{pid2}/alias", "POST", headers={"Content-Type": "application/json"},
                      raw=json.dumps({"alias": "pyalias"}), ctype="application/json")
    check("set alias", st == 200, body)
    st, body, _ = req("/p/pyalias/")
    check("alias proxy works", st == 200 and "PY-OK-98765" in body, body[:100])
    st, body, _ = app_create("alias-clash", "static", "", "<b>x</b>", alias="pyalias")
    check("alias conflict rejected", st == 409, body)
    st, body, _ = req(f"/api/apps/{nid}/env", "PUT", raw=json.dumps({"env": {"FOO": "BAR123"}}), ctype="application/json")
    check("env update", st == 200, body)

    print("== 7. FILES ==")
    st, body, _ = req(f"/api/apps/{sa['id']}/files/create", "POST", raw=json.dumps({"path": "sub", "is_dir": True}), ctype="application/json")
    check("mkdir", st == 200, body)
    st, body, _ = req(f"/api/apps/{sa['id']}/files/create", "POST", raw=json.dumps({"path": "sub/note.txt", "is_dir": False}), ctype="application/json")
    check("create file", st == 200, body)
    st, body, _ = req(f"/api/apps/{sa['id']}/files/write?path=sub/note.txt", "POST", raw="HELLO-FILE")
    check("write file", st == 200, body)
    st, body, _ = req(f"/api/apps/{sa['id']}/files/read?path=sub/note.txt")
    check("read file", st == 200 and "HELLO-FILE" in body, body)
    st, body, _ = req(f"/api/apps/{sa['id']}/files?path=sub")
    check("list files", st == 200 and "note.txt" in body, body)
    st, body, _ = req(f"/api/apps/{sa['id']}/files?path=../../..")
    check("path traversal blocked", st == 403, body)

    print("== 8. TERMINAL ==")
    st, body, _ = req(f"/api/apps/{pid2}/terminal", "POST", raw=json.dumps({"cmd": "pwd && echo TERM-OK-111"}),
                      ctype="application/json")
    check("terminal REST exec", st == 200 and "TERM-OK-111" in body, body[:200])

    print("== 9. LOGS + METRICS + EXPORT ==")
    st, body, _ = req(f"/api/apps/{pid2}/logs")
    check("logs text", st == 200 and "TERM-OK" in body, body[:100])
    st, body, _ = req(f"/api/apps/{pid2}/logs?search=TERM-OK")
    check("logs search", st == 200 and "TERM-OK" in body, body[:100])
    st, body, _ = req(f"/api/apps/{pid2}/metrics")
    m = json.loads(body); check("metrics running", m.get("running") is True, body)
    st, body, _ = req(f"/api/apps/{pid2}/export")
    check("export zip", st == 200 and body[:2] == "PK", "not zip")
    st, body, _ = req("/api/metrics")
    check("host metrics", st == 200 and "host_cpu" in body)

    print("== 10. ADMIN ==")
    st, body, _ = req("/api/admin/users")
    check("admin users list", st == 200 and "admin" in body, body[:150])
    st, body, _ = req("/api/admin/settings")
    check("admin settings", st == 200 and "auto_approve" in body, body[:150])
    st, body, _ = req("/api/admin/settings", "PUT", raw=json.dumps({"auto_approve": False, "default_ram": 512, "default_cpu": 100, "default_disk": 1024, "panel_name": "VPS-PANEL-TEST"}), ctype="application/json")
    check("settings update", st == 200, body)
    st, body, _ = req("/api/admin/backup")
    check("backup zip", st == 200 and body[:2] == "PK", "not zip")
    st, body, _ = req("/api/admin/change-password", "POST", raw=json.dumps({"new_password": "admin123"}), ctype="application/json")
    check("change password (same)", st == 200, body)

    print("== 11. SECURITY ==")
    import urllib.request as ur2
    try:
        ur2.urlopen(ur2.Request(BASE + "/api/apps"), timeout=10)
        check("unauth /api/apps blocked", False)
    except urllib.error.HTTPError as e:
        check("unauth /api/apps blocked", e.code == 401, str(e.code))
    # no CSRF
    r = urllib.request.Request(BASE + "/api/apps/1/start", data=b"", method="POST")
    try:
        ur2.urlopen(r, timeout=10)
        check("no-CSRF start blocked", False)
    except urllib.error.HTTPError as e:
        check("no-CSRF start blocked", e.code == 403, str(e.code))
    # zip-slip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.txt", "pwn")
    buf.seek(0)
    import mimetypes
    boundary = "----testboundary123"
    body_parts = []
    body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nzipattack")
    body_parts.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"app_type\"\r\n\r\nstatic")
    body_parts.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"source_type\"\r\n\r\nzip")
    body_parts.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"zip_file\"; filename=\"a.zip\"\r\nContent-Type: application/zip\r\n\r\n")
    body_parts.append(buf.getvalue().decode("latin1"))
    body_parts.append(f"\r\n--{boundary}--\r\n")
    mp = "".join(body_parts).encode("latin1")
    r2 = urllib.request.Request(BASE + "/api/apps", data=mp, method="POST")
    r2.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    r2.add_header("X-CSRF-Token", get_csrf())
    try:
        resp = opener.open(r2, timeout=15)
        check("zip-slip rejected", False, resp.read().decode()[:100])
    except urllib.error.HTTPError as e:
        check("zip-slip rejected", e.code == 400, str(e.code))
    # login lockout (wrong password x5)
    codes = []
    for _ in range(6):
        try:
            urllib.request.urlopen(urllib.request.Request(BASE + "/login", data=urllib.parse.urlencode({"username": "admin", "password": "wrong"}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=10)
            codes.append(200)
        except urllib.error.HTTPError as e:
            codes.append(e.code)
    check("lockout after 5 fails", 429 in codes, str(codes))

    print("== 12. RESTART/STOP/CLEANUP ==")
    st, body, _ = req(f"/api/apps/{pid2}/restart", "POST")
    check("restart ok", st == 200 and "success" in body, body)
    st, body, _ = req(f"/api/apps/{pid2}/stop", "POST")
    check("stop ok", st == 200 and "success" in body, body)
    for aid in (sid, pid2, nid, phid):
        req(f"/api/apps/{aid}", "DELETE")
    st, body, _ = req("/api/apps")
    check("cleanup: 0 apps", "[]" in body or body.strip() == "[]", body[:80])

    print(f"\n===== RESULT: {PASS} passed, {FAIL} failed =====")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()