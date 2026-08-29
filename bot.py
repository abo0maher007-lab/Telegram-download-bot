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
from typing import Optional, Dict, Any, List, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, RPCError, MessageNotModified
import yt_dlp

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v36.0 Engine (Dailymotion & Quality Keyboard)
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v36_0")

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

app = Client("UniversalDownloaderBot_v36_0", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()
PENDING_URLS = {}
AWAITING_TRIM_INPUT = {}
PROGRESS_QUEUES = {}

MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2 GB limit for standard Telegram upload

class ProcessCancelledException(Exception):
    pass

# ----------------------------------------------------
# 🧹 أدوات إدارة وتنظيف القرص (Disk Management Tools)
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

# ----------------------------------------------------
# 🍪 إدارة الكوكيز v36.0
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
# 🖼️ أدوات الثمبنيل والمدة وأبعاد الفيديو v36.0
# ----------------------------------------------------
def format_seconds(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

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
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.warning(f"⚠️ تعذر إنتاج thumbnail عبر FFmpeg: {e}")
    return None

def get_valid_thumbnail(video_path: str, task_id: str, existing_thumb: Optional[str] = None, suffix: str = "") -> Optional[str]:
    if existing_thumb and os.path.exists(existing_thumb) and os.path.getsize(existing_thumb) > 0:
        return existing_thumb
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

def trim_video_ffmpeg(input_path: str, start_str: str, end_str: str, output_path: str) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", start_str,
            "-to", end_str,
            "-i", input_path,
            "-c", "copy",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"FFmpeg trim error: {e}")
        return False

def split_video_file(file_path: str, task_id: str, target_size_bytes: int = 1900 * 1024 * 1024) -> List[str]:
    if not os.path.exists(file_path):
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
            "-c", "copy",
            part_out
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if os.path.exists(part_out) and os.path.getsize(part_out) > 0:
                parts.append(part_out)
        except Exception as e:
            logger.error(f"فشل تقسيم الفيديو عند الجزء {i+1}: {e}")

    return parts if parts else [file_path]

# ----------------------------------------------------
# 🌐 محرك MediaFire & Mega Direct Downloader
# ----------------------------------------------------
def inspect_mediafire_link(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
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
    file_path = os.path.join(out_dir, f"{task_id}_{file_name}")

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
            loop.call_soon_threadsafe(q.put_nowait, (label, downloaded, totalsize, start_time))

    urllib.request.urlretrieve(direct_url, file_path, reporthook=download_hook)
    
    is_doc = (target_option == "doc") or (not info["is_video"])
    
    return {
        "file_path": file_path,
        "title": file_name,
        "duration": 0,
        "thumb_path": None,
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
    new_path = os.path.join(out_dir, f"{task_id}_{filename}")
    os.rename(downloaded_path, new_path)

    return {
        "file_path": new_path,
        "title": filename,
        "duration": 0,
        "thumb_path": None,
        "is_audio": False,
        "is_document": True
    }

# ----------------------------------------------------
# 🧠 المحرك الشامل v36.0 (Dailymotion & Direct Downloads Fix)
# ----------------------------------------------------
class UniversalEngineV36:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ]

    def is_dailymotion_link(self, url: str) -> bool:
        """التعرف على روابط Dailymotion المباشرة والمختصرة"""
        return bool(re.search(r'dailymotion\.com|dai\.ly', url, re.IGNORECASE))

    def extract_info_only(self, url: str) -> Dict[str, Any]:
        """استخراج معلومات المقطع والدقات المتاحة بدقة"""
        ua = random.choice(self.user_agents)
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'skip_download': True,
            'user_agent': ua,
            'geo_bypass': True,
        }

        if self.is_dailymotion_link(url):
            ydl_opts.update({
                'http_headers': {
                    'User-Agent': ua,
                    'Referer': 'https://www.dailymotion.com/',
                    'Origin': 'https://www.dailymotion.com'
                },
                'extractor_args': {
                    'dailymotion': {
                        'geo_verification_network': 'http'
                    }
                }
            })
            if DM_COOKIE_PATH and os.path.exists(DM_COOKIE_PATH):
                ydl_opts['cookiefile'] = DM_COOKIE_PATH

        if "instagram.com" in url and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif ("twitter.com" in url or "x.com" in url) and TW_COOKIE_PATH and os.path.exists(TW_COOKIE_PATH):
            ydl_opts['cookiefile'] = TW_COOKIE_PATH
        elif "pornhub.com" in url and PH_COOKIE_PATH and os.path.exists(PH_COOKIE_PATH):
            ydl_opts['cookiefile'] = PH_COOKIE_PATH

        if HTTP_PROXY:
            ydl_opts['proxy'] = HTTP_PROXY

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

    def download_indirect_media(self, url: str, target_option: str, task_id: str, status_msg: Message, loop: asyncio.AbstractEventLoop) -> Dict[str, Any]:
        auto_disk_guard()
        
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        if "mediafire.com" in url.lower():
            return download_mediafire_file(url, target_option, task_id, loop)
        if "mega.nz" in url.lower() or "mega.co.nz" in url.lower():
            return download_mega_file(url, task_id)

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
                label = "استخراج الصوت MP3" if target_option == "mp3" else f"جاري التحميل المتوازي v36 ({target_option}p)"
                
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
                format_selector = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
            else:
                format_selector = f'bestvideo[height<={target_option}]+bestaudio/best[height<={target_option}]/bestvideo+bestaudio/best'

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
            'retries': 50,
            'fragment_retries': 50,
            'concurrent_fragment_downloads': 16,
            'skip_unavailable_fragments': True,
            'geo_bypass': True,
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            'legacyserverconnect': True,
        }

        # تكوين وتجهيز ديليموشن تلقائياً
        if self.is_dailymotion_link(url):
            ydl_opts['http_headers'].update({
                'Referer': 'https://www.dailymotion.com/',
                'Origin': 'https://www.dailymotion.com'
            })
            ydl_opts['extractor_args'] = {
                'dailymotion': {
                    'geo_verification_network': 'http'
                }
            }
            if DM_COOKIE_PATH and os.path.exists(DM_COOKIE_PATH):
                ydl_opts['cookiefile'] = DM_COOKIE_PATH

        if "facebook.com" in url.lower() or "fb.watch" in url.lower() or "fb.gg" in url.lower():
            url = url.replace("m.facebook.com", "www.facebook.com").replace("mbasic.facebook.com", "www.facebook.com")
            ydl_opts.update({
                'format': 'best',
                'check_formats': False,
                'extractor_args': {'facebook': {'skip': 'dash'}},
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0',
                    'Referer': 'https://www.facebook.com/',
                }
            })

        if "tiktok.com" in url:
            ydl_opts['extractor_args'] = {'tiktok': {'app_version': '1.0.0'}}
        
        if "instagram.com" in url and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif ("twitter.com" in url or "x.com" in url) and TW_COOKIE_PATH and os.path.exists(TW_COOKIE_PATH):
            ydl_opts['cookiefile'] = TW_COOKIE_PATH
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

            # التأكد من صحة مسار الملف المستخرج لتفادي أخطاء FileNotFound
            if not os.path.exists(final_file_path):
                if os.path.exists(filename):
                    final_file_path = filename
                else:
                    import glob
                    matched = glob.glob(f"{base}.*")
                    if matched:
                        final_file_path = matched[0]

            return {
                "file_path": final_file_path,
                "title": str(info.get('title', 'Media File')),
                "duration": safe_duration,
                "thumb_path": thumb_path,
                "is_audio": is_audio,
                "is_document": False
            }

