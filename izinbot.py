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

DEFAULT_SEBAT_LIMIT = 3
MAX_CONCURRENT_SEBAT = 3

# --- STATE & DATABASE MEMORY ---
active_sessions = {}
daily_usage = {}  # {"user_id_YYYY-MM-DD_shift_sebat": count}

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

def get_reason_icon(reason):
    icons = {"sebat": "🚬", "toilet": "🚽", "makan": "🍱"}
    return icons.get(reason, "ℹ️")

# --- COMMAND HANDLERS ---

async def cmd_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id if update.message.is_topic_message else None
    is_vip = (user.id == OWNER_ID)

    if user.id in active_sessions:
        await update.message.reply_text("⏳ <b>TOLAK:</b> Kamu masih memiliki izin aktif. Gunakan <b>/done</b> terlebih dahulu.", parse_mode='HTML')
        return

    if not context.args:
        await update.message.reply_text(
            "❌ <b>Format Salah!</b>\nPilihan izin yang tersedia:\n"
            "👉 <code>/izin sebat</code> (Otomatis 10 mnt)\n"
            "👉 <code>/izin makan</code> (Otomatis 15 mnt)\n"
            "👉 <code>/izin toilet 5</code> atau <code>/izin toilet 15</code>",
            parse_mode='HTML'
        )
        return

    args = [a.lower() for a in context.args]
    raw_reason = args[0]
    
    # --- VALIDASI INPUT & DURASI ---
    minutes = 0
    reason = ""

    if raw_reason in ["sebat", "rokok"]:
        reason = "sebat"
        minutes = 10
    elif raw_reason == "makan":
        reason = "makan"
        minutes = 15
    elif raw_reason == "toilet":
        reason = "toilet"
        if len(args) > 1 and args[1] in ["5", "15"]:
            minutes = int(args[1])
        elif len(args) > 1 and args[1] not in ["5", "15"]:
            await update.message.reply_text("❌ <b>TOLAK:</b> Izin toilet hanya boleh <b>5</b> atau <b>15</b> menit.", parse_mode='HTML')
            return
        else:
            minutes = 5 
    else:
        await update.message.reply_text("❌ <b>TOLAK:</b> Alasan tidak valid. Hanya boleh: <b>sebat, makan, toilet</b>.", parse_mode='HTML')
        return

    sisa_jatah_msg = ""

    # --- LOGIKA KHUSUS SEBAT ---
    if reason == "sebat":
        current_sebat_count = sum(1 for s in active_sessions.values() if s["reason"] == "sebat")
        if current_sebat_count >= MAX_CONCURRENT_SEBAT and not is_vip:
            await update.message.reply_text("⛔ <b>TOLAK:</b> Kuota sebat penuh! Sudah 3 orang di luar. Tunggu ada yang /done.", parse_mode='HTML')
            return

        if not is_vip:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            used = daily_usage.get(today_key, 0)
            
            if used >= DEFAULT_SEBAT_LIMIT:
                await update.message.reply_text(f"❌ <b>TOLAK:</b> Jatah sebat shift ini sudah habis ({used}/{DEFAULT_SEBAT_LIMIT}).", parse_mode='HTML')
                return
            
            daily_usage[today_key] = used + 1
            sisa = DEFAULT_SEBAT_LIMIT - (used + 1)
            sisa_jatah_msg = f"\n⚠️ Sisa jatah sebat shift ini: <b>{sisa}x</b>"

    # --- EKSEKUSI IZIN ---
    icon = get_reason_icon(reason)
    now = datetime.datetime.now(tz=timezone)
    
    if is_vip:
        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": None,
            "job": "VIP"
        }
        reply_text = f"👑 <b>[VIP] {user.first_name}</b> izin {icon} <b>{reason.upper()}</b>."
    else:
        expiration = now + datetime.timedelta(minutes=minutes)
        job = context.job_queue.run_once(
            reminder_timeout,
            when=minutes * 60,
            data={"chat_id": chat_id, "user_id": user.id, "thread_id": thread_id},
            name=f"reminder_{user.id}"
        )
        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": expiration,
            "job": job
        }
        reply_text = f"✅ <b>{user.first_name}</b> izin {icon} <b>{reason.upper()}</b> selama <b>{minutes} Menit</b>."

    await update.message.reply_text(f"{reply_text}{sisa_jatah_msg}\n\n<i>Ketik /done jika sudah kembali.</i>", parse_mode='HTML')

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    if user.id not in active_sessions:
        await update.message.reply_text("Kamu tidak sedang dalam status izin.", parse_mode='HTML')
        return

    session = active_sessions.pop(user.id)
    reason = session["reason"]
    expired_time = session["expire"]
    job = session["job"]

    if job and job != "VIP":
        job.schedule_removal()

    now = datetime.datetime.now(tz=timezone)
    icon = get_reason_icon(reason)
    
    # --- CEK KETERLAMBATAN & PENALTI ---
    if expired_time and now > expired_time:
        delay = now - expired_time
        dm, ds = divmod(int(delay.total_seconds()), 60)
        
        penalti_msg = ""
        # Potong jatah sebat HANYA jika alasan izinnya sebat
        if reason == "sebat" and user.id != OWNER_ID:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            current_used = daily_usage.get(today_key, 0)
            
            daily_usage[today_key] = current_used + 1
            sisa = DEFAULT_SEBAT_LIMIT - daily_usage[today_key]
            penalti_msg = f"\n🚫 <b>PENALTI KETERLAMBATAN:</b> Jatah Sebat dipotong 1. Sisa: <b>{max(0, sisa)}x</b>"

        alert_text = (
            f"🚨 <b>ALERT KETERLAMBATAN</b> 🚨\n\n"
            f"👤 {user.mention_html()} telah kembali dari {icon} <b>{reason.upper()}</b>\n"
            f"⏰ Terlambat: <b>{dm}m {ds}s</b>"
            f"{penalti_msg}\n\n"
            f"CC: @oimar @cartenz88"
        )
        await update.message.reply_text(alert_text, parse_mode='HTML')
    else:
        await update.message.reply_text(f"✅ {icon} <b>{user.first_name}</b> kembali tepat waktu dari <b>{reason.upper()}</b>.", parse_mode='HTML')

