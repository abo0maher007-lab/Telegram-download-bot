import os
import re
import time
import random
import logging
import asyncio
import requests
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
import yt_dlp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception,
    before_sleep_log
)
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v12 Engine
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v12")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: لم يتم العثور على API_ID أو API_HASH أو BOT_TOKEN في متغيرات البيئة!")
    exit(1)

app = Client("UniversalDownloaderBot_v12", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()

class ProcessCancelledException(Exception):
    pass

class CircuitBreakerOpenException(Exception):
    pass

# ----------------------------------------------------
# 🧠 المحرك الشامل v12 Core Engine
# ----------------------------------------------------
class UniversalEngineV12:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ]
        self.consecutive_failures = 0
        self.max_circuit_failures = 5
        self.circuit_open_until = 0

    def _get_random_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    @staticmethod
    def _is_retryable_exception(exception: Exception) -> bool:
        if isinstance(exception, requests.exceptions.RequestException):
            response = getattr(exception, 'response', None)
            if response is not None and response.status_code in [400, 401, 403, 404]:
                return False
            return True
        return False

    def download_direct_file(self, url: str, task_id: str, status_msg: Message, loop: asyncio.AbstractEventLoop) -> str:
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{task_id}_file")

        headers = self._get_random_headers()
        with requests.get(url, headers=headers, stream=True, timeout=30) as req:
            req.raise_for_status()
            total_size = int(req.headers.get('content-length', 0))
            
            ext = ".bin"
            cd = req.headers.get('content-disposition')
            if cd and 'filename=' in cd:
                fname = cd.split('filename=')[1].strip('"\'')
                ext = os.path.splitext(fname)[1] or ".bin"

            final_path = out_path + ext
            downloaded = 0
            start_time = time.time()
            last_update = [0]

            with open(final_path, 'wb') as f:
                for chunk in req.iter_content(chunk_size=1024*1024):
                    if task_id in CANCELLED_TASKS:
                        raise ProcessCancelledException("CANCELLED")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and (time.time() - last_update[0] > 2):
                            last_update[0] = time.time()
                            asyncio.run_coroutine_threadsafe(
                                update_progress_ui(status_msg, "تنزيل ملف مباشر", downloaded, total_size, start_time, task_id),
                                loop
                            )
        return final_path

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
                        update_progress_ui(status_msg, "كشط وتنزيل الميديا", downloaded, total, start_time, task_id),
                        loop
                    )

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': out_template,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': random.choice(self.user_agents),
            'progress_hooks': [ytdl_hook],
            'retries': 5
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            final_file_path = f"{base}.mp4" if not filename.endswith('.mp4') else filename

            return {
                "file_path": final_file_path if os.path.exists(final_file_path) else filename,
                "title": info.get('title', 'Media File'),
                "duration": info.get('duration', 0)
            }

engine = UniversalEngineV12()

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
        f"⚡ **[v12 Engine] {action_title}**\n\n"
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
    for f in glob.glob(f"downloads/{task_id}*"):
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
        "🚀 **أهلاً بك في بوت التحميل والكشط الشامل (v12 Engine)**\n\n"
        "أرسل أي رابط (مباشر أو غير مباشر من منصات مثل YouTube, TikTok, Facebook, Drive...) وستتم معالجته فوراً."
    )

async def process_task(client: Client, message: Message, task_id: str, url: str):
    status_msg = await message.reply_text("🔎 **جاري تحليل الرابط بآلية v12 Engine...**")
    loop = asyncio.get_event_loop()
    file_info = None

    try:
        # محاولة التحميل كميديا غير مباشرة أولاً
        try:
            file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, task_id, status_msg, loop)
        except Exception as e:
            logger.info(f"فشلت معالجة الميديا غير المباشرة، جاري التجربة كملف مباشر: {e}")
            # التراجع إلى التحميل المباشر
            file_path = await loop.run_in_executor(None, engine.download_direct_file, url, task_id, status_msg, loop)
            file_info = {"file_path": file_path, "title": "Direct File", "duration": 0}

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        file_path = file_info["file_path"]
        if os.path.exists(file_path):
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
                    caption=f"🎬 **{file_info['title']}**\n🛡️ **Engine:** `v12 Universal`",
                    progress=upload_progress
                )
            else:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=file_path,
                    caption=f"📦 **{file_info['title']}**\n🛡️ **Engine:** `v12 Universal`",
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
# 🚀 دالة تشغيل البوت الرسمية
# ----------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    logger.info("🚀 جاري تشغيل بوت v12 Engine...")
    app.run()