import asyncio
import os
import shutil
import subprocess
import time
import aiohttp
import gdown  # مكتبة دعم جوجل درايف
from mega import Mega
from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import DocumentAttributeVideo

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# تثبيت الآيدي الخاص بك
ADMIN_ID = 5414125521

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

bot = TelegramClient(None, API_ID, API_HASH, timeout=600, connection_retries=15, retry_delay=5)

mega = Mega()
m = mega.login()

ACTIVE_TASKS = {}
QUEUE = asyncio.Queue()
PROCESSED_MESSAGES = set()

# --- الدوال المساعدة ---
def format_bytes(size):
    if size >= 1024 * 1024 * 1024: return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"

def format_time(seconds):
    if seconds <= 0 or seconds > 86400: return "جاري الحساب..."
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}س {m}د {s}ث" if h > 0 else f"{m}د {s}ث"

def create_progress_bar(percentage):
    completed = int(percentage // 10)
    return f"[{'█' * completed}{'░' * (10 - completed)}]"

async def safe_edit_message(msg, text, buttons=None):
    try: await msg.edit(text, buttons=buttons)
    except MessageNotModifiedError: pass
    except Exception as e: print(f"Edit msg error: {e}")

# --- معالجة التنزيل ---
async def download_file_direct(url, output_path, cancel_event, status_msg):
    timeout_config = aiohttp.ClientTimeout(total=21600, connect=120, sock_read=300)
    
    # 1. دعم Google Drive
    if "drive.google.com" in url:
        try:
            await safe_edit_message(status_msg, "📥 **جاري التنزيل من Google Drive...**")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, gdown.download, url, output_path, False)
            if os.path.exists(output_path): return True, None
            return False, "فشل تنزيل الملف من Drive (قد يكون الرابط خاص)."
        except Exception as e: return False, f"خطأ Drive: {str(e)}"

    # 2. دعم Mega
    if "mega.nz" in url:
        try:
            await safe_edit_message(status_msg, "📥 **جاري التنزيل من Mega...**")
            loop = asyncio.get_event_loop()
            downloaded_path = await loop.run_in_executor(None, m.download_url, url)
            if downloaded_path and os.path.exists(downloaded_path):
                if downloaded_path != output_path: shutil.move(downloaded_path, output_path)
                return True, None
            return False, "فشل تنزيل ملف Mega."
        except Exception as e: return False, f"خطأ Mega: {str(e)}"

    # 3. التحميل المباشر
    async with aiohttp.ClientSession(timeout=timeout_config, headers=HEADERS) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200: return False, f"خطأ السيرفر: {resp.status}"
            total_size = int(resp.headers.get("Content-Length", 0))
            if total_size > MAX_FILE_SIZE: return False, "يتجاوز 2GB."
            
            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if cancel_event.is_set(): return False, "تم الإلغاء."
                    f.write(chunk)
            return True, None

# --- الأوامر والوظائف ---
@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    if event.sender_id != ADMIN_ID: return
    await event.respond("مرحباً! أرسل لي رابط مباشر أو رابط Mega أو Google Drive وسأقوم برفعه لك 🚀")

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def queue_handler(event):
    if event.sender_id != ADMIN_ID: return
    if event.text.startswith("/"): return
    if event.id in PROCESSED_MESSAGES: return
    PROCESSED_MESSAGES.add(event.id)
    await QUEUE.put((event, event.text.strip()))

# (باقي دوال الرفع والـ worker تبقى كما هي في الكود السابق)
# قمت باختصار جزء من الكود هنا لضمان الطول، تأكد من إكمال الدوال السابقة (process_download_job, queue_worker) كما كانت.

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot started successfully!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
