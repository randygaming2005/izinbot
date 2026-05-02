import logging
import os
import asyncio
import datetime
import pytz
import random

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

timezone = pytz.timezone("Asia/Jakarta")

# --- KONFIGURASI SPESIAL & SHIFT ---
OWNER_ID = 5043897152  # ID VIP (Mode Menyamar Aktif)

EPOCH_DATE = datetime.date(2026, 3, 23)
SHIFTS_ORDER = ["pagi", "malam", "siang"]
RESET_TIMES = {"pagi": 7, "siang": 15, "malam": 23} 

# --- STATE & DATABASE MEMORY ---
active_users = {}       
user_reasons = {}       
user_expired_times = {} 
sebat_users = []        
daily_usage = {}        

DEFAULT_SEBAT_LIMIT = 3 
sudah_kirim_reminder_rokok = False

# --- HELPER FUNCTIONS ---
def get_shift_quota_key():
    now = datetime.datetime.now(tz=timezone)
    if now.hour < 7:
        logical_now = now - datetime.timedelta(days=1)
    else:
        logical_now = now
        
    logical_date = logical_now.date()
    days_diff = (logical_date - EPOCH_DATE).days
    weeks_passed = days_diff // 7
    current_shift = SHIFTS_ORDER[weeks_passed % 3]
    
    reset_hour = RESET_TIMES[current_shift]
    if now.hour < reset_hour:
        effective_date = (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        effective_date = now.strftime('%Y-%m-%d')
        
    return f"{effective_date}_{current_shift}"

async def get_admin_ids(application, chat_id):
    try:
        members = await application.bot.get_chat_administrators(chat_id)
        return [admin.user.id for admin in members if not admin.user.is_bot]
    except Exception as e:
        logging.error(f"Error fetching admins: {e}")
        return []

def build_izin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚽 Toilet (5 Menit)", callback_data="izin_toilet_5"),
            InlineKeyboardButton("🚽 Toilet (15 Menit)", callback_data="izin_toilet_15"),
        ],
        [
            InlineKeyboardButton("🚬 Sebat (10 Menit)", callback_data="izin_sebat"),
            InlineKeyboardButton("🍽 Makan (15 Menit)", callback_data="izin_makan"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="izin_cancel"),
        ]
    ])

def build_done_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done (Kembali)", callback_data=f"done_{user_id}")]
    ])

# --- COMMAND HANDLERS ---
async def startizin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id
    
    now = datetime.datetime.now(tz=timezone)
    if now.hour < 7:
        logical_date = (now - datetime.timedelta(days=1)).date()
    else:
        logical_date = now.date()
    weeks_passed = (logical_date - EPOCH_DATE).days // 7
    current_shift = SHIFTS_ORDER[weeks_passed % 3]
    
    await update.message.reply_text(
        f"👋 Halo! Saat ini berada di <b>Shift {current_shift.capitalize()}</b>.\nSilakan pilih izin:\n",
        parse_mode='HTML',
        reply_markup=build_izin_keyboard(),
        message_thread_id=thread_id
    )

