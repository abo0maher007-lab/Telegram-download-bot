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
from typing import Optional, Dict, Any
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait, RPCError, MessageNotModified
import yt_dlp

# ----------------------------------------------------
# 🚂 إعداد التسجيل والمحيط - v29.0 Engine
# ----------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(message)s")
logger = logging.getLogger("UniversalBot_v29_0")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORNHUB_COOKIES_BASE64 = os.environ.get("PORNHUB_COOKIES_BASE64")
INSTAGRAM_COOKIES_BASE64 = os.environ.get("INSTAGRAM_COOKIES_BASE64")
TWITTER_COOKIES_BASE64 = os.environ.get("TWITTER_COOKIES_BASE64") or os.environ.get("X_COOKIES_BASE64")
YOUTUBE_COOKIES_BASE64 = os.environ.get("YOUTUBE_COOKIES_BASE64")
HTTP_PROXY = os.environ.get("HTTP_PROXY") 

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: لم يتم العثور على API_ID أو API_HASH أو BOT_TOKEN في متغيرات البيئة!")
    exit(1)

app = Client("UniversalDownloaderBot_v29_0", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()
PENDING_URLS = {}
AWAITING_TRIM_INPUT = {}
PROGRESS_QUEUES = {}

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
# 🍪 إدارة الكوكيز v29
# ----------------------------------------------------
PH_COOKIES_PATH = "ph_cookies.txt"
IG_COOKIES_PATH = "ig_cookies.txt"
TW_COOKIES_PATH = "tw_cookies.txt"
YT_COOKIES_PATH = "yt_cookies.txt"

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
YT_COOKIE_PATH = setup_cookies("YOUTUBE_COOKIES_BASE64", YT_COOKIES_PATH)

# ----------------------------------------------------
# 🖼️ أدوات الثمبنيل والمدة والقص v29
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

def convert_video_to_mp3(input_path: str, output_path: str) -> bool:
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "320k",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"FFmpeg MP3 conversion error: {e}")
        return False

# ----------------------------------------------------
# 🌐 محرك MediaFire Direct Downloader
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

# ----------------------------------------------------
# ☁️ محرك Mega.nz Direct Downloader
# ----------------------------------------------------
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
# 🧠 المحرك الشامل v29.0 (المحدث لتخطّي حظر يوتيوب واستخراج أعلى ترميز)
# ----------------------------------------------------
class UniversalEngineV29:
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ]

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
                label = "استخراج الصوت MP3 (أعلى جودة)" if target_option == "mp3" else f"جاري التحميل ({target_option}p)"
                
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
            'retries': 50,
            'fragment_retries': 50,
            'skip_unavailable_fragments': True,
            'geo_bypass': True,
            'audioquality': 0,
            # التجاوز الذكي لحماية يوتيوب واختيار الخوادم بدون حظر
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web'],
                    'skip': ['hls', 'dash']
                }
            },
            'http_headers': {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            'legacyserverconnect': True,
        }

        # استخدام الكوكيز المناسبة لكل منصة
        if ("youtube.com" in url or "youtu.be" in url) and YT_COOKIE_PATH and os.path.exists(YT_COOKIE_PATH):
            ydl_opts['cookiefile'] = YT_COOKIE_PATH
        elif "instagram.com" in url and IG_COOKIE_PATH and os.path.exists(IG_COOKIE_PATH):
            ydl_opts['cookiefile'] = IG_COOKIE_PATH
        elif ("twitter.com" in url or "x.com" in url) and TW_COOKIE_PATH and os.path.exists(TW_COOKIE_PATH):
            ydl_opts['cookiefile'] = TW_COOKIE_PATH
        elif "pornhub.com" in url and PH_COOKIE_PATH and os.path.exists(PH_COOKIE_PATH):
            ydl_opts['cookiefile'] = PH_COOKIE_PATH

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

        if "aznude" in url.lower():
            ydl_opts.update({
                'format': 'best',
                'extract_flat': False,
                'allow_unplayable_formats': True,
                'check_formats': False,
                'referer': url,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    'Referer': 'https://www.aznude.com/',
                    'Origin': 'https://www.aznude.com',
                },
                'legacy_server_connect': True,
                'source_address': '0.0.0.0'
            })

        if "tiktok.com" in url:
            ydl_opts['extractor_args'] = {'tiktok': {'app_version': '1.0.0'}}

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
                "is_audio": is_audio,
                "is_document": False
            }

