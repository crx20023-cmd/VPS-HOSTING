#!/usr/bin/env python3
"""VPS-PANEL keep-alive watchdog.
Restarts the panel server if port 8080 stops responding.
Create 'keepalive.stop' next to this file to stop the watchdog permanently.
"""
import os, sys, time, socket, subprocess

base = "/data/data/com.termux/files/home/hosting-work/vpspanel"
stop_marker = os.path.join(base, "keepalive.stop")
pidfile = os.path.join(base, "keepalive.pid")


def port_up(port=8080, tries=2):
    for _ in range(tries):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            return True
        except OSError:
            time.sleep(1)
    return False


def main():
    open(pidfile, "w").write(str(os.getpid()))
    failures = 0
    while True:
        if os.path.exists(stop_marker):
            print("stop marker found — watchdog exiting", flush=True)
            break
        if port_up():
            failures = 0
        else:
            failures += 1
            print(f"[keepalive] port down (check #{failures}) — restarting", flush=True)
            subprocess.run(
                [sys.executable, os.path.join(base, "spawn_daemon.py"), "8080"],
                cwd=base,
                stdout=open(os.path.join(base, "keepalive.log"), "ab"),
                stderr=subprocess.STDOUT,
            )
            time.sleep(5)
        time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass