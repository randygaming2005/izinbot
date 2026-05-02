import logging
import os
import asyncio
import datetime
import pytz

from aiohttp import web
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
)

# --- LOGGING SETUP ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# --- CONFIGURATIONS ---
TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
OWNER_ID = int(os.environ.get("OWNER_ID", 0))  # Set di ENV atau ganti manual
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

timezone = pytz.timezone("Asia/Jakarta")

# Konfigurasi Jadwal & Shift
EPOCH_DATE = datetime.date(2026, 3, 23)
SHIFTS_ORDER = ["pagi", "malam", "siang"]
RESET_TIMES = {"pagi": 7, "siang": 15, "malam": 23}

# Durasi Default (Menit)
REASON_DEFAULTS = {
    "toilet": 5,
    "sebat": 10,
    "rokok": 10,
    "makan": 15,
}

# --- STATE & DATABASE MEMORY ---
active_users = {}       # {user_id: job_object atau "VIP"}
user_reasons = {}       # {user_id: "reason"}
user_expired_times = {} # {user_id: datetime}
sebat_users = []        # [{"id": user_id, "name": name}]
daily_usage = {}        # {"user_id_YYYY-MM-DD_shift_sebat": count}

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

async def get_top_admins_tag(context, chat_id):
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        # Filter admin manusia & limit 3
        human_admins = [a for a in admins if not a.user.is_bot]
        top_3 = human_admins[:3]
        return " ".join([f"<a href='tg://user?id={a.user.id}'>@{a.user.first_name}</a>" for a in top_3])
    except Exception:
        return "Admin"

# --- COMMAND HANDLERS ---

async def cmd_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sudah_kirim_reminder_rokok
    user = update.effective_user
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    is_vip = (user.id == OWNER_ID)

    if not context.args:
        await update.message.reply_text(
            "❌ <b>Format Salah!</b>\nGunakan: <code>/izin [alasan] [menit]</code>\nContoh: <code>/izin sebat</code> atau <code>/izin toilet 5</code>",
            parse_mode='HTML'
        )
        return

    reason = context.args[0].lower()
    
    if len(context.args) > 1 and context.args[1].isdigit():
        minutes = int(context.args[1])
    else:
        minutes = REASON_DEFAULTS.get(reason, 10)

    if user.id in active_users:
        await update.message.reply_text("⏳ Kamu masih memiliki izin aktif. Gunakan <b>/done</b> terlebih dahulu.", parse_mode='HTML')
        return

    sisa_jatah_msg = ""
    # Logika Khusus Sebat/Rokok (Berbagi Kuota)
    if reason in ["sebat", "rokok"]:
        if not is_vip:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            used = daily_usage.get(today_key, 0)
            
            if used >= DEFAULT_SEBAT_LIMIT:
                await update.message.reply_text(f"❌ <b>IZIN DITOLAK:</b>\nJatah merokok shift ini sudah habis ({used}/{DEFAULT_SEBAT_LIMIT}).", parse_mode='HTML')
                return
            
            daily_usage[today_key] = used + 1
            sisa = DEFAULT_SEBAT_LIMIT - (used + 1)
            sisa_jatah_msg = f"\n⚠️ Jatah sisa: <b>{sisa}</b> kali."
            
        sebat_users.append({"id": user.id, "name": user.first_name})

    user_reasons[user.id] = reason
    
    if is_vip:
        active_users[user.id] = "VIP"
        user_expired_times[user.id] = None
        reply_text = f"👑 <b>{user.first_name}</b> (VIP) izin <b>{reason}</b>."
    else:
        expiration = datetime.datetime.now(tz=timezone) + datetime.timedelta(minutes=minutes)
        user_expired_times[user.id] = expiration
        job = context.job_queue.run_once(
            reminder_timeout,
            when=minutes * 60,
            data={"chat_id": chat_id, "user_id": user.id, "reason": reason, "thread_id": thread_id},
            name=f"reminder_{user.id}"
        )
        active_users[user.id] = job
        reply_text = f"✅ <b>{user.first_name}</b> izin <b>{reason}</b> selama {minutes} menit."

    await update.message.reply_text(f"{reply_text}{sisa_jatah_msg}\n\nKetik <b>/done</b> jika sudah kembali.", parse_mode='HTML')

    # Monitor jumlah orang merokok
    if reason in ["sebat", "rokok"]:
        if len(sebat_users) > 3 and not sudah_kirim_reminder_rokok:
            msg_teguran = f"🚬 <b>PERINGATAN:</b> Sudah {len(sebat_users)} orang izin merokok!"
            await context.bot.send_message(chat_id=chat_id, text=msg_teguran, parse_mode='HTML', message_thread_id=thread_id)
            sudah_kirim_reminder_rokok = True

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sudah_kirim_reminder_rokok
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if user.id not in active_users:
        return

    reason = user_reasons.pop(user.id, None)
    expired_time = user_expired_times.pop(user.id, None)
    job = active_users.pop(user.id, None)

    if job and job != "VIP":
        job.schedule_removal()

    if reason in ["sebat", "rokok"]:
        global sebat_users
        sebat_users = [u for u in sebat_users if u["id"] != user.id]
        if len(sebat_users) <= 3:
            sudah_kirim_reminder_rokok = False

    now = datetime.datetime.now(tz=timezone)
    
    # Cek Keterlambatan
    if expired_time and now > expired_time:
        delay = now - expired_time
        dm, ds = divmod(int(delay.total_seconds()), 60)
        
        # Ambil Tag Admin
        admin_tags = await get_top_admins_tag(context, chat_id)
        
        penalti_msg = ""
        # Denda jatah jika sebat/rokok telat
        if reason in ["sebat", "rokok"] and (user.id != OWNER_ID):
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            daily_usage[today_key] = daily_usage.get(today_key, 0) + 1
            sisa = DEFAULT_SEBAT_LIMIT - daily_usage[today_key]
            penalti_msg = f"\n🚫 <b>PENALTI:</b> Jatah dipotong 1. Sisa: <b>{max(0, sisa)}</b>"

        alert_text = (
            f"🚨 <b>ALERT KETERLAMBATAN</b> 🚨\n\n"
            f"User: {user.mention_html()}\n"
            f"Alasan: <b>{reason}</b>\n"
            f"Terlambat: <b>{dm}m {ds}s</b>\n"
            f"{penalti_msg}\n\n"
            f"CC: {admin_tags}"
        )
        await update.message.reply_text(alert_text, parse_mode='HTML')
    else:
        await update.message.reply_text(f"✅ <b>{user.first_name}</b> kembali tepat waktu.", parse_mode='HTML')