engine = UniversalEngineV29()

# ----------------------------------------------------
# 🛠️ مدير الواجهة والتقدم
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
                    f"⚡ **[v29.0 Universal Engine]**\n"
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
        "🚀 **أهلاً بك في بوت v29.0 Universal Engine**\n\n"
        "✨ **الجديد في v29.0:**\n"
        "• استخدام الأمر المباشر `/trim [البداية] [النهاية] [الرابط]` لقص الفيديو مباشرة قبل التنزيل والرفع!\n"
        "• حل مشكلة انتهت صلاحية الطلب نهائياً.\n"
        "• دعم قص وتعديل الفيديوهات المحولة للبوت.\n"
        "• تنزيل ملفات حتى **2 جيجابايت**.\n\n"
        "💡 **مثال لاستخدام القص:**\n"
        "`/trim 00:10 01:30 https://example.com/video.mp4`"
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
            "`/trim 00:10 01:30 https://example.com/video.mp4`\n"
            "`/trim 00:00:10 00:01:20 https://youtube.com/watch?v=xxxx`",
            quote=True
        )
        return

    start_str = args[1]
    end_str = args[2]
    url = args[3]

    if not re.match(r'^https?://', url):
        await message.reply_text("❌ **الرابط غير صالحة، يرجى كتابة رابط مباشر صحيح.**", quote=True)
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
            await status_msg.edit_text("❌ **فشل قص الفيديو! تأكد من إدخال صيغة وقت صحيحة (مثال: 00:10 01:30)**")
            return

        upload_start = time.time()

        def upload_progress(current, total):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            q = PROGRESS_QUEUES.get(task_id)
            if q:
                loop.call_soon_threadsafe(q.put_nowait, ("رفع الفيديو المقصوص", current, total, upload_start))

        duration = get_media_duration(trimmed_path)
        thumb_path = generate_ffmpeg_thumbnail(trimmed_path, task_id)

        await client.send_video(
            chat_id=status_msg.chat.id,
            video=trimmed_path,
            thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
            duration=duration,
            caption=f"✂️ **{file_info['title']} (مقصوص)**\n⏱️ **من:** `{start_str}` **إلى:** `{end_str}`\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
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

def build_disk_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧹 تنظيف القرص الفوري", callback_data="disk_purge"),
            InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="disk_refresh")
        ],
        [
            InlineKeyboardButton("🚫 إلغاء كافة المهام الحالية", callback_data="disk_clear_tasks")
        ]
    ])

@app.on_message(filters.command("disk") & filters.private)
async def disk_command(client: Client, message: Message):
    if ADMIN_ID != 0 and message.from_user.id != ADMIN_ID:
        await message.reply_text("⚠️ هذا الأمر مخصص لمدير البوت فقط.")
        return

    info = get_disk_info()
    text = (
        "💾 **لوحة التحكم في قرص التحميلات:**\n\n"
        f"📊 **حجم مجلد التحميلات:** `{info['downloads_mb']:.2f} MB`\n"
        f"💿 **المساحة المتاحة للقرص:** `{info['free_gb']:.2f} GB`\n"
        f"🖥️ **إجمالي مساحة القرص:** `{info['total_gb']:.2f} GB`\n"
        f"⚙️ **العمليات النشطة حالياً:** `{len(ACTIVE_TASKS)}`"
    )
    await message.reply_text(text, reply_markup=build_disk_keyboard())

