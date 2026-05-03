import logging
import os
import asyncio
import datetime
import pytz
import { supabase } from './supabase';

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
AUTO_CANCEL_MINUTES = 30 # Menit tambahan sebelum dihapus paksa

# --- STATE & DATABASE MEMORY ---
active_sessions = {}
daily_usage = {}  # {"user_id_YYYY-MM-DD_shift_sebat": count}

  // Attempt to load from Supabase
    try {
        const { data, error } = await supabase.from('bot_config').select('config').eq('id', 1).single();
        if (data && data.config) {
            // Merge with current config to preserve any new fields added in code
            config = { ...config, ...data.config };
            console.log("[SUPABASE] Config loaded successfully. Bot Enabled:", config.botEnabled);
            console.log("[SUPABASE] Loaded Brands:", config.brands.join(', '));
            
            // Prioritize token from config (set via UI)
            if (config.botToken) {
                currentToken = config.botToken;
                console.log("[SUPABASE] Bot token loaded from config");
            }
        } else if (error) {
            console.log("[SUPABASE] Config load error (might be empty):", error.message);
        }
    } catch (e) {
        console.log("[SUPABASE] Config error:", e);
    }

    // --- TOKEN FALLBACKS ---
    // If token not loaded from Supabase or is invalid, try local file
    if ((!currentToken || currentToken.length < 10) && fs.existsSync(TOKEN_FILE_PATH)) {
        try {
            const data = JSON.parse(fs.readFileSync(TOKEN_FILE_PATH, 'utf-8'));
            if (data.token && data.token.length > 10) {
                currentToken = data.token;
                console.log("[BOT] Token loaded from local file");
            }
        } catch (e) {
            console.error("Error reading token file", e);
        }
    }

    // Finally, fallback to environment variable (Highest priority if others are missing)
    if ((!currentToken || currentToken.length < 10) && process.env.BOT_TOKEN) {
        currentToken = process.env.BOT_TOKEN;
        console.log("[ENV] Token loaded from environment variable (BOT_TOKEN)");
        // Update config so it's persisted
        config.botToken = currentToken;
    }

    if (!currentToken || currentToken.length < 10) {
        console.warn("❌ CRITICAL: No valid Bot Token found in Supabase, Local JSON, or Environment Variables.");
    }

    console.log(`[BOT] Token status: ${currentToken && currentToken.length > 10 ? 'Configured (starts with ' + currentToken.substring(0, 5) + '...)' : 'Not Configured'}`);

    // --- ENVIRONMENT OVERRIDES (Master Override) ---
    // This allows the bot to recover IDs automatically from .env if they are set
    if (process.env.GROUP_ID) {
        config.groupId = process.env.GROUP_ID;
        console.log(`[ENV] Group ID overridden from Environment: ${config.groupId}`);
    }
    if (process.env.ALERT_TOPIC_ID) {
        config.alertTopicId = parseInt(process.env.ALERT_TOPIC_ID);
        console.log(`[ENV] Alert Topic ID overridden from Environment: ${config.alertTopicId}`);
    }
    if (process.env.REPORT_TOPIC_ID) {
        config.reportTopicId = parseInt(process.env.REPORT_TOPIC_ID);
        console.log(`[ENV] Report Topic ID overridden from Environment: ${config.reportTopicId}`);
    }

    // Load Limits
    if (fs.existsSync(LIMITS_FILE_PATH)) {
        try {
            userLimits = JSON.parse(fs.readFileSync(LIMITS_FILE_PATH, 'utf-8'));
        } catch (e) {
            console.error("Error reading limits file", e);
        }
    }
    try {
        const { data, error } = await supabase.from('bot_limits').select('limits').single();
        if (data && data.limits) {
            userLimits = data.limits;
            console.log("[SUPABASE] Limits loaded successfully");
        }
    } catch (e) {
        console.log("[SUPABASE] Limits table not found or error");
    }

    // Load Reports
    let fileReports: BrandReport[] = [];
    if (fs.existsSync(REPORTS_FILE_PATH)) {
        try {
            fileReports = JSON.parse(fs.readFileSync(REPORTS_FILE_PATH, 'utf-8'));
            brandReports = fileReports;
        } catch (e) {
            console.error("Error reading reports file", e);
        }
    }
    try {
        const { data, error } = await supabase.from('bot_reports').select('reports').single();
        if (data && data.reports && Array.isArray(data.reports)) {
            // Only overwrite if Supabase has more or newer data, or if local is empty
            if (data.reports.length >= brandReports.length) {
                brandReports = data.reports;
                console.log("[SUPABASE] Reports loaded successfully (Supabase preferred)");
            } else {
                console.log("[SUPABASE] Local reports are newer/larger, keeping local");
            }
        }
        if (error) console.log("[SUPABASE] Reports table error:", error.message);
    } catch (e: any) {
        console.log("[SUPABASE] Reports load exception:", e.message);
    }

    // Load Permits
    if (fs.existsSync(PERMITS_FILE_PATH)) {
        try {
            permits = JSON.parse(fs.readFileSync(PERMITS_FILE_PATH, 'utf-8'));
        } catch (e) {
            console.error("Error reading permits file", e);
        }
    }
    try {
        const { data, error } = await supabase.from('bot_permits').select('permits').single();
        if (data && data.permits) {
            permits = data.permits;
            console.log("[SUPABASE] Permits loaded successfully");
        }
    } catch (e) {
        console.log("[SUPABASE] Permits table not found or error");
    }

    // Load Usage
    if (fs.existsSync(USAGE_FILE_PATH)) {
        try {
            dailyUsage = JSON.parse(fs.readFileSync(USAGE_FILE_PATH, 'utf-8'));
        } catch (e) {
            console.error("Error reading usage file", e);
        }
    }
    try {
        const { data, error } = await supabase.from('bot_usage').select('usage').single();
        if (data && data.usage) {
            dailyUsage = data.usage;
            console.log("[SUPABASE] Usage loaded successfully");
        }
    } catch (e) {
        console.log("[SUPABASE] Usage table not found or error");
    }

    loadLastPin();
};

