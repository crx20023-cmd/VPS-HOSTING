#!/usr/bin/env python3
"""python-telegram-bot (PTB) template — long-polling (no webhook/HTTPS needed).
Fast pure-python install. Setup:
1. Create app with this template, open ENV tab, add:  BOT_TOKEN = <token from @BotFather>
2. START it. Log shows "Bot started".
"""
import logging
import os
import sys

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN is not set.")
    print("   → Open the ENV tab of this app and add:  BOT_TOKEN = <token from @BotFather>")
    print("   → Then press RESTART.")
    sys.exit(1)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👋 Hi {update.effective_user.first_name}! PTB bot online.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"You said: {update.message.text}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    print("✅ Bot started — polling Telegram for updates...")
    app.run_polling()


if __name__ == "__main__":
    main()
