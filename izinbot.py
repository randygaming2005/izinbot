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

# --- STATE MEMORY ---
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
    # Menambahkan ikon untuk ambil makan dan ambil minum
    icons = {"sebat": "🚬", "toilet": "🚽", "makan": "🍱", "ambil makan": "🍱", "ambil minum": "🥤"}
    return icons.get(reason, "ℹ️")

# --- COMMAND HANDLERS ---

async def cmd_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id if update.message.is_topic_message else None
    
    user_cmd_id = update.message.message_id  
    is_vip = (user.id == OWNER_ID)

    if user.id in active_sessions:
        await update.message.reply_text("⏳ <b>TOLAK:</b> Kamu masih memiliki izin aktif. Gunakan <b>/done</b> terlebih dahulu.", parse_mode='HTML')
        return

    if not context.args:
        await update.message.reply_text(
            "❌ <b>Format Salah!</b>\nPilihan izin:\n"
            "👉 <code>/izin sebat</code>\n"
            "👉 <code>/izin makan</code>\n"
            "👉 <code>/izin toilet</code>\n"
            "👉 <code>/izin ambil makan</code>\n"
            "👉 <code>/izin ambil minum</code>",
            parse_mode='HTML'
        )
        return

    # Gabung semua argumen untuk membaca multi-kata seperti "ambil makan" atau "ambil minum"
    raw_reason = " ".join([a.lower() for a in context.args])
    
    minutes = 0
    reason = ""

    # --- PEMISAHAN LOGIKA IZIN ---
    if raw_reason in ["sebat", "rokok"]:
        reason = "sebat"
        minutes = 10
    elif raw_reason == "makan":
        reason = "makan"
        minutes = 15 # Silakan ubah angka ini jika durasi izin makan utama berbeda
    elif raw_reason in ["ambil makan", "ambil minum"]:
        reason = raw_reason # Menyimpan secara spesifik "ambil makan" atau "ambil minum"
        minutes = 15
    elif raw_reason == "toilet":
        reason = "toilet"
        minutes = 15
    else:
        await update.message.reply_text("❌ <b>TOLAK:</b> Alasan tidak valid. (sebat/makan/toilet/ambil makan/ambil minum).", parse_mode='HTML')
        return

    sisa = 0
    current_sebat_count = sum(1 for s in active_sessions.values() if s["reason"] == "sebat")

    if reason == "sebat":
        if current_sebat_count >= MAX_CONCURRENT_SEBAT and not is_vip:
            await update.message.reply_text("⛔ <b>TOLAK:</b> Kuota sebat penuh! Tunggu ada yang /done.", parse_mode='HTML')
            return

        if not is_vip:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            used = daily_usage.get(today_key, 0)
            
            if used >= DEFAULT_SEBAT_LIMIT:
                await update.message.reply_text(f"❌ <b>TOLAK:</b> Jatah sebat shift ini habis ({used}/{DEFAULT_SEBAT_LIMIT}).", parse_mode='HTML')
                return
            
            daily_usage[today_key] = used + 1
            sisa = DEFAULT_SEBAT_LIMIT - (used + 1)

    now = datetime.datetime.now(tz=timezone)
    start_time_str = now.strftime("%H.%M")
    
    # Menyesuaikan teks kapital untuk tampilan di pesan
    display_reason = "ROKOK" if reason == "sebat" else reason.upper()

    # --- FORMAT PESAN (IZIN DICATAT) ---
    reply_text = (
        f"⏳ <b>IZIN DICATAT:</b>\n\n"
        f"Nama : <b>{user.first_name}</b>\n"
        f"Alasan : <b>{display_reason}</b>\n"
        f"Jam : <b>{start_time_str}</b>\n"
    )

    if reason == "sebat":
        if not is_vip:
            reply_text += f"\nJatah Anda Sisa : <b>{sisa}</b>\n"
        reply_text += f"Saat ini ada <b>{current_sebat_count + 1} ORANG</b> yang sedang merokok\n"

    if is_vip:
        reply_text += f"\n<i>(Waktu Bebas - VIP)</i>\n"
    else:
        reply_text += f"\n<i>(Waktu Izin: {minutes} Menit)</i>\n"

    reply_text += "\n📩 Reply <b>/done</b> jika sudah kembali"

    sent_msg = await update.message.reply_text(reply_text, parse_mode='HTML')

    if is_vip:
        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": None,
            "job": "VIP",
            "start_time": now,
            "penalized": False,
            "bot_msg_id": sent_msg.message_id,
            "user_cmd_id": user_cmd_id
        }
    else:
        expiration = now + datetime.timedelta(minutes=minutes)
        job_reminder = context.job_queue.run_once(
            reminder_timeout,
            when=minutes * 60,
            data={"chat_id": chat_id, "user_id": user.id, "thread_id": thread_id},
            name=f"reminder_{user.id}"
        )
        
        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": expiration,
            "job": job_reminder,
            "start_time": now,
            "penalized": False,
            "bot_msg_id": sent_msg.message_id,
            "user_cmd_id": user_cmd_id
        }

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id not in active_sessions:
        await update.message.reply_text("Kamu tidak sedang dalam status izin.", parse_mode='HTML')
        return

    session = active_sessions.pop(user.id)
    reason = session["reason"]
    expired_time = session["expire"]
    job = session["job"]
    start_time = session["start_time"]
    was_penalized = session.get("penalized", False)

    if job and job != "VIP":
        job.schedule_removal()

    now = datetime.datetime.now(tz=timezone)
    
    total_seconds = int((now - start_time).total_seconds())
    dh, remainder = divmod(total_seconds, 3600)
    dm, ds = divmod(remainder, 60)
    
    if dh > 0:
        dur_str = f"{dh} Jam {dm} Menit {ds} Detik"
    else:
        dur_str = f"{dm} Menit {ds} Detik"

    # Logika telat atau tidak
    is_late = expired_time and now > expired_time

    if is_late:
        invoice_text = (
            f"❌ <b>IZIN TERLAMBAT:</b>\n"
            f"{user.first_name} Sudah kembali dari {reason.upper()}!\n"
            f"Waktu Keluar : {dur_str}"
        )
    else:
        invoice_text = (
            f"✅ <b>IZIN SELESAI:</b>\n"
            f"{user.first_name} Sudah kembali dari {reason.upper()}!\n"
            f"Waktu Keluar : {dur_str}"
        )
    
    await update.message.reply_text(invoice_text, parse_mode='HTML')