// Call initial load
export const init = async () => {
    await loadInitialData();
};

// --- DATA PERSISTENCE ---
const TOKEN_FILE_PATH = path.resolve('backend/token.json');

const saveConfig = async () => {
    try {
        fs.writeFileSync(CONFIG_FILE_PATH, JSON.stringify(config, null, 2));
        // Sync to Supabase
        await supabase.from('bot_config').upsert({ id: 1, config: config, updated_at: new Date() });
    } catch (e) {
        console.error("Error saving config", e);
    }
};

const saveLimits = async () => {
    try {
        fs.writeFileSync(LIMITS_FILE_PATH, JSON.stringify(userLimits, null, 2));
        // Sync to Supabase
        await supabase.from('bot_limits').upsert({ id: 1, limits: userLimits, updated_at: new Date() });
    } catch (e) {
        console.error("Error saving limits", e);
    }
};

const saveReports = async () => {
    try {
        fs.writeFileSync(REPORTS_FILE_PATH, JSON.stringify(brandReports, null, 2));
        // Sync to Supabase
        const { error } = await supabase.from('bot_reports').upsert({ id: 1, reports: brandReports, updated_at: new Date() });
        if (error) {
            console.error("[SUPABASE] Error saving reports:", error.message);
        } else {
            console.log(`[SUPABASE] Reports synced successfully (${brandReports.length} items)`);
        }
    } catch (e) {
        console.error("Error saving reports locally:", e);
    }
};

const savePermits = async () => {
    try {
        fs.writeFileSync(PERMITS_FILE_PATH, JSON.stringify(permits, null, 2));
        // Sync to Supabase
        await supabase.from('bot_permits').upsert({ id: 1, permits: permits, updated_at: new Date() });
    } catch (e) {
        console.error("Error saving permits", e);
    }
};

const saveUsage = async () => {
    try {
        fs.writeFileSync(USAGE_FILE_PATH, JSON.stringify(dailyUsage, null, 2));
        // Sync to Supabase
        await supabase.from('bot_usage').upsert({ id: 1, usage: dailyUsage, updated_at: new Date() });
    } catch (e) {
        console.error("Error saving usage", e);
    }
};

