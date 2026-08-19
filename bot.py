import os
import math
import time
import asyncio
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
import yt_dlp

# إعداد الـ Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بيانات الاتصال (تأكد من ضبط المتغيرات في Railway)
API_ID = int(os.environ.get("API_ID", "123456"))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

MAX_TG_SIZE = 1.95 * 1024 * 1024 * 1024  # 1.95 GB
executor = ThreadPoolExecutor(max_workers=4)

ACTIVE_TASKS = {}
CANCEL_REQUESTS = {}

# ----------------------------------------------------
# 1. قاعدة البيانات SQLite
# ----------------------------------------------------
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def db_add_user(user_id: int):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

# ----------------------------------------------------
# 2. التنسيق وأدوات شريط التقدم
# ----------------------------------------------------
def format_time(seconds: float) -> str:
    if not seconds or seconds < 0 or math.isnan(seconds) or math.isinf(seconds):
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

def format_size(bytes_size: float) -> str:
    if not bytes_size:
        return "0 MB"
    mb = bytes_size / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.2f} MB"

def create_progress_bar(percent: float, length: int = 10) -> str:
    filled = int(round(length * percent / 100))
    return '▓' * filled + '░' * (length - filled)

def render_status_text(title: str, percent: float, current: float, total: float, speed: float, eta: float) -> str:
    bar = create_progress_bar(percent)
    speed_str = f"{format_size(speed)}/s" if speed else "0 MB/s"
    return (
        f"🚀 **{title}**\n\n"
        f"[{bar}] `{percent:.1f}%`\n\n"
        f"📊 **الحجم:** `{format_size(current)}` / `{format_size(total)}`\n"
        f"⚡ **السرعة:** `{speed_str}`\n"
        f"⏱ **الوقت المتبقي:** `{format_time(eta)}`"
    )

def parse_time_to_seconds(time_str: str) -> int:
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0

