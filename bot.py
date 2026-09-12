import os
import re
import time
import base64
import random
import logging
import asyncio
import shutil
import subprocess
import urllib.request
import urllib.error
import math
import glob
import threading
from urllib.parse import urlparse, quote
from typing import Optional, Dict, Any, List, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto
from pyrogram.errors import FloodWait, RPCError, MessageNotModified
import yt_dlp

# استيراد مترجم النصوص للترجمة إلى العربية
try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False

# استيراد curl_cffi لتجاوز حظر Cloudflare TLS Fingerprint
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v63 Engine (Anti-429 & Anti-Cloudflare 403 Resolved)
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v63")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORNHUB_COOKIES_BASE64 = os.environ.get("PORNHUB_COOKIES_BASE64")
INSTAGRAM_COOKIES_BASE64 = os.environ.get("INSTAGRAM_COOKIES_BASE64")
TWITTER_COOKIES_BASE64 = os.environ.get("TWITTER_COOKIES_BASE64") or os.environ.get("X_COOKIES_BASE64")
DAILYMOTION_COOKIES_BASE64 = os.environ.get("DAILYMOTION_COOKIES_BASE64")
HTTP_PROXY = os.environ.get("HTTP_PROXY") 

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: لم يتم العثور على API_ID أو API_HASH أو BOT_TOKEN في متغيرات البيئة!")
    exit(1)

app = Client("UniversalDownloaderBot_v63", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()
ACTIVE_CANCEL_EVENTS = {}
PENDING_URLS = {}
PENDING_COMPRESS: Dict[str, Message] = {}
AWAITING_TRIM_INPUT = {}
PROGRESS_QUEUES = {}

USER_CONFIGS: Dict[int, Dict[str, bool]] = {}

MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2 GB limit for standard Telegram upload

USER_AGENTS_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'
]

def get_random_headers() -> dict:
    ua = random.choice(USER_AGENTS_POOL)
    return {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
    }

class ProcessCancelledException(Exception):
    pass

# ----------------------------------------------------
# ⚙️ إدارة إعدادات التفضيلات للمستخدمين
# ----------------------------------------------------
def get_user_settings(chat_id: int) -> Dict[str, bool]:
    if chat_id not in USER_CONFIGS:
        USER_CONFIGS[chat_id] = {
            "snapshots_direct": False,
            "snapshots_dailymotion": False,
            "snapshots_social": False
        }
    return USER_CONFIGS[chat_id]

def build_settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    cfg = get_user_settings(chat_id)
    btn_direct = "✅ الروابط المباشرة" if cfg["snapshots_direct"] else "❌ الروابط المباشرة"
    btn_dm = "✅ ديليموشن" if cfg["snapshots_dailymotion"] else "❌ ديليموشن"
    btn_social = "✅ منصات التواصل" if cfg["snapshots_social"] else "❌ منصات التواصل"
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"لقطات: {btn_direct}", callback_data="cfg_toggle_direct")],
        [InlineKeyboardButton(f"لقطات: {btn_dm}", callback_data="cfg_toggle_dm")],
        [InlineKeyboardButton(f"لقطات: {btn_social}", callback_data="cfg_toggle_social")],
        [InlineKeyboardButton("إغلاق الإعدادات ✖️", callback_data="cfg_close")]
    ])
    return keyboard

# ----------------------------------------------------
# 🧹 أدوات إدارة وتنظيف القرص والذاكرة
# ----------------------------------------------------
def get_dir_size(path: str = "downloads") -> float:
    total_size = 0
    if os.path.exists(path):
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    return total_size / (1024 * 1024)

def get_disk_info() -> Dict[str, float]:
    total, used, free = shutil.disk_usage(".")
    return {
        "total_gb": total / (1024**3),
        "used_gb": used / (1024**3),
        "free_gb": free / (1024**3),
        "downloads_mb": get_dir_size("downloads")
    }

def purge_downloads_folder() -> int:
    deleted_count = 0
    if os.path.exists("downloads"):
        for filename in os.listdir("downloads"):
            file_path = os.path.join("downloads", filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                    deleted_count += 1
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                    deleted_count += 1
            except Exception as e:
                logger.error(f"⚠️ فشل حذف {file_path}: {e}")
    return deleted_count

def auto_disk_guard():
    info = get_disk_info()
    if info["free_gb"] < 0.5 or info["downloads_mb"] > 2000:
        logger.warning("🧹 تفعيل الحارس التلقائي: مساحة القرص منخفضة، جاري تنظيف الملفات المؤقتة...")
        purge_downloads_folder()

def force_release_memory():
    import gc
    gc.collect()

# ----------------------------------------------------
# 🍪 إدارة الكوكيز
# ----------------------------------------------------
PH_COOKIES_PATH = "ph_cookies.txt"
IG_COOKIES_PATH = "ig_cookies.txt"
TW_COOKIES_PATH = "tw_cookies.txt"
DM_COOKIES_PATH = "dm_cookies.txt"

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
TW_COOKIE_PATH = setup_cookies("TWITTER_COOKIES_BASE64", TW_COOKIES_PATH) or setup_cookies("X_COOKIES_BASE64", TW_COOKIES_PATH)
DM_COOKIE_PATH = setup_cookies("DAILYMOTION_COOKIES_BASE64", DM_COOKIES_PATH)

# ----------------------------------------------------
# 🖼️ أدوات الثمبنيل والمدة وأبعاد الفيديو والترجمة والضغط v63
# ----------------------------------------------------
def format_size(bytes_val: float) -> str:
    if not bytes_val: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024: return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"

def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def get_video_dimensions(file_path: str) -> Tuple[int, int]:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            file_path
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
        if "x" in output:
            w, h = output.split("x")
            return int(w), int(h)
    except Exception as e:
        logger.warning(f"⚠️ تعذر استخراج أبعاد الفيديو: {e}")
    return 0, 0

def sanitize_thumb(thumb_path: Optional[str]) -> Optional[str]:
    if not thumb_path or not isinstance(thumb_path, str) or not os.path.exists(thumb_path):
        return None
    try:
        if os.path.getsize(thumb_path) == 0:
            return None
        ext = os.path.splitext(thumb_path)[1].lower()
        if ext in ['.webp', '.png', '.bmp']:
            jpg_thumb = f"{os.path.splitext(thumb_path)[0]}_conv.jpg"
            cmd = ["ffmpeg", "-y", "-i", thumb_path, "-vframes", "1", jpg_thumb]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if os.path.exists(jpg_thumb) and os.path.getsize(jpg_thumb) > 0:
                return jpg_thumb
            return None
        return thumb_path
    except Exception:
        return None

def generate_ffmpeg_thumbnail(video_path: str, task_id: str, suffix: str = "") -> Optional[str]:
    thumb_path = f"downloads/thumb_{task_id}{suffix}.jpg"
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
        return sanitize_thumb(thumb_path)
    except Exception as e:
        logger.warning(f"⚠️ تعذر إنتاج thumbnail عبر FFmpeg: {e}")
    return None

def get_valid_thumbnail(video_path: str, task_id: str, existing_thumb: Optional[str] = None, suffix: str = "") -> Optional[str]:
    clean_existing = sanitize_thumb(existing_thumb)
    if clean_existing:
        return clean_existing
    return generate_ffmpeg_thumbnail(video_path, task_id, suffix)

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

async def get_video_metadata_and_thumb(file_path: str) -> Tuple[int, int, int, Optional[str]]:
    loop = asyncio.get_running_loop()
    duration = await loop.run_in_executor(None, get_media_duration, file_path)
    w, h = await loop.run_in_executor(None, get_video_dimensions, file_path)
    thumb = await loop.run_in_executor(None, generate_ffmpeg_thumbnail, file_path, f"meta_{int(time.time())}")
    return duration, w, h, thumb

async def convert_to_mp4(file_path: str) -> str:
    if file_path.lower().endswith(".mp4"):
        return file_path
    base, _ = os.path.splitext(file_path)
    out_mp4 = f"{base}_conv.mp4"
    cmd = ["ffmpeg", "-y", "-i", file_path, "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", out_mp4]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
            os.remove(file_path)
            return out_mp4
    except Exception: pass
    return file_path

# ----------------------------------------------------
# 🗜️ محرك ضغط الفيديو FFmpeg v63
# ----------------------------------------------------
def compress_video_ffmpeg(input_path: str, target_quality: str, output_path: str, task_id: Optional[str] = None, loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
    try:
        if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
            logger.error("❌ ملف الإدخال غير موجود أو فارغ.")
            return False

        total_duration = get_media_duration(input_path)

        if target_quality == "1080":
            max_height = 1080
            crf = "24"
            preset = "fast"
            audio_bitrate = "128k"
        elif target_quality == "720":
            max_height = 720
            crf = "26"
            preset = "fast"
            audio_bitrate = "128k"
        elif target_quality == "480":
            max_height = 480
            crf = "28"
            preset = "faster"
            audio_bitrate = "96k"
        elif target_quality == "360":
            max_height = 360
            crf = "30"
            preset = "faster"
            audio_bitrate = "64k"
        else:  # auto
            max_height = 720
            crf = "27"
            preset = "fast"
            audio_bitrate = "128k"

        vf_scale = f"scale=-2:'min({max_height},ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"

        cmd = [
            "ffmpeg", "-y",
            "-progress", "pipe:1",
            "-nostats",
            "-i", input_path,
            "-vf", vf_scale,
            "-c:v", "libx264",
            "-crf", crf,
            "-preset", preset,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-ac", "2",
            "-movflags", "+faststart",
            output_path
        ]

        logger.info(f"⚙️ جاري تنفيذ أمر الضغط بـ FFmpeg v63: {' '.join(cmd)}")
        
        start_time = time.time()
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)

        q = PROGRESS_QUEUES.get(task_id) if task_id else None

        out_time_sec = 0.0
        if process.stdout:
            for line in process.stdout:
                if task_id and task_id in CANCELLED_TASKS:
                    process.kill()
                    return False
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        val = int(line.split("=")[1])
                        out_time_sec = val / 1_000_000.0
                    except Exception:
                        pass
                elif line.startswith("out_time="):
                    try:
                        time_str = line.split("=")[1]
                        parts = time_str.split(":")
                        if len(parts) == 3:
                            out_time_sec = float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                    except Exception:
                        pass

                if line.startswith("progress="):
                    if q and loop and total_duration > 0:
                        label = f"ضغط الفيديو بـ FFmpeg إلى ({target_quality}p)"
                        loop.call_soon_threadsafe(q.put_nowait, (label, out_time_sec, total_duration, start_time, "time"))

        process.wait()
        return process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"❌ استثناء غير متوقع أثناء ضغط الفيديو بـ FFmpeg: {e}")
        return False

