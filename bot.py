import os
import re
import glob
import time
import asyncio
import logging
import subprocess
import requests
import random
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DownloaderBot-v10")

# ----------------------------------------------------
# 🚂 الإعداد والمتغيرات - v10 Titan
# ----------------------------------------------------
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PROXY_URL = os.environ.get("PROXY_URL")

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: المتغيرات الأساسية (API_ID, API_HASH, BOT_TOKEN) غير معرفة!")
    exit(1)

app = Client("SmartDownloaderBot_v10", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

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
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.108 Mobile/15E148 Safari/604.1"
]

# ----------------------------------------------------
# 🛠️ أدوات التنظيف والواجهة
# ----------------------------------------------------

def cleanup_temp_files(task_id: str):
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
        InlineKeyboardButton("❌ إلغاء العملية (v10 Titan)", callback_data=f"cancel_{task_id}")
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
        f"🛡️ **[v10 Titan Engine] {status_text}**\n\n"
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
# 🔍 محرك استخراج السيرفرات وميديافاير وجوجل درايف
# ----------------------------------------------------

def extract_gdrive_id(url: str) -> str:
    """استخراج File ID لروابط Google Drive"""
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if 'id' in qs:
        return qs['id'][0]
    return None

def extract_direct_url_from_host(url: str) -> tuple[str, str, dict]:
    """استخراج رابط التحميل المباشر واسم المصدر وهيدرات الجلسة"""
    session = requests.Session()
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    if PROXY_URL:
        session.proxies = {"http": PROXY_URL, "https": PROXY_URL}

    domain = urlparse(url).netloc.lower()

    try:
        # 1. MediaFire Engine
        if "mediafire.com" in domain:
            res = session.get(url, headers=headers, timeout=20)
            soup = BeautifulSoup(res.text, "html.parser")
            download_btn = soup.find("a", {"id": "downloadButton"}) or soup.find("a", {"aria-label": "Download file"})
            if download_btn and "href" in download_btn.attrs:
                return download_btn["href"], "MediaFire", {}

        # 2. Google Drive Engine
        elif "drive.google.com" in domain or "docs.google.com" in domain:
            file_id = extract_gdrive_id(url)
            if file_id:
                confirm_url = f"https://docs.google.com/uc?export=download&id={file_id}"
                res = session.get(confirm_url, headers=headers, cookies=session.cookies)
                
                # فحص ما إذا كان الملف يتطلب تأكيد الحجم المباشر (Large File Warning)
                confirm_token = None
                for k, v in session.cookies.items():
                    if k.startswith('download_warning'):
                        confirm_token = v
                        break
                
                if not confirm_token:
                    # البحث عن كود التأكيد داخل HTML
                    match = re.search(r'confirm=([a-zA-Z0-9_-]+)', res.text)
                    if match:
                        confirm_token = match.group(1)

                if confirm_token:
                    direct_gdrive_url = f"https://docs.google.com/uc?export=download&confirm={confirm_token}&id={file_id}"
                else:
                    direct_gdrive_url = confirm_url

                return direct_gdrive_url, "Google Drive", session.cookies.get_dict()

        # 3. 1Fichier
        elif "1fichier.com" in domain:
            res = session.post(url, headers=headers, data={"dl_no_ssl": "on"})
            soup = BeautifulSoup(res.text, "html.parser")
            btn = soup.find("a", {"class": "btn-general"})
            if btn and "href" in btn.attrs:
                return btn["href"], "1Fichier", {}

        # 4. MegaUp
        elif "megaup.net" in domain:
            res = session.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
            btn = soup.find("a", {"class": "btn btn-default"})
            if btn and "href" in btn.attrs:
                return btn["href"], "MegaUp", {}

        # 5. السيرفرات السحابية المتنوعة (Send, VikingFile, KoramaUp, BowFile, 1Cloudfile)
        elif any(h in domain for h in ["1cloudfile.com", "bowfile.com", "vikingfile.com", "koramaup.com", "send.cm"]):
            res = session.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if any(ext in href.lower() for ext in [".mp4", ".mkv", ".zip", ".rar", ".avi", ".mov", ".pdf"]):
                    return href, f"{domain}", {}
            
            btn = soup.find("a", {"id": "downloadbtn"}) or soup.find("a", {"class": "downloadbtn"})
            if btn and "href" in btn.attrs:
                return btn["href"], f"{domain}", {}

    except Exception as e:
        logger.warning(f"فشل استخراج الهوست المباشر لـ {domain}: {e}")

    return url, "Standard Stream/URL", {}

