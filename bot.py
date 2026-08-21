import os
import time
import asyncio
import logging
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# 🚂 جلب المتغيرات
# ----------------------------------------------------
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: المتغيرات غير معرفة في Railway Variables!")
    exit(1)

try:
    API_ID = int(API_ID)
except ValueError:
    logger.critical("❌ خطأ: قيمة API_ID يجب أن تكون رقمية!")
    exit(1)

app = Client("SmartDownloaderBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()

class ProcessCancelledException(Exception):
    pass

# ----------------------------------------------------
# 📊 أدوات تنسيق الوقت، الوسائط وشريط التقدم
# ----------------------------------------------------

def get_video_duration(video_path: str) -> int:
    """استخراج مدة الفيديو الدقيقة بالثواني مباشرة من الملف باستخدام ffprobe"""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
        return int(float(output))
    except Exception as e:
        logger.warning(f"تعذر استخراج مدة الفيديو عبر ffprobe: {e}")
        return 0

def format_eta(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes:02d}m {secs:02d}s"
    else:
        return f"{secs:02d}s"

def get_cancel_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel_{task_id}")
    ]])

def render_progress_bar(percentage: float) -> str:
    filled = int(percentage // 10)
    return "█" * filled + "░" * (10 - filled)

async def update_status_ui(message: Message, status_text: str, current: int, total: int, start_time: float, task_id: str):
    if task_id in CANCELLED_TASKS:
        raise ProcessCancelledException("تم إلغاء العملية بواسطة المستخدم")

    now = time.time()
    diff = now - start_time
    if diff <= 0:
        return

    percentage = (current / total) * 100 if total > 0 else 0
    speed = current / diff
    eta_seconds = round((total - current) / speed) if speed > 0 else 0
    formatted_eta = format_eta(eta_seconds)

    text = (
        f"⏳ **{status_text}**\n\n"
        f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
        f"📦 **الحجم:** `{current / (1024*1024):.1f}MB` / `{total / (1024*1024):.1f}MB`\n"
        f"🚀 **السرعة:** `{speed / (1024*1024):.2f} MB/s`\n"
        f"⏱️ **المتبقي:** `{formatted_eta}`"
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
# 🧠 محرك الاستخراج - 15 استراتيجية متقدمة
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
    return opts

async def smart_extractor(url: str, status_msg: Message, task_id: str):
    loop = asyncio.get_event_loop()

    thumb_opts = {
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}]
    }

    # 🚀 15 استراتيجية فائقة لتجاوز الحظر والسيرفرات المحمية
    strategies = [
        # 1. القياسية (دقة عالية)
        ({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": f"downloads/{task_id}_s1.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True,
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            **thumb_opts
        }, "1/15: Standard High-Quality"),

        # 2. آيفون Safari
        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s2.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
            "referer": "https://www.google.com/",
            **thumb_opts
        }, "2/15: Mobile Safari Spoofing"),

        # 3. محاكاة Googlebot (تجاوز جدران الحماية)
        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s3.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "headers": {"Accept-Language": "en-US,en;q=0.9"},
            **thumb_opts
        }, "3/15: Googlebot Bypass"),

        # 4. HLS/DASH Stream
        ({
            "format": "bv*+ba/b",
            "hls_prefer_native": True,
            "outtmpl": f"downloads/{task_id}_s4.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "allow_unplayable_formats": True,
            **thumb_opts
        }, "4/15: HLS/DASH Stream Extractor"),

        # 5. التجاوز الجغرافي القسري
        ({
            "format": "worstvideo+worstaudio/worst",
            "outtmpl": f"downloads/{task_id}_s5.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True,
            "legacy_server_connect": True,
            **thumb_opts
        }, "5/15: Legacy Network / Geo-Bypass"),

        # 6. Smart TV Agent
        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s6.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebKit/537.36 SamsungBrowser/4.0 Chrome/76.0.3809.146 TV Safari/537.36",
            **thumb_opts
        }, "6/15: Smart TV Agent"),

        # 7. Force Generic Extractor
        ({
            "format": "best/worst",
            "outtmpl": f"downloads/{task_id}_s7.%(ext)s",
            "quiet": True, "force_generic_extractor": True, "nocheckcertificate": True,
            **thumb_opts
        }, "7/15: Direct File / Force Generic"),

        # 8. Fallback Direct MP4
        ({
            "format": "mp4/best",
            "outtmpl": f"downloads/{task_id}_s8.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "prefer_insecure": True,
            **thumb_opts
        }, "8/15: Fallback Direct MP4"),

        # 9. محاكاة أندرويد Chrome (سيرفرات البث العربي)
        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s9.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
            "headers": {"Sec-Fetch-Mode": "navigate"},
            **thumb_opts
        }, "9/15: Android Chrome Agent"),

        # 10. محاكاة Facebook External Hit (لتجاوز حظر الروابط المباشرة)
        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s10.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            **thumb_opts
        }, "10/15: Facebook Scraper Spoof"),

        # 11. تفكيك أجزاء HLS المباشرة بدون دمج سريع
        ({
            "format": "all/best",
            "hls_use_mpegts": True,
            "outtmpl": f"downloads/{task_id}_s11.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            **thumb_opts
        }, "11/15: HLS MPEG-TS Raw Stream"),

        # 12. محاكاة متصفح Firefox macOS مع رؤوس Referer ديناميكية
        ({
            "format": "bestvideo+bestaudio/best",
            "outtmpl": f"downloads/{task_id}_s12.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
            "headers": {"Referer": url, "Origin": url.split("/")[0] + "//" + url.split("/")[2]},
            **thumb_opts
        }, "12/15: macOS Firefox with Dynamic Referer"),

        # 13. خيار التجاوز المنخفض والتنزيل الجزئي القسري (Insecure Protocol)
        ({
            "format": "worst",
            "outtmpl": f"downloads/{task_id}_s13.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "no_check_certificates": True,
            "source_address": "0.0.0.0",
            **thumb_opts
        }, "13/15: Low-Level Insecure Transport"),

        # 14. محاكاة كائنات الأجهزة الذكية (Apple TV)
        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s14.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "AppleTV11,1/11.1",
            **thumb_opts
        }, "14/15: Apple TV Client Spoof"),

        # 15. المحاولة الأخيرة: استخراج أي مسار MP4/WebM/FLV غير معروف برمجياً
        ({
            "format": "b/best",
            "outtmpl": f"downloads/{task_id}_s15.%(ext)s",
            "quiet": True, "force_generic_extractor": True,
            "nocheckcertificate": True, "ignoreerrors": True,
            **thumb_opts
        }, "15/15: Final Deep Generic Extract")
    ]

    for idx, (st_opts, st_name) in enumerate(strategies, 1):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")
        try:
            await update_status_ui(status_msg, f"جاري تجربة المحاولة ({idx}/15)...", 0, 100, time.time(), task_id)
            
            opts = make_ytdl_opts(st_opts, status_msg, loop, task_id, f"تحميل الفيديو ({idx}/15)")
            def run_yt():
                with YoutubeDL(opts) as ytdl:
                    info = ytdl.extract_info(url, download=True)
                    return ytdl.prepare_filename(info), info.get("title", "Video"), info.get("duration", 0)

            file_path, title, duration = await loop.run_in_executor(None, run_yt)
            if file_path and os.path.exists(file_path):
                return file_path, title, duration, st_name
        except ProcessCancelledException:
            raise
        except Exception as e:
            logger.warning(f"فشلت الاستراتيجية {idx}: {e}")
            continue

    return None, None, 0, None

