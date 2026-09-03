#!/usr/bin/env python3
"""Pyrogram Telegram bot template — long-polling (no webhook/HTTPS needed).

Setup:
1. Create app with this template, open ENV tab, add:  BOT_TOKEN = <token from @BotFather>
2. Optionally add API_ID / API_HASH (from https://my.telegram.org) for user-account features.
3. START it. The session file is saved inside the app folder — it survives restarts
   and is included in snapshots/backups.
"""
import os
import sys

from pyrogram import Client, filters
from pyrogram.types import Message

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN is not set.")
    print("   → Open the ENV tab of this app and add:  BOT_TOKEN = <token from @BotFather>")
    print("   → Then press RESTART.")
    sys.exit(1)

API_ID = int(os.environ.get("API_ID", "2040"))
API_HASH = os.environ.get("API_HASH", "b18441a1ff607e10a989891a5462e627")

app = Client("my_bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)


@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    await message.reply(f"👋 Hi {message.from_user.first_name}! Pyrogram bot online.")


@app.on_message(filters.text)
async def echo(client: Client, message: Message):
    await message.reply(f"You said: {message.text}")


print("✅ Bot starting...")
app.run()