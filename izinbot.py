import logging
import os
import asyncio
import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, Defaults
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

# Data
active_izin = {}  # user_id: {'type': 'jojo', 'expires': datetime, 'message_id': int, 'thread_id': int}
sebat_users = []  # list of user_ids (max 3)

async def start_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("izin jojo ya ndan (5 menit)", callback_data="izin_jojo")],
        [InlineKeyboardButton("izin ee ya ndan (10 menit)", callback_data="izin_ee")],
        [InlineKeyboardButton("izin sebat ya ndan (10 menit)", callback_data="izin_sebat")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Pilih jenis izin:", reply_markup=reply_markup)

async def handle_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    thread_id = query.message.message_thread_id
    username = query.from_user.first_name

    if user_id in active_izin:
        await query.message.reply_text("⛔ Kamu sudah mengambil izin. Tekan DONE dulu sebelum izin lagi.", message_thread_id=thread_id)
        return

    izin_type = query.data.split("_")[1]  # jojo, ee, sebat
    duration = 300 if izin_type == "jojo" else 600

    if izin_type == "sebat":
        if len(sebat_users) >= 3:
            names = ", ".join(context.bot.get_chat_member(chat_id, uid).user.first_name for uid in sebat_users)
            await query.message.reply_text(f"⛔ Silahkan kerjakan dahulu tugasmu dan tunggu {names} kembali.", message_thread_id=thread_id)
            return
        sebat_users.append(user_id)

    # Send DONE button
    done_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("DONE ✅", callback_data=f"done_{user_id}_{izin_type}")]
    ])
    msg = await query.message.reply_text(
        f"✅ {username} izin {izin_type} selama {duration//60} menit.",
        reply_markup=done_keyboard,
        message_thread_id=thread_id
    )

    expires = datetime.datetime.now() + datetime.timedelta(seconds=duration)
    active_izin[user_id] = {
        "type": izin_type,
        "expires": expires,
        "message_id": msg.message_id,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "name": username
    }

    context.application.create_task(schedule_reminder(user_id, context))

async def schedule_reminder(user_id, context):
    await asyncio.sleep((active_izin[user_id]['expires'] - datetime.datetime.now()).total_seconds())

    if user_id in active_izin:
        izin_info = active_izin[user_id]
        admins = await context.bot.get_chat_administrators(izin_info['chat_id'])
        for admin in admins:
            try:
                await context.bot.send_message(
                    admin.user.id,
                    f"⚠️ {izin_info['name']} belum kembali setelah izin {izin_info['type']}."
                )
            except Exception as e:
                logger.warning(f"Gagal kirim ke admin: {e}")
        if izin_info['type'] == 'sebat':
            sebat_users.remove(user_id)
        del active_izin[user_id]

async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    if len(data_parts) < 3:
        return

    done_user_id = int(data_parts[1])
    izin_type = data_parts[2]
    from_user_id = query.from_user.id

    if from_user_id != done_user_id:
        await query.message.reply_text("⛔ Hanya yang mengambil izin yang bisa menekan DONE.", message_thread_id=query.message.message_thread_id)
        return

    if from_user_id in active_izin:
        if izin_type == "sebat" and from_user_id in sebat_users:
            sebat_users.remove(from_user_id)
        del active_izin[from_user_id]
        await query.message.reply_text("✅ Welcome back! Izin kamu sudah dicatat selesai.", message_thread_id=query.message.message_thread_id)

async def test_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.text = '/StartIzin'
    await start_izin(update, context)

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .defaults(Defaults(parse_mode="HTML"))
        .build()
    )

    application.add_handler(CommandHandler("StartIzin", start_izin))
    application.add_handler(CommandHandler("test", test_izin))
    application.add_handler(CallbackQueryHandler(handle_izin, pattern="^izin_"))
    application.add_handler(CallbackQueryHandler(handle_done, pattern="^done_"))

    async def webhook_handler(request):
        update = await request.json()
        await application.update_queue.put(Update.de_json(update, application.bot))
        return web.Response()

    async def handle_root(request):
        return web.Response(text="Bot is running")

    app = web.Application()
    app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, webhook_handler),
    ])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8000)))
    await site.start()

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to {WEBHOOK_URL}")

    await application.initialize()
    await application.start()
    logger.info("Bot started")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
