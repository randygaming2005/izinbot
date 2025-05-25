import logging
import os
import asyncio
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from aiohttp import web

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("TOKEN")
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")  # Contoh: https://yourbot.onrender.com
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}"

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
    nama = parts[2]
    user = query.from_user
    chat_id = query.message.chat_id
    thread_id = query.message.message_thread_id

    # Kirim tombol Done
    done_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data=f"done_{user.id}_{nama}")]
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{user.full_name} minta izin {nama} selama {menit} menit.",
        reply_markup=done_keyboard,
        message_thread_id=thread_id
    )

    # Simpan status izin di context
    context.chat_data[f"izin_{user.id}"] = {
        "time": datetime.datetime.utcnow(),
        "duration": menit,
        "done": False,
        "user": user,
        "nama": nama,
        "chat_id": chat_id,
        "thread_id": thread_id,
    }

    # Jalankan timer
    asyncio.create_task(timer_check(user.id, context))

# === Handle tombol Done ===
async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    _, user_id, nama = data.split("_")
    user_id = int(user_id)
    izin_data = context.chat_data.get(f"izin_{user_id}")

    if izin_data:
        izin_data["done"] = True
        await query.edit_message_text(text=f"{query.from_user.full_name} telah kembali dari {nama}.")
    else:
        await query.edit_message_text(text="⏱️ Izin tidak ditemukan atau sudah selesai.")

# === Timer: kirim ke admin jika belum done ===
async def timer_check(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(5)  # Delay sedikit untuk jaga stabilitas

    izin_key = f"izin_{user_id}"
    izin = context.chat_data.get(izin_key)
    if not izin:
        return

    await asyncio.sleep(izin["duration"] * 60)  # Tunggu selama izin

    if not izin["done"]:
        try:
            admins = await context.bot.get_chat_administrators(izin["chat_id"])
            for admin in admins:
                if not admin.user.is_bot:
                    try:
                        await context.bot.send_message(
                            chat_id=admin.user.id,
                            text=(
                                f"⚠️ {izin['user'].full_name} belum menekan Done setelah izin {izin['nama']} "
                                f"{izin['duration']} menit lalu di grup {izin['chat_id']}"
                            )
                        )
                    except Exception as e:
                        logging.warning(f"Gagal kirim ke admin {admin.user.full_name}: {e}")
        except Exception as e:
            logging.error(f"Gagal mengambil admin grup: {e}")

    context.chat_data.pop(izin_key, None)

# === Error Handler ===
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Error:", exc_info=context.error)

# === Webhook Setup ===
async def handle_webhook(request):
    app = request.app["application"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.update_queue.put(update)
    return web.Response()

async def handle_root(request):
    return web.Response(text="Bot aktif!")

async def start_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_izin, pattern=r"^izin_\d+_.+"))
    app.add_handler(CallbackQueryHandler(handle_done, pattern=r"^done_\d+_.+"))
    app.add_error_handler(error_handler)

    web_app = web.Application()
    web_app["application"] = app
    web_app.router.add_post(WEBHOOK_PATH, handle_webhook)
    web_app.router.add_get("/", handle_root)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8000)))
    await site.start()

    if WEBHOOK_URL:
        await app.bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set: {WEBHOOK_URL}")
    else:
        logging.warning("WEBHOOK_URL not set, bot will not receive updates!")

    await app.initialize()
    await app.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(start_bot())
