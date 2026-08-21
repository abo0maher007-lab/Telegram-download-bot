import os
import re
import time
import base64
import random
import logging
import asyncio
import subprocess
from typing import Optional, Dict, Any
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, RPCError, MessageNotModified
import yt_dlp

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v20.0 AZNude Support
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v20_0")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORNHUB_COOKIES_BASE64 = os.environ.get("PORNHUB_COOKIES_BASE64")
INSTAGRAM_COOKIES_BASE64 = os.environ.get("INSTAGRAM_COOKIES_BASE64")
HTTP_PROXY = os.environ.get("HTTP_PROXY") 

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: لم يتم العثور على API_ID أو API_HASH أو BOT_TOKEN في متغيرات البيئة!")
    exit(1)

app = Client("UniversalDownloaderBot_v20_0", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()
PENDING_URLS = {}
PROGRESS_QUEUES = {}

class ProcessCancelledException(Exception):
    pass

# ----------------------------------------------------
# 🍪 إدارة الكوكيز
# ----------------------------------------------------
PH_COOKIES_PATH = "ph_cookies.txt"
IG_COOKIES_PATH = "ig_cookies.txt"

def setup_cookies(env_var_name: str, file_path: str) -> Optional[str]:
    b64_data = os.environ.get(env_var_name)
    if b64_data:
        try:
            decoded_cookies = base64.b64decode(b64_data).decode('utf-8')
            with open(file_path, "w", encoding="utf-8") as f:
                if "# Netscape HTTP Cookie File" not in decoded_cookies:
                    f.write("# Netscape HTTP Cookie File\n")
                f.write(decoded_cookies)
            logger.info(f"✅ تم تجهيز كوكيز {env_var_name} بنجاح.")
            return file_path
        except Exception as e:
            logger.error(f"❌ فشل فك تشفير {env_var_name}: {e}")
    return None

PH_COOKIE_PATH = setup_cookies("PORNHUB_COOKIES_BASE64", PH_COOKIES_PATH)
IG_COOKIE_PATH = setup_cookies("INSTAGRAM_COOKIES_BASE64", IG_COOKIES_PATH)

# ----------------------------------------------------
# 🖼️ أدوات الثمبنيل والمدة
# ----------------------------------------------------
def generate_ffmpeg_thumbnail(video_path: str, task_id: str) -> Optional[str]:
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

def get_media_duration(file_path: str) -> int:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
        return int(float(output))
    except Exception:
        return 0

# ----------------------------------------------------
# 🧠 المحرك الشامل v20.0 (AZNude Edition)
# ----------------------------------------------------
class UniversalEngineV20:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0"
        ]

    def download_indirect_media(self, url: str, target_option: str, task_id: str, status_msg: Message, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        out_template = os.path.join(out_dir, f"{task_id}_%(title)s.%(ext)s")

        start_time = time.time()

        def ytdl_hook(d):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            if d.get("status") in ["downloading", "finished"]:
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                label = "استخراج الصوت MP3" if target_option == "mp3" else f"جاري التحميل ({target_option}p)"
                
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    loop.call_soon_threadsafe(q.put_nowait, (label, downloaded, total, start_time))

        user_agent = random.choice(self.user_agents)

        if target_option == "mp3":
            format_selector = 'bestaudio/best'
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }]
            is_audio = True
        else:
            is_audio = False
            postprocessors = [{'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}]
            if target_option == "best":
                format_selector = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            else:
                format_selector = f'bestvideo[height<={target_option}][ext=mp4]+bestaudio[ext=m4a]/best[height<={target_option}][ext=mp4]/best[height<={target_option}]/best'

        ydl_opts = {
            'format': format_selector,
            'outtmpl': out_template,
            'writethumbnail': not is_audio,
            'postprocessors': postprocessors,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': user_agent,
            'progress_hooks': [ytdl_hook],
            'retries': 30,
            'fragment_retries': 30,
            'concurrent_fragment_downloads': 5,
            'geo_bypass': True,
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Referer': 'https://www.google.com/',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'cross-site',
            },
            'legacyserverconnect': True,
        }

        # ----------------------------------------------------
        # 🎯 تخصيصات موقع AZNude لمنع التجمد على 0%
        # ----------------------------------------------------
        if "aznude.com" in url or "aznude" in url:
            ydl_opts['http_headers']['Referer'] = 'https://www.aznude.com/'
            ydl_opts['format'] = 'best' # AZNude عادة يستضيف ملفات MP4 مباشرة
            ydl_opts['hls_use_mpegts'] = True
            ydl_opts['check_formats'] = False

        if "tiktok.com" in url:
            ydl_opts['extractor_args'] = {'tiktok': {'app_version': '1.0.0'}}
        
        if "instagram.com" in url and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif "pornhub.com" in url and PH_COOKIE_PATH and os.path.exists(PH_COOKIE_PATH):
            ydl_opts['cookiefile'] = PH_COOKIE_PATH

        if HTTP_PROXY:
            ydl_opts['proxy'] = HTTP_PROXY

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)

            raw_duration = info.get('duration')
            safe_duration = int(float(raw_duration)) if raw_duration is not None else 0

            if is_audio:
                final_file_path = f"{base}.mp3"
                thumb_path = None
            else:
                final_file_path = f"{base}.mp4" if not filename.endswith('.mp4') else filename
                thumb_path = None
                for ext in ['.jpg', '.png', '.webp']:
                    possible_thumb = f"{base}{ext}"
                    if os.path.exists(possible_thumb):
                        thumb_path = possible_thumb
                        break

            return {
                "file_path": final_file_path if os.path.exists(final_file_path) else filename,
                "title": str(info.get('title', 'Media File')),
                "duration": safe_duration,
                "thumb_path": thumb_path,
                "is_audio": is_audio
            }