async def handle_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sudah_kirim_reminder_rokok
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    chat = query.message.chat
    thread_id = query.message.message_thread_id

    user_id = user.id
    is_vip = (user_id == OWNER_ID)

    reason_map = {
        "izin_toilet_5": ("toilet", 5),
        "izin_toilet_15": ("toilet", 15),
        "izin_sebat": ("sebat", 10),
        "izin_makan": ("makan", 15),
    }

    data = query.data

    if data == "izin_cancel":
        if user_id in active_users:
            job = active_users.pop(user_id, None)
            if job and job != "VIP":
                job.schedule_removal()
            
            reason_pop = user_reasons.pop(user_id, None)
            user_expired_times.pop(user_id, None)
            sebat_users[:] = [u for u in sebat_users if u["id"] != user_id]

            if reason_pop == "sebat" and len(sebat_users) <= 3:
                sudah_kirim_reminder_rokok = False

            await query.message.reply_text("❌ Izin kamu telah dibatalkan.")
        else:
            await query.message.reply_text("❌ Kamu tidak memiliki izin aktif untuk dibatalkan.")
        return

    if data not in reason_map:
        await query.message.reply_text("❌ Data izin tidak valid.")
        return

    reason, minutes = reason_map[data]

    if user_id in active_users:
        await query.message.reply_text(
            "⏳ Kamu masih punya izin aktif, silakan tekan Done dulu sebelum izin lagi.",
            reply_markup=build_done_keyboard(user_id)
        )
        return

    sisa_jatah_msg = ""
    if reason == "sebat":
        if any(u["id"] == user_id for u in sebat_users):
            await query.message.reply_text("⏳ Kamu sudah dalam izin sebat. Tekan Done dulu.")
            return
            
        if not is_vip:
            shift_key = get_shift_quota_key()
            today_key = f"{user_id}_{shift_key}_sebat"
            used_today = daily_usage.get(today_key, 0)
            
            if used_today >= DEFAULT_SEBAT_LIMIT:
                await query.message.reply_text(
                    f"❌ <b>IZIN DITOLAK:</b>\nJatah sebat/rokok kamu di shift ini sudah habis ({used_today}/{DEFAULT_SEBAT_LIMIT}).",
                    parse_mode='HTML'
                )
                return
            
            daily_usage[today_key] = used_today + 1
            sisa = DEFAULT_SEBAT_LIMIT - (used_today + 1)
            sisa_jatah_msg = f"\n⚠️ Jatah sebat tersisa: <b>{sisa}</b> kali (Shift ini)."
        else:
            # PENYAMARAN JATAH VIP: Tampilkan 1 atau 2 agar terlihat sisa
            jatah_palsu = random.randint(1, 2)
            sisa_jatah_msg = f"\n⚠️ Jatah sebat tersisa: <b>{jatah_palsu}</b> kali (Shift ini)."
            
        sebat_users.append({"id": user_id, "name": user.first_name})

    user_reasons[user_id] = reason

    reply_msg = (
        f"✅ <b>{user.first_name}</b> sudah izin <b>{reason}</b> selama {minutes} menit."
        f"{sisa_jatah_msg}\n\nSilakan tekan tombol <b>Done</b> di bawah ini setelah kembali bekerja."
    )

    await query.message.reply_text(
        reply_msg,
        parse_mode='HTML',
        reply_markup=build_done_keyboard(user_id),
        message_thread_id=thread_id
    )

    if reason == "sebat":
        jumlah_sebat = len(sebat_users)
        if jumlah_sebat > 3 and not sudah_kirim_reminder_rokok:
            msg_teguran = f"🚬 Sudah lebih dari 3 orang yang keluar untuk merokok!\nSaat ini: {jumlah_sebat} orang."
            await context.bot.send_message(chat_id=chat.id, text=msg_teguran, message_thread_id=thread_id)
            
            admins = await get_admin_ids(context.application, chat.id)
            for admin_id in admins:
                try:
                    await context.bot.send_message(admin_id, msg_teguran)
                except:
                    pass
            sudah_kirim_reminder_rokok = True

    # Set expired time untuk semuanya (agar VIP punya waktu mundur palsu di listizin)
    expiration = datetime.datetime.now(tz=timezone) + datetime.timedelta(minutes=minutes)
    user_expired_times[user_id] = expiration

    if not is_vip:
        job = context.job_queue.run_once(
            reminder_timeout,
            when=minutes * 60,
            data={"chat_id": chat.id, "user_id": user_id, "reason": reason, "thread_id": thread_id},
            name=f"reminder_{user_id}"
        )
        active_users[user_id] = job
    else:
        active_users[user_id] = "VIP"

