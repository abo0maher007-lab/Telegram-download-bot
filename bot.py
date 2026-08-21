import os
import time
import asyncio
import logging
import requests
from pyrogram import Client, filters
from pyrogram.types import Message
from yt_dlp import YoutubeDL

# إعداد السجلات لمتابعة الأداء الأخطاء
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# 🚂 جلب المتغيرات المربوطة بـ Railway (Variables Tab)
# ----------------------------------------------------
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# التحقق السليم من وجود المتغيرات بدون أخطاء النحو (Syntax/NameError)
if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: المتغيرات (API_ID, API_HASH, BOT_TOKEN) غير معرفة في Railway Variables!")
    exit(1)

try:
    API_ID = int(API_ID)
except ValueError:
    logger.critical("❌ خطأ: قيمة API_ID يجب أن تكون رقمية فقط!")
    exit(1)

app = Client("SmartDownloaderBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ----------------------------------------------------
# 🛠️ خيارات الاستخراج المتقدمة (استراتيجيات yt-dlp)
# ----------------------------------------------------

STRATEGY_1_OPTS = {
    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    "outtmpl": "downloads/%(id)s_s1.%(ext)s",
    "quiet": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
}

STRATEGY_2_OPTS = {
    "format": "best",
    "outtmpl": "downloads/%(id)s_s2.%(ext)s",
    "quiet": True,
    "nocheckcertificate": True,
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "referer": "https://www.google.com/"
}

STRATEGY_3_OPTS = {
    "format": "worst/worstvideo",
    "outtmpl": "downloads/%(id)s_s3.%(ext)s",
    "quiet": True,
    "force_generic_extractor": True,
    "nocheckcertificate": True
}

# ----------------------------------------------------
# 🧠 محرك الفحص الاستخراجي الذكي (Smart Scraper)
# ----------------------------------------------------

def direct_http_download(url: str, output_path: str) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*"
    }
    try:
        response = requests.get(url, headers=headers, stream=True, timeout=15, verify=False)
        content_type = response.headers.get("Content-Type", "").lower()
        
        if "video" in content_type or url.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")):
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            return True
    except Exception as e:
        logger.warning(f"فشلت طريقة HTTP المباشرة: {e}")
    return False


async def smart_extractor(url: str, status_msg: Message):
    loop = asyncio.get_event_loop()
    
    # ─── Option 1: yt-dlp القياسي ───
    try:
        await status_msg.edit_text("🔍 **[محاولة 1/4]:** فحص المشغلات المعروفة بأعلى جودة...")
        def run_s1():
            with YoutubeDL(STRATEGY_1_OPTS) as ytdl:
                info = ytdl.extract_info(url, download=True)
                return ytdl.prepare_filename(info), info.get("title", "Video")
        
        file_path, title = await loop.run_in_executor(None, run_s1)
        return file_path, title, "الاستراتيجية 1 (High Quality)"
    except Exception:
        pass

    # ─── Option 2: محاكاة iPhone / Mobile Headers ───
    try:
        await status_msg.edit_text("🔄 **[محاولة 2/4]:** تجاوز حماية السيرفر بمحاكاة متصفح هاتف...")
        def run_s2():
            with YoutubeDL(STRATEGY_2_OPTS) as ytdl:
                info = ytdl.extract_info(url, download=True)
                return ytdl.prepare_filename(info), info.get("title", "Video")
        
        file_path, title = await loop.run_in_executor(None, run_s2)
        return file_path, title, "الاستراتيجية 2 (Mobile Spoofing)"
    except Exception:
        pass

    # ─── Option 3: الاستخراج المباشر عبر Requests ───
    try:
        await status_msg.edit_text("⚡ **[محاولة 3/4]:** تجربة السحب المباشر لسيرفر الفيديو...")
        raw_path = f"downloads/direct_{int(time.time())}.mp4"
        success = await loop.run_in_executor(None, direct_http_download, url, raw_path)
        if success:
            return raw_path, "Direct Video File", "الاستراتيجية 3 (HTTP Stream)"
    except Exception:
        pass

    # ─── Option 4: Generic Extractor ───
    try:
        await status_msg.edit_text("⚙️ **[محاولة 4/4]:** استخراج ذكي وشامل من داخل كود الصفحة...")
        def run_s3():
            with YoutubeDL(STRATEGY_3_OPTS) as ytdl:
                info = ytdl.extract_info(url, download=True)
                return ytdl.prepare_filename(info), info.get("title", "Video")
        
        file_path, title = await loop.run_in_executor(None, run_s3)
        return file_path, title, "الاستراتيجية 4 (Generic Parsing)"
    except Exception:
        pass

    return None, None, None

# ----------------------------------------------------
# 📡 معالجة الرسائل والرفع
# ----------------------------------------------------

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text("👋 أهلاً بك! أرسل رابط الفيديو المباشر أو غير المباشر وستتم معالجته فوراً.")

@app.on_message(filters.text & filters.private & ~filters.forwarded)
async def handle_download(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        return

    status_msg = await message.reply_text("⏳ **جاري بدء عملية الفحص الذكي...**")
    
    file_path, title, used_method = await smart_extractor(url, status_msg)

    if file_path and os.path.exists(file_path):
        try:
            await status_msg.edit_text(f"✅ **تم النجاح عبر ({used_method})!**\n⬆️ جاري الرفع إلى تلجرام...")
            
            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=f"🎬 **{title}**\n🛠️ الطريقة المستخدمة: `{used_method}`"
            )
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ خطأ أثناء الرفع: `{str(e)}`")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        await status_msg.edit_text(
            "❌ **فشلت جميع الطرق الذكية (4/4) في استخراج الفيديو.**\n"
            "السبب محتمل: الرابط يطلب تسجيل دخول، أو يستلزم كود Token مؤقت غير متاح."
        )

# ----------------------------------------------------
# 🚀 بدء التشغيل المتوافق مع Railway
# ----------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    logger.info("🚀 جاري تشغيل البوت على Railway...")
    app.run()
