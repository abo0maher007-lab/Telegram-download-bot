import asyncio
import os
import shutil
import time
import aiohttp
import gdown
from mega import Mega
from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ADMIN_ID = 5414125521
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

bot = TelegramClient(None, API_ID, API_HASH, timeout=600, connection_retries=15, retry_delay=5)

# تهيئة Mega بشكل آمن
try:
    mega = Mega()
    m = mega.login()
except Exception as e:
    print(f"Mega login bypass/warning: {e}")
    m = None

ACTIVE_TASKS = {}
QUEUE = asyncio.Queue()
PROCESSED_MESSAGES = set()

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
    except Exception as e:
        print(f"Edit msg error: {e}")

# --- معالجة التنزيل ---
async def download_file_direct(url, output_path, cancel_event, status_msg):
    timeout_config = aiohttp.ClientTimeout(total=21600, connect=120, sock_read=300)
    
    # 1. دعم Google Drive
    if "drive.google.com" in url:
        try:
            await safe_edit_message(status_msg, "📥 **جاري التنزيل من Google Drive...**")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: gdown.download(url, output_path, quiet=True))
            if os.path.exists(output_path):
                return True, None
            return False, "فشل تنزيل الملف من Drive (قد يكون الرابط خاص)."
        except Exception as e:
            return False, f"خطأ Drive: {str(e)}"

    # 2. دعم Mega
    if "mega.nz" in url:
        if not m:
            return False, "خدمة Mega غير مهيأة حالياً."
        try:
            await safe_edit_message(status_msg, "📥 **جاري التنزيل من Mega...**")
            loop = asyncio.get_running_loop()
            downloaded_path = await loop.run_in_executor(None, lambda: m.download_url(url))
            if downloaded_path and os.path.exists(downloaded_path):
                if downloaded_path != output_path:
                    shutil.move(downloaded_path, output_path)
                return True, None
            return False, "فشل تنزيل ملف Mega."
        except Exception as e:
            return False, f"خطأ Mega: {str(e)}"

    # 3. التحميل المباشر
    async with aiohttp.ClientSession(timeout=timeout_config, headers=HEADERS) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return False, f"خطأ السيرفر: {resp.status}"
            total_size = int(resp.headers.get("Content-Length", 0))
            if total_size > MAX_FILE_SIZE:
                return False, "حجم الملف يتجاوز الحد المسموح (2GB)."
            
            downloaded = 0
            start_time = time.time()
            last_edit = start_time
            
            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if cancel_event.is_set():
                        return False, "تم إلغاء التنزيل بواسطة المستخدم."
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # تحديث نسبة التقدم كل 3 ثوانٍ
                    now = time.time()
                    if now - last_edit >= 3 and total_size > 0:
                        speed = downloaded / (now - start_time)
                        eta = (total_size - downloaded) / speed if speed > 0 else 0
                        percent = (downloaded / total_size) * 100
                        text = (
                            f"📥 **جاري التحميل المباشر...**\n"
                            f"{create_progress_bar(percent)} {percent:.1f}%\n"
                            f"⚡ **السرعة:** {format_bytes(speed)}/ثانية\n"
                            f"📦 **الحجم:** {format_bytes(downloaded)} / {format_bytes(total_size)}\n"
                            f"⏱ **الوقت المتبقي:** {format_time(eta)}"
                        )
                        await safe_edit_message(status_msg, text)
                        last_edit = now
            return True, None

# --- معالجة المهام والطابور (إضافة الدوال المفقودة) ---
async def process_download_job(event, url):
    cancel_event = asyncio.Event()
    status_msg = await event.respond("⏳ **تمت إضافة الرابط للطابور وبدء المعالجة...**")
    filename = f"file_{int(time.time())}.tmp"
    output_path = os.path.join(os.getcwd(), filename)
    
    try:
        success, error = await download_file_direct(url, output_path, cancel_event, status_msg)
        if not success:
            await safe_edit_message(status_msg, f"❌ **حدث خطأ أثناء التنزيل:** {error}")
            return
            
        await safe_edit_message(status_msg, "📤 **جاري رفع الملف إلى تلغرام...**")
        
        # رفع الملف إلى تلغرام
        await bot.send_file(
            event.chat_id,
            output_path,
            caption="✅ **تم الرفع بنجاح!**",
            supports_streaming=True
        )
        await safe_edit_message(status_msg, "🎉 **تمت العملية بنجاح!**")
        
    except Exception as e:
        await safe_edit_message(status_msg, f"❌ **خطأ غير متوقع:** {str(e)}")
    finally:
        if os.path.exists(output_path):
            os.remove(output_path)

async def queue_worker():
    while True:
        event, url = await QUEUE.get()
        try:
            await process_download_job(event, url)
        except Exception as e:
            print(f"Queue worker error: {e}")
        finally:
            QUEUE.task_done()

# --- الأحداث والأوامر ---
@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    await event.respond("مرحباً! أرسل لي رابط مباشر أو رابط Mega أو Google Drive وسأقوم برفعه لك 🚀")

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def queue_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    if event.text.startswith("/"):
        return
    if event.id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(event.id)
    await QUEUE.put((event, event.text.strip()))
    await event.respond("📥 **تم استلام الرابط وإضافته لقائمة الانتظار.**")

# --- نقطة الدخول الرئيسية ---
async def main():
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot started successfully!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
