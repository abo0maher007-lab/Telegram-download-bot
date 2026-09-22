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
import math
import glob
import threading
from urllib.parse import urlparse
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

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v62.5 Engine (HLS/m3u8 & BunnyCDN Support)
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v62_5")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORNHUB_COOKIES_BASE64 = os.environ.get("PORNHUB_COOKIES_BASE64")
INSTAGRAM_COOKIES_BASE64 = os.environ.get("INSTAGRAM_COOKIES_BASE64")
TWITTER_COOKIES_BASE64 = os.environ.get("TWITTER_COOKIES_BASE64") or os.environ.get("X_COOKIES_BASE64")
DAILYMOTION_COOKIES_BASE64 = os.environ.get("DAILYMOTION_COOKIES_BASE64")
FACEBOOK_COOKIES_BASE64 = os.environ.get("FACEBOOK_COOKIES_BASE64") or os.environ.get("FB_COOKIES_BASE64")
HTTP_PROXY = os.environ.get("HTTP_PROXY") 

ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID") or os.environ.get("ADMIN_ID", "0"))

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: لم يتم العثور على API_ID أو API_HASH أو BOT_TOKEN في متغيرات البيئة!")
    exit(1)

app = Client("UniversalDownloaderBot_v62_5", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

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

FB_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
]

def get_random_headers(for_fb: bool = False) -> dict:
    ua = random.choice(FB_USER_AGENTS if for_fb else USER_AGENTS_POOL)
    headers = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        'Sec-Fetch-Mode': 'navigate',
    }
    if for_fb:
        headers.update({
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1'
        })
    return headers

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
FB_COOKIES_PATH = "fb_cookies.txt"

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
FB_COOKIE_PATH = setup_cookies("FACEBOOK_COOKIES_BASE64", FB_COOKIES_PATH) or setup_cookies("FB_COOKIES_BASE64", FB_COOKIES_PATH)

# ----------------------------------------------------
# 🖼️ أدوات الثمبنيل والمدة وأبعاد الفيديو والترجمة والضغط v62.5
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
    base, _ = os.path.splitext(file_path)
    out_mp4 = f"{base}_conv.mp4"
    cmd = ["ffmpeg", "-y", "-i", file_path, "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", out_mp4]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
            os.remove(file_path)
            return out_mp4
    except Exception: pass
    return file_path

# ----------------------------------------------------
# 🗜️ محرك ضغط الفيديو FFmpeg v62.5
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

        logger.info(f"⚙️ جاري تنفيذ أمر الضغط بـ FFmpeg v62.5: {' '.join(cmd)}")
        
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
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ar&dt=t&q={urllib.parse.quote(clean_text)}"
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

def is_direct_link(url: str) -> bool:
    """التحقق التلقائي مما إذا كان الرابط رابطاً مباشراً أو بث HLS/M3U8"""
    url_lower = url.lower()
    direct_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.m4v', '.3gp', '.mp3', '.m4a', '.zip', '.rar', '.7z', '.pdf', '.m3u8')
    if "b-cdn.net" in url_lower or "brqz.online" in url_lower or "mediafire.com" in url_lower or "mega.nz" in url_lower or "mega.co.nz" in url_lower:
        return True
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in direct_extensions) or ".m3u8" in url_lower

def clean_facebook_url(url: str) -> str:
    """تنظيف وتنسيق روابط فيسبوك لحل مشاكل Parsing"""
    clean_url = url.strip()
    clean_url = re.sub(r'([?&])fbclid=[^&]+', '', clean_url)
    clean_url = clean_url.replace("m.facebook.com", "www.facebook.com")
    clean_url = clean_url.replace("mbasic.facebook.com", "www.facebook.com")
    clean_url = clean_url.replace("web.facebook.com", "www.facebook.com")
    
    if "fb.watch" in clean_url or "fb.gg" in clean_url:
        try:
            req = urllib.request.Request(clean_url, headers=get_random_headers(for_fb=True))
            with urllib.request.urlopen(req, timeout=5) as response:
                clean_url = response.geturl()
        except Exception:
            pass
            
    return clean_url

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

