import logging
import os
import asyncio
import datetime
import pytz
import html

from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from supabase import create_client, Client

# --- LOGGING SETUP ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- CONFIGURATIONS & SUPABASE ---
TOKEN = os.environ.get("TOKEN") or "YOUR_BOT_TOKEN_HERE"
OWNER_ID = int(os.environ.get("OWNER_ID", 0))  # VIP User
WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL_BASE = os.environ.get("WEBHOOK_URL_BASE")
WEBHOOK_URL = f"{WEBHOOK_URL_BASE}{WEBHOOK_PATH}" if WEBHOOK_URL_BASE else None

SUPABASE_URL = os.environ.get("SUPABASE_URL", "MASUKKAN_URL_SUPABASE_ANDA_DI_SINI")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "MASUKKAN_ANON_KEY_SUPABASE_ANDA_DI_SINI")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logging.info("Berhasil terhubung ke Supabase!")
except Exception as e:
    logging.error(f"Gagal menghubungkan ke Supabase: {e}")

timezone = pytz.timezone("Asia/Jakarta")

# Konfigurasi Jadwal & Shift
EPOCH_DATE = datetime.date(2026, 3, 23)
SHIFTS_ORDER = ["pagi", "malam", "siang"]
RESET_TIMES = {"pagi": 7, "siang": 15, "malam": 23}

job_references = {} 

# --- HELPER FUNCTIONS ---
def get_bot_settings():
    default_settings = {
        "limit_sebat_shift": 3,
        "max_orang_sebat": 3,
        "durasi_sebat": 10,
        "durasi_makan": 15,
        "durasi_toilet": 15,
        "durasi_ambil_makan": 10,
        "max_orang_ambil_makan": 2,
        "admin_tags": "@oimar @cartenz88"
    }
    try:
        res = supabase.table("bot_settings").select("*").eq("id", 1).execute()
        if len(res.data) > 0:
            db_data = res.data[0]
            for key in default_settings.keys():
                if db_data.get(key) is not None:
                    default_settings[key] = db_data[key]
    except Exception as e:
        logging.error(f"Gagal mengambil bot_settings. Error: {e}")
    return default_settings

