import os
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

# إعداد التسجيل (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# جلب توكن البوت من متغيرات البيئة
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البداية"""
    welcome_text = (
        "مرحباً بك في بوت التحميل الشامل الذكي! 🚀\n\n"
        "أرسل لي أي رابط فيديو أو مشغّل ميديا (YouTube, TikTok, Instagram, Twitter, Facebook...) "
        "وسأقوم بسحبه وتحميله لك مباشرة."
    )
    await update.message.reply_text(welcome_text)

async def download_media(url: str, output_path: str) -> dict:
    """دالة سحب الوسائط باستخدام yt-dlp في مسار منفصل لتجنب حظر البوت"""
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'max_filesize': 50 * 1024 * 1024,  # الحد الأقصى للبوت العادي هو 50MB
    }

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _extract)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل والروابط"""
    url = update.message.text.strip()
    
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text("الرجاء إرسال رابط صحيح يبدأ بـ http أو https.")
        return

    status_msg = await update.message.reply_text("⏳ جاري معالجة الرابط وسحب الفيديو...")
    
    # اسم ملف مؤقت محدد بـ ID المستخدم
    file_prefix = f"downloads/{update.effective_user.id}_{update.message.message_id}"
    output_template = f"{file_prefix}.%(ext)s"

    os.makedirs("downloads", exist_ok=True)

    try:
        # جلب وسحب الميديا
        info = await download_media(url, output_template)
        title = info.get('title', 'فيديو بدون عنوان')
        
        # البحث عن الملف المكتمل بعد التحميل
        downloaded_file = None
        for file in os.listdir("downloads"):
            if file.startswith(f"{update.effective_user.id}_{update.message.message_id}"):
                downloaded_file = os.path.join("downloads", file)
                break

        if downloaded_file and os.path.exists(downloaded_file):
            await status_msg.edit_text("⬆️ جاري رفع الفيديو إلى تلغرام...")
            
            with open(downloaded_file, 'rb') as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=f"🎬 **{title}**",
                    parse_mode="Markdown"
                )
            
            # تنظيف الملفات المؤقتة بعد الرفع
            os.remove(downloaded_file)
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ متعذر إيجاد الملف بعد التحميل.")

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        await status_msg.edit_text("❌ تعذر تحميل الفيديو. قد يكون الرابط غير مدعوم، أو الفيديو محمي/خاص، أو يتجاوز الحجم المسموح (50MB).")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await status_msg.edit_text("❌ حدث خطأ غير متوقع أثناء معالجة الطلب.")
    finally:
        # التأكد من تنظيف أي ملفات متبقية في حال حدوث استثناء
        for file in os.listdir("downloads"):
            if file.startswith(f"{update.effective_user.id}_{update.message.message_id}"):
                try:
                    os.remove(os.path.join("downloads", file))
                except Exception:
                    pass

def main():
    if not TOKEN:
        raise ValueError("لم يتم العثور على TELEGRAM_BOT_TOKEN في متغيرات البيئة!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
