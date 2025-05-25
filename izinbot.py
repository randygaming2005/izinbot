import logging
import os
import asyncio
import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberAdministrator
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")  # Contoh: https://yourbot.onrender.com
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

# Simpan izin yang aktif
active_izin = {}

# === Command Start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id
    keyboard = [
        [
            InlineKeyboardButton("🕔 Izin Jojo (5 menit)", callback_data="izin_5_jojo"),
            InlineKeyboardButton("🕙 Izin Ee (10 menit)", callback_data="izin_10_ee"),
        ],
        [
            InlineKeyboardButton("🚬 Izin Sebat (10 menit)", callback_data="izin_10_sebat")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Pilih izin:", reply_markup=reply_markup, message_thread_id=thread_id)

# === Handle tombol izin ===
async def handle_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    parts = data.split("_")
    if len(parts) != 3:
        return

    menit = int(parts[1])
    alasan = parts[2]
    user = query.from_user
    chat_id = query.message.chat_id
    thread_id = query.message.message_thread_id

    key = f"{chat_id}_{user.id}"
    deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=menit)
    active_izin[key] = {"user": user, "alasan": alasan, "deadline": deadline}

    # Kirim tombol Done
    done_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data=f"done_{user.id}_{alasan}")]
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🕒 {user.full_name} minta izin {alasan} selama {menit} menit.",
        reply_markup=done_keyboard,
        message_thread_id=thread_id
    )

    # Jadwalkan pengecekan
    await asyncio.sleep(menit * 60)
    if key in active_izin:
        del active_izin[key]
        await notify_admins(chat_id, context, f"⚠️ {user.full_name} belum tekan Done setelah izin {alasan}.")

# === Handle Done ===
async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    if len(parts) < 3:
        return

    user_id = int(parts[1])
    alasan = parts[2]
    chat_id = query.message.chat_id

    key = f"{chat_id}_{user_id}"
    if key in active_izin:
        del active_izin[key]
        await query.edit_message_text(f"✅ Izin {alasan} telah selesai ditekan oleh user.")

# === Notifikasi ke semua admin ===
async def notify_admins(chat_id, context, message):
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for admin in admins:
            if isinstance(admin, ChatMemberAdministrator):
                try:
                    await context.bot.send_message(chat_id=admin.user.id, text=message)
                except Exception as e:
                    logging.warning(f"Gagal kirim ke admin {admin.user.id}: {e}")
    except Exception as e:
        logging.error(f"Gagal ambil admin grup: {e}")

# === Command Test (1 menit izin) ===
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.text = "/test"
    update.message.message_thread_id = update.message.message_thread_id
    fake_callback = type("obj", (object,), {
        "data": "izin_1_test",
        "from_user": update.message.from_user,
        "message": update.message
    })()
    update.callback_query = fake_callback
    await handle_izin(update, context)

# === AIOHTTP Webhook Setup ===
async def handle_root(request):
    return web.Response(text="Bot is running.")

async def handle_webhook(request):
    app = request.app["application"]
    data = await request.json()
    from telegram import Update as TgUpdate
    update = TgUpdate.de_json(data, app.bot)
    await app.update_queue.put(update)
    return web.Response()

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test))
    application.add_handler(CallbackQueryHandler(handle_izin, pattern="^izin_"))
    application.add_handler(CallbackQueryHandler(handle_done, pattern="^done_"))

    app = web.Application()
    app["application"] = application
    app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    await application.initialize()
    await application.start()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
