# VPS-PANEL v1

Multi-language hosting panel — deploy & manage **Python / Node.js / PHP / static** apps
with a web terminal, file manager, resource monitoring, per-user quotas and admin
approval. Runs on a single host (Termux or VPS), no external services required.

Built on: **FastAPI + vanilla JS** (no CDN, works offline) + **SQLite** + subprocess
sandboxing. Runtime layer is pluggable via `RuntimeDriver` (`local` now,
`docker` stub ready for VPS containers).

---

## Quick start (Termux)

```bash
pkg install python nodejs php        # runtimes
pkg install python-psutil           # system resource lib
pip install fastapi uvicorn python-multipart httpx jinja2 websockets

cd vpspanel
python3 -m uvicorn main:app --host 127.0.0.1 --port 8080
# or: ./start.sh          (PORT / HOST / VPSPANEL_DRIVER env respected)
```

Open `http://127.0.0.1:8080` → login with the seeded admin:

- username: `admin`
- password: `admin123`  (override with env `ADMIN_PASSWORD`)

Change the password right away from the Admin panel.

> Termux tip: `python3 spawn_daemon.py 8080` runs the server as a detached daemon
> (writes `server.pid`); `python3 spawn_daemon.py stop` kills it.

## Quick start (VPS with Docker — later phase)

The driver layer is ready: implement `DockerDriver` in `runtime/base.py`
(container-per-app, cgroups quota, shared reverse proxy). Everything else
(auth, quotas, proxy, monitoring, terminal, file manager) is driver-agnostic.

```bash
pip install -r requirements.txt
PORT=80 HOST=0.0.0.0 ./start.sh
```

Put it behind nginx/Caddy TLS. Panel itself is CSRF + rate-limit protected.

## What you can deploy

| Type     | Start command                      | Notes                          |
|----------|-----------------------------------|--------------------------------|
| `static` | panel serves files directly       | HTML/JS/CSS, no process        |
| `python` | `python main.py` (venv preferred) | `requirements.txt` auto-pip     |
| `node`   | `node app.js`                     | `package.json` auto-npm         |
| `php`    | `php -S 127.0.0.1:PORT -t .`      | built-in dev server            |

Every app gets:
- isolated sandbox dir `data/apps/{app_id}/`, own port, `PORT`/`APP_PORT` env
- **/app/{app_id}/** URL + optional **/p/{alias}/** short URL
- web terminal (PTY over WebSocket) + one-shot command runner
- file manager: browse / edit / upload / unzip (zip-slip & size guarded)
- env vars (JSON), logs (search + download), export as ZIP
- auto-restart on crash (toggle), crash & quota alerts to Telegram (optional)

## Security model

- PBKDF2 password hashing, session cookie (HttpOnly) 7-day TTL + 24h idle expiry
- CSRF: token cookie + `X-CSRF-Token` header on every state-changing request
- Login lockout after 5 fails (10 min), registration rate-limit (10/hour/IP)
- Registration → admin approval (`auto_approve` setting) or immediate
- Per-user quotas: app count, RAM (MB), CPU (%), disk (MB) — enforced by monitor
- Upload caps (100MB), zip-bomb guard (uncompressed limit, entry count),
  path-traversal guards on file APIs, entrypoint whitelist regex
- Sandboxed shells: app processes run with own cwd/env; terminal runs in app dir
- Ban button kills sessions instantly

## Admin panel

- users: approve / ban / unban / delete (with all apps), per-user quotas
- hoster settings: auto-approve, default quotas, Telegram alert token/chat, panel name
- one-click **panel backup** (SQLite + logs → ZIP), per-app export

## Environment variables

| Var                | Default       | Purpose                          |
|--------------------|---------------|----------------------------------|
| `VPSPANEL_DB`      | `data/panel.db` | SQLite path                    |
| `ADMIN_PASSWORD`   | `admin123`    | seeded admin password            |
| `VPSPANEL_DRIVER`  | `local`       | runtime driver (`docker` later)  |
| `PORT` / `HOST`    | 8080 / 127.0.0.1 | uvicorn bind (start.sh)      |
| `SESSION_MAX_AGE_SECONDS` | 604800 | session TTL                  |

## Project layout

```
main.py            panel core (auth, apps, files, terminal, proxy, admin)
db.py              SQLite layer (users/apps/settings, PBKDF2)
runtime/base.py    RuntimeDriver interface + DockerDriver stub
runtime/local.py   LocalDriver — subprocess sandbox, quotas, PTY, monitor
templates/         login.html, dashboard.html (dark VPS UI)
static/            app.js (CSRF fetch, WS terminal, canvas charts), styles.css
start.sh           server launcher
spawn_daemon.py    Termux detached-daemon helper (pid file)
test_live.py       50-check live test battery (API + security)
SPEC.md            full design document
```

## Not in v1 (planned)

- DockerDriver implementation (container isolation + cgroups on VPS)
- Custom domains per user, payments/billing, multi-node
- DB migration tooling (SQLite → Postgres)

## License / ethics

For authorized infrastructure only. The panel is your own software — test it on
apps you own. The included Telegram alerts, quotas and approval flow exist so a
public deployment stays abuse-resistant.
