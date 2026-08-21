import os
import re
import time
import base64
import random
import logging
import asyncio
import subprocess
import requests
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v13 Engine
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v13")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORNHUB_COOKIES_BASE64 = os.environ.get("PORNHUB_COOKIES_BASE64")

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: لم يتم العثور على API_ID أو API_HASH أو BOT_TOKEN في متغيرات البيئة!")
    exit(1)

app = Client("UniversalDownloaderBot_v13", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()

class ProcessCancelledException(Exception):
    pass

# ----------------------------------------------------
# 🍪 إدارة الكوكيز المشفّرة (Base64 Cookieman)
# ----------------------------------------------------
COOKIES_FILE_PATH = "ph_cookies.txt"

def setup_cookies():
    """فك تشفير كوكيز Pornhub من متغيرات البيئة بـ Base64 وحفظها مؤقتاً"""
    if PORNHUB_COOKIES_BASE64:
        try:
            decoded_cookies = base64.b64decode(PORNHUB_COOKIES_BASE64).decode('utf-8')
            with open(COOKIES_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(decoded_cookies)
            logger.info("✅ تم تجهيز وفك تشفير كوكيز Pornhub بنجاح.")
            return COOKIES_FILE_PATH
        except Exception as e:
            logger.error(f"❌ فشل فك تشفير PORNHUB_COOKIES_BASE64: {e}")
    return None

COOKIE_PATH = setup_cookies()

# ----------------------------------------------------
# 🖼️ أدوات معالجة الثمبنيل (Thumbnail Generator)
# ----------------------------------------------------
def generate_ffmpeg_thumbnail(video_path: str, task_id: str) -> Optional[str]:
    """توليد صورة مصغرة تلقائية من الفيديو باستخدام FFmpeg في حال عدم وجود غلاف"""
    thumb_path = f"downloads/thumb_{task_id}.jpg"
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", "00:00:02",
            "-i", video_path,
            "-vframes", "1",
            "-vf", "scale=320:-1",
            thumb_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.warning(f"⚠️ تعذر إنتاج thumbnail عبر FFmpeg: {e}")
    return None

def get_video_duration(video_path: str) -> int:
    """استخراج مدة الفيديو بالثواني لضمان عرض الثمبنيل بشكل صحيح على تلجرام"""
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

# ----------------------------------------------------
# 🧠 المحرك الشامل v13 Core Engine
# ----------------------------------------------------
class UniversalEngineV13:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
        ]

    def download_indirect_media(self, url: str, task_id: str, status_msg: Message, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        out_template = os.path.join(out_dir, f"{task_id}_%(title)s.%(ext)s")

        start_time = time.time()
        last_update = [0]

        def ytdl_hook(d):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0 and (time.time() - last_update[0] > 2):
                    last_update[0] = time.time()
                    asyncio.run_coroutine_threadsafe(
                        update_progress_ui(status_msg, "جاري الكشط والتنزيل", downloaded, total, start_time, task_id),
                        loop
                    )

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': out_template,
            'writethumbnail': True,
            'postprocessors': [{'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}],
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': random.choice(self.user_agents),
            'progress_hooks': [ytdl_hook],
            'retries': 10
        }

        # ربط الكوكيز الخاصة بـ Pornhub أو المواقع المقيدة إذا تم إعدادها
        if COOKIE_PATH and os.path.exists(COOKIE_PATH):
            ydl_opts['cookiefile'] = COOKIE_PATH

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            final_file_path = f"{base}.mp4" if not filename.endswith('.mp4') else filename

            # محاولة البحث عن الصورة المصغرة التي تم تنزيلها بواسطة yt-dlp
            thumb_path = None
            for ext in ['.jpg', '.png', '.webp']:
                possible_thumb = f"{base}{ext}"
                if os.path.exists(possible_thumb):
                    thumb_path = possible_thumb
                    break

            return {
                "file_path": final_file_path if os.path.exists(final_file_path) else filename,
                "title": info.get('title', 'Media File'),
                "duration": info.get('duration', 0),
                "thumb_path": thumb_path
            }

engine = UniversalEngineV13()

# ----------------------------------------------------
# 🛠️ أدوات التنسيق والواجهة
# ----------------------------------------------------
def render_progress_bar(percentage: float) -> str:
    filled = int(percentage // 10)
    return "█" * filled + "░" * (10 - filled)

async def update_progress_ui(message: Message, action_title: str, current: int, total: int, start_time: float, task_id: str):
    if task_id in CANCELLED_TASKS:
        raise ProcessCancelledException("CANCELLED")

    diff = time.time() - start_time
    if diff <= 0: return

    percentage = (current / total) * 100 if total > 0 else 0
    speed = current / diff
    eta = round((total - current) / speed) if speed > 0 else 0

    text = (
        f"⚡ **[v13 Engine] {action_title}**\n\n"
        f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
        f"📦 **الحجم:** `{current / (1024*1024):.1f}MB` / `{total / (1024*1024):.1f}MB`\n"
        f"🚀 **السرعة:** `{speed / (1024*1024):.2f} MB/s` | ⏱️ `{eta}s`"
    )
    try:
        await message.edit_text(
            text, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel_{task_id}")]])
        )
    except Exception:
        pass

def cleanup_files(task_id: str):
    import glob
    for f in glob.glob(f"downloads/{task_id}*") + glob.glob(f"downloads/thumb_{task_id}*"):
        try: os.remove(f)
        except Exception: pass

# ----------------------------------------------------
# 📡 الأحداث والمعالجة
# ----------------------------------------------------
@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    CANCELLED_TASKS.add(task_id)
    cleanup_files(task_id)
    await callback.answer("🛑 تم إلغاء العملية!", show_alert=True)
    try:
        await callback.message.edit_text("❌ **تم إلغاء عملية التحميل.**")
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🚀 **أهلاً بك في بوت التحميل والكشط v13 Engine**\n\n"
        "• دعم كامل للصورة المصغرة (Thumbnail Fix)\n"
        "• دعم منصات الوسائط والحسابات المعقدة عبر الكوكيز المشفّرة\n"
        "أرسل رابط الميديا لبدء المعالجة فوراً."
    )

async def process_task(client: Client, message: Message, task_id: str, url: str):
    status_msg = await message.reply_text("🔎 **جاري تحليل الكشط واستخراج الملف بآلية v13...**")
    loop = asyncio.get_event_loop()

    try:
        file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, task_id, status_msg, loop)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        file_path = file_info["file_path"]
        thumb_path = file_info.get("thumb_path")
        duration = file_info.get("duration", 0)

        if os.path.exists(file_path):
            # إصلاح معالجة الصورة المصغرة ومدتها
            if not duration or duration == 0:
                duration = get_video_duration(file_path)

            if not thumb_path or not os.path.exists(thumb_path):
                thumb_path = generate_ffmpeg_thumbnail(file_path, task_id)

            upload_start = time.time()
            last_up = [0]

            async def upload_progress(current, total):
                if time.time() - last_up[0] > 2:
                    last_up[0] = time.time()
                    await update_progress_ui(status_msg, "رفع الملف إلى تلجرام", current, total, upload_start, task_id)

            if file_path.lower().endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov')):
                await client.send_video(
                    chat_id=message.chat.id,
                    video=file_path,
                    thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                    duration=duration,
                    caption=f"🎬 **{file_info['title']}**\n🛡️ **Engine:** `v13 Universal`",
                    progress=upload_progress
                )
            else:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=file_path,
                    thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                    caption=f"📦 **{file_info['title']}**\n🛡️ **Engine:** `v13 Universal`",
                    progress=upload_progress
                )
            await status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Task cancelled: {task_id}")
    except Exception as e:
        logger.error(f"Execution Error: {e}")
        try:
            await status_msg.edit_text(f"❌ **حدث خطأ أثناء معالجة الرابط:**\n`{str(e)[:150]}`")
        except Exception:
            pass
    finally:
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

@app.on_message(filters.text & filters.private & ~filters.forwarded)
async def handle_message(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        return

    task_id = f"{message.from_user.id}_{int(time.time())}"
    task = asyncio.create_task(process_task(client, message, task_id, url))
    ACTIVE_TASKS[task_id] = task

# ----------------------------------------------------
# 🚀 دالة تشغيل البوت
# ----------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    logger.info("🚀 جاري تشغيل بوت v13 Engine...")
    app.run()