async def reminder_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    user_id = data["user_id"]
    reason = data["reason"]

    active_users.pop(user_id, None)
    if reason == "sebat":
        sebat_users[:] = [u for u in sebat_users if u["id"] != user_id]

    admins = await get_admin_ids(context.application, chat_id)
    if not admins:
        return

    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        name = member.user.first_name
        username_tag = f"(@{member.user.username})" if member.user.username else ""
    except Exception:
        name = "Seseorang"
        username_tag = ""

    msg = f"⚠️ Peringatan: {name} {username_tag} belum kembali setelah batas waktu izin {reason}."

    for admin_id in admins:
        try:
            await context.bot.send_message(admin_id, msg)
        except Exception:
            pass

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sudah_kirim_reminder_rokok
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

        await query.message.reply_text(f"❌ {user.first_name}, tombol Done ini bukan untukmu! Ini milik {owner_name}.")
        return

    is_vip = (user.id == OWNER_ID)
    reason = user_reasons.pop(user.id, None)
    
    sebat_users[:] = [u for u in sebat_users if u["id"] != user.id]
    if reason == "sebat" and len(sebat_users) <= 3:
        sudah_kirim_reminder_rokok = False

    expired_time = user_expired_times.pop(user.id, None)
    job = active_users.pop(user.id, None)
    
    if job and job != "VIP":
        job.schedule_removal()

    now = datetime.datetime.now(tz=timezone)
    
    if expired_time:
        delay = now - expired_time
        
        # LOGIKA BARU: Jika bukan VIP dan telat, maka tampilkan terlambat.
        # Sebaliknya (jika VIP, ATAU jika user biasa tepat waktu), selalu tampilkan tepat waktu.
        if delay.total_seconds() > 0 and not is_vip:
            delay_min = int(delay.total_seconds() // 60)
            delay_sec = int(delay.total_seconds() % 60)
            
            await query.message.reply_text(
                f"⚠️ <b>{user.first_name}</b> selesai izin {reason}, namun <b>terlambat kembali</b> selama {delay_min}m {delay_sec}s.",
                parse_mode='HTML'
            )
            
            # CEPU KE ADMIN HANYA UNTUK NON-VIP YANG TELAT
            admins = await get_admin_ids(context.application, update.effective_chat.id)
            for admin_id in admins:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"Laporan Keterlambatan: {user.first_name} terlambat kembali dari {reason} selama {delay_min}m {delay_sec}s."
                    )
                except Exception:
                    pass
        else:
            # VIP (Seberapa pun telatnya) dan User Normal yang tepat waktu akan masuk ke sini
            await query.message.reply_text(
                f"✅ <b>{user.first_name}</b> telah selesai dari izin <b>{reason}</b> tepat waktu.",
                parse_mode='HTML'
            )
    else:
        await query.message.reply_text(f"✅ {user.first_name}, izin kamu sudah ditutup.")

async def list_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id
    if not active_users:
        await update.message.reply_text("✅ Tidak ada pengguna yang sedang izin saat ini.", message_thread_id=thread_id)
        return

    now = datetime.datetime.now(tz=timezone)
    lines = ["📋 <b>Daftar pengguna yang sedang izin:</b>\n"]
    
    for user_id, job in active_users.items():
        reason = user_reasons.get(user_id, "tidak diketahui")
        
        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
            name = member.user.first_name
        except:
            name = "Seseorang"

        expired = user_expired_times.get(user_id, now)
        remaining = expired - now
        if remaining.total_seconds() > 0:
            minutes = int(remaining.total_seconds() // 60)
            seconds = int(remaining.total_seconds() % 60)
        else:
            minutes = 0
            seconds = 0
            
        lines.append(f"👤 <b>{name}</b> ({reason}) - Sisa waktu: {minutes}m {seconds}s")

    await update.message.reply_text("\n".join(lines), parse_mode='HTML', message_thread_id=thread_id)

# --- WEBHOOK & APP SERVER ---
async def handle_root(request):
    return web.Response(text="Bot Izin Berjalan Mulus.")

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
