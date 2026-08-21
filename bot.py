import os
import re
import glob
import time
import asyncio
import logging
import subprocess
import requests
import random
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DownloaderBot-v8")

# ----------------------------------------------------
# 🚂 الإعداد والمتغيرات - v8 Phoenix
# ----------------------------------------------------
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PROXY_URL = os.environ.get("PROXY_URL")  # مثال: socks5://user:pass@host:port أو http://host:port

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: المتغيرات الأساسية (API_ID, API_HASH, BOT_TOKEN) غير معرفة!")
    exit(1)

app = Client("SmartDownloaderBot_v8", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()
START_TIME = time.time()

COOKIES_FILE = "cookies.txt" if os.path.exists("cookies.txt") else None

class ProcessCancelledException(Exception):
    pass

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.108 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
]

# ----------------------------------------------------
# 📊 أدوات v8 Phoenix
# ----------------------------------------------------

def cleanup_temp_files(task_id: str):
    """تنظيف شامل لكافة الملفات المؤقتة والجلسات"""
    patterns = [
        f"downloads/{task_id}*",
        f"downloads/thumb_{task_id}*"
    ]
    for pattern in patterns:
        for file_path in glob.glob(pattern):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

def get_video_duration(video_path: str) -> int:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
        return int(float(output))
    except Exception:
        return 0

def ffmpeg_direct_download(stream_url: str, output_path: str) -> bool:
    """تحميل مباشر لروابط hls/m3u8 كحل أخير عبر ffmpeg"""
    try:
        cmd = [
            "ffmpeg", "-y",
            "-user_agent", random.choice(USER_AGENTS),
            "-i", stream_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        logger.warning(f"FFmpeg Direct Stream fallback failed: {e}")
        return False

def format_eta(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes:02d}m {secs:02d}s"
    return f"{secs:02d}s"

def get_cancel_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ إلغاء العملية (v8 Phoenix)", callback_data=f"cancel_{task_id}")
    ]])

