import os
import asyncio
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
persistence = PicklePersistence(filepath="reminder_data.pkl")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Halo! Gunakan /izin di grup untuk minta izin.")

async def izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("izin jojo ya ndan (5 menit)", callback_data="izin_jojo_5")],
        [InlineKeyboardButton("izin ee ya ndan (10 menit)", callback_data="izin_ee_10")],
        [InlineKeyboardButton("izin sebat ya ndan (10 menit)", callback_data="izin_sebat_10")],
    ]
    await update.message.reply_text("Pilih jenis izin:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_izin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    chat = update.effective_chat

    reason_map = {
        "izin_jojo_5": ("jojo", 5),
        "izin_ee_10": ("ee", 10),
        "izin_sebat_10": ("sebat", 10),
    }

    reason, minutes = reason_map[query.data]

    # Simpan status izin aktif user
    context.user_data["izin_active"] = True

    # Kirim pesan dengan tombol Done
    done_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data="done_pressed")]
    ])
    await query.edit_message_text(
        f"✅ Izin *{reason}* diterima. Waktu: *{minutes} menit*.\n"
        "Silakan tekan *Done* jika sudah kembali.",
        reply_markup=done_keyboard,
        parse_mode="Markdown"
    )

    # Ambil semua admin non-bot di grup
    admins = await context.bot.get_chat_administrators(chat.id)
    admin_ids = [admin.user.id for admin in admins if not admin.user.is_bot]

    # Simpan admin list untuk job nanti
    context.chat_data["admin_ids"] = admin_ids
    context.chat_data["user_name"] = user.full_name
    context.chat_data["user_id"] = user.id

    # Jadwalkan job cek izin
    context.job_queue.run_once(
        notify_admin_if_not_done,
        when=minutes * 60,
        chat_id=user.id,
        name=f"izin_{user.id}",
        data={"user_id": user.id, "user_name": user.full_name, "admin_ids": admin_ids}
    )

async def handle_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["izin_active"] = False
    await query.edit_message_text("✅ Selamat datang kembali! Status izin selesai.")

async def notify_admin_if_not_done(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data["user_id"]
    user_name = data["user_name"]
    admin_ids = data["admin_ids"]

    # Cek user status izin
    # Kalau kamu pakai persistence, cek disini, atau bisa pakai user_data (tapi harus diingat user_data biasanya per update session)
    # Di sini pakai aplikasi chat_data sebagai contoh
    user_status = context.application.user_data.get(user_id, {})
    izin_active = user_status.get("izin_active", True)  # Default True untuk aman

    if izin_active:
        for admin_id in admin_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"⚠️ {user_name} belum kembali dari izin!"
                )
            except Exception as e:
                logging.error(f"Gagal kirim pesan ke admin {admin_id}: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Exception:", exc_info=context.error)

async def handle_root(request):
    return web.Response(text="Bot is running")

async def handle_webhook(request):
    app = request.app["application"]
    update = await request.json()
    from telegram import Update as TgUpdate
    tg_update = TgUpdate.de_json(update, app.bot)
    await app.update_queue.put(tg_update)
    return web.Response()

async def start_jobqueue(app):
    await app.job_queue.start()

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(persistence)
        .post_init(start_jobqueue)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("izin", izin))
    application.add_handler(CallbackQueryHandler(handle_izin_callback, pattern="^izin_"))
    application.add_handler(CallbackQueryHandler(handle_done_callback, pattern="^done_pressed$"))
    application.add_error_handler(error_handler)

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
    logging.info(f"Server running on port {port}")

    await application.initialize()
    await application.start()

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