let bot: Telegraf | null = null;
let currentToken = '';
let lastBotError: string | null = null;
let retryCount = 0;
let conflictCount = 0; // NEW: Track consecutive 409 conflicts
const MAX_RETRIES = 15; // Increased retries
let reconnectTimeout: NodeJS.Timeout | null = null;
let watchdogInterval: NodeJS.Timeout | null = null;
let isStarting = false;
let isConflictDisabled = false; // NEW: Flag to stop auto-restart on conflict

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
    user_cmd_id = update.message.message_id  
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
        sent_msg = await update.message.reply_text(f"{reply_text}{sisa_jatah_msg}", parse_mode='HTML')
        
        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": None,
            "job": "VIP",
            "job_autocancel": "VIP",
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
        
        job_autocancel = context.job_queue.run_once(
            autocancel_timeout,
            when=(minutes + AUTO_CANCEL_MINUTES) * 60,
            data={"chat_id": chat_id, "user_id": user.id, "thread_id": thread_id},
            name=f"autocancel_{user.id}"
        )
        
        reply_text = (
            f"⏳ <b>MENCATAT IZIN...</b>\n"
            f"👤 Nama : <b>{user.first_name}</b>\n"
            f"{icon} Izin : <b>{reason.upper()}</b>\n"
            f"⏱ Waktu : <b>{minutes} Menit</b>"
        )
        sent_msg = await update.message.reply_text(f"{reply_text}{sisa_jatah_msg}", parse_mode='HTML')

        active_sessions[user.id] = {
            "name": user.first_name,
            "reason": reason,
            "expire": expiration,
            "job": job_reminder,
            "job_autocancel": job_autocancel,
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
    job_autocancel = session.get("job_autocancel")
    start_time = session["start_time"]
    was_penalized = session.get("penalized", False)

    if job and job != "VIP":
        job.schedule_removal()
    if job_autocancel and job_autocancel != "VIP":
        job_autocancel.schedule_removal()

    now = datetime.datetime.now(tz=timezone)
    
    total_seconds = int((now - start_time).total_seconds())
    dh, remainder = divmod(total_seconds, 3600)
    dm, ds = divmod(remainder, 60)
    
    if dh > 0:
        dur_str = f"{dh} Jam {dm} Menit {ds} Detik"
    else:
        dur_str = f"{dm} Menit {ds} Detik"
    
    penalti_msg = ""
    cc_admin = ""

    if expired_time and now > expired_time:
        cc_admin = '\n\n⚠️ <i>Melewati batas waktu!</i>\n👀 <a href="tg://user?id=7616244848">@oimar</a> <a href="tg://user?id=986211789">@cartenz88</a>'
        
        # Hanya tampilkan sisa, tidak usah kurangi lagi karena sudah dikurangi di reminder_timeout
        if reason == "sebat" and user.id != OWNER_ID:
            shift_key = get_shift_quota_key()
            today_key = f"{user.id}_{shift_key}_sebat"
            sisa = DEFAULT_SEBAT_LIMIT - daily_usage.get(today_key, 0)
            penalti_msg = f"\n🚫 Penalti: <b>Jatah Sebat -1 (Sisa: {max(0, sisa)}x)</b>"

    invoice_text = (
        f"✅ <b>IZIN SELESAI:</b>\n"
        f"<b>{user.first_name}</b> Sudah kembali dari <b>{reason.upper()}</b>!\n"
        f"Waktu Keluar : <b>{dur_str}</b>"
        f"{penalti_msg}{cc_admin}"
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

        msg = (
            f"⚠️ <a href=\"tg://user?id={user_id}\">{name}</a> belum kembali setelah batas waktu izin\n\n"
            f"Alasan: {reason.capitalize()}\n"
            f"Keluar sejak: {start_str}\n"
            f"Durasi sekarang: {dur_str}"
            f"{penalti_text}\n\n"
            f"👀 <a href=\"tg://user?id=7616244848\">@oimar</a> <a href=\"tg://user?id=986211789\">@cartenz88</a>"
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

async def autocancel_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data["user_id"]
    chat_id = data["chat_id"]
    thread_id = data.get("thread_id")
    
    if user_id in active_sessions:
        session = active_sessions.pop(user_id) 
        reason = session["reason"]
        name = session["name"]

        penalti_msg = ""
        if reason == "sebat" and user_id != OWNER_ID:
            shift_key = get_shift_quota_key()
            today_key = f"{user_id}_{shift_key}_sebat"
            sisa = DEFAULT_SEBAT_LIMIT - daily_usage.get(today_key, 0)
            penalti_msg = f"\n\n🚫 <b>PENALTI SUDAH DITERAPKAN:</b>\nJatah sebat dikurangi 1. (Sisa: <b>{max(0, sisa)}x</b>)"

        msg = (
            f"☠️ <b>IZIN DIHAPUS PAKSA!</b> ☠️\n"
            f"👤 <a href=\"tg://user?id={user_id}\">{name}</a>\n"
            f"Alasan: <b>Lupa ketik /done lebih dari 30 menit.</b>{penalti_msg}\n\n"
            f"👀 <a href=\"tg://user?id=7616244848\">@oimar</a> <a href=\"tg://user?id=986211789\">@cartenz88</a>"
        )

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode='HTML',
                message_thread_id=thread_id
            )
        except Exception as e:
            logging.error(f"Gagal mengirim pesan auto cancel: {e}")

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