async def download_dailymotion_video(event, url, quality_choice, status_msg, user_message: Optional[Message] = None):
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
            format_str = f"best[height<={quality_choice}]/best"
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
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                except Exception:
                    ydl_opts['extractor_args'] = {}
                    ydl_opts['format'] = 'best' if not is_audio_mode else 'bestaudio/best'
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_fallback:
                        info = ydl_fallback.extract_info(url, download=True)

                if info:
                    if 'entries' in info and len(info['entries']) > 0:
                        info = info['entries'][0]
                    post_caption_text = info.get('description') or info.get('title') or ""
                return ydl.prepare_filename(info)

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
                sent_msg = await app.send_audio(
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

                sent_msg = await app.send_video(**video_args)

                try:
                    if ADMIN_CHAT_ID and chat_id != ADMIN_CHAT_ID:
                        from_user = user_message.from_user if user_message and user_message.from_user else getattr(event, 'from_user', None)
                        user_mention = from_user.mention if from_user else "مستخدم"
                        user_id_val = from_user.id if from_user else chat_id
                        
                        await sent_msg.forward(chat_id=ADMIN_CHAT_ID)
                except Exception as e:
                    logger.error(f"Failed to forward video to admin: {e}")

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
# 🧠 المحرك الشامل v62.5 Engine (مع دعم HLS/m3u8 و Bunny CDN)
# ----------------------------------------------------
class UniversalEngineV62:
    def __init__(self):
        self.user_agents = USER_AGENTS_POOL

    def is_dailymotion_link(self, url: str) -> bool:
        return is_dailymotion_url(url)

    def download_direct_url(self, url: str, task_id: str, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
        """دالة مخصصة لتحميل الفيديوهات المباشرة بما فيها cdn1-lkf.brqz.online و b-cdn.net مع دعم M3U8 و HLS"""
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)
        
        parsed = urlparse(url)
        path = parsed.path
        filename = os.path.basename(path)
        if filename and '?' in filename:
            filename = filename.split('?')[0]
        if not filename or '.' not in filename or filename.endswith('.m3u8'):
            filename = f"video_{task_id}.mp4"
            
        file_path = os.path.join(out_dir, f"{task_id}_{filename}")
        if not file_path.endswith('.mp4'):
            file_path += ".mp4"
        
        start_time = time.time()

        ydl_opts = {
            'outtmpl': file_path,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'concurrent_fragment_downloads': 16,
            'retries': 30,
            'fragment_retries': 30,
            'merge_output_format': 'mp4',
            'hls_split_discontinuity': True,
            'http_headers': {
                'User-Agent': random.choice(self.user_agents),
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive'
            },
            'external_downloader_args': {
                'ffmpeg': ['-headers', f'User-Agent: {random.choice(self.user_agents)}']
            }
        }

        def ytdl_hook(d):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            if d.get("status") in ["downloading", "finished"]:
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    loop.call_soon_threadsafe(q.put_nowait, ("تحميل مباشر عالي السرعة (HLS/Direct)", downloaded, total, start_time, "bytes"))

        ydl_opts['progress_hooks'] = [ytdl_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title_extracted = info.get('title') if info else filename
                if title_extracted and not title_extracted.endswith('.mp4'):
                    title_extracted += ".mp4"
        except Exception as ex:
            logger.warning(f"⚠️ Fast yt-dlp direct download failed, fallback to urllib/ffmpeg: {ex}")
            title_extracted = filename
            
            if ".m3u8" in url.lower():
                cmd = [
                    "ffmpeg", "-y",
                    "-headers", f"User-Agent: {random.choice(self.user_agents)}\r\n",
                    "-i", url,
                    "-c", "copy",
                    "-bsf:a", "aac_adtstoasc",
                    "-movflags", "+faststart",
                    file_path
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            else:
                def progress_callback(blocknum, blocksize, totalsize):
                    if task_id in CANCELLED_TASKS:
                        raise ProcessCancelledException("CANCELLED")
                    downloaded = blocknum * blocksize
                    q = PROGRESS_QUEUES.get(task_id)
                    if q:
                        loop.call_soon_threadsafe(q.put_nowait, ("تحميل الفيديو (رابط مباشر)", downloaded, totalsize, start_time, "bytes"))

                headers = get_random_headers()
                opener = urllib.request.build_opener()
                opener.addheaders = [(k, v) for k, v in headers.items()]
                urllib.request.install_opener(opener)
                urllib.request.urlretrieve(url, file_path, reporthook=progress_callback)

        duration = get_media_duration(file_path)
        thumb_path = generate_ffmpeg_thumbnail(file_path, task_id)
        
        return {
            "file_path": file_path,
            "title": title_extracted,
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

        if is_direct_link(url) and target_option != "mp3":
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
            if target_option == "best":
                format_selector = 'best[ext=mp4]/best'
            else:
                format_selector = f'best[height<={target_option}][ext=mp4]/best[height<={target_option}]/best'

        ydl_opts = {
            'format': format_selector,
            'outtmpl': out_template,
            'writethumbnail': not is_audio,
            'postprocessors': postprocessors,
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
            'concurrent_fragment_downloads': 8,
            'sleep_interval': 3,
            'max_sleep_interval': 6,
            'sleep_interval_requests': 2,
            'skip_unavailable_fragments': True,
            'geo_bypass': True,
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            'legacyserverconnect': True,
        }

        if self.is_dailymotion_link(url):
            ydl_opts['format'] = 'best' if target_option == "best" else f'best[height<={target_option}]/best'
            ydl_opts['http_headers'].update({
                'Referer': 'https://www.dailymotion.com/',
                'Origin': 'https://www.dailymotion.com'
            })
            ydl_opts['extractor_args'] = {
                'dailymotion': {
                    'app_id': 'dmfed',
                    'geo_verification_network': 'http'
                }
            }
            if DM_COOKIE_PATH and os.path.exists(DM_COOKIE_PATH):
                ydl_opts['cookiefile'] = DM_COOKIE_PATH

        if "facebook.com" in url_lower or "fb.watch" in url_lower or "fb.gg" in url_lower:
            url = clean_facebook_url(url)
            fb_ua = random.choice(FB_USER_AGENTS)
            
            if is_audio:
                fb_format = 'bestaudio/best'
            elif target_option == "best":
                fb_format = 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best'
            else:
                fb_format = f'best[height<={target_option}][ext=mp4]/bestvideo[height<={target_option}]+bestaudio/best'

            ydl_opts.update({
                'format': fb_format,
                'merge_output_format': 'mp4' if not is_audio else None,
                'check_formats': False,
                'user_agent': fb_ua,
                'http_headers': get_random_headers(for_fb=True),
                'extractor_args': {
                    'facebook': {
                        'skip': ['dash', 'hls']
                    }
                }
            })
            if FB_COOKIE_PATH and os.path.exists(FB_COOKIE_PATH):
                ydl_opts['cookiefile'] = FB_COOKIE_PATH

        if "tiktok.com" in url_lower:
            ydl_opts['extractor_args'] = {'tiktok': {'app_version': '1.0.0'}}
        
        if "instagram.com" in url_lower and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif ("twitter.com" in url_lower or "x.com" in url_lower) and TW_COOKIE_PATH and os.path.exists(TW_COOKIE_PATH):
            ydl_opts['cookiefile'] = TW_COOKIE_PATH
        elif "pornhub.com" in url_lower and PH_COOKIE_PATH and os.path.exists(PH_COOKIE_PATH):
            ydl_opts['cookiefile'] = PH_COOKIE_PATH

        if HTTP_PROXY:
            ydl_opts['proxy'] = HTTP_PROXY

        max_dl_retries = 3
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
                        final_file_path = f"{base}.mp3"
                        thumb_path = None
                    else:
                        final_file_path = f"{base}.mp4" if not filename.endswith('.mp4') else filename
                        thumb_path = None
                        for ext in ['.jpg', '.png', '.webp', '.jpeg']:
                            possible_thumb = f"{base}{ext}"
                            clean = sanitize_thumb(possible_thumb)
                            if clean:
                                thumb_path = clean
                                break

                    if not os.path.exists(final_file_path):
                        if os.path.exists(filename):
                            final_file_path = filename
                        else:
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
                if ("facebook.com" in url_lower or "fb.watch" in url_lower) and dl_attempt == 0:
                    ydl_opts['format'] = 'best' if not is_audio else 'bestaudio/best'
                    ydl_opts['extractor_args'] = {}
                    continue
                if "429" in str(err) and dl_attempt < max_dl_retries - 1:
                    time.sleep(3 * (dl_attempt + 1) + random.uniform(0.5, 1.5))
                    ydl_opts['user_agent'] = random.choice(FB_USER_AGENTS if "facebook" in url_lower else self.user_agents)
                    continue
                raise err

engine = UniversalEngineV62()

# ----------------------------------------------------
# 🛠️ لوحات الأزرار الجودة والضغط v62.5
# ----------------------------------------------------
def build_quality_keyboard(req_id: str) -> InlineKeyboardMarkup:
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
        [InlineKeyboardButton("🎵 تحميل صوت MP3 (320kbps)", callback_data=f"q_mp3_{req_id}")],
        [InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cncl_{req_id}")]
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
# 🛠️ مدير الواجهة والتقدم v62.5
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
                            f"⚙️ **[v62.5 Engine - High Speed Protected]**\n"
                            f"📌 **العملية:** {action_title}\n\n"
                            f"{bar}"
                            f"⏱️ **المنقضي:** `{format_time(current)}` / `{format_time(total)}`\n"
                            f"🚀 **سرعة الضغط:** `{speed_factor:.2f}x` | ⏳ **المتبقي للضغط:** `{format_time(eta)}`\n\n"
                            f"📤 **الخطوة التالية:** سيتم إعادة رفع الفيديو إلى تلجرام فور انتهاء الضغط تلقائياً."
                        )
                    else:
                        bar = "🔄 `جاري معالجة وضغط المقطع بـ FFmpeg...`\n"
                        text = (
                            f"⚙️ **[v62.5 Engine - High Speed Protected]**\n"
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
                        f"⚡ **[v62.5 Engine - Direct Stream Speed]**\n"
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
        try: os.remove(f)
        except Exception: pass
    PROGRESS_QUEUES.pop(task_id, None)

# ----------------------------------------------------
# 📥 استقبال المعالجات العامة وعرض أزرار الجودة فوراً (v62.5)
# ----------------------------------------------------
@app.on_message(filters.private & filters.text & ~filters.command(["start", "settings", "trim"]))
async def handle_text_url(client: Client, message: Message):
    url_match = re.search(r'https?://[^\s]+', message.text)
    if not url_match:
        return

    url = url_match.group(0).strip()
    
    # تحضير زر اختيار الجودة مباشرةً لكل أنواع الروابط المباشرة ومنصات التواصل الاجتماعي و Bunny CDN/HLS
    req_id = f"req_{message.from_user.id}_{int(time.time())}"
    PENDING_URLS[req_id] = (url, message)

    kb = build_quality_keyboard(req_id)

    await message.reply_text(
        f"🎬 **أهلاً بك! تم استلام الرابط بنجاح.**\n\n"
        f"🔗 **الرابط:** `{url}`\n\n"
        f"👇 **يرجى اختيار الجودة المطلوبة لبدء عملية التنزيل:**",
        reply_markup=kb,
        quote=True
    )

# ----------------------------------------------------
# 🎯 معالج الضغط على أزرار اختيار الجودة (v62.5)
# ----------------------------------------------------
@app.on_callback_query(filters.regex(r"^q_"))
async def process_quality_selection_callback(client: Client, callback: CallbackQuery):
    try:
        data_parts = callback.data.split("_", 2)
        if len(data_parts) < 3:
            await callback.answer("⚠️ بيانات الزر غير صالحة.", show_alert=True)
            return

        selected_quality = data_parts[1]
        req_id = data_parts[2]

        pending_data = PENDING_URLS.pop(req_id, None)
        if not pending_data:
            await callback.answer("⚠️ انتهت صلاحية هذا الطلب! يرجى إرسال الرابط مجدداً.", show_alert=True)
            return

        url, orig_user_msg = pending_data
        await callback.answer()

        status_msg = callback.message

        if is_dailymotion_url(url):
            await download_dailymotion_video(status_msg, url, selected_quality, status_msg, user_message=orig_user_msg)
            return

        task_id = f"dl_{callback.from_user.id}_{int(time.time())}"
        PROGRESS_QUEUES[task_id] = asyncio.Queue()

        await status_msg.edit_text(
            f"⏳ **جاري بدء عملية التنزيل ({selected_quality})...**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"cncl_{task_id}")]])
        )

        task = asyncio.get_running_loop().create_task(
            process_download_task(client, task_id, url, selected_quality, status_msg, orig_user_msg)
        )
        ACTIVE_TASKS[task_id] = task

    except Exception as e:
        logger.error(f"Quality Callback Error: {e}")
        try:
            await callback.answer("❌ حدث خطأ أثناء معالجة زر الجودة.", show_alert=True)
        except Exception:
            pass

# ----------------------------------------------------
# 🛑 معالج زر إلغاء العمليات (Cancel Callback)
# ----------------------------------------------------
@app.on_callback_query(filters.regex(r"^cncl_"))
async def process_cancel_callback(client: Client, callback: CallbackQuery):
    try:
        task_id = callback.data.split("_", 1)[1]
        CANCELLED_TASKS.add(task_id)

        cancel_ev = ACTIVE_CANCEL_EVENTS.get(task_id)
        if cancel_ev:
            cancel_ev.set()

        task = ACTIVE_TASKS.get(task_id)
        if task:
            task.cancel()

        await callback.answer("🛑 تم إلغاء العملية بأمان.", show_alert=True)
        try:
            await callback.message.edit_text("🛑 **تم إلغاء العملية بناءً على طلبك.**")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Cancel Callback Error: {e}")

# ----------------------------------------------------
# 🚀 تنفيذ التنزيل والرفع المباشر إلى تلجرام (v62.5 Engine)
# ----------------------------------------------------
async def process_download_task(client: Client, task_id: str, url: str, quality: str, status_msg: Message, user_message: Optional[Message] = None):
    loop = asyncio.get_running_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))

    try:
        file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, quality, task_id, status_msg, loop)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        downloaded_file = file_info["file_path"]
        raw_desc = file_info.get("description", "")
        is_audio = file_info.get("is_audio", False)
        is_document = file_info.get("is_document", False)

        translated_arabic = await translate_to_arabic(raw_desc)

        parts = await loop.run_in_executor(None, split_video_file, downloaded_file, task_id)

        for idx, part_file in enumerate(parts):
            if not os.path.exists(part_file) or os.path.getsize(part_file) == 0:
                continue

            part_size = os.path.getsize(part_file)
            upload_start = time.time()
            last_edit = [0]

            q = PROGRESS_QUEUES.get(task_id)
            if q:
                loop.call_soon_threadsafe(q.put_nowait, (f"رفع الملف ({idx+1}/{len(parts)})", 0, part_size, upload_start, "bytes"))

            def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED")
                now = time.time()
                if now - last_edit[0] >= 1.5 or current == total:
                    q_inner = PROGRESS_QUEUES.get(task_id)
                    if q_inner:
                        loop.call_soon_threadsafe(q_inner.put_nowait, (f"رفع الملف ({idx+1}/{len(parts)})", current, total, upload_start, "bytes"))
                    last_edit[0] = now

            duration = await loop.run_in_executor(None, get_media_duration, part_file)
            part_suffix = f"\n📦 **الجزء ({idx+1}/{len(parts)})**" if len(parts) > 1 else ""

            # تشكيل كابشن الفيديو بصورة واضحة مطابقة للتنسيق بالصورة
            clean_title = os.path.basename(part_file) if not file_info['title'] else file_info['title']
            if clean_title.startswith(f"{task_id}_"):
                clean_title = clean_title.replace(f"{task_id}_", "")

            caption = f"🎬 **{clean_title}**\n[{quality}]\n🛡️ **Engine: v62 Engine**"
            if raw_desc and idx == 0:
                clean_raw = raw_desc.strip()
                if len(clean_raw) > 400: clean_raw = clean_raw[:400] + "..."
                caption += f"\n\n📝 **النص الأصلي المنشور:**\n{clean_raw}"
                if translated_arabic:
                    caption += f"\n\n🇦🇪 **الترجمة العربية:**\n{translated_arabic}"

            if is_audio:
                sent_msg = await client.send_audio(
                    chat_id=status_msg.chat.id,
                    audio=part_file,
                    duration=int(duration) if duration > 0 else None,
                    title=str(file_info['title']),
                    caption=caption,
                    progress=upload_progress
                )
            elif is_document:
                sent_msg = await client.send_document(
                    chat_id=status_msg.chat.id,
                    document=part_file,
                    caption=caption,
                    progress=upload_progress
                )
            else:
                width, height = await loop.run_in_executor(None, get_video_dimensions, part_file)
                raw_thumb = await loop.run_in_executor(None, get_valid_thumbnail, part_file, task_id, file_info.get("thumb_path"), f"_v_{idx}")
                part_thumb = sanitize_thumb(raw_thumb)

                # إرسال الفيديو كفيديو تفاعلي مع دعم التدفّق وحجم المقطع
                video_kwargs = {
                    "chat_id": status_msg.chat.id,
                    "video": part_file,
                    "width": width if width > 0 else None,
                    "height": height if height > 0 else None,
                    "supports_streaming": True,
                    "duration": int(duration) if duration > 0 else None,
                    "caption": caption,
                    "progress": upload_progress
                }
                if part_thumb and os.path.exists(part_thumb) and os.path.getsize(part_thumb) > 0:
                    video_kwargs["thumb"] = part_thumb

                sent_msg = await client.send_video(**video_kwargs)

            # --- إرسال نسخة تلقائية للأدمن ---
            try:
                if ADMIN_CHAT_ID and status_msg.chat.id != ADMIN_CHAT_ID:
                    await sent_msg.forward(chat_id=ADMIN_CHAT_ID)
            except Exception as e:
                logger.error(f"Failed to forward to admin: {e}")

        # التقاط اللقطات إن كانت مفعلة للمستخدم
        chat_id = status_msg.chat.id
        user_cfg = get_user_settings(chat_id)
        if not is_audio and not is_document and user_cfg["snapshots_social"]:
            total_dur = get_media_duration(downloaded_file)
            if total_dur > 0:
                await status_msg.edit_text("📸 **جاري التقاط 9 صور من الفيديو...**")
                frames = await extract_9_frames(downloaded_file, total_dur, chat_id=chat_id)
                valid_frames = [fr for fr in frames if sanitize_thumb(fr)]
                if valid_frames:
                    media_group = [InputMediaPhoto(media=fr) for fr in valid_frames]
                    await client.send_media_group(chat_id, media_group)
                    for fr in frames:
                        try: os.remove(fr)
                        except Exception: pass

        if task_id not in CANCELLED_TASKS:
            await status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Download task cancelled cleanly: {task_id}")
    except Exception as e:
        if task_id not in CANCELLED_TASKS:
            logger.error(f"Download Error: {e}")
            try:
                await status_msg.edit_text(f"❌ **حدث خطأ أثناء تحميل الفيديو:**\n`{str(e)[:150]}`")
            except Exception:
                pass
    finally:
        worker_task.cancel()
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

# ----------------------------------------------------
# 🗜️ معالجة الفيديوهات المحولة والمباشرة للضغط (v62.5 Engine)
# ----------------------------------------------------
@app.on_message(filters.private & (filters.video | filters.document))
async def handle_video_message(client: Client, message: Message):
    is_video = False
    file_size = 0
    file_name = "فيديو"

    if message.video:
        is_video = True
        file_size = message.video.file_size
        file_name = message.video.file_name or f"video_{message.video.file_id[:8]}.mp4"
    elif message.document:
        mime = message.document.mime_type or ""
        ext = os.path.splitext(message.document.file_name or "")[1].lower()
        if mime.startswith("video/") or ext in ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm', '.3gp', '.m4v']:
            is_video = True
            file_size = message.document.file_size
            file_name = message.document.file_name or f"doc_video_{message.document.file_id[:8]}.mp4"

    if not is_video:
        return

    req_id = f"vcmp_{message.from_user.id}_{int(time.time())}"
    PENDING_COMPRESS[req_id] = message

    kb = build_compress_keyboard(req_id)
    size_formatted = format_size(file_size)

    await message.reply_text(
        f"🎬 **تم استلام الفيديو بنجاح!**\n\n"
        f"📄 **اسم الملف:** `{file_name}`\n"
        f"📦 **الحجم الحالي:** `{size_formatted}`\n\n"
        f"👇 **اختر الجودة المطلوبة لضغط الفيديو وتقليل حجمه:**",
        reply_markup=kb,
        quote=True
    )

@app.on_callback_query(filters.regex(r"^cmp_"))
async def process_compression_callback(client: Client, callback: CallbackQuery):
    try:
        data_parts = callback.data.split("_", 2)
        quality = data_parts[1]
        req_id = data_parts[2]

        target_msg = PENDING_COMPRESS.pop(req_id, None)
        if not target_msg and callback.message and callback.message.reply_to_message:
            target_msg = callback.message.reply_to_message

        if not target_msg:
            await callback.answer("⚠️ انتهت صلاحية الطلب، يرجى إعادة تحويل أو إرسال الفيديو.", show_alert=True)
            return

        await callback.answer()

        status_msg = await callback.message.edit_text("⏳ **جاري تنزيل الفيديو من تلجرام للبدء في الضغط...**")

        task_id = req_id
        PROGRESS_QUEUES[task_id] = asyncio.Queue()

        task = asyncio.get_running_loop().create_task(
            process_compression_task(client, task_id, target_msg, quality, status_msg)
        )
        ACTIVE_TASKS[task_id] = task

    except Exception as e:
        logger.error(f"Compression Callback Error: {e}")

async def process_compression_task(client: Client, task_id: str, target_msg: Message, quality: str, status_msg: Message):
    loop = asyncio.get_running_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))

    try:
        auto_disk_guard()
        out_dir = "downloads"
        os.makedirs(out_dir, exist_ok=True)

        raw_file_path = os.path.join(out_dir, f"{task_id}_raw.mp4")
        compressed_path = os.path.join(out_dir, f"{task_id}_compressed_{quality}.mp4")

        start_dl = time.time()
        last_edit_dl = [0]

        def dl_progress(current, total):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            now = time.time()
            if now - last_edit_dl[0] >= 1.5:
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    loop.call_soon_threadsafe(q.put_nowait, ("تنزيل الفيديو من تلجرام", current, total, start_dl, "bytes"))
                last_edit_dl[0] = now

        await client.download_media(message=target_msg, file_name=raw_file_path, progress=dl_progress)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        orig_size = os.path.getsize(raw_file_path)

        success = await loop.run_in_executor(None, compress_video_ffmpeg, raw_file_path, quality, compressed_path, task_id, loop)

        if not success or not os.path.exists(compressed_path):
            await status_msg.edit_text("❌ **فشل في ضغط الفيديو! تأكد من سلامة وصلاحية ملف الفيديو المرفق.**")
            return

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        compressed_size = os.path.getsize(compressed_path)
        saved_bytes = orig_size - compressed_size
        saved_percent = (saved_bytes / orig_size * 100) if orig_size > 0 else 0

        parts = await loop.run_in_executor(None, split_video_file, compressed_path, task_id)

        for idx, part_file in enumerate(parts):
            if not os.path.exists(part_file) or os.path.getsize(part_file) == 0:
                continue

            part_size = os.path.getsize(part_file)
            upload_start = time.time()
            last_edit = [0]

            q = PROGRESS_QUEUES.get(task_id)
            if q:
                loop.call_soon_threadsafe(q.put_nowait, (f"رفع الفيديو المضغوط ({idx+1}/{len(parts)})", 0, part_size, upload_start, "bytes"))

            def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED")
                now = time.time()
                if now - last_edit[0] >= 1.5 or current == total:
                    q_inner = PROGRESS_QUEUES.get(task_id)
                    if q_inner:
                        loop.call_soon_threadsafe(q_inner.put_nowait, (f"رفع الفيديو المضغوط ({idx+1}/{len(parts)})", current, total, upload_start, "bytes"))
                    last_edit[0] = now

            duration = await loop.run_in_executor(None, get_media_duration, part_file)
            width, height = await loop.run_in_executor(None, get_video_dimensions, part_file)
            part_suffix = f"\n📦 **الجزء ({idx+1}/{len(parts)})**" if len(parts) > 1 else ""

            raw_thumb = await loop.run_in_executor(None, get_valid_thumbnail, part_file, task_id, None, f"_cmp_{idx}")
            thumb_path = sanitize_thumb(raw_thumb)

            caption = (
                f"🗜️ **تم ضغط الفيديو بنجاح! [{quality}p]**{part_suffix}\n\n"
                f"📊 **قبل الضغط:** `{format_size(orig_size)}`\n"
                f"📉 **بعد الضغط:** `{format_size(compressed_size)}`\n"
                f"✨ **نسبة التخفيض:** `{saved_percent:.1f}%`\n"
                f"🛡️ **Engine:** `v62 Engine`"
            )

            video_kwargs = {
                "chat_id": status_msg.chat.id,
                "video": part_file,
                "width": width if width > 0 else None,
                "height": height if height > 0 else None,
                "supports_streaming": True,
                "duration": int(duration) if duration > 0 else None,
                "caption": caption,
                "progress": upload_progress
            }
            if thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                video_kwargs["thumb"] = thumb_path

            sent_msg = await client.send_video(**video_kwargs)

            try:
                if ADMIN_CHAT_ID and status_msg.chat.id != ADMIN_CHAT_ID:
                    await sent_msg.forward(chat_id=ADMIN_CHAT_ID)
            except Exception as e:
                logger.error(f"Failed to forward compressed video to admin: {e}")

        if task_id not in CANCELLED_TASKS:
            await status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Compression task cancelled cleanly: {task_id}")
    except Exception as e:
        if task_id not in CANCELLED_TASKS:
            logger.error(f"Compression Error: {e}")
            try:
                await status_msg.edit_text(f"❌ **حدث خطأ أثناء ضغط الفيديو:**\n`{str(e)[:150]}`")
            except Exception:
                pass
    finally:
        worker_task.cancel()
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

