#!/usr/bin/env python3
"""Telethon Telegram bot template — long-polling (no webhook/HTTPS needed).

Setup:
1. Create app with this template, open ENV tab, add:  BOT_TOKEN = <token from @BotFather>
2. START it. The session file is saved inside the app folder — it survives restarts
   and is included in snapshots/backups.
"""
import os
import sys

from telethon import TelegramClient, events

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN is not set.")
    print("   → Open the ENV tab of this app and add:  BOT_TOKEN = <token from @BotFather>")
    print("   → Then press RESTART.")
    sys.exit(1)

API_ID = int(os.environ.get("API_ID", "2040"))
API_HASH = os.environ.get("API_HASH", "b18441a1ff607e10a989891a5462e627")

bot = TelegramClient("my_bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)


@bot.on(events.NewMessage(pattern="/start"))
async def on_start(event):
    await event.respond(f"👋 Hi {event.sender.first_name}! Telethon bot online.")


@bot.on(events.NewMessage)
async def echo(event):
    if event.text and not event.text.startswith("/"):
        await event.respond(f"You said: {event.text}")


print("✅ Bot starting...")
bot.run_until_disconnected()