def render_progress_bar(percentage: float) -> str:
    filled = int(percentage // 10)
    return "█" * filled + "░" * (10 - filled)

async def update_status_ui(message: Message, status_text: str, current: int, total: int, start_time: float, task_id: str):
    if task_id in CANCELLED_TASKS:
        raise ProcessCancelledException("CANCELLED_BY_USER")

    now = time.time()
    diff = now - start_time
    if diff <= 0:
        return

    percentage = (current / total) * 100 if total > 0 else 0
    speed = current / diff
    eta_seconds = round((total - current) / speed) if speed > 0 else 0

    text = (
        f"🔥 **[v8 Phoenix Engine] {status_text}**\n\n"
        f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
        f"📦 **الحجم:** `{current / (1024*1024):.1f}MB` / `{total / (1024*1024):.1f}MB`\n"
        f"⚡ **السرعة:** `{speed / (1024*1024):.2f} MB/s`\n"
        f"⏱️ **المتبقي:** `{format_eta(eta_seconds)}`"
    )

    try:
        await message.edit_text(text, reply_markup=get_cancel_keyboard(task_id))
    except Exception:
        pass

def generate_ffmpeg_thumbnail(video_path: str, thumb_path: str) -> bool:
    try:
        cmd = f'ffmpeg -y -i "{video_path}" -ss 00:00:02 -vframes 1 "{thumb_path}"'
        os.system(cmd)
        return os.path.exists(thumb_path)
    except Exception:
        return False

# ----------------------------------------------------
# 🧠 محرك الاستخراج - v8 Phoenix Engine
# ----------------------------------------------------

def make_ytdl_opts(base_opts: dict, status_msg: Message, loop: asyncio.AbstractEventLoop, task_id: str, action_name: str):
    start_time = time.time()
    last_update = [0]

    def hook(d):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")

        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            
            if total > 0 and (time.time() - last_update[0] > 2):
                last_update[0] = time.time()
                asyncio.run_coroutine_threadsafe(
                    update_status_ui(status_msg, action_name, downloaded, total, start_time, task_id),
                    loop
                )

    opts = base_opts.copy()
    opts["progress_hooks"] = [hook]
    opts["concurrent_fragment_downloads"] = 10
    opts["socket_timeout"] = 30
    opts["retries"] = 15
    opts["fragment_retries"] = 15
    
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
        
    if PROXY_URL:
        opts["proxy"] = PROXY_URL

    return opts

async def smart_extractor_v8(url: str, status_msg: Message, task_id: str):
    loop = asyncio.get_event_loop()

    thumb_opts = {
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}]
    }

    strategies = [
        ({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": f"downloads/{task_id}_s1.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True,
            "extractor_args": {"youtube": {"player_client": ["tv_embedded", "android", "web"]}},
            "headers": {"User-Agent": USER_AGENTS[0]},
            **thumb_opts
        }, "1/18: v8 Phoenix Main Engine"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s2.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": USER_AGENTS[1],
            "referer": "https://www.google.com/",
            **thumb_opts
        }, "2/18: Safari Referrer Mode"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s3.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "headers": {"Accept-Language": "en-US,en;q=0.9"},
            **thumb_opts
        }, "3/18: Googlebot Engine"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s4.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": USER_AGENTS[2],
            "headers": {"Sec-Fetch-Mode": "navigate"},
            **thumb_opts
        }, "4/18: Android Ultra Protocol"),

        ({
            "format": "bv*+ba/b",
            "hls_prefer_native": True,
            "outtmpl": f"downloads/{task_id}_s5.%(ext)s",
            "quiet": True, "nocheckcertificate": True, "allow_unplayable_formats": True,
            **thumb_opts
        }, "5/18: HLS Native Bypass"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s6.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "headers": {
                "User-Agent": USER_AGENTS[4],
                "Referer": url, "Origin": f"{url.split('/')[0]}//{url.split('/')[2]}"
            },
            **thumb_opts
        }, "6/18: Origin Header Bypass"),

        ({
            "format": "worstvideo+worstaudio/worst",
            "outtmpl": f"downloads/{task_id}_s7.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True, "legacy_server_connect": True,
            **thumb_opts
        }, "7/18: Legacy Connect Bypass"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s8.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 7.0) AppleWebKit/537.36 SamsungBrowser/5.0 TV Safari/537.36",
            **thumb_opts
        }, "8/18: Smart TV Client"),

        ({
            "format": "best/worst",
            "outtmpl": f"downloads/{task_id}_s9.%(ext)s",
            "quiet": True, "force_generic_extractor": True, "nocheckcertificate": True,
            **thumb_opts
        }, "9/18: Generic Phoenix Fallback"),

        ({
            "format": "mp4/best",
            "outtmpl": f"downloads/{task_id}_s10.%(ext)s",
            "quiet": True, "nocheckcertificate": True, "prefer_insecure": True,
            **thumb_opts
        }, "10/18: HTTP Fallback Protocol"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s11.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            **thumb_opts
        }, "11/18: Meta Scraper"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s12.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Twitterbot/1.0",
            **thumb_opts
        }, "12/18: Twitter Crawler Engine"),

        ({
            "format": "all/best",
            "hls_use_mpegts": True,
            "outtmpl": f"downloads/{task_id}_s13.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            **thumb_opts
        }, "13/18: MPEG-TS Stream"),

        ({
            "format": "worst",
            "outtmpl": f"downloads/{task_id}_s14.%(ext)s",
            "quiet": True, "nocheckcertificate": True, "no_check_certificates": True,
            **thumb_opts
        }, "14/18: Insecure SSL Bypass"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s15.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "AppleTV11,1/11.1",
            **thumb_opts
        }, "15/18: Apple TV Engine"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s16.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": USER_AGENTS[3],
            **thumb_opts
        }, "16/18: iOS Chrome Engine"),

        ({
            "format": "b/best",
            "outtmpl": f"downloads/{task_id}_s17.%(ext)s",
            "quiet": True, "force_generic_extractor": True, "nocheckcertificate": True, "ignoreerrors": True,
            **thumb_opts
        }, "17/18: Deep Generic Mode"),

        ({
            "format": "all",
            "outtmpl": f"downloads/{task_id}_s18.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            **thumb_opts
        }, "18/18: Force Stream Fetch")
    ]

    last_error_log = ""

    for idx, (st_opts, st_name) in enumerate(strategies, 1):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")
        try:
            await update_status_ui(status_msg, f"فحص Phoenix ({idx}/18)...", 0, 100, time.time(), task_id)
            
            opts = make_ytdl_opts(st_opts, status_msg, loop, task_id, f"تحميل الميديا ({idx}/18)")
            def run_yt(target_url):
                with YoutubeDL(opts) as ytdl:
                    info = ytdl.extract_info(target_url, download=True)
                    if info:
                        if "entries" in info and len(info["entries"]) > 0:
                            info = info["entries"][0]
                        return ytdl.prepare_filename(info), info.get("title", "Video"), info.get("duration", 0)
                    return None, None, 0

            file_path, title, duration = await loop.run_in_executor(None, run_yt, url)
            if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
                return file_path, title, duration, st_name, None
        except ProcessCancelledException:
            raise
        except Exception as e:
            last_error_log = str(e)
            logger.warning(f"محاولة v8 Phoenix رقم {idx} لم تنجح: {e}")
            continue

    # 🚀 خطة الطوارئ النهائية v8: المحاولة عبر FFmpeg Direct Stream إذا كان الرابط مباشر للبث
    if ".m3u8" in url or ".mpd" in url:
        try:
            await update_status_ui(status_msg, "🔥 تشغيل محرك FFmpeg Direct Stream...", 0, 100, time.time(), task_id)
            out_file = f"downloads/{task_id}_ffmpeg.mp4"
            success = await loop.run_in_executor(None, ffmpeg_direct_download, url, out_file)
            if success:
                return out_file, "Direct Stream Video", 0, "🔥 FFmpeg Direct Stream v8", None
        except Exception as e:
            logger.error(f"فشلت محاولة FFmpeg المباشرة: {e}")

    return None, None, 0, None, last_error_log