async def reminder_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    
    if user_id in active_sessions:
        session = active_sessions[user_id]
        reason = session["reason"]
        name = session["name"]
        start_time = session["start_time"]
        
        now = datetime.datetime.now(tz=timezone)
        start_str = start_time.strftime("%H:%M")
        
        total_seconds = int((now - start_time).total_seconds())
        dm, ds = divmod(total_seconds, 60)
        dur_str = f"{dm} Menit"
        
        penalti_text = ""
        # Kurangi kuota langsung di sini jika dia sebat & bukan bos
        if reason == "sebat" and user_id != OWNER_ID:
            if not session.get("penalized"):
                shift_key = get_shift_quota_key()
                today_key = f"{user_id}_{shift_key}_sebat"
                current_used = daily_usage.get(today_key, 0)
                
                daily_usage[today_key] = current_used + 1
                sisa = DEFAULT_SEBAT_LIMIT - daily_usage[today_key]
                session["penalized"] = True
                
                penalti_text = (
                    f"\n\nPenalty : Jatah rokok dikurangi 1\n"
                    f"Sisa jatah: {max(0, sisa)}"
                )

        # Menggunakan direct mention @username agar push notification masuk dengan akurat
        msg = (
            f"⚠️ <a href=\"tg://user?id={user_id}\">{name}</a> belum kembali setelah batas waktu izin\n\n"
            f"Alasan: {reason.capitalize()}\n"
            f"Keluar sejak: {start_str}\n"
            f"Durasi sekarang: {dur_str}"
            f"{penalti_text}\n\n"
            f"👀 @oimar @cartenz88"
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
        start = data["start_time"].strftime("%H:%M")
        icon = get_reason_icon(reason)
        
        if expire is None: 
            timer_str = "∞ (VIP)"
        else:
            if now > expire:
                delay = now - expire
                dm, ds = divmod(int(delay.total_seconds()), 60)
                timer_str = f"❗️ TELAT {dm}m {ds}s"
            else:
                sisa = expire - now
                dm, ds = divmod(int(sisa.total_seconds()), 60)
                timer_str = f"⏳ Sisa {dm}m {ds}s"
                
        res.append(f"{icon} <b>{name}</b> ({reason.upper()})\n   └ Keluar: {start} | {timer_str}\n")
        
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