engine = UniversalEngineV36()

# ----------------------------------------------------
# 🛠️ منشئ لوحة أزرار الجودة (Quality Keyboard Builder v36.0)
# ----------------------------------------------------
def build_quality_keyboard(req_id: str, resolutions: List[int] = None) -> InlineKeyboardMarkup:
    """بناء لوحة أزرار الجودة القياسية بدقة (1080p, 720p, 480p, 360p)"""
    target_qualities = [1080, 720, 480, 360]
    
    # فلترة أو توفير الجودات المستهدفة
    buttons = [
        [
            InlineKeyboardButton("🎬 1080p", callback_data=f"q_1080_{req_id}"),
            InlineKeyboardButton("🎬 720p", callback_data=f"q_720_{req_id}")
        ],
        [
            InlineKeyboardButton("🎬 480p", callback_data=f"q_480_{req_id}"),
            InlineKeyboardButton("🎬 360p", callback_data=f"q_360_{req_id}")
        ],
        [InlineKeyboardButton("✨ أفضل جودة متاحة (Auto)", callback_data=f"q_best_{req_id}")],
        [InlineKeyboardButton("🎵 تحميل صوت MP3 (320kbps)", callback_data=f"q_mp3_{req_id}")]
    ]

    return InlineKeyboardMarkup(buttons)