engine = UniversalEngineV20()

# ----------------------------------------------------
# 🛠️ مدير تحديثات الواجهة المرن (Adaptive Queue Worker)
# ----------------------------------------------------
def render_progress_bar(percentage: float) -> str:
    filled = int(percentage // 10)
    return "█" * filled + "░" * (10 - filled)

async def progress_ui_worker(task_id: str, message: Message):
    q = PROGRESS_QUEUES.get(task_id)
    if not q:
        return

    last_update_time = 0
    last_text = ""

    while task_id not in CANCELLED_TASKS:
        try:
            data = await asyncio.wait_for(q.get(), timeout=1.0)
            action_title, current, total, start_time = data
            
            now = time.time()
            if now - last_update_time >= 3.0:
                diff = now - start_time
                if diff <= 0:
                    continue

                speed = current / diff
                
                # التعامل مع المواقع التي لا تعطي إجمالي الحجم (مثل AZNude في بعض السيرفرات)
                if total > 0:
                    percentage = (current / total) * 100
                    eta = round((total - current) / speed) if speed > 0 else 0
                    total_str = f"`{total / (1024*1024):.1f}MB`"
                    bar = f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
                    eta_str = f"| ⏱️ `{eta}s`"
                else:
                    percentage = 0
                    total_str = "غير معروف"
                    bar = "🔄 `جاري تدفق البيانات (Stream)...`\n"
                    eta_str = ""

                text = (
                    f"⚡ **[v20.0 AZNude Engine]**\n"
                    f"📌 **العملية:** {action_title}\n\n"
                    f"{bar}"
                    f"📦 **الحجم:** `{current / (1024*1024):.1f}MB` / {total_str}\n"
                    f"🚀 **السرعة:** `{speed / (1024*1024):.2f} MB/s` {eta_str}"
                )

                if text != last_text:
                    try:
                        await message.edit_text(
                            text,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"cncl_{task_id}")]])
                        )
                        last_text = text
                        last_update_time = now
                    except MessageNotModified:
                        pass
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except RPCError:
                        pass

        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"UI Worker Exception: {e}")
            break

def cleanup_files(task_id: str):
    import glob
    for f in glob.glob(f"downloads/{task_id}*") + glob.glob(f"downloads/thumb_{task_id}*"):
        try: os.remove(f)
        except Exception: pass
    PROGRESS_QUEUES.pop(task_id, None)

# ----------------------------------------------------
# 📡 الأحداث والتفاعل
# ----------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🚀 **أهلاً بك في بوت v20.0 Engine (AZNude Edition)**\n\n"
        "• حل مشكلة تجمد التحميل على 0% لموقع AZNude والمواقع غير المستقرة.\n"
        "• دعم البث المباشر وقراءة الأحجام المجهولة.\n"
        "• دعم منصات (AZNude, Instagram, TikTok, YouTube...)\n\n"
        "أرسل رابط الميديا الآن للبدء."
    )

