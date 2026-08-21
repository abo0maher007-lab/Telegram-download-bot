import os
import time
import asyncio
import logging
import requests
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ----------------------------------------------------
# 🚂 جلب المتغيرات
# ----------------------------------------------------
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: المتغيرات غير معرفة في Railway Variables!")
    exit(1)

try:
    API_ID = int(API_ID)
except ValueError:
    logger.critical("❌ خطأ: قيمة API_ID يجب أن تكون رقمية!")
    exit(1)

app = Client("SmartDownloaderBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# قاموس لتتبع العمليات الملغاة محلياً
CANCELLED_TASKS = set()

# ----------------------------------------------------
# 📊 أدوات شريط التقدم وزر الإلغاء
# ----------------------------------------------------

def get_cancel_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """إعادة زر إلغاء إنتراكتيف"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ إلغاء العملية", callback_data=f"cancel_{task_id}")
    ]])

def render_progress_bar(percentage: float) -> str:
    """بناء شريط التقدم المرئي"""
    filled = int(percentage // 10)
    return "█" * filled + "░" * (10 - filled)

async def update_status_ui(message: Message, status_text: str, current: int, total: int, start_time: float, task_id: str):
    """تحديث واجهة المستخدم بالمعدل، السرعة، والوقت المتبقي"""
    if task_id in CANCELLED_TASKS:
        raise Exception("CANCELLED_BY_USER")

    now = time.time()
    diff = now - start_time
    if diff <= 0:
        return

    percentage = (current / total) * 100 if total > 0 else 0
    speed = current / diff
    eta = round((total - current) / speed) if speed > 0 else 0

    speed_mb = speed / (1024 * 1024)
    downloaded_mb = current / (1024 * 1024)
    total_mb = total / (1024 * 1024)

    text = (
        f"⏳ **{status_text}**\n\n"
        f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
        f"📦 **الحجم:** `{downloaded_mb:.1f}MB` / `{total_mb:.1f}MB`\n"
        f"🚀 **السرعة:** `{speed_mb:.2f} MB/s`\n"
        f"⏱️ **المتبقي:** `{eta}s`"
    )

    try:
        await message.edit_text(text, reply_markup=get_cancel_keyboard(task_id))
    except Exception:
        pass

# ----------------------------------------------------
# 🧠 محرك الفحص والتحميل مع دعم التقدم والإلغاء
# ----------------------------------------------------

def make_ytdl_opts(base_opts: dict, status_msg: Message, loop: asyncio.AbstractEventLoop, task_id: str, action_name: str):
    start_time = time.time()
    last_update = [0]

    def hook(d):
        if task_id in CANCELLED_TASKS:
            raise Exception("CANCELLED_BY_USER")

        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            
            # تقليص المعدل لتجنب حظر FloodWait من تلجرام (كل 2 ثانية)
            if total > 0 and (time.time() - last_update[0] > 2):
                last_update[0] = time.time()
                asyncio.run_coroutine_threadsafe(
                    update_status_ui(status_msg, action_name, downloaded, total, start_time, task_id),
                    loop
                )

    opts = base_opts.copy()
    opts["progress_hooks"] = [hook]
    return opts

async def smart_extractor(url: str, status_msg: Message, task_id: str):
    loop = asyncio.get_event_loop()

    # ─── 1. الاستراتيجية الأولى ───
    try:
        opts = make_ytdl_opts({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": f"downloads/{task_id}_s1.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True,
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        }, status_msg, loop, task_id, "تحميل الفيديو (دقة عالية)")

        def run_s1():
            with YoutubeDL(opts) as ytdl:
                info = ytdl.extract_info(url, download=True)
                return ytdl.prepare_filename(info), info.get("title", "Video")

        return await loop.run_in_executor(None, run_s1) + ("الاستراتيجية 1",)
    except Exception as e:
        if "CANCELLED_BY_USER" in str(e): raise e

    # ─── 2. الاستراتيجية الثانية (هاتف) ───
    try:
        opts = make_ytdl_opts({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s2.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)",
            "referer": "https://www.google.com/"
        }, status_msg, loop, task_id, "تحميل الفيديو (محاكاة هاتف)")

        def run_s2():
            with YoutubeDL(opts) as ytdl:
                info = ytdl.extract_info(url, download=True)
                return ytdl.prepare_filename(info), info.get("title", "Video")

        return await loop.run_in_executor(None, run_s2) + ("الاستراتيجية 2",)
    except Exception as e:
        if "CANCELLED_BY_USER" in str(e): raise e

    # ─── 3. Generic Extractor ───
    try:
        opts = make_ytdl_opts({
            "format": "worst/worstvideo",
            "outtmpl": f"downloads/{task_id}_s3.%(ext)s",
            "quiet": True, "force_generic_extractor": True, "nocheckcertificate": True
        }, status_msg, loop, task_id, "استخراج ذكي شامل")

        def run_s3():
            with YoutubeDL(opts) as ytdl:
                info = ytdl.extract_info(url, download=True)
                return ytdl.prepare_filename(info), info.get("title", "Video")

        return await loop.run_in_executor(None, run_s3) + ("الاستراتيجية 3",)
    except Exception as e:
        if "CANCELLED_BY_USER" in str(e): raise e

    return None, None, None

# ----------------------------------------------------
# 📡 معالجة الرسائل والرفع
# ----------------------------------------------------

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    CANCELLED_TASKS.add(task_id)
    await callback.answer("🛑 جاري إلغاء العملية...", show_alert=True)
    try:
        await callback.message.edit_text("❌ **تم إلغاء العملية بناءً على طلبك.**")
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text("👋 أهلاً بك! أرسل رابط الفيديو وستتم معالجته فوراً مع متابعة التقدم وإمكانية الإلغاء.")

@app.on_message(filters.text & filters.private & ~filters.forwarded)
async def handle_download(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        return

    task_id = f"{message.from_user.id}_{int(time.time())}"
    status_msg = await message.reply_text(
        "⏳ **جاري بدء عملية الفحص والتحميل...**",
        reply_markup=get_cancel_keyboard(task_id)
    )

    file_path = None
    try:
        file_path, title, used_method = await smart_extractor(url, status_msg, task_id)

        if task_id in CANCELLED_TASKS:
            raise Exception("CANCELLED_BY_USER")

        if file_path and os.path.exists(file_path):
            upload_start = time.time()
            last_up_update = [0]

            async def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise Exception("CANCELLED_BY_USER")
                if time.time() - last_up_update[0] > 2:
                    last_up_update[0] = time.time()
                    await update_status_ui(status_msg, "جاري الرفع إلى تلجرام", current, total, upload_start, task_id)

            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                caption=f"🎬 **{title}**\n🛠️ الطريقة: `{used_method}`",
                progress=upload_progress
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ **فشلت جميع المحاولات في استخراج الفيديو.**")

    except Exception as e:
        if "CANCELLED_BY_USER" in str(e) or task_id in CANCELLED_TASKS:
            logger.info(f"تم إلغاء المهمة: {task_id}")
        else:
            logger.error(f"خطأ غير متوقع: {e}")
            await status_msg.edit_text(f"❌ حدث خطأ أثناء المعالجة: `{str(e)[:150]}`")
    finally:
        # تنظيف الملف وإزالة التتبع
        if file_path and os.path.exists(file_path):
            try: os.remove(file_path)
            except Exception: pass
        CANCELLED_TASKS.discard(task_id)

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    logger.info("🚀 جاري تشغيل البوت على Railway مع إضافات Progress & Cancel...")
    app.run()