# ----------------------------------------------------
# 🛠️ مدير الواجهة والتقدم v36.0
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
            if now - last_update_time >= 2.5:
                diff = now - start_time
                if diff <= 0:
                    continue

                speed = current / diff
                
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
                    f"⚡ **[v36.0 Engine - Downloader & Dailymotion]**\n"
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
    import glob
    for f in glob.glob(f"downloads/{task_id}*") + glob.glob(f"downloads/thumb_{task_id}*"):
        try: os.remove(f)
        except Exception: pass
    PROGRESS_QUEUES.pop(task_id, None)

# ----------------------------------------------------
# 📡 الأحداث والأوامر
# ----------------------------------------------------
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🚀 **أهلاً بك في بوت v36.0 Universal Engine**\n\n"
        "✨ **تحديثات v36.0:**\n"
        "• 🎬 **عرض أزرار اختيار الجودة القياسية:** (1080p, 720p, 480p, 360p).\n"
        "• 🌐 **التعرف المباشر والتلقائي على روابط Dailymotion** واستخراج محتواها بمرونة.\n"
        "• 🛠️ **إصلاح كامل لدالة التنزيل**، ومعالجة أخطاء فقدان مسارات الملفات المؤقتة.\n"
    )

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

    task = asyncio.get_event_loop().create_task(
        process_url_trim_task(client, task_id, url, start_str, end_str, status_msg)
    )
    ACTIVE_TASKS[task_id] = task

async def process_url_trim_task(client: Client, task_id: str, url: str, start_str: str, end_str: str, status_msg: Message):
    loop = asyncio.get_event_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))

    try:
        file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, "best", task_id, status_msg, loop)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        downloaded_path = file_info["file_path"]
        out_dir = "downloads"
        trimmed_path = os.path.join(out_dir, f"{task_id}_trimmed.mp4")

        await status_msg.edit_text("✂️ **جاري قص واقتطاع المقطع عبر FFmpeg...**")
        
        success = await loop.run_in_executor(None, trim_video_ffmpeg, downloaded_path, start_str, end_str, trimmed_path)
        
        if not success:
            await status_msg.edit_text("❌ **فشل قص الفيديو! تأكد من إدخال صيغة وقت صحيحة.**")
            return

        parts = await loop.run_in_executor(None, split_video_file, trimmed_path, task_id)
        
        for idx, part_file in enumerate(parts):
            upload_start = time.time()

            def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED")
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    loop.call_soon_threadsafe(q.put_nowait, (f"رفع الجزء ({idx+1}/{len(parts)})", current, total, upload_start))

            duration = get_media_duration(part_file)
            width, height = get_video_dimensions(part_file)
            thumb_path = get_valid_thumbnail(part_file, task_id, file_info.get("thumb_path"), f"_{idx}")

            part_caption = f"✂️ **{file_info['title']} (مقصوص)**\n⏱️ **من:** `{start_str}` **إلى:** `{end_str}`\n🛡️ **Engine:** `v36.0 Fix Engine`"
            if len(parts) > 1:
                part_caption += f"\n📦 **الجزء ({idx+1}/{len(parts)})**"

            await client.send_video(
                chat_id=status_msg.chat.id,
                video=part_file,
                width=width if width > 0 else None,
                height=height if height > 0 else None,
                supports_streaming=True,
                thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                duration=duration,
                caption=part_caption,
                progress=upload_progress
            )

        if task_id not in CANCELLED_TASKS:
            await status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"URL Trim task cancelled cleanly: {task_id}")
    except Exception as e:
        if task_id not in CANCELLED_TASKS:
            logger.error(f"URL Trim Error: {e}")
            try:
                await status_msg.edit_text(f"❌ **حدث خطأ أثناء معالجة القص:**\n`{str(e)[:150]}`")
            except Exception:
                pass
    finally:
        worker_task.cancel()
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