async def reminder_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    
    if user_id in active_sessions:
        session = active_sessions[user_id]
        reason = session["reason"]
        name = session["name"]
        icon = get_reason_icon(reason)

        msg = (
            f"⚠️ <b>WAKTU HABIS!</b> ⚠️\n\n"
            f"{icon} <b>{name}</b> belum kembali dari <b>{reason.upper()}</b>!\n"
            f"Segera ketik /done jika sudah kembali ke posisi.\n\n"
            f"CC Petinggi: @oimar @cartenz88"
        )

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode='HTML',
                message_thread_id=thread_id
            )
        except Exception as e:
            logging.error(f"Gagal mengirim pesan timeout: {e}")

async def list_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not active_sessions:
        await update.message.reply_text("✅ Tidak ada anggota yang sedang izin keluar.", parse_mode='HTML')
        return
    
    now = datetime.datetime.now(tz=timezone)
    res = ["📋 <b>DAFTAR IZIN AKTIF:</b>\n"]
    
    for uid, data in active_sessions.items():
        name = data["name"]
        reason = data["reason"]
        expire = data["expire"]
        icon = get_reason_icon(reason)
        
        if expire is None: 
            timer_str = "∞ (VIP)"
        else:
            if now > expire:
                delay = now - expire
                dm, ds = divmod(int(delay.total_seconds()), 60)
                timer_str = f"❗️ TERLAMBAT {dm}m {ds}s"
            else:
                sisa = expire - now
                dm, ds = divmod(int(sisa.total_seconds()), 60)
                timer_str = f"⏳ Sisa {dm}m {ds}s"
                
        res.append(f"{icon} <b>{name}</b> - {reason.upper()} [{timer_str}]")
        
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

    application.add_handler(CommandHandler(["izin", "Izin"], cmd_izin))
    application.add_handler(CommandHandler(["done", "Done"], cmd_done))
    application.add_handler(CommandHandler("listizin", list_izin))

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
