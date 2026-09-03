#!/usr/bin/env python3
"""Spawn uvicorn as a true detached daemon. Usage: spawn_daemon.py [port] [stop]"""
import os, subprocess, sys, time, socket

base = "/data/data/com.termux/files/home/hosting-work/vpspanel"
pidfile = os.path.join(base, "server.pid")

if len(sys.argv) > 1 and sys.argv[1] == "stop":
    if os.path.exists(pidfile):
        pid = int(open(pidfile).read().strip())
        try:
            os.kill(pid, 9)
            print(f"killed {pid}")
        except ProcessLookupError:
            print("already dead")
        os.remove(pidfile)
    # kill any stray uvicorn on this project
    os.system("pkill -9 -f 'uvicorn main:app --host 127.0.0.1' 2>/dev/null")
    sys.exit(0)

PORT = sys.argv[1] if len(sys.argv) > 1 else "8080"
os.chdir(base)
log = open(os.path.join(base, "server.log"), "ab")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", PORT],
    cwd=base, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
    start_new_session=True,
)
open(pidfile, "w").write(str(proc.pid))
print(f"spawned pid={proc.pid}")
time.sleep(1.5)
if proc.poll() is not None:
    print("PROCESS DIED — see server.log")
    sys.exit(1)
for _ in range(40):
    try:
        s = socket.create_connection(("127.0.0.1", int(PORT)), timeout=1)
        s.close()
        if proc.poll() is None:
            print("PORT OPEN (our pid)")
            sys.exit(0)
    except OSError:
        time.sleep(0.5)
print("TIMEOUT")
sys.exit(1)