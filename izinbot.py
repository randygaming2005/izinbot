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
    user_cmd_id = update.message.message_id  # Simpan ID pesan command user
    is_vip = (user.id == OWNER_ID)

    if user.id in active_sessions:
        await update.message.reply_text("⏳ <b>TOLAK:</b> Kamu masih memiliki izin aktif. Gunakan <b>/done</b> terlebih dahulu.", parse_mode='HTML')
        return

    if not context.args:
        await update.message.reply_text(
            "❌ <b>Format Salah!</b>\nPilihan izin:\n"
            "👉 <code>/izin sebat</code>\n👉 <code>/izin makan</code>\n👉 <code>/izin toilet 5</code> (atau 15)",
            parse_mode='HTML'
        )
        return

    args = [a.lower() for a in context.args]
    raw_reason = args[0]
    
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
        await update.message.reply_text("❌ <b>TOLAK:</b> Alasan tidak valid. (sebat/makan/toilet).", parse_mode='HTML')
        return

    sisa_jatah_msg = ""

    if reason == "sebat":
        current_sebat_count = sum(1 for s in active_sessions.values() if s["reason"] == "sebat")
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
            sisa_jatah_msg = f"\n🎟 Sisa Jatah Sebat: <b>{sisa}x</b>"

    icon = get_reason_icon(reason)
    now = datetime.datetime.now(tz=timezone)
    
    if is_vip:
        reply_text = (
            f"👑 <b>MENCATAT IZIN... (VIP)</b>\n"
            f"👤 Nama : <b>{user.first_name}</b>\n"
            f"{icon} Izin : <b>{reason.upper()}</b>\n"
            f"⏳ Waktu : <b>Tidak Terbatas</b>"
        )
        sent_msg = await update.message.reply_text(f"{reply_text}{sisa_jatah_msg}\n\n<i>Pesan ini otomatis dihapus saat /done.</i>", parse_mode='HTML')
        
        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": None,
            "job": "VIP",
            "start_time": now,
            "bot_msg_id": sent_msg.message_id,
            "user_cmd_id": user_cmd_id
        }
    else:
        expiration = now + datetime.timedelta(minutes=minutes)
        job = context.job_queue.run_once(
            reminder_timeout,
            when=minutes * 60,
            data={"chat_id": chat_id, "user_id": user.id, "thread_id": thread_id},
            name=f"reminder_{user.id}"
        )
        reply_text = (
            f"⏳ <b>MENCATAT IZIN...</b>\n"
            f"👤 Nama : <b>{user.first_name}</b>\n"
            f"{icon} Izin : <b>{reason.upper()}</b>\n"
            f"⏱ Waktu : <b>{minutes} Menit</b>"
        )
        sent_msg = await update.message.reply_text(f"{reply_text}{sisa_jatah_msg}\n\n<i>Pesan ini otomatis dihapus saat /done.</i>", parse_mode='HTML')

        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": expiration,
            "job": job,
            "start_time": now,
            "bot_msg_id": sent_msg.message_id,
            "user_cmd_id": user_cmd_id
        }

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    done_cmd_id = update.message.message_id
    
    if user.id not in active_sessions:
        await update.message.reply_text("Kamu tidak sedang dalam status izin.", parse_mode='HTML')
        return

    session = active_sessions.pop(user.id)
    reason = session["reason"]
    expired_time = session["expire"]
    job = session["job"]
    start_time = session["start_time"]
    bot_msg_id = session["bot_msg_id"]
    user_cmd_id = session["user_cmd_id"]

    if job and job != "VIP":
        job.schedule_removal()

    # --- PEMBERSIHAN CHAT (AUTO-DELETE) ---
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=bot_msg_id)  # Hapus respon izin bot
        await context.bot.delete_message(chat_id=chat_id, message_id=user_cmd_id) # Hapus komen /izin
        await context.bot.delete_message(chat_id=chat_id, message_id=done_cmd_id) # Hapus komen /done
    except Exception as e:
        logging.warning(f"Gagal menghapus beberapa pesan (mungkin sudah dihapus manual): {e}")

    now = datetime.datetime.now(tz=timezone)
    icon = get_reason_icon(reason)
    
    start_str = start_time.strftime("%H:%M")
    end_str = now.strftime("%H:%M")
    
    # --- CEK KETERLAMBATAN & PENALTI ---
    status_text = "✅ Tepat Waktu"
    penalti_msg = ""
    cc_admin = ""

    if expired_time and now > expired_time:
        delay = now - expired_time
        dm, ds = divmod(int(delay.total_seconds()), 60)
        status_text = f"❌ Terlambat {dm}m {ds}s"
        cc_admin = "\nCC Petinggi: @oimar @cartenz88"
        
        if reason == "sebat" and user.id != OWNER_ID:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            current_used = daily_usage.get(today_key, 0)
            
            daily_usage[today_key] = current_used + 1
            sisa = DEFAULT_SEBAT_LIMIT - daily_usage[today_key]
            penalti_msg = f"\n🚫 Penalti: <b>Jatah Sebat -1 (Sisa: {max(0, sisa)}x)</b>"

    # --- INVOICE FINAL ---
    invoice_text = (
        f"🧾 <b>REKAP IZIN SELESAI</b>\n"
        f"👤 Nama    : <b>{user.first_name}</b>\n"
        f"{icon} Izin    : <b>{reason.upper()}</b>\n"
        f"📤 Keluar  : <b>{start_str}</b>\n"
        f"📥 Kembali : <b>{end_str}</b>\n"
        f"📊 Status  : <b>{status_text}</b>"
        f"{penalti_msg}{cc_admin}"
    )
    
    # Mengirim invoice sebagai pesan baru karena komen /done sudah dihapus
    thread_id = update.message.message_thread_id if update.message.is_topic_message else None
    await context.bot.send_message(
        chat_id=chat_id,
        text=invoice_text,
        parse_mode='HTML',
        message_thread_id=thread_id
    )

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
            f"⚠️ <b>WAKTU HABIS!</b> ⚠️\n"
            f"👤 Nama : <b>{name}</b>\n"
            f"{icon} Izin : <b>{reason.upper()}</b>\n\n"
            f"<i>Segera ketik /done jika sudah kembali ke posisi.</i>\n"
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