@app.on_callback_query(filters.regex(r"^disk_"))
async def disk_callback_handler(client: Client, callback: CallbackQuery):
    action = callback.data
    
    if action == "disk_refresh":
        info = get_disk_info()
        text = (
            "💾 **لوحة التحكم في قرص التحميلات (محدث):**\n\n"
            f"📊 **حجم مجلد التحميلات:** `{info['downloads_mb']:.2f} MB`\n"
            f"💿 **المساحة المتاحة للقرص:** `{info['free_gb']:.2f} GB`\n"
            f"🖥️ **إجمالي مساحة القرص:** `{info['total_gb']:.2f} GB`\n"
            f"⚙️ **العمليات النشطة حالياً:** `{len(ACTIVE_TASKS)}`"
        )
        try:
            await callback.message.edit_text(text, reply_markup=build_disk_keyboard())
            await callback.answer("✅ تم تحديث البيانات")
        except MessageNotModified:
            await callback.answer("⚠️ البيانات محدثة بالفعل")

    elif action == "disk_purge":
        deleted = purge_downloads_folder()
        await callback.answer(f"🧹 تم تنظيف القرص وحذف {deleted} ملف/مجلد", show_alert=True)
        info = get_disk_info()
        text = (
            "💾 **لوحة التحكم في قرص التحميلات (بعد التنظيف):**\n\n"
            f"📊 **حجم مجلد التحميلات:** `{info['downloads_mb']:.2f} MB`\n"
            f"💿 **المساحة المتاحة للقرص:** `{info['free_gb']:.2f} GB`\n"
            f"🖥️ **إجمالي مساحة القرص:** `{info['total_gb']:.2f} GB`\n"
            f"⚙️ **العمليات النشطة حالياً:** `{len(ACTIVE_TASKS)}`"
        )
        await callback.message.edit_text(text, reply_markup=build_disk_keyboard())

    elif action == "disk_clear_tasks":
        count = len(ACTIVE_TASKS)
        for t_id, task in list(ACTIVE_TASKS.items()):
            CANCELLED_TASKS.add(t_id)
            if task and not task.done():
                task.cancel()
            cleanup_files(t_id)
        ACTIVE_TASKS.clear()
        PENDING_URLS.clear()
        AWAITING_TRIM_INPUT.clear()
        await callback.answer(f"🛑 تم إيقاف {count} مهمة وإلغاؤها", show_alert=True)

# ----------------------------------------------------
# 📩 معالجة الفيديو الموجه والملفات (Stateless Forward Engine v29)
# ----------------------------------------------------
@app.on_message(filters.private & (filters.video | filters.document))
async def handle_video_or_document(client: Client, message: Message):
    is_vid = False
    file_size = 0
    if message.video:
        is_vid = True
        file_size = message.video.file_size
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("video/"):
        is_vid = True
        file_size = message.document.file_size

    if not is_vid:
        return

    if file_size > 2 * 1024 * 1024 * 1024:
        await message.reply_text("⚠️ حجم الفيديو يتجاوز الحد المسموح (2 جيجابايت).", quote=True)
        return

    c_id = message.chat.id
    m_id = message.id

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 إرسال كـ فيديو", callback_data=f"cv_vid_{c_id}_{m_id}"),
            InlineKeyboardButton("🎵 تحويل إلى MP3", callback_data=f"cv_mp3_{c_id}_{m_id}"),
        ],
        [
            InlineKeyboardButton("📁 إرسال كـ مستند", callback_data=f"cv_doc_{c_id}_{m_id}"),
            InlineKeyboardButton("✂️ قص وتعديل الفيديو", callback_data=f"cv_trm_{c_id}_{m_id}")
        ]
    ])

    await message.reply_text(
        "🎬 **تم استلام الفيديو بنجاح!**\nاختر العملية أو التعديل المطلوبة:",
        reply_markup=keyboard,
        quote=True
    )

@app.on_callback_query(filters.regex(r"^cv_"))
async def conversion_callback_handler(client: Client, callback: CallbackQuery):
    try:
        parts = callback.data.split("_")
        mode = parts[1]
        chat_id = int(parts[2])
        msg_id = int(parts[3])

        try:
            target_msg = await client.get_messages(chat_id, msg_id)
            if not target_msg or target_msg.empty:
                raise Exception("Empty message")
        except Exception:
            await callback.answer("⚠️ تعذر الوصول للرسالة الأصلية، قد تكون حُذفت من المحادثة.", show_alert=True)
            return

        if mode == "trm":
            await callback.answer()
            user_id = callback.from_user.id
            AWAITING_TRIM_INPUT[user_id] = {
                "chat_id": chat_id,
                "msg_id": msg_id,
                "status_msg": callback.message
            }
            await callback.message.edit_text(
                "✂️ **إعداد قص الفيديو:**\n\n"
                "أرسل أوقات القص بالشكل التالي (البداية والنهاية):\n"
                "`MM:SS MM:SS` (مثال: `00:10 01:30`)\n"
                "أو أرسل `HH:MM:SS HH:MM:SS` للمقاطع الطويلة."
            )
            return

        await callback.answer()
        status_msg = await callback.message.edit_text("⏳ **جاري معالجة وتحويل الفيديو...**")
        
        task_id = f"cv_{chat_id}_{msg_id}_{int(time.time())}"
        PROGRESS_QUEUES[task_id] = asyncio.Queue()

        task = asyncio.get_event_loop().create_task(process_video_conversion(client, task_id, target_msg, mode, status_msg))
        ACTIVE_TASKS[task_id] = task

    except Exception as e:
        logger.error(f"Conversion Callback Error: {e}")