# ----------------------------------------------------
# 3. التحميل عبر yt-dlp مع تجاوز حماية Referer و 404
# ----------------------------------------------------
def _yt_dlp_download_sync(url: str, output_template: str, task_id: str, client: Client, chat_id: int, message_id: int, loop: asyncio.AbstractEventLoop):
    last_update_time = [0]

    def progress_hook(d):
        if CANCEL_REQUESTS.get(task_id):
            raise Exception("CANCELLED_BY_USER")

        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update_time[0] >= 1.5:
                last_update_time[0] = now
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                percent = (downloaded / total * 100) if total > 0 else 0

                text = render_status_text("جاري تنزيل الفيديو إلى السيرفر...", percent, downloaded, total, speed, eta)
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel_{task_id}")]])

                async def safe_edit():
                    try:
                        await client.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
                    except Exception:
                        pass
                
                asyncio.run_coroutine_threadsafe(safe_edit(), loop)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': url,
    }

    ydl_opts = {
        'format': 'best',
        'outtmpl': output_template,
        'writethumbnail': False,
        'writeinfojson': False,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'http_headers': headers,
        'referer': url,
        'progress_hooks': [progress_hook]
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        return info, filename

# ----------------------------------------------------
# 4. أدوات المعالجة عبر FFmpeg
# ----------------------------------------------------
async def run_ffmpeg_command(cmd: list):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg Error: {stderr.decode()}")
        raise RuntimeError("فشلت عملية FFmpeg")

async def get_video_metadata(file_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration", "-of", "csv=p=0", file_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        out = stdout.decode().strip().split(",")
        return {
            "width": int(out[0]) if len(out) > 0 and out[0].isdigit() else 1280,
            "height": int(out[1]) if len(out) > 1 and out[1].isdigit() else 720,
            "duration": int(float(out[2])) if len(out) > 2 and out[2] else 0
        }
    except Exception:
        return {"width": 1280, "height": 720, "duration": 0}

async def generate_thumbnail(video_path: str, thumb_path: str):
    if not os.path.exists(thumb_path):
        cmd = ["ffmpeg", "-y", "-ss", "00:00:01", "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", thumb_path]
        try:
            await run_ffmpeg_command(cmd)
        except Exception:
            pass

async def trim_video(input_path: str, output_path: str, start_sec: int, duration_sec: int):
    cmd = ["ffmpeg", "-y", "-ss", str(start_sec), "-i", input_path, "-t", str(duration_sec), "-c", "copy", "-movflags", "+faststart", output_path]
    await run_ffmpeg_command(cmd)

async def split_video_if_needed(file_path: str) -> list:
    file_size = os.path.getsize(file_path)
    if file_size <= MAX_TG_SIZE:
        return [file_path]

    meta = await get_video_metadata(file_path)
    total_duration = meta["duration"] or 1
    num_parts = math.ceil(file_size / MAX_TG_SIZE)
    part_duration = math.floor(total_duration / num_parts)

    output_files = []
    base_name, ext = os.path.splitext(file_path)

    for i in range(num_parts):
        start_time = i * part_duration
        part_out = f"{base_name}_part{i+1}{ext}"
        cmd = [
            "ffmpeg", "-y", "-ss", str(start_time), "-i", file_path,
            "-t", str(part_duration if i < num_parts - 1 else total_duration - start_time),
            "-c", "copy", "-movflags", "+faststart", part_out
        ]
        await run_ffmpeg_command(cmd)
        output_files.append(part_out)

    return output_files

# ----------------------------------------------------
# 5. التطبيق الرئيسي
# ----------------------------------------------------
app = Client("media_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    db_add_user(message.from_user.id)
    await message.reply_text(
        "مرحباً بك! أرسل رابط الفيديو للتحميل المباشر والرفع السريع.\n\n"
        "للقص أرسل:\n`/trim [الرابط] [البداية] [النهاية]`"
    )

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_callback(client: Client, query: CallbackQuery):
    task_id = query.data.replace("cancel_", "")
    CANCEL_REQUESTS[task_id] = True
    if task_id in ACTIVE_TASKS:
        ACTIVE_TASKS[task_id].cancel()
    await query.answer("جاري إلغاء العملية...")
    await query.edit_message_text("🛑 **تم إلغاء العملية.**")

async def process_task(client: Client, message: Message, url: str, is_trim=False, start_sec=0, duration_sec=0):
    user_id = message.from_user.id
    task_id = f"{user_id}_{int(time.time())}"
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel_{task_id}")]])
    status_msg = await message.reply_text("⏳ **جاري الاتصال بالسيرفر...**", reply_markup=keyboard)

    os.makedirs("downloads", exist_ok=True)
    prefix = f"downloads/{task_id}"
    output_template = f"{prefix}_raw.%(ext)s"

    try:
        ACTIVE_TASKS[task_id] = asyncio.current_task()
        loop = asyncio.get_running_loop()

        info, downloaded_file = await loop.run_in_executor(
            executor, _yt_dlp_download_sync, url, output_template, task_id, client, status_msg.chat.id, status_msg.id, loop
        )

        if not downloaded_file or not os.path.exists(downloaded_file):
            await status_msg.edit_text("❌ فشل العثور على الملف المحمل.")
            return

        final_file = downloaded_file
        if is_trim:
            await status_msg.edit_text("✂️ **جاري قص الفيديو...**")
            trimmed_file = f"{prefix}_cut.mp4"
            await trim_video(downloaded_file, trimmed_file, start_sec, duration_sec)
            final_file = trimmed_file

        thumb_path = f"{prefix}_thumb.jpg"
        await generate_thumbnail(final_file, thumb_path)
        meta = await get_video_metadata(final_file)
        parts = await split_video_if_needed(final_file)

        last_upload_update = [0]
        start_time = time.time()

        async def upload_progress(current, total):
            if CANCEL_REQUESTS.get(task_id):
                raise Exception("CANCELLED_BY_USER")
            
            now = time.time()
            if now - last_upload_update[0] >= 1.5 or current == total:
                last_upload_update[0] = now
                elapsed = now - start_time
                speed = current / elapsed if elapsed > 0 else 0
                eta = (total - current) / speed if speed > 0 else 0
                percent = (current / total * 100) if total > 0 else 0

                text = render_status_text("جاري الرفع إلى تلغرام...", percent, current, total, speed, eta)
                try:
                    await status_msg.edit_text(text, reply_markup=keyboard)
                except Exception:
                    pass

        total_parts = len(parts)
        title = info.get("title", "مقطع فيديو")

        for idx, part_path in enumerate(parts, 1):
            caption = f"🎬 **{title}**"
            if total_parts > 1:
                caption += f"\n📦 **الجزء ({idx}/{total_parts})**"

            part_meta = await get_video_metadata(part_path)
            thumb = thumb_path if os.path.exists(thumb_path) else None

            await client.send_video(
                chat_id=message.chat.id,
                video=part_path,
                caption=caption,
                duration=part_meta["duration"],
                width=part_meta["width"],
                height=part_meta["height"],
                thumb=thumb,
                progress=upload_progress
            )

        await status_msg.delete()

    except Exception as e:
        if CANCEL_REQUESTS.get(task_id) or "CANCELLED_BY_USER" in str(e):
            logger.info("Task cancelled by user.")
        else:
            logger.error(f"Process Error: {e}")
            await status_msg.edit_text(f"❌ **حدث خطأ أثناء المعالجة:**\n`{str(e)}`")
    finally:
        ACTIVE_TASKS.pop(task_id, None)
        CANCEL_REQUESTS.pop(task_id, None)
        for f in os.listdir("downloads"):
            if f.startswith(task_id):
                try:
                    os.remove(os.path.join("downloads", f))
                except Exception:
                    pass

@app.on_message(filters.command("trim"))
async def trim_cmd(client: Client, message: Message):
    db_add_user(message.from_user.id)
    args = message.text.split()[1:]
    if len(args) < 3:
        await message.reply_text("❌ الصيغة الصحيحة:\n`/trim [الرابط] [البداية] [النهاية]`")
        return
    url, start_str, end_str = args[0], args[1], args[2]
    try:
        s_sec = parse_time_to_seconds(start_str)
        e_sec = parse_time_to_seconds(end_str)
        d_sec = e_sec - s_sec
        if d_sec <= 0:
            await message.reply_text("❌ وقت النهاية يجب أن يكون بعد وقت البداية.")
            return
        await process_task(client, message, url, is_trim=True, start_sec=s_sec, duration_sec=d_sec)
    except Exception:
        await message.reply_text("❌ صيغة الوقت غير صحيحة.")

@app.on_message(filters.text & ~filters.command(["start", "trim"]))
async def text_handler(client: Client, message: Message):
    db_add_user(message.from_user.id)
    url = message.text.strip()
    if url.startswith("http://") or url.startswith("https://"):
        await process_task(client, message, url)
    else:
        await message.reply_text("الرجاء إرسال رابط مباشر يبدأ بـ http أو https.")

if __name__ == "__main__":
    init_db()
    print("==========================================")
    print("تم تشغيل البوت بنجاح عبر Pyrogram!")
    print("==========================================")
    app.run()
