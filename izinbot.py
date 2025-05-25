import logging
import os
import asyncio
import datetime
from typing import Dict, List

from aiohttp import web
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, constants
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    PicklePersistence,
)

# --- Konfigurasi ---
TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")  # e.g., https://yourapp.onrender.com
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
ADMIN_CHAT_IDS = []  # Akan otomatis diisi dari anggota admin grup

timeouts: Dict[int, asyncio.Task] = {}
active_requests: Dict[int, Dict] = {}
sebat_quota: List[int] = []

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# --- Helper ---
def build_keyboard():
    keyboard = [
        [InlineKeyboardButton("izin jojo ya ndan (5 menit)", callback_data="izin_jojo")],
        [InlineKeyboardButton("izin ee ya ndan (10 menit)", callback_data="izin_ee")],
        [InlineKeyboardButton("izin sebat ya ndan (10 menit)", callback_data="izin_sebat")],
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Command /StartIzin ---
async def start_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Silakan pilih jenis izin:", reply_markup=build_keyboard(), message_thread_id=update.message.message_thread_id)

# --- Handle tombol izin ---
async def handle_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.full_name
    chat_id = query.message.chat_id
    message_thread_id = query.message.message_thread_id
    jenis = query.data.split("_")[1]  # jojo, ee, sebat

    if user_id in active_requests:
        await query.message.reply_text("🚫 Kamu masih dalam status izin. Tekan tombol Done dulu.", message_thread_id=message_thread_id)
        return

    if jenis == "sebat" and len(sebat_quota) >= 3:
        names = ", ".join([context.bot.get_chat_member(chat_id, uid).user.full_name for uid in sebat_quota])
        await query.message.reply_text(f"🚫 Batas izin sebat tercapai.
Silakan tunggu {names} kembali.", message_thread_id=message_thread_id)
        return

    durasi = 300 if jenis == "jojo" else 600
    label = {
        "jojo": "izin jojo ya ndan",
        "ee": "izin ee ya ndan",
        "sebat": "izin sebat ya ndan",
    }[jenis]

    done_button = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data=f"done_{user_id}")]
    ])

    await query.message.reply_text(
        f"✅ {username} telah mengambil {label} ({durasi // 60} menit).",
        reply_markup=done_button,
        message_thread_id=message_thread_id
    )

    async def reminder():
        await asyncio.sleep(durasi)
        if user_id in active_requests:
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    await context.bot.send_message(admin_id, f"⚠️ {username} belum kembali setelah {label}.")
                except Exception as e:
                    logging.warning(f"Gagal mengirim ke admin: {e}")
            active_requests.pop(user_id)
            if jenis == "sebat":
                sebat_quota.remove(user_id)

    task = asyncio.create_task(reminder())
    timeouts[user_id] = task
    active_requests[user_id] = {"name": username, "jenis": jenis}
    if jenis == "sebat":
        sebat_quota.append(user_id)

# --- Handle Done ---
async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.full_name
    data = query.data

    if not data.endswith(str(user_id)):
        await query.message.reply_text("🚫 Hanya pengguna yang izin yang dapat menekan tombol Done.")
        return

    if user_id in timeouts:
        timeouts[user_id].cancel()
        del timeouts[user_id]
    if user_id in active_requests:
        jenis = active_requests[user_id]["jenis"]
        if jenis == "sebat" and user_id in sebat_quota:
            sebat_quota.remove(user_id)
        del active_requests[user_id]

    await query.message.reply_text(f"✅ {username} telah kembali dari izin.", message_thread_id=query.message.message_thread_id)

# --- Command list izin ---
async def list_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_requests:
        await update.message.reply_text("📭 Tidak ada yang sedang izin.", message_thread_id=update.message.message_thread_id)
    else:
        isi = "\n".join([f"- {data['name']} ({data['jenis']})" for data in active_requests.values()])
        await update.message.reply_text(f"📋 Yang sedang izin:\n{isi}", message_thread_id=update.message.message_thread_id)

# --- Command test (1 menit) ---
async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.full_name
    chat_id = update.effective_chat.id
    message_thread_id = update.message.message_thread_id

    if user_id in active_requests:
        await update.message.reply_text("🚫 Kamu masih dalam status izin.", message_thread_id=message_thread_id)
        return

    await update.message.reply_text(
        f"✅ {username} sedang dalam mode test (1 menit).",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data=f"done_{user_id}")]
        ]),
        message_thread_id=message_thread_id
    )

    async def reminder():
        await asyncio.sleep(60)
        if user_id in active_requests:
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    await context.bot.send_message(admin_id, f"⚠️ {username} belum kembali dari mode test.")
                except Exception as e:
                    logging.warning(f"Gagal mengirim ke admin: {e}")
            active_requests.pop(user_id)

    task = asyncio.create_task(reminder())
    timeouts[user_id] = task
    active_requests[user_id] = {"name": username, "jenis": "test"}

# --- Webhook dan setup ---
async def handle_root(request):
    return web.Response(text="Bot aktif")

async def handle_webhook(request):
    app = request.app["application"]
    update = await request.json()
    from telegram import Update as TgUpdate
    tg_update = TgUpdate.de_json(update, app.bot)
    await app.update_queue.put(tg_update)
    return web.Response()

async def fetch_admins(app):
    global ADMIN_CHAT_IDS
    try:
        # Ganti dengan ID grup target utama
        TARGET_GROUP_ID = int(os.environ.get("GROUP_ID"))
        admins = await app.bot.get_chat_administrators(chat_id=TARGET_GROUP_ID)
        ADMIN_CHAT_IDS = [admin.user.id for admin in admins if not admin.user.is_bot]
        logging.info(f"Admin terdeteksi: {ADMIN_CHAT_IDS}")
    except Exception as e:
        logging.error(f"Gagal mengambil daftar admin: {e}")

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(PicklePersistence(filepath="state.pkl"))
        .build()
    )

    application.add_handler(CommandHandler("StartIzin", start_izin))
    application.add_handler(CallbackQueryHandler(handle_izin, pattern="^izin_"))
    application.add_handler(CallbackQueryHandler(handle_done, pattern="^done_"))
    application.add_handler(CommandHandler("listIzin", list_izin))
    application.add_handler(CommandHandler("test", test))

    await fetch_admins(application)

    app = web.Application()
    app["application"] = application
    app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Webserver started on port {port}")

    await application.initialize()
    await application.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