async def process_video_conversion(client: Client, task_id: str, target_msg: Message, mode: str, status_msg: Message, trim_times: Optional[tuple] = None):
    loop = asyncio.get_event_loop()
    worker_task = asyncio.create_task(progress_ui_worker(task_id, status_msg))
    out_dir = "downloads"
    os.makedirs(out_dir, exist_ok=True)
    download_path = os.path.join(out_dir, f"{task_id}_input.mp4")

    try:
        start_dl = time.time()
        
        def dl_progress(current, total):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            q = PROGRESS_QUEUES.get(task_id)
            if q:
                loop.call_soon_threadsafe(q.put_nowait, ("تنزيل الفيديو المعالج", current, total, start_dl))

        await client.download_media(message=target_msg, file_name=download_path, progress=dl_progress)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED")

        final_file = download_path
        is_audio = False
        is_doc = False

        if trim_times:
            start_str, end_str = trim_times
            trimmed_path = os.path.join(out_dir, f"{task_id}_trimmed.mp4")
            await status_msg.edit_text("✂️ **جاري قص وتعديل الفيديو عبر FFmpeg...**")
            success = await loop.run_in_executor(None, trim_video_ffmpeg, download_path, start_str, end_str, trimmed_path)
            if success:
                final_file = trimmed_path
            else:
                await status_msg.edit_text("❌ **فشل قص الفيديو! يرجى التأكد من كتابة الوقت بشكل صحيح.**")
                return

        if mode == "mp3":
            mp3_path = os.path.join(out_dir, f"{task_id}_audio.mp3")
            await status_msg.edit_text("🎵 **جاري استخراج وتحويل الصوت إلى MP3...**")
            success = await loop.run_in_executor(None, convert_video_to_mp3, final_file, mp3_path)
            if success:
                final_file = mp3_path
                is_audio = True
            else:
                await status_msg.edit_text("❌ **فشل استخراج الصوت من الفيديو.**")
                return
        elif mode == "doc":
            is_doc = True

        upload_start = time.time()

        def upload_progress(current, total):
            if task_id in CANCELLED_TASKS:
                raise ProcessCancelledException("CANCELLED")
            q = PROGRESS_QUEUES.get(task_id)
            if q:
                label = "رفع المستند" if is_doc else ("رفع الصوت MP3" if is_audio else "رفع الفيديو")
                loop.call_soon_threadsafe(q.put_nowait, (label, current, total, upload_start))

        duration = get_media_duration(final_file)
        file_title = os.path.basename(final_file)

        if is_doc:
            await client.send_document(
                chat_id=status_msg.chat.id,
                document=final_file,
                caption=f"📁 **{file_title}**\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
                progress=upload_progress
            )
        elif is_audio:
            await client.send_audio(
                chat_id=status_msg.chat.id,
                audio=final_file,
                duration=duration,
                caption=f"🎵 **{file_title}**\n🎼 **الصيغة:** `MP3 320kbps`\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
                progress=upload_progress
            )
        else:
            thumb_path = generate_ffmpeg_thumbnail(final_file, task_id)
            await client.send_video(
                chat_id=status_msg.chat.id,
                video=final_file,
                thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                duration=duration,
                caption=f"🎬 **{file_title}**\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
                progress=upload_progress
            )

        if task_id not in CANCELLED_TASKS:
            await status_msg.delete()

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"Conversion task cancelled cleanly: {task_id}")
    except Exception as e:
        if task_id not in CANCELLED_TASKS:
            logger.error(f"Conversion Error: {e}")
            try:
                await status_msg.edit_text(f"❌ **حدث خطأ أثناء المعالجة:**\n`{str(e)[:150]}`")
            except Exception:
                pass
    finally:
        worker_task.cancel()
        cleanup_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

