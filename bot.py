import os
import sys
import math
import time
import asyncio
import logging
import sqlite3
import base64
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
)
import yt_dlp

# إعداد التسجيل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_TG_SIZE = 1.95 * 1024 * 1024 * 1024  # 1.95 GB limit

ACTIVE_TASKS = {}
CANCEL_REQUESTS = {}

# ----------------------------------------------------
# 1. إدارة قاعدة البيانات SQLite لتخزين الروابط المطلوبة
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_requests (
            req_id TEXT PRIMARY KEY,
            url TEXT,
            is_trim INTEGER,
            start_sec INTEGER,
            duration_sec INTEGER,
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

def db_save_request(req_id: str, url: str, is_trim: bool = False, start_sec: int = 0, duration_sec: int = 0):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO pending_requests (req_id, url, is_trim, start_sec, duration_sec) VALUES (?, ?, ?, ?, ?)",
        (req_id, url, 1 if is_trim else 0, start_sec, duration_sec)
    )
    conn.commit()
    conn.close()

def db_get_request(req_id: str):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT url, is_trim, start_sec, duration_sec FROM pending_requests WHERE req_id = ?", (req_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'url': row[0],
            'is_trim': bool(row[1]),
            'start_sec': row[2],
            'duration_sec': row[3]
        }
    return None

# ----------------------------------------------------
# 2. أدوات تنسيق الوقت، الأحجام، وشريط التقدم
# ----------------------------------------------------
def format_time(seconds: float) -> str:
    if seconds is None or seconds < 0 or math.isnan(seconds) or math.isinf(seconds):
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
    bar = '▓' * filled + '░' * (length - filled)
    return bar

def render_status_text(action_title: str, percent: float, downloaded: float, total: float, speed: float, eta: float) -> str:
    bar = create_progress_bar(percent)
    speed_str = f"{format_size(speed)}/s" if speed else "0 MB/s"
    
    return (
        f"🚀 **{action_title}**\n\n"
        f"[{bar}] `{percent:.1f}%`\n\n"
        f"📊 **الحجم:** `{format_size(downloaded)}` / `{format_size(total)}`\n"
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
# 3. خطاف yt-dlp واستخراج الجودات
# ----------------------------------------------------
def make_yt_dlp_hook(bot, chat_id, message_id, task_id, loop):
    last_update_time = [0]

    def hook(d):
        if CANCEL_REQUESTS.get(task_id):
            raise Exception("CANCELLED_BY_USER")

        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update_time[0] >= 2.0:
                last_update_time[0] = now
                
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                percent = (downloaded / total * 100) if total > 0 else 0

                text = render_status_text("جاري التنزيل من السيرفر...", percent, downloaded, total, speed, eta)
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel_{task_id}")]])

                coro = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="Markdown", reply_markup=keyboard)
                asyncio.run_coroutine_threadsafe(coro, loop)

    return hook

async def extract_video_qualities(url: str) -> tuple[str, list]:
    loop = asyncio.get_running_loop()

    def _extract():
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    info = await loop.run_in_executor(None, _extract)
    title = info.get('title', 'مقطع فيديو')
    
    available_heights = set()
    formats = info.get('formats', [])
    for f in formats:
        height = f.get('height')
        if height and height <= 1080:
            available_heights.add(height)

    sorted_qualities = sorted(list(available_heights))
    if not sorted_qualities:
        sorted_qualities = [360, 480, 720, 1080]

    return title, sorted_qualities

async def download_yt_dlp(url: str, output_path: str, format_spec: str, bot, chat_id, message_id, task_id) -> dict:
    loop = asyncio.get_running_loop()
    hook = make_yt_dlp_hook(bot, chat_id, message_id, task_id, loop)

    ydl_opts = {
        'format': format_spec,
        'outtmpl': output_path,
        'writethumbnail': True,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [hook]
    }

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    res = await loop.run_in_executor(None, _extract)
    return res

# ----------------------------------------------------
# 4. معالجة الفيديو والميديا عبر FFmpeg
# ----------------------------------------------------
async def run_ffmpeg_command(cmd: list):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg error: {stderr.decode()}")
        raise RuntimeError("فشلت عملية المعالجة عبر FFmpeg.")

async def get_video_metadata(file_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-of", "csv=p=0", file_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode().strip().split(",")
    
    width = int(output[0]) if len(output) > 0 and output[0].isdigit() else 1280
    height = int(output[1]) if len(output) > 1 and output[1].isdigit() else 720
    duration = int(float(output[2])) if len(output) > 2 and output[2] else 0
    
    return {"width": width, "height": height, "duration": duration}

async def generate_thumbnail_if_missing(video_path: str, thumb_path: str):
    if not os.path.exists(thumb_path):
        cmd = [
            "ffmpeg", "-y", "-ss", "00:00:02", "-i", video_path,
            "-vframes", "1", "-vf", "scale=320:-1", thumb_path
        ]
        try:
            await run_ffmpeg_command(cmd)
        except Exception as e:
            logger.error(f"Failed to generate thumbnail: {e}")

async def trim_video(input_path: str, output_path: str, start_sec: int, duration_sec: int):
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec), "-i", input_path,
        "-t", str(duration_sec), "-c", "copy", "-movflags", "+faststart", output_path
    ]
    await run_ffmpeg_command(cmd)