# ----------------------------------------------------
# 📡 معالجة الأحداث والرسائل والأوامر
# ----------------------------------------------------

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    CANCELLED_TASKS.add(task_id)
    
    if task_id in ACTIVE_TASKS:
        task = ACTIVE_TASKS[task_id]
        if not task.done():
            task.cancel()

    await callback.answer("🛑 تم إلغاء العملية وتنظيف الملفات!", show_alert=True)
    cleanup_temp_files(task_id)
    try:
        await callback.message.edit_text("❌ **تم إلغاء العملية وتنظيف الذاكرة المخصصة.**")
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    status_cookies = "مفعل 🍪" if COOKIES_FILE else "غير مفعل"
    status_proxy = "مفعل 🌐" if PROXY_URL else "غير مفعل"
    await message.reply_text(
        f"🔥 **أهلاً بك في الإصدار v8 Phoenix**\n\n"
        f"🍪 **نظام الكوكيز:** `{status_cookies}`\n"
        f"🌐 **نظام البروكسي:** `{status_proxy}`\n\n"
        f"أرسل رابط الفيديو الآن لبدء استخراجه وتحليله."
    )

@app.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client: Client, message: Message):
    uptime_sec = int(time.time() - START_TIME)
    active_count = len(ACTIVE_TASKS)
    await message.reply_text(
        f"📊 **إحصائيات البوت v8 Phoenix:**\n\n"
        f"⏱️ **مدة التشغيل:** `{format_eta(uptime_sec)}`\n"
        f"🔄 **المهام النشطة:** `{active_count}`\n"
        f"🍪 **الكوكيز:** `{'موجود' if COOKIES_FILE else 'غير موجود'}`\n"
        f"🌐 **البروكسي:** `{'مفعل' if PROXY_URL else 'غير مفعل'}`"
    )

async def process_download(client: Client, message: Message, task_id: str, url: str):
    status_msg = await message.reply_text(
        "⏳ **[v8 Phoenix] جاري تحليل الرابط واختراق الحماية...**",
        reply_markup=get_cancel_keyboard(task_id)
    )

    file_path = None
    thumb_path = None

    try:
        file_path, title, duration, used_method, error_log = await smart_extractor_v8(url, status_msg, task_id)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")

        if file_path and os.path.exists(file_path):
            if not duration or duration <= 0:
                duration = get_video_duration(file_path)

            base_path = os.path.splitext(file_path)[0]
            possible_thumbs = [f"{base_path}.jpg", f"{base_path}.png", f"{base_path}.webp"]
            
            for p in possible_thumbs:
                if os.path.exists(p):
                    thumb_path = p
                    break

            if not thumb_path or not os.path.exists(thumb_path):
                gen_thumb = f"downloads/thumb_{task_id}.jpg"
                if generate_ffmpeg_thumbnail(file_path, gen_thumb):
                    thumb_path = gen_thumb

            upload_start = time.time()
            last_up_update = [0]

            async def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED_BY_USER")
                if time.time() - last_up_update[0] > 2:
                    last_up_update[0] = time.time()
                    await update_status_ui(status_msg, "رفع الفيديو إلى تلجرام", current, total, upload_start, task_id)

            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                duration=int(duration) if duration else 0,
                caption=f"🎬 **{title}**\n⚡ **الطريقة:** `{used_method}`\n🔥 **Engine:** `v8 Phoenix`",
                progress=upload_progress
            )
            await status_msg.delete()
        else:
            # تحليل التشخيص بناءً على السجل
            diag_reason = "تشفير حماية DRM أو يتطلب توكينات جلسة خاصة بالمنصة."
            if "DRM" in str(error_log).upper() or "PROTECTED" in str(error_log).upper():
                diag_reason = "الفيديو مشفر بنظام حماية DRM المشفرة (Widevine/FairPlay)."
            elif "LOGIN" in str(error_log).upper() or "403" in str(error_log):
                diag_reason = "الفيديو يتطلب تسجل دخول أو حساب بريميوم (ينصح بإضافة cookies.txt)."

            await status_msg.edit_text(
                "❌ **تعذر استخراج الفيديو باستخدام v8 Phoenix.**\n\n"
                f"🧐 **التشخيص:** {diag_reason}\n\n"
                "💡 **حلول مقترحة:**\n"
                "1. إضافة ملف `cookies.txt` موثق للحساب في المجلد الرئيسي.\n"
                "2. إسناد متغير بيئة `PROXY_URL` للالتفاف على حظر الـ IP السحابي."
            )

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"🛑 تم إيقاف المهمة بنجاح: {task_id}")
    except Exception as e:
        logger.error(f"خطأ أثناء المعالجة: {e}")
        try:
            await status_msg.edit_text(f"❌ حدث خطأ غير متوقع: `{str(e)[:150]}`")
        except Exception:
            pass
    finally:
        cleanup_temp_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

@app.on_message(filters.text & filters.private & ~filters.forwarded)
async def handle_download(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        return

    task_id = f"{message.from_user.id}_{int(time.time())}"
    task = asyncio.create_task(process_download(client, message, task_id, url))
    ACTIVE_TASKS[task_id] = task

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    logger.info("🚀 تم تشغيل محرك v8 Phoenix بنجاح...")
    app.run()