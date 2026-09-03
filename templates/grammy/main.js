// grammY Telegram bot template (Node.js) — long-polling (no webhook/HTTPS needed).
// Setup:
// 1. Create app with this template, open ENV tab, add:  BOT_TOKEN = <token from @BotFather>
// 2. START it. Log will show "Bot started".

const { Bot } = require("grammy");

const BOT_TOKEN = process.env.BOT_TOKEN;
if (!BOT_TOKEN) {
  console.log("❌ BOT_TOKEN is not set.");
  console.log("   → Open the ENV tab of this app and add:  BOT_TOKEN = <token from @BotFather>");
  console.log("   → Then press RESTART.");
  process.exit(1);
}

const bot = new Bot(BOT_TOKEN);

bot.command("start", async (ctx) => {
  await ctx.reply(`👋 Hi ${ctx.from.first_name}! grammY bot online.`);
});

bot.on("message:text", async (ctx) => {
  await ctx.reply(`You said: ${ctx.message.text}`);
});

bot.catch((err) => {
  console.error("bot error:", err.message || err);
});

bot.start();
console.log("✅ Bot started — polling Telegram for updates...");