# ----------------------------------------------------
# 🌐 محرك الترجمة العربية النصية
# ----------------------------------------------------
def is_arabic_text(text: str) -> bool:
    arabic_chars = re.findall(r'[\u0600-\u06FF]', text)
    return len(arabic_chars) > (len(text) * 0.2)

async def translate_to_arabic(text: str) -> str:
    if not text or not text.strip():
        return ""
    if is_arabic_text(text):
        return ""
    
    clean_text = text.strip()
    if len(clean_text) > 800:
        clean_text = clean_text[:800] + "..."

    def _do_translate():
        if HAS_TRANSLATOR:
            try:
                return GoogleTranslator(source='auto', target='ar').translate(clean_text)
            except Exception as e:
                logger.error(f"Deep Translator Error: {e}")
        try:
            ua = random.choice(USER_AGENTS_POOL)
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ar&dt=t&q={quote(clean_text)}"
            req = urllib.request.Request(url, headers={'User-Agent': ua})
            res = urllib.request.urlopen(req, timeout=5).read().decode('utf-8')
            import json
            data = json.loads(res)
            translated = "".join([item[0] for item in data[0] if item[0]])
            return translated
        except Exception as ex:
            logger.error(f"Fallback translate error: {ex}")
            return ""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _do_translate)

def determine_url_type(url: str) -> str:
    url_lower = url.lower()
    if is_dailymotion_url(url):
        return "dailymotion"
    social_domains = ["instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com", "fb.watch", "fb.gg", "youtube.com", "youtu.be"]
    if any(domain in url_lower for domain in social_domains):
        return "social"
    return "direct"

async def extract_9_frames(file_path: str, duration: int, chat_id: int) -> List[str]:
    frames = []
    if duration <= 0 or not os.path.exists(file_path): return frames
    step = duration / 10
    for i in range(1, 10):
        t = step * i
        out_f = f"downloads/frame_{chat_id}_{i}_{int(time.time())}.jpg"
        cmd = ["ffmpeg", "-y", "-ss", str(t), "-i", file_path, "-vframes", "1", "-q:v", "2", out_f]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if os.path.exists(out_f) and os.path.getsize(out_f) > 0:
                frames.append(out_f)
        except Exception: pass
    return frames

def trim_video_ffmpeg(input_path: str, start_str: str, end_str: str, output_path: str) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", start_str,
            "-to", end_str,
            "-i", input_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"FFmpeg trim error: {e}")
        return False

def split_video_file(file_path: str, task_id: str, target_size_bytes: int = 1900 * 1024 * 1024) -> List[str]:
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return []
    file_size = os.path.getsize(file_path)
    if file_size <= target_size_bytes:
        return [file_path]

    total_duration = get_media_duration(file_path)
    if total_duration <= 0:
        return [file_path]

    num_parts = math.ceil(file_size / target_size_bytes)
    segment_duration = total_duration / num_parts
    parts = []

    out_dir = "downloads"
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    for i in range(num_parts):
        start_sec = i * segment_duration
        part_out = os.path.join(out_dir, f"{base_name}_part{i+1}.mp4")
        
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-i", file_path,
            "-t", str(segment_duration),
            "-c:v", "copy",
            "-c:a", "copy",
            "-movflags", "+faststart",
            part_out
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if os.path.exists(part_out) and os.path.getsize(part_out) > 0:
                parts.append(part_out)
        except Exception as e:
            logger.error(f"فشل تقسيم الفيديو عند الجزء {i+1}: {e}")

    return [p for p in parts if os.path.exists(p) and os.path.getsize(p) > 0] if parts else [file_path]