# ----------------------------------------------------
# 📩 معالجة الرسائل والروابط ومدخلات القص
# ----------------------------------------------------
@app.on_message(filters.private & filters.text & ~filters.command(["start", "disk", "trim"]))
async def handle_message(client: Client, message: Message):
    text = message.text.strip()
    user_id = message.from_user.id

    if user_id in AWAITING_TRIM_INPUT:
        data = AWAITING_TRIM_INPUT.pop(user_id)
        chat_id = data["chat_id"]
        msg_id = data["msg_id"]

        times = text.split()
        if len(times) == 2:
            start_time_str, end_time_str = times[0], times[1]
            try:
                target_msg = await client.get_messages(chat_id, msg_id)
                if target_msg and not target_msg.empty:
                    task_id = f"cv_{chat_id}_{msg_id}_{int(time.time())}"
                    PROGRESS_QUEUES[task_id] = asyncio.Queue()
                    edit_msg = await message.reply_text("✂️ **جاري البدء في عملية القص والتحويل...**", quote=True)
                    task = asyncio.get_event_loop().create_task(
                        process_video_conversion(client, task_id, target_msg, "vid", edit_msg, trim_times=(start_time_str, end_time_str))
                    )
                    ACTIVE_TASKS[task_id] = task
                    return
            except Exception as e:
                logger.error(f"Error fetching target msg for trim: {e}")
                await message.reply_text("❌ تعذر الوصول للفيديو الأصلي.", quote=True)
                return
        else:
            await message.reply_text("⚠️ تنسيق أوقات القص غير صحيح! أرسل مثل: `00:10 01:30`", quote=True)
            return

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
            InlineKeyboardButton("🎵 تحميل صوت MP3 (أعلى جودة 320kbps)", callback_data=f"q_mp3_{req_id}")
        ]
    ])

    await message.reply_text(
        "🌐 **تم التعرّف على الرابط!**\nاختر الجودة المطلوبة للبدء:",
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
            await callback.answer("⚠️ انتهت صلاحية الطلب، يرجى إعادة إرسال الرابط.", show_alert=True)
            return

        msg_text = "📁 **جاري جلب وتحميل الملف...**" if option in ["doc", "vid"] else ("🎵 **جاري استخراج الصوت بأعلى جودة MP3...**" if option == "mp3" else f"🔎 **جاري التنزيل ({option}p)...**")
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
        thumb_path = file_info.get("thumb_path")
        duration = int(file_info.get("duration", 0))
        is_audio = file_info.get("is_audio", False)
        is_document = file_info.get("is_document", False)

        if os.path.exists(file_path):
            upload_start = time.time()

            def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED")
                q = PROGRESS_QUEUES.get(task_id)
                if q:
                    label = "رفع الملف المستند" if is_document else ("رفع الملف الصوتي MP3" if is_audio else "رفع الفيديو")
                    loop.call_soon_threadsafe(q.put_nowait, (label, current, total, upload_start))

            if is_document:
                await client.send_document(
                    chat_id=init_status_msg.chat.id,
                    document=file_path,
                    caption=f"📁 **{file_info['title']}**\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
                    progress=upload_progress
                )
            elif is_audio:
                if duration <= 0: duration = get_media_duration(file_path)
                await client.send_audio(
                    chat_id=init_status_msg.chat.id,
                    audio=file_path,
                    duration=int(duration),
                    title=str(file_info['title']),
                    caption=f"🎵 **{file_info['title']}**\n🎼 **الصيغة:** `MP3 320kbps (HQ)`\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
                    progress=upload_progress
                )
            else:
                if duration <= 0: duration = get_media_duration(file_path)
                if not thumb_path or not os.path.exists(thumb_path):
                    thumb_path = generate_ffmpeg_thumbnail(file_path, task_id)

                await client.send_video(
                    chat_id=init_status_msg.chat.id,
                    video=file_path,
                    thumb=thumb_path if (thumb_path and os.path.exists(thumb_path)) else None,
                    duration=int(duration),
                    caption=f"🎬 **{file_info['title']}**\n🛡️ **Engine:** `v29.0 Hybrid Edition`",
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

    logger.info("🚀 جاري تشغيل بوت v29.0 Universal Engine...")
    app.run()
