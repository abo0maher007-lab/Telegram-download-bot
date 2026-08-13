import asyncio
import os
import re
import shutil
import subprocess
import time
import aiohttp
from bs4 import BeautifulSoup
from mega import Mega
from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import DocumentAttributeVideo

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5414125521"))
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# هيدرز قوية لمحاكاة متصفح Chrome
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

bot = TelegramClient(None, API_ID, API_HASH)
mega = Mega()
m = mega.login()

ACTIVE_TASKS = {}
QUEUE = asyncio.Queue()

# --- دوال مساعدة ---
def format_bytes(size):
    return f"{size / (1024 * 1024):.1f} MB" if size < 1024*1024*1024 else f"{size / (1024*1024*1024):.2f} GB"

async def safe_edit_message(msg, text, buttons=None):
    try: await msg.edit(text, buttons=buttons)
    except: pass

# --- دوال المعالجة الذكية للملفات ---
def get_video_metadata(video_path):
    """استخراج معلومات الفيديو، وإرجاع None إذا كان الفيديو تالفاً"""
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip().split("\n")
        return int(output[0]), int(output[1]), int(float(output[2]))
    except:
        return None # في حال كان الفيديو تالفاً (moov atom error)

def generate_thumbnail(video_path):
    thumb = f"{video_path}.jpg"
    try:
        cmd = ["ffmpeg", "-y", "-ss", "00:00:03", "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", thumb]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return thumb if os.path.exists(thumb) else None
    except:
        return None

# --- محرك التحميل ---
async def download_generic(url, output_path, status_msg, cancel_event):
    """محرك تحميل مرن يدعم أغلب المواقع المباشرة"""
    request_headers = HEADERS.copy()
    domain = re.match(r"https?://([^/]+)", url)
    if domain: request_headers["Referer"] = f"{domain.group(0)}/"

    async with aiohttp.ClientSession(headers=request_headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return False, f"فشل الاتصال: {resp.status}"
            
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            
            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024*1024):
                    if cancel_event.is_set(): return False, "تم الإلغاء"
                    f.write(chunk)
                    downloaded += len(chunk)
                    # تحديث التقدم كل 10 ميجا لتخفيف الضغط على التليجرام
                    if downloaded % (10 * 1024 * 1024) == 0:
                        await safe_edit_message(status_msg, f"📥 جاري التحميل: {format_bytes(downloaded)} / {format_bytes(total_size)}")
            return True, None

# --- المعالج الرئيسي ---
async def process_job(event, url):
    task_id = f"{event.chat_id}_{event.id}"
    cancel_event = asyncio.Event()
    ACTIVE_TASKS[task_id] = {"cancel_event": cancel_event}
    
    status_msg = await event.respond("⏳ جاري المعالجة...")
    file_name = os.path.basename(url.split("?")[0]) or "video.mp4"
    
    # 1. التحميل
    success, error = await download_generic(url, file_name, status_msg, cancel_event)
    if not success:
        await status_msg.edit(f"❌ خطأ: {error}")
        return

    # 2. فحص الفيديو والتحضير للرفع
    await status_msg.edit("📤 جاري الرفع إلى تلغرام...")
    is_video = file_name.lower().endswith(('.mp4', '.mkv', '.mov', '.avi', '.webm'))
    thumb = None
    attributes = None
    
    if is_video:
        meta = get_video_metadata(file_name)
        if meta: # إذا كان الفيديو سليماً
            w, h, dur = meta
            thumb = generate_thumbnail(file_name)
            attributes = [DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True)]
    
    # 3. الرفع
    try:
        await bot.send_file(event.chat_id, file_name, thumb=thumb, attributes=attributes, caption="✅ تم الرفع بنجاح!")
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit(f"❌ فشل الرفع: {e}")
    finally:
        if os.path.exists(file_name): os.remove(file_name)
        if thumb and os.path.exists(thumb): os.remove(thumb)

# --- التشغيل ---
@bot.on(events.NewMessage(pattern=r"^https?://"))
async def handler(event):
    if event.sender_id != ADMIN_ID: return
    await process_job(event, event.text.strip())

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("Bot is running...")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