# ----------------------------------------------------
# 📌 تنزيل ورفع فيديوهات Dailymotion
# ----------------------------------------------------
def is_dailymotion_url(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(x in domain for x in ['dailymotion.com', 'dai.ly']) or '/video/' in url.lower()

async def download_dailymotion_video(event, url, quality_choice, status_msg):
    chat_id = event.chat.id if hasattr(event, 'chat') else event.chat_id
    task_id = f"dm_{int(time.time() * 1000)}"
    cancel_event = threading.Event()
    ACTIVE_CANCEL_EVENTS[task_id] = cancel_event

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cncl_{task_id}")]])
    
    label_quality = f"جودة {quality_choice}p" if quality_choice in ['1080', '720', '480', '320'] else ("صوت MP3" if quality_choice == 'mp3' else "أفضل جودة")
    
    if not status_msg:
        status_msg = await app.send_message(chat_id, f"⏳ **جاري بدء عملية التحميل ({label_quality}) من Dailymotion...**", reply_markup=cancel_btn)
    else:
        await status_msg.edit_text(f"⏳ **جاري بدء عملية التحميل ({label_quality}) من Dailymotion...**", reply_markup=cancel_btn)

    loop = asyncio.get_running_loop()
    last_update_time = [0]

    async def safe_edit_text(text: str, reply_markup):
        try:
            await status_msg.edit_text(text, reply_markup=reply_markup)
        except (MessageNotModified, FloodWait, RPCError):
            pass

    def progress_hook(d):
        if cancel_event.is_set():
            raise Exception("CANCELLED")

        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update_time[0] >= 2.0:
                last_update_time[0] = now
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0) or 0
                eta = d.get('eta', 0) or 0
                
                percentage = (downloaded / total * 100) if total > 0 else 0
                completed = int(percentage // 10)
                bar = "█" * completed + "░" * (10 - completed)
                
                text = (
                    f"📥 **جاري تنزيل الفيديو من Dailymotion ({label_quality})...**\n\n"
                    f"[{bar}] {percentage:.1f}%\n"
                    f"🚀 **السرعة:** {format_size(speed)}/s\n"
                    f"📦 **الحجم:** {format_size(downloaded)} / {format_size(total)}\n"
                    f"⏱️ **المتبقي:** {format_time(eta)}"
                )
                
                coro = safe_edit_text(text, cancel_btn)
                asyncio.run_coroutine_threadsafe(coro, loop)

    task_dir = os.path.join("downloads", task_id)
    os.makedirs(task_dir, exist_ok=True)

    if quality_choice == 'mp3':
        format_str = 'bestaudio/best'
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }]
        is_audio_mode = True
    else:
        is_audio_mode = False
        postprocessors = [{'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}]
        if quality_choice in ['1080', '720', '480', '320']:
            format_str = f"bestvideo[height<={quality_choice}]+bestaudio/best[height<={quality_choice}]/best"
        else:
            format_str = "best"

    headers = get_random_headers()
    headers.update({
        'Referer': 'https://www.dailymotion.com/',
        'Origin': 'https://www.dailymotion.com',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'cross-site'
    })

    ydl_opts = {
        'format': format_str,
        'outtmpl': os.path.join(task_dir, '%(title).40s_%(id)s.%(ext)s'),
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'headers': headers,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'impersonate': 'chrome',
        'merge_output_format': 'mp4' if not is_audio_mode else None,
        'postprocessors': postprocessors,
        'postprocessor_args': {
            'ffmpeg': ['-movflags', '+faststart']
        },
        'retries': 30,
        'fragment_retries': 30,
        'sleep_interval': 3,
        'max_sleep_interval': 6,
        'sleep_interval_requests': 2,
        'skip_unavailable_fragments': True,
        'extractor_args': {
            'generic': {
                'impersonate': ['chrome']
            },
            'dailymotion': {
                'app_id': 'dmfed',
                'geo_verification_network': 'http'
            }
        }
    }

    if DM_COOKIE_PATH and os.path.exists(DM_COOKIE_PATH):
        ydl_opts['cookiefile'] = DM_COOKIE_PATH

    if HTTP_PROXY:
        ydl_opts['proxy'] = HTTP_PROXY

    try:
        if cancel_event.is_set():
            raise Exception("CANCELLED")

        post_caption_text = ""
        
        def run_dl():
            nonlocal post_caption_text
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        try:
                            info = ydl.extract_info(url, download=True)
                        except Exception:
                            ydl_opts['extractor_args'] = {'generic': {'impersonate': ['chrome']}}
                            ydl_opts['format'] = 'best' if not is_audio_mode else 'bestaudio/best'
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl_fallback:
                                info = ydl_fallback.extract_info(url, download=True)

                        if info:
                            if 'entries' in info and len(info['entries']) > 0:
                                info = info['entries'][0]
                            post_caption_text = info.get('description') or info.get('title') or ""
                        return ydl.prepare_filename(info)
                except Exception as err:
                    err_str = str(err)
                    if ("429" in err_str or "403" in err_str) and attempt < max_retries - 1:
                        sleep_time = (2 ** attempt) + random.uniform(1.0, 3.0)
                        logger.warning(f"⚠️ Dailymotion retry {attempt+1} due to HTTP 429/403. Sleeping {sleep_time:.1f}s...")
                        time.sleep(sleep_time)
                        ydl_opts['headers'] = get_random_headers()
                        continue
                    raise err

        downloaded_file = await loop.run_in_executor(None, run_dl)

        if cancel_event.is_set():
            raise Exception("CANCELLED")

        base_file, _ = os.path.splitext(downloaded_file)
        
        if is_audio_mode:
            file_path = f"{base_file}.mp3" if os.path.exists(f"{base_file}.mp3") else downloaded_file
        else:
            file_path = await convert_to_mp4(downloaded_file)

        parts = await loop.run_in_executor(None, split_video_file, file_path, task_id)

        await status_msg.edit_text("📤 **اكتمل التنزيل بنجاح! جاري رفع الملفات إلى تلجرام...**", reply_markup=cancel_btn)

        translated_arabic = await translate_to_arabic(post_caption_text)

        for idx, part_file in enumerate(parts):
            if not os.path.exists(part_file) or os.path.getsize(part_file) == 0:
                continue

            start_time = time.time()
            last_upload_update = [0]

            async def upload_progress_callback(current, total):
                if cancel_event.is_set():
                    raise Exception("CANCELLED")
                now = time.time()
                if now - last_upload_update[0] >= 2.0 or current == total:
                    last_upload_update[0] = now
                    elapsed = now - start_time
                    speed = current / elapsed if elapsed > 0 else 0
                    percentage = (current / total * 100) if total > 0 else 0
                    completed = int(percentage // 10)
                    bar = "█" * completed + "░" * (10 - completed)
                    eta = (total - current) / speed if speed > 0 else 0

                    part_label = f" (الجزء {idx+1}/{len(parts)})" if len(parts) > 1 else ""
                    text = (
                        f"📤 **جاري رفع Dailymotion{part_label}...**\n\n"
                        f"[{bar}] {percentage:.1f}%\n"
                        f"🚀 **السرعة:** {format_size(speed)}/s\n"
                        f"📦 **الحجم:** {format_size(current)} / {format_size(total)}\n"
                        f"⏱️ **المتبقي:** {format_time(eta)}"
                    )
                    try:
                        await status_msg.edit_text(text, reply_markup=cancel_btn)
                    except Exception: pass

            duration, width, height, raw_thumb = await get_video_metadata_and_thumb(part_file)
            thumb_path = sanitize_thumb(raw_thumb)

            caption_out = f"🎬 **Dailymotion Media [{quality_choice}]**"
            if len(parts) > 1:
                caption_out += f"\n📦 **الجزء ({idx+1}/{len(parts)})**"

            if post_caption_text and idx == 0:
                clean_desc = post_caption_text.strip()
                if len(clean_desc) > 400: clean_desc = clean_desc[:400] + "..."
                caption_out += f"\n\n📝 **النص الأصلي المنشور:**\n{clean_desc}"
                if translated_arabic:
                    caption_out += f"\n\n🇦🇪 **الترجمة العربية:**\n{translated_arabic}"

            if is_audio_mode:
                await app.send_audio(
                    chat_id=chat_id,
                    audio=part_file,
                    caption=caption_out,
                    duration=duration if duration > 0 else None,
                    title=os.path.basename(part_file),
                    progress=upload_progress_callback
                )
            else:
                video_args = {
                    "chat_id": chat_id,
                    "video": part_file,
                    "caption": caption_out,
                    "width": width if width > 0 else None,
                    "height": height if height > 0 else None,
                    "duration": int(duration) if duration > 0 else None,
                    "supports_streaming": True,
                    "progress": upload_progress_callback
                }
                if thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                    video_args["thumb"] = thumb_path

                await app.send_video(**video_args)

            if thumb_path and os.path.exists(thumb_path):
                try: os.remove(thumb_path)
                except Exception: pass

        user_cfg = get_user_settings(chat_id)
        if not is_audio_mode and user_cfg["snapshots_dailymotion"]:
            total_duration = get_media_duration(file_path)
            if total_duration > 0:
                await status_msg.edit_text("📸 **جاري التقاط 9 صور من الفيديو...**")
                frames = await extract_9_frames(file_path, total_duration, chat_id=chat_id)
                valid_frames = [fr for fr in frames if sanitize_thumb(fr)]
                if valid_frames:
                    media_group = [InputMediaPhoto(media=fr) for fr in valid_frames]
                    await app.send_media_group(chat_id, media_group)
                    for fr in frames:
                        try: os.remove(fr)
                        except Exception: pass

        await status_msg.delete()

    except Exception as e:
        if str(e) == "CANCELLED":
            await status_msg.edit_text("🛑 **تم إلغاء العملية بناءً على طلبك.**", reply_markup=None)
        elif "429" in str(e):
            await status_msg.edit_text("⚠️ **تنبيه (HTTP Error 429):** قامت المنصة بتحديد عدد الطلبات مؤقتاً. تم التراجع بأمان، جرب مجدداً بعد دقيقة.", reply_markup=None)
        else:
            logger.error(f"Dailymotion Error: {e}")
            await status_msg.edit_text(f"❌ **حدث خطأ أثناء تحميل Dailymotion:**\n`{str(e)[:200]}`", reply_markup=None)

    finally:
        ACTIVE_CANCEL_EVENTS.pop(task_id, None)
        if os.path.exists(task_dir):
            try: shutil.rmtree(task_dir)
            except Exception: pass
        force_release_memory()

# ----------------------------------------------------
# 🌐 محرك MediaFire & Mega Direct Downloader
# ----------------------------------------------------
def inspect_mediafire_link(url: str) -> Dict[str, Any]:
    ua = random.choice(USER_AGENTS_POOL)
    req = urllib.request.Request(url, headers={'User-Agent': ua})
    html = urllib.request.urlopen(req).read().decode('utf-8')
    
    download_url_match = re.search(r'href="((?:https?://download\d+\.mediafire\.com/[^"]+))"', html)
    if not download_url_match:
        raise Exception("فشل في استخراج رابط التنزيل المباشر من MediaFire")
    
    direct_url = download_url_match.group(1)
    file_name = direct_url.split('/')[-1]
    
    video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v', '.3gp']
    ext = os.path.splitext(file_name)[1].lower()
    is_video = ext in video_exts
    
    return {
        "direct_url": direct_url,
        "file_name": file_name,
        "is_video": is_video,
        "ext": ext
    }

def download_mediafire_file(url: str, target_option: str, task_id: str, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
    info = inspect_mediafire_link(url)
    direct_url = info["direct_url"]
    file_name = info["file_name"]
    
    out_dir = "downloads"
    os.makedirs(out_dir, exist_ok=True)
    file_path = os.path.join(out_dir, f"{task_id}_{file_name[:50]}")

    if task_id in CANCELLED_TASKS:
        raise ProcessCancelledException("CANCELLED")

    start_time = time.time()
    
    def download_hook(blocknum, blocksize, totalsize):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")
        downloaded = blocknum * blocksize
        q = PROGRESS_QUEUES.get(task_id)
        if q:
            label = "تحميل الفيديو من MediaFire" if target_option == "vid" else "تحميل المستند من MediaFire"
            loop.call_soon_threadsafe(q.put_nowait, (label, downloaded, totalsize, start_time, "bytes"))

    ua = random.choice(USER_AGENTS_POOL)
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', ua)]
    urllib.request.install_opener(opener)

    urllib.request.urlretrieve(direct_url, file_path, reporthook=download_hook)
    
    is_doc = (target_option == "doc") or (not info["is_video"])
    
    return {
        "file_path": file_path,
        "title": file_name,
        "duration": 0,
        "thumb_path": None,
        "description": "",
        "is_audio": False,
        "is_document": is_doc
    }

def download_mega_file(url: str, task_id: str) -> Dict[str, Any]:
    try:
        from mega import Mega
    except ImportError:
        raise Exception("يرجى تثبيت مكتبة mega.py عبر الأمر: pip install mega.py")
    
    mega = Mega()
    m = mega.login()
    
    out_dir = "downloads"
    os.makedirs(out_dir, exist_ok=True)
    
    if task_id in CANCELLED_TASKS:
        raise ProcessCancelledException("CANCELLED")

    downloaded_path = m.download_url(url, dest_path=out_dir)
    filename = os.path.basename(downloaded_path)
    new_path = os.path.join(out_dir, f"{task_id}_{filename[:50]}")
    os.rename(downloaded_path, new_path)

    return {
        "file_path": new_path,
        "title": filename,
        "duration": 0,
        "thumb_path": None,
        "description": "",
        "is_audio": False,
        "is_document": True
    }

# ----------------------------------------------------
# 🧠 المحرك الشامل v63 Engine (مع تفادي الحظر Anti-429 & Anti-Cloudflare 403)
# ----------------------------------------------------
class UniversalEngineV63:
    def __init__(self):
        self.user_agents = USER_AGENTS_POOL

    def is_dailymotion_link(self, url: str) -> bool:
        return is_dailymotion_url(url)

    def extract_info_only(self, url: str) -> Dict[str, Any]:
        ua = random.choice(self.user_agents)
        url_lower = url.lower()
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'skip_download': True,
            'user_agent': ua,
            'geo_bypass': True,
            'check_formats': False,
            'impersonate': 'chrome',
            'extractor_args': {
                'generic': {
                    'impersonate': ['chrome']
                }
            },
            'sleep_interval': 3,
            'max_sleep_interval': 6,
            'sleep_interval_requests': 2,
        }

        if self.is_dailymotion_link(url):
            ydl_opts['http_headers'] = {
                'User-Agent': ua,
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://www.dailymotion.com/',
                'Origin': 'https://www.dailymotion.com'
            }
            ydl_opts['extractor_args']['dailymotion'] = {
                'app_id': 'dmfed',
                'geo_verification_network': 'http'
            }
            if DM_COOKIE_PATH and os.path.exists(DM_COOKIE_PATH):
                ydl_opts['cookiefile'] = DM_COOKIE_PATH

        if "instagram.com" in url_lower and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif ("twitter.com" in url_lower or "x.com" in url_lower) and TW_COOKIE_PATH and os.path.exists(TW_COOKIE_PATH):
            ydl_opts['cookiefile'] = TW_COOKIE_PATH
        elif "pornhub.com" in url_lower and PH_COOKIE_PATH and os.path.exists(PH_COOKIE_PATH):
            ydl_opts['cookiefile'] = PH_COOKIE_PATH

        if HTTP_PROXY:
            ydl_opts['proxy'] = HTTP_PROXY

        max_retries = 4
        for attempt in range(max_retries):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if 'entries' in info and len(info['entries']) > 0:
                        info = info['entries'][0]

                    formats = info.get('formats', [])
                    resolutions_set = set()
                    for f in formats:
                        h = f.get('height')
                        if h and isinstance(h, int) and h >= 144:
                            resolutions_set.add(h)

                    return {
                        "title": info.get('title', 'فيديو بدون عنوان'),
                        "duration": int(info.get('duration') or 0),
                        "uploader": info.get('uploader', info.get('extractor', 'غير معروف')),
                        "resolutions": sorted(list(resolutions_set), reverse=True)
                    }
            except Exception as e:
                err_text = str(e)
                if ("429" in err_text or "403" in err_text) and attempt < max_retries - 1:
                    sleep_time = (2 ** attempt) + random.uniform(1.0, 2.5)
                    logger.warning(f"⚠️ Encountered {err_text[:100]} on info fetch attempt {attempt+1}. Retrying in {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                    ydl_opts['user_agent'] = random.choice(self.user_agents)
                    continue
                if self.is_dailymotion_link(url):
                    ydl_opts['extractor_args'] = {'generic': {'impersonate': ['chrome']}}
                    ydl_opts['format'] = 'best'
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_retry:
                        info = ydl_retry.extract_info(url, download=False)
                        if 'entries' in info and len(info['entries']) > 0:
                            info = info['entries'][0]
                        return {
                            "title": info.get('title', 'فيديو بدون عنوان'),
                            "duration": int(info.get('duration') or 0),
                            "uploader": info.get('uploader', info.get('extractor', 'غير معروف')),
                            "resolutions": []
                        }
                raise e

    def download_direct_url(self, url: str, task_id: str, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        
        parsed = urlparse(url)
        path = parsed.path
        filename = os.path.basename(path)
        if not filename or '.' not in filename:
            filename = f"video_{task_id}.mp4"
            
        file_path = os.path.join(out_dir, f"{task_id}_{filename[:50]}")
        start_time = time.time()

        def update_q(downloaded, total):
            q = PROGRESS_QUEUES.get(task_id)
            if q:
                loop.call_soon_threadsafe(q.put_nowait, ("تحميل الفيديو (رابط مباشر)", downloaded, total, start_time, "bytes"))

        max_attempts = 5
        for attempt in range(max_attempts):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            
            try:
                if HAS_CURL_CFFI:
                    headers = get_random_headers()
                    response = curl_requests.get(
                        url,
                        headers=headers,
                        impersonate="chrome120",
                        stream=True,
                        timeout=30
                    )
                    if response.status_code == 429:
                        raise urllib.error.HTTPError(url, 429, "Too Many Requests", response.headers, None)
                    elif response.status_code == 403:
                        raise urllib.error.HTTPError(url, 403, "Forbidden / Cloudflare Challenge", response.headers, None)
                    response.raise_for_status()

                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    
                    with open(file_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=1024*1024):
                            if task_id in CANCELLED_TASKS:
                                raise ProcessCancelledException("CANCELLED")
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                update_q(downloaded, total_size)
                    break
                else:
                    headers = get_random_headers()
                    opener = urllib.request.build_opener()
                    opener.addheaders = [(k, v) for k, v in headers.items()]
                    urllib.request.install_opener(opener)

                    def progress_callback(blocknum, blocksize, totalsize):
                        if task_id in CANCELLED_TASKS:
                            raise ProcessCancelledException("CANCELLED")
                        downloaded = blocknum * blocksize
                        update_q(downloaded, totalsize)

                    urllib.request.urlretrieve(url, file_path, reporthook=progress_callback)
                    break

            except Exception as e:
                error_str = str(e)
                is_429 = "429" in error_str
                is_403 = "403" in error_str
                
                if (is_429 or is_403) and attempt < max_attempts - 1:
                    sleep_time = (2 ** attempt) + random.uniform(1.0, 3.0)
                    logger.warning(f"⚠️ Direct download retry {attempt+1} due to {e}. Retrying in {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                    continue
                raise e

        duration = get_media_duration(file_path)
        thumb_path = generate_ffmpeg_thumbnail(file_path, task_id)
        
        return {
            "file_path": file_path,
            "title": filename,
            "duration": duration,
            "thumb_path": thumb_path,
            "description": "",
            "is_audio": False,
            "is_document": False
        }

    def download_indirect_media(self, url: str, target_option: str, task_id: str, status_msg: Message, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
        auto_disk_guard()
        url_lower = url.lower()
        
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        if "mediafire.com" in url_lower:
            return download_mediafire_file(url, target_option, task_id, loop)
        if "mega.nz" in url_lower or "mega.co.nz" in url_lower:
            return download_mega_file(url, task_id)

        direct_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v', '.3gp')
        if any(url_lower.endswith(ext) for ext in direct_extensions) and target_option != "mp3":
            try:
                return self.download_direct_url(url, task_id, loop)
            except Exception as ex:
                logger.warning(f"⚠️ Direct download failed, falling back to yt-dlp: {ex}")

        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        out_template = os.path.join(out_dir, f"{task_id}_%(title).50s.%(ext)s")

        start_time = time.time()

        def ytdl_hook(d):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            if d.get("status") in ["downloading", "finished"]:
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                label = "استخراج الصوت MP3" if target_option == "mp3" else f"تحميل المقطع ({target_option})"
                
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    loop.call_soon_threadsafe(q.put_nowait, (label, downloaded, total, start_time, "bytes"))

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
            if target_option in ["best", "vid", "doc"]:
                format_selector = 'bestvideo+bestaudio/best'
            else:
                format_selector = f'bestvideo[height<={target_option}]+bestaudio/best[height<={target_option}]/best'

        ydl_opts = {
            'format': format_selector,
            'outtmpl': out_template,
            'writethumbnail': not is_audio,
            'postprocessors': postprocessors,
            'merge_output_format': 'mp4' if not is_audio else None,
            'postprocessor_args': {
                'ffmpeg': ['-movflags', '+faststart']
            },
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': user_agent,
            'progress_hooks': [ytdl_hook],
            'retries': 50,
            'fragment_retries': 50,
            'sleep_interval': 3,
            'max_sleep_interval': 6,
            'sleep_interval_requests': 2,
            'skip_unavailable_fragments': True,
            'geo_bypass': True,
            'impersonate': 'chrome',
            'extractor_args': {
                'generic': {
                    'impersonate': ['chrome']
                }
            },
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            'legacyserverconnect': True,
        }

        if self.is_dailymotion_link(url):
            ydl_opts['format'] = 'best' if target_option == "best" else f'bestvideo[height<={target_option}]+bestaudio/best[height<={target_option}]/best'
            ydl_opts['http_headers'].update({
                'Referer': 'https://www.dailymotion.com/',
                'Origin': 'https://www.dailymotion.com'
            })
            ydl_opts['extractor_args']['dailymotion'] = {
                'app_id': 'dmfed',
                'geo_verification_network': 'http'
            }
            if DM_COOKIE_PATH and os.path.exists(DM_COOKIE_PATH):
                ydl_opts['cookiefile'] = DM_COOKIE_PATH

        if "facebook.com" in url_lower or "fb.watch" in url_lower or "fb.gg" in url_lower:
            url = url.replace("m.facebook.com", "www.facebook.com").replace("mbasic.facebook.com", "www.facebook.com")
            ydl_opts.update({
                'format': 'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
                'check_formats': False,
                'extractor_args': {'facebook': {'skip': ['hls']}, 'generic': {'impersonate': ['chrome']}},
                'http_headers': {
                    'User-Agent': user_agent,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Sec-Fetch-Mode': 'navigate',
                    'Referer': 'https://www.facebook.com/',
                }
            })

        if "tiktok.com" in url_lower:
            ydl_opts['extractor_args']['tiktok'] = {'app_version': '1.0.0'}
        
        if "instagram.com" in url_lower and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif ("twitter.com" in url_lower or "x.com" in url_lower) and TW_COOKIE_PATH and os.path.exists(TW_COOKIE_PATH):
            ydl_opts['cookiefile'] = TW_COOKIE_PATH
        elif "pornhub.com" in url_lower and PH_COOKIE_PATH and os.path.exists(PH_COOKIE_PATH):
            ydl_opts['cookiefile'] = PH_COOKIE_PATH

        if HTTP_PROXY:
            ydl_opts['proxy'] = HTTP_PROXY

        max_dl_retries = 4
        for dl_attempt in range(max_dl_retries):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    base, _ = os.path.splitext(filename)

                    raw_duration = info.get('duration')
                    safe_duration = int(float(raw_duration)) if raw_duration is not None else 0
                    post_desc = info.get('description') or info.get('title') or ""

                    if is_audio:
                        final_file_path = f"{base}.mp3" if os.path.exists(f"{base}.mp3") else filename
                        thumb_path = None
                    else:
                        final_file_path = f"{base}.mp4" if os.path.exists(f"{base}.mp4") else filename
                        thumb_path = None
                        for ext in ['.jpg', '.png', '.webp', '.jpeg']:
                            possible_thumb = f"{base}{ext}"
                            clean = sanitize_thumb(possible_thumb)
                            if clean:
                                thumb_path = clean
                                break

                    if not os.path.exists(final_file_path):
                        matched = glob.glob(f"{base}.*")
                        if matched:
                            final_file_path = matched[0]

                    return {
                        "file_path": final_file_path,
                        "title": str(info.get('title', 'Media File')),
                        "duration": safe_duration,
                        "thumb_path": thumb_path,
                        "description": post_desc,
                        "is_audio": is_audio,
                        "is_document": False
                    }
            except Exception as err:
                err_msg = str(err)
                if ("429" in err_msg or "403" in err_msg) and dl_attempt < max_dl_retries - 1:
                    sleep_time = (2 ** dl_attempt) + random.uniform(1.5, 3.0)
                    logger.warning(f"⚠️ YTDL retry {dl_attempt+1} after HTTP 429/403. Retrying in {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                    ydl_opts['user_agent'] = random.choice(self.user_agents)
                    ydl_opts['http_headers']['User-Agent'] = ydl_opts['user_agent']
                    continue
                raise err

engine = UniversalEngineV63()

# ----------------------------------------------------
# 🛠️ لوحات الأزرار الجودة والضغط v63
# ----------------------------------------------------
def build_quality_keyboard(req_id: str, resolutions: List[int] = None) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🎬 1080p (Full HD)", callback_data=f"q_1080_{req_id}"),
            InlineKeyboardButton("🎬 720p (HD)", callback_data=f"q_720_{req_id}")
        ],
        [
            InlineKeyboardButton("🎬 480p (SD)", callback_data=f"q_480_{req_id}"),
            InlineKeyboardButton("🎬 320p (Low)", callback_data=f"q_320_{req_id}")
        ],
        [InlineKeyboardButton("✨ أفضل جودة متاحة (Auto)", callback_data=f"q_best_{req_id}")],
        [InlineKeyboardButton("🎵 تحميل صوت MP3 (320kbps)", callback_data=f"q_mp3_{req_id}")]
    ]
    return InlineKeyboardMarkup(buttons)

def build_compress_keyboard(req_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("🎬 ضغط إلى 1080p", callback_data=f"cmp_1080_{req_id}"),
            InlineKeyboardButton("🎬 ضغط إلى 720p", callback_data=f"cmp_720_{req_id}")
        ],
        [
            InlineKeyboardButton("🎬 ضغط إلى 480p", callback_data=f"cmp_480_{req_id}"),
            InlineKeyboardButton("🎬 ضغط إلى 360p", callback_data=f"cmp_360_{req_id}")
        ],
        [InlineKeyboardButton("⚡ ضغط ذكي تلقائي (Smart Compression)", callback_data=f"cmp_auto_{req_id}")],
        [InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cncl_{req_id}")]
    ]
    return InlineKeyboardMarkup(buttons)

# ----------------------------------------------------
# 🛠️ مدير الواجهة والتقدم v63
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
            if len(data) == 5:
                action_title, current, total, start_time, mode = data
            else:
                action_title, current, total, start_time = data
                mode = "bytes"

            now = time.time()
            if now - last_update_time >= 2.0 or current == 0 or current == total:
                diff = now - start_time
                speed = current / diff if diff > 0 else 0

                if mode == "time":
                    if total > 0:
                        percentage = min(100.0, (current / total) * 100)
                        eta = round((total - current) / speed) if speed > 0 else 0
                        bar = f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
                        speed_factor = speed
                        text = (
                            f"⚙️ **[v63 Engine - Anti-429 & Anti-403]**\n"
                            f"📌 **العملية:** {action_title}\n\n"
                            f"{bar}"
                            f"⏱️ **المنقضي:** `{format_time(current)}` / `{format_time(total)}`\n"
                            f"🚀 **سرعة الضغط:** `{speed_factor:.2f}x` | ⏳ **المتبقي للضغط:** `{format_time(eta)}`\n\n"
                            f"📤 **الخطوة التالية:** سيتم إعادة رفع الفيديو إلى تلجرام فور انتهاء الضغط تلقائياً."
                        )
                    else:
                        bar = "🔄 `جاري معالجة وضغط المقطع بـ FFmpeg...`\n"
                        text = (
                            f"⚙️ **[v63 Engine - Anti-429 & Anti-403]**\n"
                            f"📌 **العملية:** {action_title}\n\n"
                            f"{bar}"
                            f"📤 **ملاحظة:** سيتم رفع الفيديو فور اكتمال عملية الضغط."
                        )
                else:
                    if total > 0:
                        percentage = (current / total) * 100
                        eta = round((total - current) / speed) if speed > 0 else 0
                        total_str = f"`{total / (1024*1024):.1f}MB`"
                        bar = f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
                        eta_str = f"| ⏱️ `{eta}s`"
                    else:
                        total_str = "جاري الحساب..."
                        bar = "🔄 `جاري تدفق البيانات والمعالجة...`\n"
                        eta_str = ""

                    text = (
                        f"⚡ **[v63 Engine - Anti-Cloudflare Protected]**\n"
                        f"📌 **العملية:** {action_title}\n\n"
                        f"{bar}"
                        f"📦 **الحجم:** `{current / (1024*1024):.1f}MB` / {total_str}\n"
                        f"🚀 **السرعة:** `{speed / (1024*1024):.2f} MB/s` {eta_str}"
                    )

                if text != last_text and task_id not in CANCELLED_TASKS:
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
    for f in glob.glob(f"downloads/{task_id}*") + glob.glob(f"downloads/thumb_{task_id}*"):
        try:
            if os.path.isfile(f) or os.path.islink(f):
                os.remove(f)
            elif os.path.isdir(f):
                shutil.rmtree(f)
        except Exception:
            pass

# ----------------------------------------------------
# 🎥 معالج ضغط مقاطع الفيديو المباشرة للمستخدم
# ----------------------------------------------------
@app.on_message(filters.private & (filters.video | filters.document))
async def handle_video_message(client: Client, message: Message):
    chat_id = message.chat.id
    doc = message.document
    vid = message.video
    
    if doc:
        mime = doc.mime_type or ""
        if not (mime.startswith("video/") or doc.file_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v'))):
            return

    req_id = f"cmp_{int(time.time() * 1000)}"
    PENDING_COMPRESS[req_id] = message

    kb = build_compress_keyboard(req_id)
    await message.reply_text(
        "🎬 **تم استلام مقطع الفيديو بنجاح!**\n"
        "اختَر خيار الضغط أو الجودة التي تريد تقليل حجم الفيديو إليها:",
        reply_markup=kb,
        quote=True
    )

@app.on_callback_query(filters.regex(r"^cmp_"))
async def process_compression_callback(client: Client, callback: CallbackQuery):
    data = callback.data
    parts = data.split("_")
    target_q = parts[1]
    req_id = f"cmp_{parts[2]}" if len(parts) > 2 else ""

    orig_msg = PENDING_COMPRESS.get(req_id)
    if not orig_msg:
        await callback.answer("⚠️ انتهت صلاحية الطلب أو تم إلغاؤه.", show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.edit_text("⏳ **جاري بدء عملية تنزيل وضغط الفيديو...**")

    task_id = f"task_{int(time.time() * 1000)}"
    asyncio.create_task(process_compression_task(client, orig_msg, target_q, task_id, status_msg))

async def process_compression_task(client: Client, orig_msg: Message, target_q: str, task_id: str, status_msg: Message):
    loop = asyncio.get_running_loop()
    PROGRESS_QUEUES[task_id] = asyncio.Queue()
    ui_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))

    try:
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        raw_input_path = os.path.join(out_dir, f"raw_{task_id}.mp4")
        compressed_output_path = os.path.join(out_dir, f"compressed_{task_id}.mp4")

        start_time = time.time()
        
        async def dl_progress(current, total):
            q = PROGRESS_QUEUES.get(task_id)
            if q:
                await q.put(("تحميل الفيديو المطلوب ضغطه", current, total, start_time, "bytes"))

        downloaded_file = await orig_msg.download(file_name=raw_input_path, progress=dl_progress)
        
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        success = await loop.run_in_executor(
            None, compress_video_ffmpeg, downloaded_file, target_q, compressed_output_path, task_id, loop
        )

        if not success or not os.path.exists(compressed_output_path):
            raise Exception("فشلت عملية ضغط الفيديو عبر FFmpeg.")

        parts = await loop.run_in_executor(None, split_video_file, compressed_output_path, task_id)

        for idx, part_file in enumerate(parts):
            up_start = time.time()
            async def ul_progress(current, total):
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    await q.put((f"رفع الفيديو المضغوط (جزء {idx+1}/{len(parts)})", current, total, up_start, "bytes"))

            duration, width, height, raw_thumb = await get_video_metadata_and_thumb(part_file)
            thumb = sanitize_thumb(raw_thumb)

            await orig_msg.reply_video(
                video=part_file,
                caption=f"⚡ **تم ضغط الفيديو بنجاح ({target_q}p)**",
                duration=duration,
                width=width,
                height=height,
                thumb=thumb,
                progress=ul_progress,
                quote=True
            )

        await status_msg.delete()

    except Exception as e:
        if str(e) == "CANCELLED":
            await status_msg.edit_text("🛑 **تم إلغاء عملية الضغط.**")
        else:
            logger.error(f"Compression error: {e}")
            await status_msg.edit_text(f"❌ **حدث خطأ أثناء الضغط:**\n`{str(e)[:200]}`")
    finally:
        ui_task.cancel()
        PROGRESS_QUEUES.pop(task_id, None)
        cleanup_files(task_id)
        force_release_memory()

# ----------------------------------------------------
# 📌 معالج الأوامر والرسائل
# ----------------------------------------------------
@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "👋 **أهلاً بك في بوت التحميل والضغط الشامل v63 Engine!**\n\n"
        "✨ **المميزات المتاحة:**\n"
        "• تنزيل الميديا من يوتيوب، تويتر (X)، إنستغرام، تيك توك، فيسبوك، وDailymotion.\n"
        "• دعم تنزيل الملفات المباشرة من MediaFire و Mega وأي رابط مباشر.\n"
        "• ضغط الفيديوهات وتقليل حجمها بجودات متطورة عبر FFmpeg.\n"
        "• ترجمة أوصاف المقاطع تلقائياً واستخراج 9 صور مصغرة عند التفعيل.\n\n"
        "🔗 **أرسل لي أي رابط أو مقطع فيديو للبدء فوراً!**"
    )

@app.on_message(filters.command("settings"))
async def settings_cmd(client: Client, message: Message):
    kb = build_settings_keyboard(message.chat.id)
    await message.reply_text("⚙️ **إعدادات التقاط اللقطات (9 صور مصغرة) للروابط:**", reply_markup=kb)

@app.on_callback_query(filters.regex(r"^cfg_"))
async def settings_callback_handler(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    cfg = get_user_settings(chat_id)
    data = callback.data

    if data == "cfg_toggle_direct":
        cfg["snapshots_direct"] = not cfg["snapshots_direct"]
    elif data == "cfg_toggle_dm":
        cfg["snapshots_dailymotion"] = not cfg["snapshots_dailymotion"]
    elif data == "cfg_toggle_social":
        cfg["snapshots_social"] = not cfg["snapshots_social"]
    elif data == "cfg_close":
        await callback.message.delete()
        return

    kb = build_settings_keyboard(chat_id)
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer("تم تحديث الإعدادات")

@app.on_message(filters.command(["trim", "cut"]))
async def trim_url_command(client: Client, message: Message):
    AWAITING_TRIM_INPUT[message.chat.id] = True
    await message.reply_text("✂️ **أرسل رابط الفيديو ثم وقت البداية والنيابة بالشكل التالي:**\n`https://link.com 00:00:10 00:00:40`")

@app.on_message(filters.private & filters.text & ~filters.command(["start", "settings", "trim", "cut"]))
async def handle_message(client: Client, message: Message):
    text = message.text.strip()
    chat_id = message.chat.id

    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        return

    url = url_match.group(0)

    # التحقق من حالة تيك توك التلقائي بدون علامة مائية
    if "tiktok.com" in url.lower() and "photo" in url.lower():
        status_msg = await message.reply_text("⏳ **جاري تنزيل صور TikTok Slideshow...**", quote=True)
        task_id = f"task_{int(time.time() * 1000)}"
        asyncio.create_task(process_tiktok_ss_task(client, url, task_id, status_msg))
        return

    req_id = f"req_{int(time.time() * 1000)}"
    PENDING_URLS[req_id] = url

    loading_msg = await message.reply_text("🔍 **جاري فحص الرابط واستخراج معلومات الفيديو والجودات المتاحة...**", quote=True)

    loop = asyncio.get_running_loop()
    try:
        if is_dailymotion_url(url):
            info = {"title": "فيديو Dailymotion", "duration": 0, "uploader": "Dailymotion", "resolutions": [1080, 720, 480, 320]}
        else:
            info = await loop.run_in_executor(None, engine.extract_info_only, url)

        title = info.get("title", "فيديو بدون عنوان")
        duration_sec = info.get("duration", 0)
        uploader = info.get("uploader", "غير معروف")
        duration_str = format_time(duration_sec) if duration_sec > 0 else "غير معروف"

        kb = build_quality_keyboard(req_id, info.get("resolutions", []))

        info_text = (
            f"🎬 **معلومات الفيديو المحدد:**\n\n"
            f"📌 **العنوان:** `{title}`\n"
            f"⏱️ **المدة الزمانية:** `{duration_str}`\n"
            f"👤 **المصدر/القناة:** `{uploader}`\n\n"
            f"👇 **اختر الجودة المطلوبة لتنزيل المقطع:**"
        )
        await loading_msg.edit_text(info_text, reply_markup=kb)

    except Exception as e:
        logger.error(f"Error extracting video info: {e}")
        # خيار الدعم في حالة عدم استخراج المعلومات
        kb = build_quality_keyboard(req_id, [])
        await loading_msg.edit_text(
            "⚠️ **تعذر جلب تفاصيل الجودات المتاحة تلقائياً.**\nاختر الجودة أو الصيغة المطلوبة للتحميل المباشر:",
            reply_markup=kb
        )

async def process_tiktok_ss_task(client: Client, url: str, task_id: str, status_msg: Message):
    try:
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, engine.download_indirect_media, url, "best", task_id, status_msg, loop)
        file_p = res.get("file_path")
        if file_p and os.path.exists(file_p):
            await client.send_document(status_msg.chat.id, file_p, caption="📸 **صور TikTok**")
            os.remove(file_p)
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ **فشل تحميل صور TikTok:**\n`{e}`")

# ----------------------------------------------------
# 📥 معالج استعلامات الجودة وإدارة عمليات التحميل والرفع
# ----------------------------------------------------
@app.on_callback_query(filters.regex(r"^(q_|cncl_)"))
async def process_download_callback(client: Client, callback: CallbackQuery):
    data = callback.data

    if data.startswith("cncl_"):
        task_id = data.replace("cncl_", "")
        CANCELLED_TASKS.add(task_id)
        if task_id in ACTIVE_CANCEL_EVENTS:
            ACTIVE_CANCEL_EVENTS[task_id].set()
        await callback.answer("🛑 جاري إلغاء العملية...", show_alert=True)
        return

    parts = data.split("_")
    quality_choice = parts[1]
    req_id = f"req_{parts[2]}" if len(parts) > 2 else ""

    url = PENDING_URLS.pop(req_id, None)
    if not url:
        await callback.answer("⚠️ انتهت صلاحية الطلب أو تم اختياره سابقاً.", show_alert=True)
        return

    await callback.answer()

    status_msg = callback.message
    task_id = f"task_{int(time.time() * 1000)}"

    if is_dailymotion_url(url):
        asyncio.create_task(download_dailymotion_video(callback.message, url, quality_choice, status_msg))
    else:
        asyncio.create_task(process_download_task(client, callback.message, url, quality_choice, task_id, status_msg))

async def process_download_task(client: Client, orig_msg: Message, url: str, quality_choice: str, task_id: str, status_msg: Message):
    chat_id = orig_msg.chat.id
    loop = asyncio.get_running_loop()

    PROGRESS_QUEUES[task_id] = asyncio.Queue()
    ui_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))

    try:
        media_info = await loop.run_in_executor(
            None, engine.download_indirect_media, url, quality_choice, task_id, status_msg, loop
        )

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        file_path = media_info.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise Exception("لم يتم العثور على الملف المحمل بعد انتهاء عملية التنزيل.")

        is_audio = media_info.get("is_audio", False)
        is_document = media_info.get("is_document", False)

        # التحويل التلقائي للـ MP4 للفيديوهات لضمان توافقها مع التلجرام
        if not is_audio and not is_document:
            file_path = await convert_to_mp4(file_path)

        parts = await loop.run_in_executor(None, split_video_file, file_path, task_id)

        post_desc = media_info.get("description", "")
        translated_arabic = await translate_to_arabic(post_desc) if post_desc else ""

        for idx, part_file in enumerate(parts):
            if not os.path.exists(part_file) or os.path.getsize(part_file) == 0:
                continue

            up_start = time.time()
            async def upload_callback(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED")
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    part_str = f" (الجزء {idx+1}/{len(parts)})" if len(parts) > 1 else ""
                    await q.put((f"رفع الملف إلى تلجرام{part_str}", current, total, up_start, "bytes"))

            duration, width, height, raw_thumb = await get_video_metadata_and_thumb(part_file)
            thumb = sanitize_thumb(raw_thumb) or sanitize_thumb(media_info.get("thumb_path"))

            caption = f"🎬 **{media_info.get('title', 'Media')}**"
            if len(parts) > 1:
                caption += f"\n📦 **الجزء ({idx+1}/{len(parts)})**"

            if post_desc and idx == 0:
                clean = post_desc.strip()
                if len(clean) > 350: clean = clean[:350] + "..."
                caption += f"\n\n📝 **الوصف الأصلي:**\n{clean}"
                if translated_arabic:
                    caption += f"\n\n🇦🇪 **الترجمة العربية:**\n{translated_arabic}"

            if is_audio:
                await client.send_audio(
                    chat_id=chat_id,
                    audio=part_file,
                    caption=caption,
                    duration=duration if duration > 0 else None,
                    title=media_info.get("title"),
                    progress=upload_callback
                )
            elif is_document:
                await client.send_document(
                    chat_id=chat_id,
                    document=part_file,
                    caption=caption,
                    progress=upload_callback
                )
            else:
                await client.send_video(
                    chat_id=chat_id,
                    video=part_file,
                    caption=caption,
                    duration=duration if duration > 0 else None,
                    width=width if width > 0 else None,
                    height=height if height > 0 else None,
                    thumb=thumb,
                    supports_streaming=True,
                    progress=upload_callback
                )

        # التقاط اللقطات 9 صور مصغرة إن كانت مفعّلة في الإعدادات
        url_type = determine_url_type(url)
        user_cfg = get_user_settings(chat_id)
        
        should_snap = (
            (url_type == "direct" and user_cfg["snapshots_direct"]) or
            (url_type == "social" and user_cfg["snapshots_social"])
        )

        if not is_audio and not is_document and should_snap:
            tot_dur = get_media_duration(file_path)
            if tot_dur > 0:
                await status_msg.edit_text("📸 **جاري التقاط 9 صور مصغرة من الفيديو...**")
                frames = await extract_9_frames(file_path, tot_dur, chat_id)
                valid_frames = [fr for fr in frames if sanitize_thumb(fr)]
                if valid_frames:
                    media_group = [InputMediaPhoto(media=fr) for fr in valid_frames]
                    await client.send_media_group(chat_id, media_group)
                    for fr in frames:
                        try: os.remove(fr)
                        except Exception: pass

        await status_msg.delete()

    except Exception as e:
        if str(e) == "CANCELLED":
            await status_msg.edit_text("🛑 **تم إلغاء عملية التنزيل والرفع.**")
        else:
            logger.error(f"Download/Upload task error: {e}")
            await status_msg.edit_text(f"❌ **حدث خطأ أثناء التنزيل/الرفع:**\n`{str(e)[:250]}`")

    finally:
        ui_task.cancel()
        PROGRESS_QUEUES.pop(task_id, None)
        cleanup_files(task_id)
        force_release_memory()

# ----------------------------------------------------
# 🚀 تشغيل البوت v63
# ----------------------------------------------------
if __name__ == "__main__":
    auto_disk_guard()
    logger.info("⚡ تم تشغيل المحرك UniversalDownloaderBot_v63 بنجاح.")
    app.run()
