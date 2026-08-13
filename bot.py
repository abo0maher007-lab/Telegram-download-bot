import asyncio
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import aiohttp
import yt_dlp
from bs4 import BeautifulSoup
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo

# --- التحديث التلقائي للإصدار ---
VERSION = "v8.6"

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5414125521"))
PORT = int(os.environ.get("PORT", 8080))

bot = TelegramClient(None, API_ID, API_HASH)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
}

# --- سيرفر وهمي لمنع إغلاق الخدمة في Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running healthily!")
    def log_message(self, format, *args): return

def run_health_check_server():
    try:
        httpd = HTTPServer(('', PORT), HealthCheckHandler)
        httpd.serve_forever()
    except: pass

threading.Thread(target=run_health_check_server, daemon=True).start()

def format_bytes(size):
    return f"{size / (1024 * 1024 * 1024):.2f} GB" if size >= 1024*1024*1024 else f"{size / (1024 * 1024):.1f} MB"

async def safe_edit(msg, text):
    try: await msg.edit(text)
    except: pass

# --- محرك التحميل العام ---
async def download_direct(url, output_path, status_msg):
    req_headers = HEADERS.copy()
    timeout = aiohttp.ClientTimeout(total=21600, connect=60)
    try:
        async with aiohttp.ClientSession(headers=req_headers, timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200: return False, f"HTTP {resp.status}"
                
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_update = 0
                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_update > 4:
                            last_update = now
                            progress = f"📥 **جاري التحميل ({VERSION})...**\n📦 تم تنزيل: `{format_bytes(downloaded)}`"
                            if total_size > 0: progress += f" / `{format_bytes(total_size)}` (`{(downloaded/total_size)*100:.1f}%`)"
                            await safe_edit(status_msg, progress)
                return True, None
    except Exception as e: return False, str(e)

# --- محرك yt-dlp العام (يدعم أي صيغة) ---
async def download_generic(url, status_msg):
    loop = asyncio.get_event_loop()
    out_name = f"file_{int(time.time())}.tmp"
    
    ydl_opts = {
        'outtmpl': out_name,
        'format': 'best', # تحميل أفضل صيغة متاحة دون تحويل
        'quiet': True,
        'no_warnings': True,
        'http_headers': HEADERS,
    }
    
    def run_dl():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        return filename if os.path.exists(filename) else None
    
    try:
        file_path = await loop.run_in_executor(None, run_dl)
        if file_path and os.path.exists(file_path): return True, file_path
    except Exception as e: print(f"Download error: {e}")
    return False, "تعذر تحميل الملف (قد يتطلب كابتشا أو تسجيل دخول)"

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def handler(event):
    if event.sender_id != ADMIN_ID: return
    url = event.text.strip()
    status_msg = await event.respond("⏳ **جاري تحليل الرابط...**")

    # محاولة التحميل
    file_name = f"download_{int(time.time())}"
    success, err = await download_direct(url, file_name, status_msg)
    
    if not success:
        await safe_edit(status_msg, "🛡️ **الرابط محمي، جاري استخدام المحرك العام...**")
        success, file_name = await download_generic(url, status_msg)

    if not success:
        await safe_edit(status_msg, f"❌ **فشلت العملية:**\n`{err}`")
        return

    # الرفع
    await safe_edit(status_msg, f"📤 **جاري الرفع إلى تلغرام ({VERSION})...**")
    is_video = file_name.lower().endswith(('.mp4', '.mkv', '.webm', '.mov'))
    
    try:
        if is_video:
            await bot.send_file(event.chat_id, file_name, caption=f"🎬 **ملف فيديو تم رفعه بنجاح عبر {VERSION}!**", supports_streaming=True)
        else:
            await bot.send_file(event.chat_id, file_name, caption=f"📦 **ملف (Zip/Doc/Other) تم رفعه بنجاح عبر {VERSION}!**")
        await status_msg.delete()
    except Exception as e: await safe_edit(status_msg, f"❌ خطأ: `{str(e)}`")
    finally:
        if os.path.exists(file_name): os.remove(file_name)

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print(f"Bot {VERSION} is running!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