# ----------------------------------------------------
# 🧠 محرك الاستخراج الشامل - v10 Titan Engine
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

async def direct_file_downloader(direct_url: str, status_msg: Message, task_id: str, extra_cookies: dict = None):
    """تحميل الملفات المباشرة (مثل MediaFire, Google Drive وغيرها) عبر Chunk Downloader"""
    loop = asyncio.get_event_loop()
    out_path = f"downloads/{task_id}_file"
    
    def download_thread():
        session = requests.Session()
        if PROXY_URL:
            session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
        if extra_cookies:
            session.cookies.update(extra_cookies)
            
        req = session.get(direct_url, stream=True, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=30)
        total_size = int(req.headers.get('content-length', 0))
        
        # استخراج امتداد واسم الملف المباشر من Header
        cd = req.headers.get('content-disposition')
        ext = ".bin"
        file_name_from_header = "Downloaded_File"
        
        if cd and 'filename=' in cd:
            fname = cd.split('filename=')[1].strip('"\'')
            file_name_from_header = fname
            ext = os.path.splitext(fname)[1]
            if not ext: ext = ".bin"
        else:
            # محاولة استخراج الاسم من URL
            parsed_path = urlparse(direct_url).path
            if "." in parsed_path.split("/")[-1]:
                ext = "." + parsed_path.split("/")[-1].split(".")[-1]
                file_name_from_header = parsed_path.split("/")[-1]

        final_file_path = out_path + ext
        downloaded = 0
        start_time = time.time()
        last_update = [0]

        with open(final_file_path, 'wb') as f:
            for chunk in req.iter_content(chunk_size=1024*1024):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED_BY_USER")
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0 and (time.time() - last_update[0] > 2):
                        last_update[0] = time.time()
                        asyncio.run_coroutine_threadsafe(
                            update_status_ui(status_msg, "تنزيل الملف المباشر", downloaded, total_size, start_time, task_id),
                            loop
                        )
        return final_file_path, file_name_from_header

    try:
        file_path, file_title = await loop.run_in_executor(None, download_thread)
        if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
            return file_path, file_title, 0, "🛡️ Titan Direct Downloader"
    except Exception as e:
        logger.error(f"خطأ التنزيل المباشر v10: {e}")
    return None, None, 0, None

async def smart_extractor_v10(url: str, status_msg: Message, task_id: str):
    loop = asyncio.get_event_loop()

    # 1. تحليل وفك شفرة رابط الاستضافة المباشر (Google Drive / MediaFire / Hosts)
    await update_status_ui(status_msg, "تحليل خادم الاستضافة والرابط المباشر...", 0, 100, time.time(), task_id)
    target_url, host_source, req_cookies = await loop.run_in_executor(None, extract_direct_url_from_host, url)

    # إذا كان المصدر MediaFire أو Google Drive أو أحد خوادم التحميل المباشر المنفصلة
    if host_source in ["MediaFire", "Google Drive"] or target_url != url:
        await update_status_ui(status_msg, f"تنزيل مباشرة من {host_source}...", 0, 100, time.time(), task_id)
        f_path, f_title, f_dur, f_method = await direct_file_downloader(target_url, status_msg, task_id, req_cookies)
        if f_path and os.path.exists(f_path):
            return f_path, f_title, f_dur, f"🛡️ {host_source} Titan Engine", None

    # 2. محركات yt-dlp التكيفية للفيديو والبث
    thumb_opts = {
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}]
    }

    strategies = [
        ({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": f"downloads/{task_id}_s1.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True,
            "headers": {"User-Agent": USER_AGENTS[0]},
            **thumb_opts
        }, f"1/10: Titan Main Engine ({host_source})"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s2.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "force_generic_extractor": True,
            **thumb_opts
        }, "2/10: Generic Direct Host Extractor"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s3.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": USER_AGENTS[1],
            "referer": url,
            **thumb_opts
        }, "3/10: Referer Spoof Mode")
    ]

    last_error_log = ""

    for idx, (st_opts, st_name) in enumerate(strategies, 1):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")
        try:
            opts = make_ytdl_opts(st_opts, status_msg, loop, task_id, f"تحميل الميديا ({idx})")
            def run_yt(t_url):
                with YoutubeDL(opts) as ytdl:
                    info = ytdl.extract_info(t_url, download=True)
                    if info:
                        if "entries" in info and len(info["entries"]) > 0:
                            info = info["entries"][0]
                        return ytdl.prepare_filename(info), info.get("title", "File"), info.get("duration", 0)
                    return None, None, 0

            file_path, title, duration = await loop.run_in_executor(None, run_yt, target_url)
            if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
                return file_path, title, duration, st_name, None
        except ProcessCancelledException:
            raise
        except Exception as e:
            last_error_log = str(e)
            logger.warning(f"محاولة v10 رقم {idx} لم تنجح: {e}")
            continue

    return None, None, 0, None, last_error_log