# ----------------------------------------------------
# 📡 معالجة الرسائل
# ----------------------------------------------------

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    CANCELLED_TASKS.add(task_id)
    
    if task_id in ACTIVE_TASKS:
        task = ACTIVE_TASKS[task_id]
        if not task.done():
            task.cancel()

    await callback.answer("🛑 تم إيقاف وإلغاء العملية فوراً!", show_alert=True)
    try:
        await callback.message.edit_text("❌ **تم إلغاء العملية وتم مسح الملفات.**")
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text("👋 أهلاً بك! أرسل رابط الفيديو وستتم معالجته عبر 15 استراتيجية تنزيل متقدمة.")

async def process_download(client: Client, message: Message, task_id: str, url: str):
    status_msg = await message.reply_text(
        "⏳ **جاري بدء عملية الفحص واستخراج الفيديو...**",
        reply_markup=get_cancel_keyboard(task_id)
    )

    file_path = None
    thumb_path = None

    try:
        file_path, title, duration, used_method = await smart_extractor(url, status_msg, task_id)

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
                    await update_status_ui(status_msg, "جاري الرفع إلى تلجرام", current, total, upload_start, task_id)

            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                duration=int(duration) if duration else 0,
                caption=f"🎬 **{title}**\n🛠️ الطريقة الناجحة: `{used_method}`",
                progress=upload_progress
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ **فشلت جميع المحاولات الـ 15 في استخراج الفيديو.**")

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"🛑 تم قطع وإلغاء المهمة بنجاح: {task_id}")
    except Exception as e:
        logger.error(f"خطأ أثناء المعالجة: {e}")
        try:
            await status_msg.edit_text(f"❌ حدث خطأ أثناء المعالجة: `{str(e)[:150]}`")
        except Exception:
            pass
    finally:
        if file_path and os.path.exists(file_path):
            try: os.remove(file_path)
            except Exception: pass
        if thumb_path and os.path.exists(thumb_path):
            try: os.remove(thumb_path)
            except Exception: pass
        
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
    
    logger.info("🚀 جاري تشغيل البوت مع 15 طريقة استخراج متقدمة...")
    app.run()