# ----------------------------------------------------
# 📩 معالجة الرسائل والروابط ومدخلات القص v36.0
# ----------------------------------------------------
@app.on_message(filters.private & filters.text & ~filters.command(["start", "disk", "trim"]))
async def handle_message(client: Client, message: Message):
    text = message.text.strip()
    user_id = message.from_user.id

    match = re.search(r'(https?://[^\s]+)', text)
    if not match:
        return

    url = match.group(1)
    req_id = f"{message.from_user.id}_{int(time.time())}"
    PENDING_URLS[req_id] = url

    if "mediafire.com" in url.lower():
        try:
            mf_info = inspect_mediafire_link(url)
            filename = mf_info["file_name"]
            
            if mf_info["is_video"]:
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🎬 إرسال كـ فيديو", callback_data=f"q_vid_{req_id}"),
                        InlineKeyboardButton("📁 إرسال كـ مستند", callback_data=f"q_doc_{req_id}")
                    ]
                ])
                await message.reply_text(
                    f"📁 **MediaFire File:** `{filename}`\n\n"
                    f"💡 تم التعرف على الملف كفيديو. اختر طريقة الإرسال المناسبة:",
                    reply_markup=keyboard,
                    quote=True
                )
            else:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📁 تنزيل الملف (مستند)", callback_data=f"q_doc_{req_id}")]
                ])
                await message.reply_text(
                    f"📁 **MediaFire File:** `{filename}`\n\n"
                    f"⚡ نوع الملف: `{mf_info['ext'].upper()}` - سيتم تنزيله وإرساله كمستند كما هو.",
                    reply_markup=keyboard,
                    quote=True
                )
            return
        except Exception as e:
            logger.error(f"Error inspecting mediafire: {e}")

    if "mega.nz" in url.lower() or "mega.co.nz" in url.lower():
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 تنزيل الملف المباشر", callback_data=f"q_doc_{req_id}")]
        ])
        await message.reply_text("📁 **روابط ملفات ميجا التلقائية:**", reply_markup=keyboard, quote=True)
        return

    loading_msg = await message.reply_text("🔍 **جاري فحص الرابط واستخراج معلومات الفيديو...**", quote=True)
    loop = asyncio.get_event_loop()

    try:
        info = await loop.run_in_executor(None, engine.extract_info_only, url)
        title = info.get("title", "فيديو بدون عنوان")
        duration_str = format_seconds(info.get("duration", 0))
        uploader = info.get("uploader", "غير معروف")

        info_text = (
            f"🎬 **معلومات الفيديو المحدد:**\n\n"
            f"📌 **العنوان:** `{title}`\n"
            f"⏱️ **المدة الزمانية:** `{duration_str}`\n"
            f"👤 **المصدر/القناة:** `{uploader}`\n\n"
            f"👇 **اختر الجودة المطلوبة للبدء:**"
        )

        keyboard = build_quality_keyboard(req_id)
        await loading_msg.edit_text(info_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error fetching video info: {e}")
        keyboard = build_quality_keyboard(req_id)
        await loading_msg.edit_text("🌐 **تم التعرّف على الرابط!**\nاختر الجودة المطلوبة للبدء:", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^q_"))
async def option_callback_handler(client: Client, callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        option = parts[1]
        req_id = f"{parts[2]}_{parts[3]}"

        url = PENDING_URLS.pop(req_id, None)
        if not url:
            await callback.answer("⚠️ انتهت صلاحية الطلب، يرجى إعادة إرسال الرابط.", show_alert=True)
            return

        msg_text = "📁 **جاري جلب وتحميل الملف...**" if option in ["doc", "vid"] else ("🎵 **جاري استخراج الصوت MP3...**" if option == "mp3" else f"🔎 **جاري التنزيل المتوازي ({option}p)...**")
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
        
        task = ACTIVE_TASKS.get(task_id)
        if task and not task.done():
            task.cancel()
            
        cleanup_files(task_id)
        await callback.answer("🛑 تم إلغاء العملية!", show_alert=True)
        await callback.message.edit_text("❌ **تم إلغاء عملية التحميل.**")
    except Exception as e:
        logger.error(f"Cancel Handler Error: {e}")

async def process_task(client: Client, task_id: str, url: str, option: str, init_status_msg: Message):
    loop = asyncio.get_event_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, init_status_msg))

    try:
        file_info = await loop.run_in_executor(None, engine.download_indirect_media, url, option, task_id, init_status_msg, loop)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        file_path = file_info["file_path"]
        
        # فحص إضافي وإصلاح خطأ اختفاء أو عدم وجود المسار
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"تعذر العثور على الملف المحمل: {file_path}")

        duration = int(file_info.get("duration", 0))
        is_audio = file_info.get("is_audio", False)
        is_document = file_info.get("is_document", False)

        parts = await loop.run_in_executor(None, split_video_file, file_path, task_id)

        for idx, part_file in enumerate(parts):
            upload_start = time.time()

            def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED")
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    label = f"رفع المستند ({idx+1}/{len(parts)})" if is_document else (f"رفع الصوت MP3 ({idx+1}/{len(parts)})" if is_audio else f"رفع الفيديو ({idx+1}/{len(parts)})")
                    loop.call_soon_threadsafe(q.put_nowait, (label, current, total, upload_start))

            part_duration = get_media_duration(part_file) if len(parts) > 1 else duration
            width, height = get_video_dimensions(part_file)
            part_suffix = f"\n📦 **الجزء ({idx+1}/{len(parts)})**" if len(parts) > 1 else ""

            if is_document:
                await client.send_document(
                    chat_id=init_status_msg.chat.id,
                    document=part_file,
                    caption=f"📁 **{file_info['title']}**{part_suffix}\n🛡️ **Engine:** `v36.0 Fix Engine`",
                    progress=upload_progress
                )
            elif is_audio:
                if part_duration <= 0: part_duration = get_media_duration(part_file)
                await client.send_audio(
                    chat_id=init_status_msg.chat.id,
                    audio=part_file,
                    duration=int(part_duration),
                    title=str(file_info['title']),
                    caption=f"🎵 **{file_info['title']}**{part_suffix}\n🎼 **الصيغة:** `MP3 320kbps`\n🛡️ **Engine:** `v36.0 Fix Engine`",
                    progress=upload_progress
                )
            else:
                if part_duration <= 0: part_duration = get_media_duration(part_file)
                part_thumb = get_valid_thumbnail(part_file, task_id, file_info.get("thumb_path"), f"_{idx}")

                await client.send_video(
                    chat_id=init_status_msg.chat.id,
                    video=part_file,
                    width=width if width > 0 else None,
                    height=height if height > 0 else None,
                    supports_streaming=True,
                    thumb=part_thumb if (part_thumb and os.path.exists(part_thumb)) else None,
                    duration=int(part_duration),
                    caption=f"🎬 **{file_info['title']}**{part_suffix}\n🛡️ **Engine:** `v36.0 Fix Engine`",
                    progress=upload_progress
                )
        
        if task_id not in CANCELLED_TASKS:
            await init_status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Task cancelled cleanly: {task_id}")
    except Exception as e:
        if task_id not in CANCELLED_TASKS:
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

    logger.info("🚀 جاري تشغيل بوت v36.0 (Dailymotion support & Standard Quality Keyboard)...")
    app.run()