async def reminder_timeout(context: ContextTypes.DEFAULT_TYPE):
    # Logika saat waktu habis (Reminder ke Admin)
    data = context.job.data
    user_id = data["user_id"]
    reason = data["reason"]
    active_users.pop(user_id, None)
    
    if reason in ["sebat", "rokok"]:
        global sebat_users
        sebat_users = [u for u in sebat_users if u["id"] != user_id]

    try:
        admins = await context.bot.get_chat_administrators(data["chat_id"])
        msg = f"⚠️ <b>Peringatan:</b> User ID {user_id} belum /done setelah waktu {reason} habis!"
        for admin in [a for a in admins if not a.user.is_bot]:
            try: await context.bot.send_message(admin.user.id, msg, parse_mode='HTML')
            except: pass
    except: pass

async def list_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_users:
        await update.message.reply_text("✅ Tidak ada yang sedang izin.")
        return
    
    res = ["📋 <b>Daftar Izin Aktif:</b>"]
    for uid, status in active_users.items():
        reason = user_reasons.get(uid, "izin")
        res.append(f"- User {uid}: {reason}")
    await update.message.reply_text("\n".join(res), parse_mode='HTML')

# --- WEB SERVER (UNTUK DEPLOY) ---
async def handle_root(request): return web.Response(text="Bot is Running.")
async def handle_webhook(request):
    app = request.app["application"]
    update = await request.json()
    await app.update_queue.put(Update.de_json(update, app.bot))
    return web.Response()

async def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler(["izin", "Izin"], cmd_izin))
    application.add_handler(CommandHandler(["done", "Done"], cmd_done))
    application.add_handler(CommandHandler("listizin", list_izin))

    # Server Setup
    app = web.Application()
    app["application"] = application
    app.add_routes([web.get("/", handle_root), web.post(WEBHOOK_PATH, handle_webhook)])
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    await application.initialize()
    await application.start()
    await application.job_queue.start()

    if WEBHOOK_URL:
        await application.bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set to {WEBHOOK_URL}")

    while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
