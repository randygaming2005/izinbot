import logging
import os
import asyncio
import datetime
import pytz

from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID") or 0)

timezone = pytz.timezone("Asia/Jakarta")

active_users = {}
user_reasons = {}
user_expired_times = {}
sebat_users = []
MAX_SEBAT = 3

async def get_admin_ids(application, chat_id):
    try:
        members = await application.bot.get_chat_administrators(chat_id)
        return [admin.user.id for admin in members]
    except Exception as e:
        logging.error(f"Error fetching admins: {e}")
        return []

def build_izin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("izin toilet (5 menit)", callback_data="izin_toilet_5"),
            InlineKeyboardButton("izin toilet (15 menit)", callback_data="izin_toilet_15"),
        ],
        [
            InlineKeyboardButton("izin sebat (10 menit)", callback_data="izin_sebat"),
            InlineKeyboardButton("cancel", callback_data="izin_cancel"),
        ]
    ])

def build_done_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Done", callback_data=f"done_{user_id}")]
    ])

async def startizin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Halo! Pilih tombol izin di bawah ini untuk izin:\n",
        reply_markup=build_izin_keyboard()
    )

async def handle_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    chat = query.message.chat
    thread_id = query.message.message_thread_id

    user_id = user.id
    reason_map = {
        "izin_toilet_5": ("toilet", 5),
        "izin_toilet_15": ("toilet", 15),
        "izin_sebat": ("sebat", 10),
    }

    data = query.data

    # Cancel existing izin
    if data == "izin_cancel":
        if user_id in active_users:
            job = active_users.pop(user_id, None)
            if job:
                job.schedule_removal()
            user_reasons.pop(user_id, None)
            user_expired_times.pop(user_id, None)
            # Always remove from sebat_users if present
            sebat_users[:] = [u for u in sebat_users if u["id"] != user_id]
            await query.message.reply_text("❌ Izin kamu telah dibatalkan.")
        else:
            await query.message.reply_text("❌ Kamu tidak memiliki izin aktif untuk dibatalkan.")
        return

    # Invalid callback data
    if data not in reason_map:
        await query.message.reply_text("❌ Data izin tidak valid.")
        return

    reason, minutes = reason_map[data]

    # Already has active izin
    if user_id in active_users:
        await query.message.reply_text(
            "⏳ Kamu masih punya izin aktif, silakan tekan Done dulu sebelum izin lagi.",
            reply_markup=build_done_keyboard(user_id),
        )
        return

    # Handle 'sebat' reason limit
    if reason == "sebat":
        if any(u["id"] == user_id for u in sebat_users):
            await query.message.reply_text("⏳ Kamu sudah dalam izin sebat. Tekan Done dulu.")
            return
        if len(sebat_users) >= MAX_SEBAT:
            names = ", ".join([u["name"] for u in sebat_users])
            await query.message.reply_text(
                f"🚫 silahkan kerjakan dahulu tugas mu, dan tunggu {names} kembali"
            )
            return
        sebat_users.append({"id": user_id, "name": user.first_name})

    # Record izin state
    user_reasons[user_id] = reason

    await query.message.reply_text(
        f"✅ {user.first_name} sudah izin {reason} selama {minutes} menit.\n"
        "Silakan tekan tombol Done setelah selesai.",
        reply_markup=build_done_keyboard(user_id),
        message_thread_id=thread_id,
    )

    # Set expiration
    expiration = datetime.datetime.now(tz=timezone) + datetime.timedelta(minutes=minutes)
    user_expired_times[user_id] = expiration

    # Schedule reminder job
    job = context.job_queue.run_once(
        reminder_timeout,
        when=minutes * 60,
        data={"chat_id": chat.id, "user_id": user_id, "reason": reason, "thread_id": thread_id},
        name=f"reminder_{user_id}"
    )
    active_users[user_id] = job

