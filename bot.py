import os
import asyncio
import logging
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from yt_dlp import YoutubeDL

# إعداد السجلات للمتابعة وتتبع الأخطاء
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# استدعاء المتغيرات من متغيرة البيئة Environment Variables (تُضبط على Railway)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# التحقق من وجود المتغيرات
if not ALL([API_ID, API_HASH, BOT_TOKEN]):
    logger.error("تنبيه: يجب إدخال API_ID, API_HASH, و BOT_TOKEN في متغيرات البيئة!")

app = Client("AdvancedVideoDownloaderBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# إعدادات متقدمة لـ yt-dlp لاستخراج المحتوى وتجاوز الحماية
YTDL_OPTIONS = {
    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "outtmpl": "downloads/%(id)s.%(ext)s",
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": False,
    "geo_bypass": True,  # تجاوز القيود الجغرافية
    "nocheckcertificate": True,  # تجاوز شهادات SSL للروابط الملتوية
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    },
    "retries": 5,
    "fragment_retries": 5,
}

def progress_bar(current, total, status_msg, start_time):
    """حساب وشريط تقدم الرفع/التحميل بشكل ديناميكي"""
    now = time.time()
    diff = now - start_time
    if diff == 0:
        return
    percentage = current * 100 / total
    speed = current / diff
    elapsed_time = round(diff)
    eta = round((total - current) / speed) if speed > 0 else 0
    
    # تكوين الشريط Visual Progress
    filled_len = int(percentage // 10)
    bar = "█" * filled_len + "░" * (10 - filled_len)
    
    text = (
        f"⏳ **جاري Processing...**\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"🚀 **السرعة:** {speed / (1024 * 1024):.2f} MB/s\n"
        f"⏱️ **الوقت المتبقي:** {eta} ثانية"
    )
    try:
        status_msg.edit_text(text)
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 مرحبًا بك في بوت تحميل الفيديوهات المتقدم.\n\n"
        "أرسل رابط الفيديو المباشر أو غير المباشر، وسيقوم البوت باستخراجه ورفعه لك مباشرة."
    )

@app.on_message(filters.text & filters.private & ~filters.forwarded)
async def download_handler(client: Client, message: Message):
    url = message.text.strip()
    
    if not url.startswith(("http://", "https://")):
        await message.reply_text("❌ يرجى إرسال رابط صحيح يتبعه http:// أو https://")
        return

    status_msg = await message.reply_text("🔎 **جاري تحليل الرابط وتجاوز الحماية...**")
    
    # استخدام ThreadPoolExecutor للتعامل مع yt-dlp المتزامن Blocking
    loop = asyncio.get_event_loop()
    
    def extract_info():
        with YoutubeDL(YTDL_OPTIONS) as ytdl:
            return ytdl.extract_info(url, download=True)

    try:
        info = await loop.run_in_executor(None, extract_info)
        file_path = ytdl.prepare_filename(info)
        title = info.get("title", "Video")
        duration = int(info.get("duration", 0))
        
        await status_msg.edit_text("⬆️ **تم الاستخراج بنجاح، جاري الرفع إلى تلجرام...**")
        start_time = time.time()
        
        # رفع الفيديو إلى تلجرام
        await client.send_video(
            chat_id=message.chat.id,
            video=file_path,
            caption=f"🎬 **{title}**",
            duration=duration,
            progress=progress_bar,
            progress_args=(status_msg, start_time)
        )
        
        await status_msg.delete()
        
        # تنظيف الملفات المؤقتة بعد الرفع
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        logger.error(f"Error during extraction: {e}")
        await status_msg.edit_text(f"❌ **حدث خطأ أثناء استخراج الفيديو:**\n`{str(e)[:200]}`")

if __name__ == "__main__":
    # إنشاء مجلد التحميلات إن لم يكن موجودًا
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    logger.info("Bot starting...")
    app.run()
