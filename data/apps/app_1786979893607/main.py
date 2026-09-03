import os, json, http.server, time
port = int(os.environ.get("PORT", "8000"))
started = time.time()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "app": "py-api-demo", "status": "running",
            "uptime_s": int(time.time() - started),
            "endpoints": ["/", "/health", "/info"],
            "hosted_by": "VPS-PANEL",
        }, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