# ----------------------------------------------------
# 📡 الأحداث والرسائل
# ----------------------------------------------------

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    CANCELLED_TASKS.add(task_id)
    
    if task_id in ACTIVE_TASKS:
        task = ACTIVE_TASKS[task_id]
        if not task.done():
            task.cancel()

    await callback.answer("🛑 تم إلغاء العملية وتنظيف الذاكرة!", show_alert=True)
    cleanup_temp_files(task_id)
    try:
        await callback.message.edit_text("❌ **تم إلغاء العملية.**")
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🛡️ **أهلاً بك في الإصدار v10 Titan**\n\n"
        "✅ **السيرفرات والمنصات المدعومة:**\n"
        "• **MediaFire (ميديا فاير)**\n"
        "• **Google Drive (جوجل درايف)**\n"
        "• MegaUp | Send | Mixdrop | 1Fichier\n"
        "• VikingFile | KoramaUp | 1Cloudfile | BowFile\n"
        "• DoodStream | EarnVids | منصات الفيديو العادية\n\n"
        "أرسل رابط الملف أو الفيديو لبدء الاستخراج فوراً."
    )

async def process_download(client: Client, message: Message, task_id: str, url: str):
    status_msg = await message.reply_text(
        "⏳ **[v10 Titan] جاري تحليل الرابط واختراق الحماية...**",
        reply_markup=get_cancel_keyboard(task_id)
    )

    file_path = None
    thumb_path = None

    try:
        file_path, title, duration, used_method, error_log = await smart_extractor_v10(url, status_msg, task_id)

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
                    await update_status_ui(status_msg, "رفع الملف إلى تلجرام", current, total, upload_start, task_id)

            # الرفع كفيديو إذا كان امتداده ميديا، أو كمستند إذا كان ملف مضغوط/برنامج/غير ذلك
            if file_path.lower().endswith(('.mp4', '.mkv', '.webm', '.avi', '.mov')):
                await client.send_video(
                    chat_id=message.chat.id,
                    video=file_path,
                    thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                    duration=int(duration) if duration else 0,
                    caption=f"🎬 **{title}**\n⚡ **المحرك:** `{used_method}`\n🛡️ **Engine:** `v10 Titan`",
                    progress=upload_progress
                )
            else:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=file_path,
                    caption=f"📦 **{title}**\n⚡ **المحرك:** `{used_method}`\n🛡️ **Engine:** `v10 Titan`",
                    progress=upload_progress
                )

            await status_msg.delete()
        else:
            await status_msg.edit_text(
                "❌ **تعذر استخراج الملف باستخدام v10 Titan.**\n\n"
                "🧐 **الأسباب المحتملة:**\n"
                "• الملف محمي بكلمة سر أو يتطلب إذن وصول (Private Access / Google Drive Permission).\n"
                "• تم تجديد الرابط أو انتهت صلاحيته."
            )

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"🛑 تم إيقاف المهمة: {task_id}")
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
    
    logger.info("🚀 تم تشغيل محرك v10 Titan بنجاح...")
    app.run()