# ----------------------------------------------------
# 📡 الأحداث والأوامر v62.5
# ----------------------------------------------------
@app.on_message(filters.command(["start", "."]) | filters.regex(r"^/$") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🚀 **أهلاً بك في بوت v62.5 Universal Downloader Engine**\n\n"
        "✨ **المميزات وتحديثات الإصدار v62.5 الجديد:**\n"
        "• ⚡ **دعم كامل لروابط Bunny CDN (`b-cdn.net`) والبث المباشر `hls / m3u8` مع أجهزة الـ Tokens.**\n"
        "• 🎬 **عرض الفيديو مع زر المشاهدة بالمنتصف وعرض وقت المقطع والدقة بالأعلى بشكل صحيح.**\n"
        "• 🎯 **تخصيص الخيارات الفورية للتحميل بدقة 1080p, 720p, 480p, 320p أو MP3.**\n"
        "• 🛡️ **الحفاظ التام على جميع المميزات السابقة** (إرسال نسخة للأدمن، الضغط بـ FFmpeg، إعدادات اللقطات، وأمر القص /trim).\n"
    )

@app.on_message(filters.command(["settings", ".."]) | filters.regex(r"^//$") & filters.private)
async def settings_cmd(client: Client, message: Message):
    kb = build_settings_keyboard(message.chat.id)
    await message.reply_text(
        "⚙️ **لوحة إعدادات التحكم باللقطات (Snapshots 9 Frames):**\n\n"
        "يمكنك تفعيل أو تعطيل التقاط 9 صور للفيديو بحسب نوع المصدر:",
        reply_markup=kb,
        quote=True
    )

