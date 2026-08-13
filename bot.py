import asyncio
import os
import shutil
import time
import threading
import subprocess
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
import gdown
import yt_dlp
from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError, FloodWaitError
from telethon.tl.types import DocumentAttributeVideo

# --- خادم HTTP خفيف لإرضاء Render وRailway ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")

    def log_message(self, format, *args):
        return

def start_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=start_health_check_server, daemon=True).start()

# --- قراءة المتغيرات الحساسة من البيئة ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5414125521"))

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
}

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v"}

bot = TelegramClient(None, API_ID, API_HASH, timeout=600, connection_retries=15, retry_delay=5)

QUEUE = asyncio.Queue()
PROCESSED_MESSAGES = set()
CANCEL_EVENTS = {}

# --- الدوال المساعدة ---
def format_bytes(size):
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"

def format_time(seconds):
    if seconds <= 0 or seconds > 86400:
        return "جاري الحساب..."
    m_val, s = divmod(int(seconds), 60)
    h, m_val = divmod(m_val, 60)
    return f"{h}س {m_val}د {s}ث" if h > 0 else f"{m_val}د {s}ث"

def create_progress_bar(percentage):
    completed = int(percentage // 10)
    return f"[{'█' * completed}{'░' * (10 - completed)}]"

async def safe_edit_message(msg, text, buttons=None):
    try:
        await msg.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
    except Exception as e:
        print(f"Edit msg error: {e}")

def extract_filename_from_url(url):
    parsed_url = urllib.parse.urlparse(url)
    filename = os.path.basename(parsed_url.path)
    filename = urllib.parse.unquote(filename)
    if filename and "." in filename:
        return filename
    return f"file_{int(time.time())}.bin"

# --- معالجة ميديا الفيديوهات (FFmpeg / FFprobe) ---
def get_video_metadata(video_path):
    width, height, duration = 1280, 720, 0
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        output = subprocess.check_output(cmd).decode("utf-8").strip().split("\n")
        if len(output) >= 2:
            width = int(output[0])
            height = int(output[1])
            if len(output) >= 3 and output[2] != "N/A":
                duration = int(float(output[2]))
    except Exception as e:
        print(f"Metadata extraction error: {e}")
    return width, height, duration

def generate_thumbnail(video_path, thumb_path):
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", "00:00:03",
            "-i", video_path, "-vframes", "1",
            "-vf", "scale=320:-1", thumb_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        print(f"Thumbnail error: {e}")
    return None

def extract_screenshots(video_path, num_shots=4):
    saved_files = []
    try:
        _, _, duration = get_video_metadata(video_path)
        if duration <= 0:
            duration = 60
            
        interval = duration / (num_shots + 1)
        for i in range(num_shots):
            ts = interval * (i + 1)
            output_file = f"preview_{i}_{os.path.basename(video_path)}.jpg"
            cmd = [
                "ffmpeg", "-y", "-ss", str(ts),
                "-i", video_path, "-vframes", "1",
                "-q:v", "2", output_file
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(output_file):
                saved_files.append(output_file)
    except Exception as e:
        print(f"Screenshot extraction error: {e}")
    return saved_files

# --- معالجة تنزيل انستغرام ---
def download_instagram_media(url):
    ydl_opts = {
        'outtmpl': 'insta_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        return filename

# --- معالجة التنزيل ---
async def download_file_direct(url, status_msg, cancel_event, task_id):
    timeout_config = aiohttp.ClientTimeout(total=21600, connect=120, sock_read=300)
    loop = asyncio.get_running_loop()
    cancel_button = [[Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]]

    # 1. Instagram
    if "instagram.com" in url or "instagr.am" in url:
        await safe_edit_message(status_msg, "📸 **جاري معالجة وتنزيل رابط إنستغرام...**", buttons=cancel_button)
        try:
            download_task = loop.run_in_executor(None, lambda: download_instagram_media(url))
            while not download_task.done():
                if cancel_event.is_set():
                    return False, "تم إلغاء التنزيل بواسطة المستخدم.", None
                await asyncio.sleep(1)
            output_path = await download_task
            if output_path and os.path.exists(output_path):
                return True, None, output_path
            return False, "فشل تنزيل الوسائط من إنستغرام.", None
        except Exception as e:
            return False, f"خطأ إنستغرام: {str(e)}", None

    # 2. Google Drive
    if "drive.google.com" in url:
        output_path = os.path.join(os.getcwd(), f"gdrive_{int(time.time())}.bin")
        await safe_edit_message(status_msg, "📥 **جاري التنزيل من Google Drive...**", buttons=cancel_button)
        try:
            download_task = loop.run_in_executor(None, lambda: gdown.download(url, output_path, quiet=True))
            while not download_task.done():
                if cancel_event.is_set():
                    return False, "تم إلغاء التنزيل بواسطة المستخدم.", None
                await asyncio.sleep(1)
            if os.path.exists(output_path):
                return True, None, output_path
            return False, "فشل تنزيل الملف من Drive.", None
        except Exception as e:
            return False, f"خطأ Drive: {str(e)}", None

    # 3. التحميل المباشر العادي
    async with aiohttp.ClientSession(timeout=timeout_config, headers=HEADERS) as session:
        while True:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 429:
                    await safe_edit_message(
                        status_msg,
                        "⚠️ **السيرفر محظور مؤقتاً (HTTP 429).**\n⏳ سيتم الانتظار 5 دقائق ثم إعادة المحاولة تلقائياً...",
                        buttons=cancel_button
                    )
                    for _ in range(300):
                        if cancel_event.is_set():
                            return False, "تم إلغاء التنزيل بواسطة المستخدم.", None
                        await asyncio.sleep(1)
                    continue

                if resp.status != 200:
                    return False, f"خطأ السيرفر: {resp.status}", None

                total_size = int(resp.headers.get("Content-Length", 0))
                if total_size > MAX_FILE_SIZE:
                    return False, "حجم الملف يتجاوز الحد المسموح (2GB).", None

                filename = None
                cd = resp.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    parts = cd.split("filename=")
                    if len(parts) > 1:
                        filename = parts[1].strip('"\' ')

                if not filename:
                    filename = extract_filename_from_url(url)

                output_path = os.path.join(os.getcwd(), filename)
                downloaded = 0
                start_time = time.time()
                last_edit = start_time

                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if cancel_event.is_set():
                            return False, "تم إلغاء التنزيل بواسطة المستخدم.", None
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        if now - last_edit >= 4 and total_size > 0:
                            speed = downloaded / (now - start_time)
                            eta = (total_size - downloaded) / speed if speed > 0 else 0
                            percent = (downloaded / total_size) * 100
                            text = (
                                f"📥 **جاري التحميل المباشر...**\n"
                                f"📄 **الملف:** `{filename}`\n"
                                f"{create_progress_bar(percent)} {percent:.1f}%\n"
                                f"⚡ **السرعة:** {format_bytes(speed)}/ثانية\n"
                                f"📦 **الحجم:** {format_bytes(downloaded)} / {format_bytes(total_size)}\n"
                                f"⏱ **الوقت المتبقي:** {format_time(eta)}"
                            )
                            asyncio.create_task(safe_edit_message(status_msg, text, buttons=cancel_button))
                            last_edit = now
                return True, None, output_path

# --- معالجة المهام والرفع ---
async def process_download_job(event, url, status_msg, task_id):
    cancel_event = asyncio.Event()
    CANCEL_EVENTS[task_id] = cancel_event
    cancel_button = [[Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]]
    output_path = None
    preview_photos = []

    try:
        success, error, output_path = await download_file_direct(url, status_msg, cancel_event, task_id)
        if not success:
            await safe_edit_message(status_msg, f"❌ **توقفت العملية:**\n`{error}`", buttons=None)
            return

        if cancel_event.is_set():
            await safe_edit_message(status_msg, "❌ **تم إلغاء العملية بواسطة المستخدم.**", buttons=None)
            return

        filename = os.path.basename(output_path)
        ext = os.path.splitext(filename)[1].lower()
        is_video = ext in VIDEO_EXTENSIONS

        if is_video:
            await safe_edit_message(status_msg, "📸 **جاري استخراج لقطات المعاينة...**", buttons=cancel_button)
            loop = asyncio.get_running_loop()
            preview_photos = await loop.run_in_executor(None, extract_screenshots, output_path, 4)
            if preview_photos:
                await bot.send_file(
                    event.chat_id,
                    file=preview_photos,
                    caption="📸 **معاينة لقطات الفيديو**",
                    reply_to=event.id
                )

        await safe_edit_message(status_msg, f"📤 **اكتمل التنزيل (`{filename}`)! جاري الرفع إلى تلغرام...**", buttons=cancel_button)

        last_edit = [time.time()]
        start_time = time.time()

        async def upload_callback(current, total):
            if cancel_event.is_set():
                raise asyncio.CancelledError("Upload cancelled by user")
            now = time.time()
            if now - last_edit[0] >= 4:
                speed = current / (now - start_time)
                eta = (total - current) / speed if speed > 0 else 0
                percent = (current / total) * 100
                text = (
                    f"📤 **جاري الرفع إلى تلغرام...**\n"
                    f"📄 **الملف:** `{filename}`\n"
                    f"{create_progress_bar(percent)} {percent:.1f}%\n"
                    f"⚡ **السرعة:** {format_bytes(speed)}/ثانية\n"
                    f"📦 **الحجم:** {format_bytes(current)} / {format_bytes(total)}\n"
                    f"⏱ **الوقت المتبقي:** {format_time(eta)}"
                )
                asyncio.create_task(safe_edit_message(status_msg, text, buttons=cancel_button))
                last_edit[0] = now

        thumb = None
        attributes = []

        if is_video:
            w, h, duration = get_video_metadata(output_path)
            thumb_path = f"{output_path}_thumb.jpg"
            thumb = generate_thumbnail(output_path, thumb_path)
            attributes = [
                DocumentAttributeVideo(
                    duration=duration if duration > 0 else 1,
                    w=w if w > 0 else 1280,
                    h=h if h > 0 else 720,
                    supports_streaming=True,
                )
            ]

        await bot.send_file(
            event.chat_id,
            output_path,
            caption=f"📱 **اسم الملف:**\n`{filename}`\n⚡ **تم الرفع بنجاح!**",
            progress_callback=upload_callback,
            thumb=thumb,
            attributes=attributes if is_video else None,
            supports_streaming=is_video,
            force_document=not is_video
        )

        if thumb and os.path.exists(thumb):
            os.remove(thumb)

        await safe_edit_message(status_msg, "🎉 **تمت العملية بنجاح!**", buttons=None)

    except asyncio.CancelledError:
        await safe_edit_message(status_msg, "❌ **تم إلغاء العملية أثناء الرفع.**", buttons=None)
    except Exception as e:
        await safe_edit_message(status_msg, f"❌ **خطأ غير متوقع:** {str(e)}", buttons=None)
    finally:
        CANCEL_EVENTS.pop(task_id, None)
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
        for img in preview_photos:
            if os.path.exists(img):
                os.remove(img)

async def queue_worker():
    while True:
        event, url, status_msg, task_id = await QUEUE.get()
        try:
            await process_download_job(event, url, status_msg, task_id)
        except Exception as e:
            print(f"Queue worker error: {e}")
        finally:
            QUEUE.task_done()

# --- الأحداث والأوامر ---
@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    if ADMIN_ID != 0 and event.sender_id != ADMIN_ID:
        return

    welcome_text = (
        "مرحباً بك في بوت التنزيل والرفع المباشر Pro 🚀\n\n"
        "💡 ماذا يقدم هذا البوت؟\n"
        "يقوم البوت بأخذ الروابط المباشرة للملفات، روابط Instagram (صور/فيديوهات/Reels)، وروابط Google Drive، ويدعم تحميلها ثم إعادة رفعها لك مباشرة داخل تلغرام بسرعة عالية جداً دون استهلاك بياناتك!\n\n"
        "📦 المميزات والصيغ المدعومة:\n"
        "• 📏 الحد الأقصى: ملفات بحجم يصل إلى 2 جيجابايت (2GB).\n"
        "• 📸 دعم كامل لروابط إنستغرام (Instagram Reels / Posts / Stories).\n"
        "• 🎬 الفيديوهات والصور: MP4, MKV, JPG, PNG, WEBM...\n"
        "• ☁️ دعم كامل لروابط Google Drive والروابط المباشرة.\n"
        "• 📸 استخراج لقطات معاينة تلقائية وصورة مصغرة للفيديوهات.\n\n"
        "✍️ كيفية الاستخدام:\n"
        "قم بنسخ الرابط المباشر أو رابط انستغرام وأرسله هنا مباشرة في المحادثة!"
    )
    await event.respond(welcome_text)

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def queue_handler(event):
    if ADMIN_ID != 0 and event.sender_id != ADMIN_ID:
        return
    if event.text.startswith("/"):
        return
    if event.id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(event.id)

    task_id = str(event.id)
    status_msg = await event.respond("⏳ **تم استلام الرابط، جاري بدء العملية...**")
    await QUEUE.put((event, event.text.strip(), status_msg, task_id))

@bot.on(events.CallbackQuery(pattern=r"^cancel_"))
async def cancel_handler(event):
    if ADMIN_ID != 0 and event.sender_id != ADMIN_ID:
        return
    task_id = event.data.decode("utf-8").replace("cancel_", "")
    if task_id in CANCEL_EVENTS:
        CANCEL_EVENTS[task_id].set()
        await event.answer("⚠️ جاري إلغاء العملية...", alert=True)
    else:
        await event.answer("⚠️ العملية غير موجودة أو اكتملت بالفعل.", alert=True)

# --- نقطة الدخول الرئيسية ---
async def main():
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot started successfully!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