async def reminder_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    user_id = data["user_id"]
    reason = data["reason"]
    thread_id = data.get("thread_id")

    # Remove active state
    active_users.pop(user_id, None)
    if reason == "sebat":
        sebat_users[:] = [u for u in sebat_users if u["id"] != user_id]

    # Fetch admin IDs
    admins = await get_admin_ids(context.application, chat_id)
    if not admins:
        logging.warning("Tidak dapat menemukan admin grup untuk kirim pesan.")
        return

    # Fetch user's name
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        name = member.user.first_name
    except Exception:
        name = "Seseorang"

    msg = f"{name} belum kembali setelah izin {reason}."

    # Notify admins
    for admin_id in admins:
        try:
            await context.bot.send_message(admin_id, msg)
        except Exception as e:
            logging.error(f"Gagal kirim pesan ke admin {admin_id}: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if not data.startswith("done_"):
        await query.message.reply_text("❌ Callback tidak dikenali.")
        return

    user_id_done = int(data.split("_")[1])
    if user.id != user_id_done:
        try:
            owner = await context.bot.get_chat_member(update.effective_chat.id, user_id_done)
            owner_name = owner.user.first_name
        except Exception:
            owner_name = "pengguna lain"

        await query.message.reply_text(
            f"❌ {user.first_name}, tombol Done ini bukan untukmu! (Ini tombol milik {owner_name})."
        )
        return

    # Pop reason and always remove from sebat_users
    reason = user_reasons.pop(user.id, None)
    sebat_users[:] = [u for u in sebat_users if u["id"] != user.id]

    expired_time = user_expired_times.pop(user.id, None)
    job = active_users.pop(user.id, None)
    if job:
        job.schedule_removal()

    now = datetime.datetime.now(tz=timezone)
    if expired_time:
        delay = now - expired_time
        if delay.total_seconds() > 0:
            delay_min = int(delay.total_seconds() // 60)
            delay_sec = int(delay.total_seconds() % 60)
            await query.message.reply_text(
                f"⚠️ {user.first_name}, kamu terlambat kembali selama {delay_min}m {delay_sec}s."
            )
            admins = await get_admin_ids(context.application, update.effective_chat.id)
            for admin_id in admins:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"{user.first_name}, Terlambat kembali selama {delay_min}m {delay_sec}s."
                    )
                except Exception as e:
                    logging.error(f"Gagal kirim pesan ke admin {admin_id}: {e}")
        else:
            await query.message.reply_text(
                f"✅ {user.first_name} sudah selesai izin {reason}."
            )
    else:
        await query.message.reply_text(
            f"✅ {user.first_name}, izin {reason or 'tidak diketahui'} kamu sudah kedaluwarsa, tapi Done tetap diterima."
        )

async def list_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_users:
        await update.message.reply_text("✅ Tidak ada pengguna yang sedang izin saat ini.")
        return

    now = datetime.datetime.now(tz=timezone)
    lines = ["📋 Daftar pengguna yang sedang izin:"]
    for user_id, job in active_users.items():
        reason = user_reasons.get(user_id, "tidak diketahui")
        remaining = job.next_t - now
        minutes = int(remaining.total_seconds() // 60)
        seconds = int(remaining.total_seconds() % 60)
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        lines.append(f"- {member.user.first_name} ({reason}, sisa {minutes}m {seconds}s)")

    await update.message.reply_text("\n".join(lines))

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
    logging.info("✅ JobQueue started.")

async def main():
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("startizin", startizin))
    application.add_handler(CommandHandler("listizin", list_izin))
    application.add_handler(CallbackQueryHandler(handle_izin, pattern="^izin_"))
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^done_"))
    application.add_error_handler(lambda update, context: logging.error(f"Error: {context.error}"))

    app = web.Application()
    app["application"] = application
    app.add_routes([
        web.get("/", handle_root),
        web.post(WEBHOOK_PATH, handle_webhook),
    ])

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
        logging.info(f"🌐 Webhook set to {WEBHOOK_URL}")
    else:
        logging.warning("⚠️ WEBHOOK_URL_BASE env not set, webhook disabled!")

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Webserver started on port {port}")

    await application.initialize()
    await application.start()
    await start_jobqueue(application)

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