@app.on_message(filters.private & filters.text)
async def handle_message(client: Client, message: Message):
    text = message.text.strip()
    match = re.search(r'(https?://[^\s]+)', text)
    if not match:
        return

    url = match.group(1)
    req_id = f"{message.from_user.id}_{int(time.time())}"
    PENDING_URLS[req_id] = url

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 1080p", callback_data=f"q_1080_{req_id}"),
            InlineKeyboardButton("🎬 720p", callback_data=f"q_720_{req_id}"),
        ],
        [
            InlineKeyboardButton("🎬 480p", callback_data=f"q_480_{req_id}"),
            InlineKeyboardButton("🎬 360p", callback_data=f"q_360_{req_id}"),
        ],
        [
            InlineKeyboardButton("✨ أفضل جودة (Auto)", callback_data=f"q_best_{req_id}")
        ],
        [
            InlineKeyboardButton("🎵 تحميل صوت MP3 (320kbps)", callback_data=f"q_mp3_{req_id}")
        ]
    ])

    await message.reply_text(
        "🌐 **تم التعرّف على الرابط!**\nاختر جودة الفيديو أو صيغة التحميل للبدء:",
        reply_markup=keyboard,
        quote=True
    )

@app.on_callback_query(filters.regex(r"^q_"))
async def option_callback_handler(client: Client, callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        option = parts[1]
        req_id = f"{parts[2]}_{parts[3]}"

        url = PENDING_URLS.pop(req_id, None)
        if not url:
            await callback.answer("⚠️ انتهت صلاحية هذا الطلب، يرجى إعادة إرسال الرابط.", show_alert=True)
            return

        msg_text = "🎵 **جاري استخراج الصوت MP3...**" if option == "mp3" else f"🔎 **جاري الاتصال بالسيرفر وتجهيز التنزيل ({option}p)...**"
        await callback.answer()
        
        status_msg = await callback.message.edit_text(msg_text)
        
        task_id = req_id
        PROGRESS_QUEUES[task_id] = asyncio.Queue()
        
        task = asyncio.get_event_loop().create_task(process_task(client, task_id, url, option, status_msg))
        ACTIVE_TASKS[task_id] = task
    except Exception as e:
        logger.error(f"Callback Error: {e}")

@app.on_callback_query(filters.regex(r"^cncl_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    try:
        task_id = callback.data.replace("cncl_", "")
        CANCELLED_TASKS.add(task_id)
        cleanup_files(task_id)
        await callback.answer("🛑 تم إلغاء العملية!", show_alert=True)
        await callback.message.edit_text("❌ **تم إلغاء عملية التحميل.**")
    except Exception:
        pass

async def process_task(client: Client, task_id: str, url: str, option: str, init_status_msg: Message):
    loop = asyncio.get_event_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, init_status_msg))

    try:
        file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, option, task_id, init_status_msg, loop)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        file_path = file_info["file_path"]
        thumb_path = file_info.get("thumb_path")
        duration = int(file_info.get("duration", 0))
        is_audio = file_info.get("is_audio", False)

        if os.path.exists(file_path):
            if duration <= 0:
                duration = get_media_duration(file_path)

            upload_start = time.time()

            def upload_progress(current, total):
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    label = "رفع الملف الصوتي MP3" if is_audio else f"رفع الفيديو ({option}p)"
                    loop.call_soon_threadsafe(q.put_nowait, (label, current, total, upload_start))

            if is_audio:
                await client.send_audio(
                    chat_id=init_status_msg.chat.id,
                    audio=file_path,
                    duration=int(duration),
                    title=str(file_info['title']),
                    caption=f"🎵 **{file_info['title']}**\n🎼 **الصيغة:** `MP3 320kbps`\n🛡️ **Engine:** `v20.0 AZNude Edition`",
                    progress=upload_progress
                )
            else:
                if not thumb_path or not os.path.exists(thumb_path):
                    thumb_path = generate_ffmpeg_thumbnail(file_path, task_id)

                await client.send_video(
                    chat_id=init_status_msg.chat.id,
                    video=file_path,
                    thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                    duration=int(duration),
                    caption=f"🎬 **{file_info['title']}**\n📊 **الجودة:** `{option}p`\n🛡️ **Engine:** `v20.0 AZNude Edition`",
                    progress=upload_progress
                )
            await init_status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Task cancelled: {task_id}")
    except Exception as e:
        logger.error(f"Execution Error: {e}")
        try:
            await init_status_msg.edit_text(f"❌ **حدث خطأ أثناء معالجة الرابط:**\n`{str(e)[:150]}`")
        except Exception:
            pass
    finally:
        worker_task.cancel()
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    logger.info("🚀 جاري تشغيل بوت v20.0 AZNude Engine...")
    app.run()