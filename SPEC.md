# VPS-PANEL — Multi-Language Hosting Panel (VPS-style)

> Status: APPROVED DESIGN (2026-08-17). ডিপ্লয়: প্রথমে Termux local, পরে Docker VPS.

## ধারণা
VPS-স্টাইল মাল্টি-টেন্যান্ট হোস্টিং প্যানেল — ব্যবহারকারীরা Python/Node/PHP/স্ট্যাটিক অ্যাপ
ডিপ্লয় করতে পারবে, নিজেদের স্যান্ডবক্সে টার্মিনাল + ফাইল ম্যানেজার + রিসোর্স মনিটরিং পাবে।
কোটা ও নিরাপত্তা কোর-ফিচার।

## আর্কিটেকচার
```
Panel Core (FastAPI + vanilla JS) — Termux ও VPS-এ একই
  └─ RuntimeDriver (অ্যাবস্ট্রাকশন)
       ├─ LocalDriver  → subprocess + rlimit (Termux টেস্ট)  [এখন]
       └─ DockerDriver → container + cgroups (VPS)           [পরে — ইন্টারফেস রেডি]
```
- Panel নিজেই রিভার্স-প্রক্সি: `/app/{id}/` + শর্ট alias `/p/{alias}/`
- App প্রতি আলাদা sandbox dir: `data/apps/{id}/`, আলাদা পোর্ট, `PORT` env ইনজেক্ট
- DB: SQLite (stdlib sqlite3) — multi-tenant এর জন্য যথেষ্ট

## অ্যাপ টাইপ (৪টা বিল্ট-ইন)
| type | run | entrypoint |
|---|---|---|
| static | panel সরাসরি ফাইল serve (প্রসেস নেই) | index.html |
| python | venv + `python {entry}` | .py |
| node | npm install + `node {entry}` | .js |
| php | `php -S 127.0.0.1:{port}` (docroot=sandbox) | - (বা router.php) |

## সিকিউরিটি
- PBKDF2 পাসওয়ার্ড, session cookie httponly + CSRF token (মিডলওয়্যার, non-GET)
- লগইন লকআউট (5 ফেল → 10 মিনিট), রেজিস্ট্রেশন রেট-লিমিট (10/ঘণ্টা/IP)
- রেজিস্ট্রেশন → pending → অ্যাডমিন অ্যাপ্রুভাল
- zip-slip + zip-bomb + আপলোড ক্যাপ (100MB), পেস্ট ক্যাপ (500K)
- Entrypoint ভ্যালিডেশন (path traversal ব্লক), git URL প্রোটোকল চেক
- Session TTL 7 দিন + idle 24h; change-password → অন্য সেশন ইনভ্যালিড

## কোটা (হার্ড-এনফোর্সড)
- প্রতি ইউজার: ram_mb, cpu_pct, disk_mb, app_limit — অ্যাডমিন সেট করে
- Monitor লুপ (২সে): সবচেয়ে ভারী অ্যাপ বন্ধ + টেলিগ্রাম অ্যালার্ট (notify bot/chat)
- create-এ disk quota প্রি-চেক

## টার্মিনাল
- WebSocket + PTY (local: pty.fork স্যান্ডবক্সে; docker: exec) — ইন্টারঅ্যাক্টিভ shell

## এন্ডপয়েন্ট ম্যাপ (API)
- auth: /login /register /logout, /api/me
- apps: CRUD + /start /stop /restart /logs(?search,limit) /logs/download /export /env /metrics
- files: /list /read /write /create /delete /upload /unzip
- terminal: WS /api/apps/{id}/terminal/ws
- admin: users (approve/ban/limit/quota/usage), settings, backup
- proxy: /app/{id}/{path}, /p/{alias}/{path}, /api/telegram/{id} (webhook, পরে)

## ফ্রন্টএন্ড
- login.html + dashboard.html (VPS ডার্ক থিম, নিজস্ব canvas গ্রাফ — CDN নেই)
- ট্যাব: অ্যাপস গ্রিড, ফাইল ম্যানেজার, টার্মিনাল, মেট্রিক্স, env, লগস, অ্যাডমিন

## ডিরেক্টরি
```
vpspanel/
  main.py            panel core
  db.py              sqlite layer
  runtime/base.py    Driver interface + DockerDriver stub
  runtime/local.py   LocalDriver
  static/ templates/ data/(apps,logs,panel.db)
  start.sh
```