async def split_video_if_needed(file_path: str) -> list:
    file_size = os.path.getsize(file_path)
    if file_size <= MAX_TG_SIZE:
        return [file_path]

    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    total_duration = float(stdout.decode().strip())

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
# 5. الأوامر والمعالجات
# ----------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_add_user(update.effective_user.id)
    welcome_text = (
        "مرحباً بك في بوت التحميل الشامل الذكي v4.1 🚀\n\n"
        "✨ **المميزات:**\n"
        "• **حفظ الطلبات الدائم:** معالجة مشكلة انتهت صلاحية الطلب نهائياً عبر قاعدة البيانات.\n"
        "• **تشغيل مباشر:** إرسال بصيغة الميديا المدعومة للعرض الفوري.\n"
        "• **غلاف مصغر تلقائي:** استخراج الصورة المصغرة لكل مقطع.\n"
        "• **اختيار الجودة:** حتى 1080p.\n"
        "• **قص الفيديو:** `/trim [الرابط] [البداية] [النهاية]`"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    task_id = query.data.replace("cancel_", "")
    CANCEL_REQUESTS[task_id] = True
    
    if task_id in ACTIVE_TASKS:
        ACTIVE_TASKS[task_id].cancel()
        
    await query.edit_message_text("🛑 **تم إلغاء العملية بناءً على طلبك.**", parse_mode="Markdown")

async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data_parts = query.data.split("_")
    quality = data_parts[1]
    req_id = data_parts[2]

    # جلب البيانات الموثوقة من قاعدة البيانات
    req_info = db_get_request(req_id)
    if not req_info:
        await query.edit_message_text("❌ لم يتم العثور على بيانات هذا الطلب. يرجى إرسال الرابط من جديد.")
        return

    url = req_info['url']
    is_trim = req_info['is_trim']
    start_sec = req_info['start_sec']
    duration_sec = req_info['duration_sec']

    if quality == "best":
        format_spec = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best"
    elif quality == "worst":
        format_spec = "worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]/worst"
    else:
        format_spec = f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[height<={quality}][ext=mp4]/best"

    await process_download(query, context, url, format_spec, is_trim, start_sec, duration_sec)

async def process_download(query, context: ContextTypes.DEFAULT_TYPE, url: str, format_spec: str, is_trim=False, start_sec=0, duration_sec=0):
    user_id = query.from_user.id
    task_id = f"{user_id}_{int(time.time())}"
    
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel_{task_id}")]])
    status_msg = await query.edit_message_text("⏳ **جاري بدء عملية التحميل...**", parse_mode="Markdown", reply_markup=keyboard)

    prefix = f"downloads/{task_id}"
    output_template = f"{prefix}_raw.%(ext)s"

    os.makedirs("downloads", exist_ok=True)

    try:
        current_task = asyncio.current_task()
        ACTIVE_TASKS[task_id] = current_task

        info = await download_yt_dlp(url, output_template, format_spec, context.bot, query.message.chat.id, status_msg.message_id, task_id)
        title = info.get('title', 'مقطع ميديا')

        downloaded_file = None
        for f in os.listdir("downloads"):
            if f.startswith(f"{task_id}_raw") and not f.endswith(('.jpg', '.png', '.webp')):
                downloaded_file = os.path.join("downloads", f)
                break

        if not downloaded_file or not os.path.exists(downloaded_file):
            await status_msg.edit_text("❌ تعذر العثور على الملف بعد التنزيل.")
            return

        final_file = downloaded_file
        if is_trim:
            await status_msg.edit_text("✂️ **جاري قص الفيديو...**", parse_mode="Markdown")
            trimmed_file = f"{prefix}_cut.mp4"
            await trim_video(downloaded_file, trimmed_file, start_sec, duration_sec)
            final_file = trimmed_file

        thumb_path = f"{prefix}_thumb.jpg"
        await generate_thumbnail_if_missing(final_file, thumb_path)
        meta = await get_video_metadata(final_file)

        await status_msg.edit_text("⚙️ **جاري تجهيز الملف للرفع...**", parse_mode="Markdown")
        parts = await split_video_if_needed(final_file)

        total_parts = len(parts)
        for idx, part_path in enumerate(parts, 1):
            if CANCEL_REQUESTS.get(task_id):
                raise asyncio.CancelledError()

            caption = f"🎬 **{title}**"
            if total_parts > 1:
                caption += f"\n📦 **الجزء ({idx}/{total_parts})**"

            await status_msg.edit_text(f"⬆️ **جاري رفع الجزء ({idx}/{total_parts})...**", parse_mode="Markdown")
            
            part_meta = await get_video_metadata(part_path) if total_parts > 1 else meta
            
            with open(part_path, 'rb') as vf:
                thumb_file = open(thumb_path, 'rb') if os.path.exists(thumb_path) else None
                try:
                    await query.message.reply_video(
                        video=vf,
                        caption=caption,
                        parse_mode="Markdown",
                        supports_streaming=True,
                        duration=part_meta["duration"],
                        width=part_meta["width"],
                        height=part_meta["height"],
                        thumbnail=thumb_file
                    )
                finally:
                    if thumb_file:
                        thumb_file.close()

        await status_msg.delete()

    except (asyncio.CancelledError, Exception) as e:
        if CANCEL_REQUESTS.get(task_id) or "CANCELLED_BY_USER" in str(e):
            logger.info("Process cancelled by user.")
        else:
            logger.error(f"Error: {e}")
            await status_msg.edit_text("❌ **حدث خطأ أثناء معالجة الطلب.**", parse_mode="Markdown")
    finally:
        ACTIVE_TASKS.pop(task_id, None)
        CANCEL_REQUESTS.pop(task_id, None)
        for f in os.listdir("downloads"):
            if f.startswith(task_id):
                try:
                    os.remove(os.path.join("downloads", f))
                except Exception:
                    pass

async def handle_url_qualities(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, is_trim=False, start_sec=0, duration_sec=0):
    fetching_msg = await update.message.reply_text("🔍 **جاري فحص الرابط واستخراج الجودات المتاحة...**", parse_mode="Markdown")
    
    try:
        title, qualities = await extract_video_qualities(url)
        
        # إنشاء معرف ذكي وحفظه دائماً بداخل SQLite
        req_id = f"req_{update.effective_user.id}_{int(time.time())}"
        db_save_request(req_id, url, is_trim, start_sec, duration_sec)

        buttons = []
        row = []
        for q in qualities:
            row.append(InlineKeyboardButton(f"🎬 {q}p", callback_data=f"q_{q}_{req_id}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([
            InlineKeyboardButton("📱 أضعف جودة", callback_data=f"q_worst_{req_id}"),
            InlineKeyboardButton("🌟 أعلى جودة (1080p)", callback_data=f"q_best_{req_id}")
        ])

        reply_markup = InlineKeyboardMarkup(buttons)
        text = f"🎬 **{title}**\n\nاختر الجودة المطلوبة للتحميل:"
        await fetching_msg.edit_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Quality fetch error: {e}")
        await fetching_msg.edit_text("❌ **فشل استخراج الجودات من الرابط.** يرجى التأكد من أن الرابط صحيح ويحتوي على فيديو.")

async def trim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_add_user(update.effective_user.id)
    args = context.args
    
    if len(args) < 3:
        await update.message.reply_text(
            "❌ **استخدام خاطئ للأمر!**\nالصيغة الصحيحة:\n`/trim [الرابط] [البداية] [النهاية]`\n"
            "مثال: `/trim https://site.com/video 00:30 01:45`",
            parse_mode="Markdown"
        )
        return

    url, start_str, end_str = args[0], args[1], args[2]
    
    try:
        start_sec = parse_time_to_seconds(start_str)
        end_sec = parse_time_to_seconds(end_str)
        duration_sec = end_sec - start_sec

        if duration_sec <= 0:
            await update.message.reply_text("❌ وقت النهاية يجب أن يكون أكبر من وقت البداية.")
            return

    except Exception:
        await update.message.reply_text("❌ صيغة الوقت غير صحيحة. استخدم MM:SS أو HH:MM:SS.")
        return

    await handle_url_qualities(update, context, url, is_trim=True, start_sec=start_sec, duration_sec=duration_sec)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_add_user(update.effective_user.id)
    url = update.message.text.strip()

    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text("الرجاء إرسال رابط صحيح يبدأ بـ http أو https.")
        return

    await handle_url_qualities(update, context, url)

# ----------------------------------------------------
# 6. التشغيل الرئيسي
# ----------------------------------------------------
def main():
    if not TOKEN:
        logger.error("خطأ: لم يتم العثور على TELEGRAM_BOT_TOKEN في متغيرات البيئة!")
        return

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("trim", trim_command))
    app.add_handler(CallbackQueryHandler(cancel_callback, pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(quality_callback, pattern="^q_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.info("==========================================")
    logging.info("تم تشغيل البوت بنجاح وهو جاهز لاستقبال الأوامر!")
    logging.info("==========================================")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