def get_shift_quota_key():
    now = datetime.datetime.now(tz=timezone)
    if now.hour < 7: logical_now = now - datetime.timedelta(days=1)
    else: logical_now = now
        
    logical_date = logical_now.date()
    days_diff = (logical_date - EPOCH_DATE).days
    weeks_passed = days_diff // 7
    current_shift = SHIFTS_ORDER[weeks_passed % 3]
    
    reset_hour = RESET_TIMES[current_shift]
    if now.hour < reset_hour: effective_date = (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    else: effective_date = now.strftime('%Y-%m-%d')
    return f"{effective_date}_{current_shift}"

def get_reason_icon(reason):
    icons = {"sebat": "🚬", "toilet": "🚽", "makan": "🍱", "ambil makan": "🍱", "ambil minum": "🥤"}
    return icons.get(reason, "ℹ️")

# --- COMMAND HANDLERS ---
async def cmd_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id if update.message.is_topic_message else None
    is_vip = (user.id == OWNER_ID)
    settings = get_bot_settings()

    cek_aktif = supabase.table("izin_aktif").select("*").eq("user_id", user.id).execute()
    if len(cek_aktif.data) > 0:
        await update.message.reply_text("⏳ <b>TOLAK:</b> Kamu masih memiliki izin aktif. Gunakan <b>/done</b> terlebih dahulu.", parse_mode='HTML')
        return

    if not context.args:
        await update.message.reply_text("❌ <b>Format Salah!</b>\nPilihan izin:\n👉 <code>/izin sebat</code>\n👉 <code>/izin makan</code>\n👉 <code>/izin toilet</code>\n👉 <code>/izin ambil makan</code>\n👉 <code>/izin ambil minum</code>", parse_mode='HTML')
        return

    raw_reason = " ".join([a.lower() for a in context.args])
    minutes = 0
    reason = ""

    if raw_reason in ["sebat", "rokok"]:
        reason = "sebat"
        minutes = settings["durasi_sebat"]
    elif raw_reason == "makan":
        reason = "makan"
        minutes = settings["durasi_makan"]
    elif raw_reason in ["ambil makan", "ambil minum"]:
        reason = raw_reason
        minutes = settings["durasi_ambil_makan"]
    elif raw_reason == "toilet":
        reason = "toilet"
        minutes = settings["durasi_toilet"]
    else:
        await update.message.reply_text("❌ <b>TOLAK:</b> Alasan tidak valid.", parse_mode='HTML')
        return

    sisa = 0
    now = datetime.datetime.now(tz=timezone)
    
    cek_semua_izin = supabase.table("izin_aktif").select("*").execute()
    current_sebat_count = sum(1 for row in cek_semua_izin.data if row["reason"] == "sebat")
    current_ambil_count = sum(1 for row in cek_semua_izin.data if row["reason"] in ["ambil makan", "ambil minum"])

    if reason == "sebat":
        if current_sebat_count >= settings["max_orang_sebat"] and not is_vip:
            await update.message.reply_text("⛔ <b>TOLAK:</b> Kuota sebat penuh! Tunggu ada yang /done.", parse_mode='HTML')
            return
        if not is_vip:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            cek_kuota = supabase.table("daily_usage").select("used").eq("id", today_key).execute()
            used = cek_kuota.data[0]["used"] if len(cek_kuota.data) > 0 else 0
            if used >= settings["limit_sebat_shift"]:
                await update.message.reply_text(f"❌ <b>TOLAK:</b> Jatah sebat shift ini habis ({used}/{settings['limit_sebat_shift']}).", parse_mode='HTML')
                return
            supabase.table("daily_usage").upsert({"id": today_key, "used": used + 1}).execute()
            sisa = settings["limit_sebat_shift"] - (used + 1)

    if reason in ["ambil makan", "ambil minum"] and not is_vip:
        if current_ambil_count >= settings["max_orang_ambil_makan"]:
            await update.message.reply_text(f"⛔ <b>TOLAK:</b> Maksimal {settings['max_orang_ambil_makan']} orang ambil makan/minum bersamaan!", parse_mode='HTML')
            return

    start_time_str = now.strftime("%H.%M")
    display_reason = "ROKOK" if reason == "sebat" else reason.upper()
    safe_name = html.escape(user.first_name)

    reply_text = f"⏳ <b>IZIN DICATAT:</b>\n\nNama : <b>{safe_name}</b>\nAlasan : <b>{display_reason}</b>\nJam : <b>{start_time_str}</b>\n"

    if reason == "sebat":
        if not is_vip: reply_text += f"\nJatah Anda Sisa : <b>{sisa}</b>\n"
        reply_text += f"Saat ini ada <b>{current_sebat_count + 1} ORANG</b> yang sedang merokok\n"

    expiration_iso = None
    if is_vip: reply_text += f"\n<i>(Waktu Bebas - VIP)</i>\n"
    else:
        expiration = now + datetime.timedelta(minutes=minutes)
        expiration_iso = expiration.isoformat()
        reply_text += f"\n<i>(Waktu Izin: {minutes} Menit)</i>\n"

    reply_text += "\n📩 Reply <b>/done</b> jika sudah kembali"
    await update.message.reply_text(reply_text, parse_mode='HTML')

    data_izin = {
        "user_id": user.id, "name": user.first_name, "reason": reason, 
        "start_time": now.isoformat(), "expire_time": expiration_iso, "penalized": False
    }
    supabase.table("izin_aktif").insert(data_izin).execute()

    if not is_vip:
        job_reminder = context.job_queue.run_once(
            reminder_timeout, when=minutes * 60, 
            data={"chat_id": chat_id, "user_id": user.id, "thread_id": thread_id}, name=f"reminder_{user.id}"
        )
        job_references[user.id] = job_reminder

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cek_aktif = supabase.table("izin_aktif").select("*").eq("user_id", user.id).execute()
    
    if len(cek_aktif.data) == 0:
        await update.message.reply_text("Kamu tidak sedang dalam status izin.", parse_mode='HTML')
        return

    session = cek_aktif.data[0]
    reason = session["reason"]
    # PERBAIKAN ZONA WAKTU: Pastikan UTC dikonversi ke WIB sebelum diolah
    start_time = datetime.datetime.fromisoformat(session["start_time"]).astimezone(timezone)
    expire_time_str = session.get("expire_time")
    
    if user.id in job_references:
        try:
            job_references[user.id].schedule_removal()
            del job_references[user.id]
        except Exception: pass

    now = datetime.datetime.now(tz=timezone)
    total_seconds = int((now - start_time).total_seconds())
    dh, remainder = divmod(total_seconds, 3600)
    dm, ds = divmod(remainder, 60)
    dur_str = f"{dh} Jam {dm} Menit {ds} Detik" if dh > 0 else f"{dm} Menit {ds} Detik"
    safe_name = html.escape(user.first_name)

    is_late = False
    if expire_time_str:
        expire_time = datetime.datetime.fromisoformat(expire_time_str).astimezone(timezone)
        if now > expire_time: is_late = True

    if is_late: invoice_text = (f"❌ <b>IZIN TERLAMBAT:</b>\n<b>{safe_name}</b> Sudah kembali dari {reason.upper()}!\nWaktu Keluar : {dur_str}")
    else: invoice_text = (f"✅ <b>IZIN SELESAI:</b>\n<b>{safe_name}</b> Sudah kembali dari {reason.upper()}!\nWaktu Keluar : {dur_str}")
    
    # PERBAIKAN: is_late sekarang dicatat agar panel Leaderboard Telat bekerja!
    data_riwayat = {
        "user_id": session["user_id"], "name": session["name"], "reason": session["reason"],
        "start_time": session["start_time"], "end_time": now.isoformat(), "penalized": session["penalized"],
        "is_late": is_late
    }
    try: supabase.table("riwayat_izin").insert(data_riwayat).execute()
    except Exception as e: logging.error(f"Gagal menyimpan riwayat: {e}")

    supabase.table("izin_aktif").delete().eq("user_id", user.id).execute()
    await update.message.reply_text(invoice_text, parse_mode='HTML')

async def reminder_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    
    cek_aktif = supabase.table("izin_aktif").select("*").eq("user_id", user_id).execute()
    if len(cek_aktif.data) > 0:
        session = cek_aktif.data[0]
        reason = session["reason"]
        name = session["name"]
        penalized = session["penalized"]
        now = datetime.datetime.now(tz=timezone)
        
        # PERBAIKAN ZONA WAKTU: Ubah jam UTC database menjadi jam WIB
        start_time = datetime.datetime.fromisoformat(session["start_time"]).astimezone(timezone)
        start_str = start_time.strftime("%H:%M") # Akan menampilkan 07:30, bukan 00:30 lagi!
        
        dm, ds = divmod(int((now - start_time).total_seconds()), 60)
        dur_str = f"{dm} Menit"
        penalti_text = ""
        settings = get_bot_settings()
        
        if reason == "sebat" and user_id != OWNER_ID and not penalized:
            shift_key = get_shift_quota_key()
            today_key = f"{user_id}_{shift_key}_sebat"
            cek_kuota = supabase.table("daily_usage").select("used").eq("id", today_key).execute()
            current_used = cek_kuota.data[0]["used"] if len(cek_kuota.data) > 0 else 0
            new_used = current_used + 1
            supabase.table("daily_usage").upsert({"id": today_key, "used": new_used}).execute()
            supabase.table("izin_aktif").update({"penalized": True}).eq("user_id", user_id).execute()
            sisa = max(0, settings["limit_sebat_shift"] - new_used)
            penalti_text = f"\n\nPenalty : Jatah rokok dikurangi 1\nSisa jatah: {sisa}"

        safe_name = html.escape(name)
        msg = (f"⚠️ <a href=\"tg://user?id={user_id}\">{safe_name}</a> belum kembali setelah batas waktu izin\n\n"
               f"Alasan: {reason.capitalize()}\nKeluar sejak: {start_str}\nDurasi sekarang: {dur_str}{penalti_text}\n\n👀 {settings['admin_tags']}")
        try: await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML', message_thread_id=thread_id)
        except Exception as e: logging.error(f"Gagal mengirim pesan timeout: {e}")

async def list_izin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cek_semua = supabase.table("izin_aktif").select("*").execute()
    data_aktif = cek_semua.data
    if len(data_aktif) == 0:
        await update.message.reply_text("✅ Tidak ada anggota yang sedang izin keluar.", parse_mode='HTML')
        return
    
    now = datetime.datetime.now(tz=timezone)
    res = ["📋 <b>DAFTAR IZIN AKTIF:</b>\n"]
    
    for session in data_aktif:
        name, reason = session["name"], session["reason"]
        expire_time_str = session.get("expire_time")
        
        # PERBAIKAN ZONA WAKTU
        start_time = datetime.datetime.fromisoformat(session["start_time"]).astimezone(timezone)
        start_str = start_time.strftime("%H:%M")
        
        icon = get_reason_icon(reason)
        if not expire_time_str: timer_str = "∞ (VIP)"
        else:
            expire_time = datetime.datetime.fromisoformat(expire_time_str).astimezone(timezone)
            if now > expire_time:
                dm, ds = divmod(int((now - expire_time).total_seconds()), 60)
                timer_str = f"❗️ TELAT {dm}m {ds}s"
            else:
                dm, ds = divmod(int((expire_time - now).total_seconds()), 60)
                timer_str = f"⏳ Sisa {dm}m {ds}s"
        res.append(f"{icon} <b>{html.escape(name)}</b> ({reason.upper()})\n   └ Keluar: {start_str} | {timer_str}\n")
    await update.message.reply_text("\n".join(res), parse_mode='HTML')

# --- WEB SERVER ---
async def handle_root(request): return web.Response(text="Bot is Running dengan Supabase Zone Fix.")
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
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8000))).start()
    await application.initialize()
    await application.start()
    await application.job_queue.start()
    if WEBHOOK_URL: await application.bot.set_webhook(WEBHOOK_URL)
    while True: await asyncio.sleep(3600)

if __name__ == "__main__": asyncio.run(main())