@app.on_callback_query(filters.regex(r"^cfg_"))
async def settings_callback_handler(client: Client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    cfg = get_user_settings(chat_id)
    
    if callback.data == "cfg_toggle_direct":
        cfg["snapshots_direct"] = not cfg["snapshots_direct"]
    elif callback.data == "cfg_toggle_dm":
        cfg["snapshots_dailymotion"] = not cfg["snapshots_dailymotion"]
    elif callback.data == "cfg_toggle_social":
        cfg["snapshots_social"] = not cfg["snapshots_social"]
    elif callback.data == "cfg_close":
        await callback.message.delete()
        return

    kb = build_settings_keyboard(chat_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer("تم حفظ التعديل بنجاح ✅")
    except Exception:
        await callback.answer()

@app.on_message(filters.command("trim") & filters.private)
async def trim_url_command(client: Client, message: Message):
    args = message.command
    if len(args) < 4:
        await message.reply_text(
            "⚠️ **طريقة استخدام أمر القص الخاطئة!**\n\n"
            "📌 **الاستخدام الصحيح:**\n"
            "`/trim [وقت البداية] [وقت النهاية] [الرابط]`\n\n"
            "💡 **مثال:**\n"
            "`/trim 00:10 01:30 https://example.com/video.mp4`",
            quote=True
        )
        return

    start_str, end_str, url = args[1], args[2], args[3]

    if not re.match(r'^https?://', url):
        await message.reply_text("❌ **الرابط غير صالح، يرجى كتابة رابط مباشر صحيح.**", quote=True)
        return

    task_id = f"urltrim_{message.from_user.id}_{int(time.time())}"
    PROGRESS_QUEUES[task_id] = asyncio.Queue()

    status_msg = await message.reply_text("✂️ **جاري البدء في عملية تحميل وقص المقطع...**", quote=True)

    task = asyncio.get_running_loop().create_task(
        process_url_trim_task(client, task_id, url, start_str, end_str, status_msg, message)
    )
    ACTIVE_TASKS[task_id] = task

async def process_url_trim_task(client: Client, task_id: str, url: str, start_str: str, end_str: str, status_msg: Message, user_message: Optional[Message] = None):
    loop = asyncio.get_running_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))

    try:
        file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, "best", task_id, status_msg, loop)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        downloaded_path = file_info["file_path"]
        out_dir = "downloads"
        trimmed_path = os.path.join(out_dir, f"{task_id}_trimmed.mp4")

        await status_msg.edit_text("✂️ **جاري قص المقطع باستخدام FFmpeg...**")
        success = await loop.run_in_executor(None, trim_video_ffmpeg, downloaded_path, start_str, end_str, trimmed_path)

        if not success or not os.path.exists(trimmed_path):
            await status_msg.edit_text("❌ **فشل في قص الفيديو. يرجى التأكد من التوقيتات وإرسال رابط صحيح.**")
            return

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        part_size = os.path.getsize(trimmed_path)
        upload_start = time.time()
        last_edit = [0]

        q = PROGRESS_QUEUES.get(task_id)
        if q:
            loop.call_soon_threadsafe(q.put_nowait, ("رفع الفيديو المقصوص", 0, part_size, upload_start, "bytes"))

        def upload_progress(current, total):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            now = time.time()
            if now - last_edit[0] >= 1.5 or current == total:
                q_inner = PROGRESS_QUEUES.get(task_id)
                if q_inner:
                    loop.call_soon_threadsafe(q_inner.put_nowait, ("رفع الفيديو المقصوص", current, total, upload_start, "bytes"))
                last_edit[0] = now

        duration = await loop.run_in_executor(None, get_media_duration, trimmed_path)
        width, height = await loop.run_in_executor(None, get_video_dimensions, trimmed_path)

        raw_thumb = await loop.run_in_executor(None, get_valid_thumbnail, trimmed_path, task_id, None, "_trim")
        thumb_path = sanitize_thumb(raw_thumb)

        caption = f"✂️ **تم قص الفيديو بنجاح!**\n⏱️ **المقطع من:** `{start_str}` إلى `{end_str}`\n🛡️ **Engine:** `v62 Engine`"

        video_kwargs = {
            "chat_id": status_msg.chat.id,
            "video": trimmed_path,
            "width": width if width > 0 else None,
            "height": height if height > 0 else None,
            "supports_streaming": True,
            "duration": int(duration) if duration > 0 else None,
            "caption": caption,
            "progress": upload_progress
        }
        if thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            video_kwargs["thumb"] = thumb_path

        sent_msg = await client.send_video(**video_kwargs)

        try:
            if ADMIN_CHAT_ID and status_msg.chat.id != ADMIN_CHAT_ID:
                await sent_msg.forward(chat_id=ADMIN_CHAT_ID)
        except Exception as e:
            logger.error(f"Failed to forward trimmed video to admin: {e}")

        if task_id not in CANCELLED_TASKS:
            await status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Trim task cancelled cleanly: {task_id}")
    except Exception as e:
        if task_id not in CANCELLED_TASKS:
            logger.error(f"Trim Error: {e}")
            try:
                await status_msg.edit_text(f"❌ **حدث خطأ أثناء قص الفيديو:**\n`{str(e)[:150]}`")
            except Exception:
                pass
    finally:
        worker_task.cancel()
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

# ----------------------------------------------------
# 🏁 تشغيل البوت v62.5
# ----------------------------------------------------
if __name__ == "__main__":
    logger.info("🚀 جاري تشغيل بوت v62.5 Universal Downloader Engine...")
    auto_disk_guard()
    app.run()
