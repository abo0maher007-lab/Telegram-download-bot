import asyncio
import os
import threading
import time
import requests
import yt_dlp
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient, events, Button

# --- الإعدادات ---
VERSION = "v9.0-DirectDownload"
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))

ACTIVE_DOWNLOADS = {}
LAST_UPDATE_TIME = {}

# --- سيرفر وهمي ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

bot = TelegramClient('bot_session', int(API_ID), API_HASH)

@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    welcome_text = (
        f"🚀 **أهلاً بك في بوت التنزيل والرفع المتقدم ({VERSION})**\n\n"
        "✨ **المميزات الجدية:**\n"
        " 📥 **عداد تنزيل ورفع فريد:** متابعة الرفع والتنزيل بالنسبة المئوية والسرعة.\n"
        " 🔗 **دعم كامل للروابط المباشرة:** تحميل مباشر لسيرفرات الأفلام والمسلسلات (`.mp4`).\n"
        " 🎬 **تشغيل مباشر:** يدعم المشاهدة داخل تليجرام (Streaming).\n"
        " ❌ **إلغاء أسرع:** زر إلغاء في جميع مراحل العمليات.\n\n"
        "👇 **أرسل أي رابط للبدء!**"
    )
    await event.respond(welcome_text)

# --- دالة تحديث عداد الرفع إلى تليجرام ---
async def upload_progress_callback(current, total, status_msg, cancel_event):
    if cancel_event.is_set():
        raise Exception("CANCELLED_BY_USER")
        
    now = time.time()
    msg_id = status_msg.id
    if msg_id in LAST_UPDATE_TIME and (now - LAST_UPDATE_TIME[msg_id]) < 2.0:
        return
        
    LAST_UPDATE_TIME[msg_id] = now
    percent = (current / total) * 100
    
    # تحويل الحجم إلى ميجابايت
    curr_mb = current / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    
    text = (
        f"📤 **جاري الرفع إلى تليجرام...**\n"
        f"📊 النسبة: `{percent:.1f}%`\n"
        f"💾 الحجم: `{curr_mb:.1f}MB / {total_mb:.1f}MB`"
    )
    buttons = [Button.inline("❌ إلغاء الرفع", data=f"cancel_{status_msg.id}")]
    try:
        await status_msg.edit(text, buttons=buttons)
    except: pass

# --- دالة التحميل المباشر للروابط التي تنتهي بـ .mp4 ---
def download_direct_file(url, filepath, status_msg, loop, cancel_event):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    response = requests.get(url, stream=True, headers=headers, timeout=30)
    response.raise_for_status()
    
    total_length = response.headers.get('content-length')
    total = int(total_length) if total_length else 0
    downloaded = 0
    
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=1024*1024): # 1MB chunks
            if cancel_event.is_set():
                raise Exception("CANCELLED_BY_USER")
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                
                # تحديث عداد التحميل
                now = time.time()
                msg_id = status_msg.id
                if msg_id not in LAST_UPDATE_TIME or (now - LAST_UPDATE_TIME[msg_id]) >= 2.0:
                    LAST_UPDATE_TIME[msg_id] = now
                    p = f"{(downloaded / total * 100):.1f}%" if total else "N/A"
                    d_mb = downloaded / (1024*1024)
                    t_mb = total / (1024*1024) if total else 0
                    
                    text = f"📥 **جاري التحميل المباشر...**\n📊 النسبة: `{p}`\n💾 المحمل: `{d_mb:.1f}MB / {t_mb:.1f}MB`"
                    buttons = [Button.inline("❌ إلغاء التحميل", data=f"cancel_{status_msg.id}")]
                    asyncio.run_coroutine_threadsafe(status_msg.edit(text, buttons=buttons), loop)

# --- معالج التحميل الرئيسي ---
@bot.on(events.NewMessage(pattern=r"^https?://"))
async def download_handler(event):
    url = event.text.strip()
    status_msg = await event.respond("🔍 **جاري فحص الرابط...**", buttons=[Button.inline("❌ إلغاء", data="cancel_init")])
    
    cancel_event = threading.Event()
    ACTIVE_DOWNLOADS[status_msg.id] = cancel_event
    loop = asyncio.get_event_loop()

    filename = f"downloads/{status_msg.id}_video.mp4"
    
    try:
        # 1. فحص ما إذا كان الرابط ملفاً مباشراً (ينتهي بـ .mp4 أو يحتوي على امتداد فيديو)
        is_direct_url = any(url.lower().rsplit('?')[0].endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov'])
        
        if is_direct_url:
            await status_msg.edit("📥 **بدء التحميل المباشر...**", buttons=[Button.inline("❌ إلغاء", data=f"cancel_{status_msg.id}")])
            await loop.run_in_executor(None, download_direct_file, url, filename, status_msg, loop, cancel_event)
        else:
            # 2. الاستعانة بـ yt_dlp للروابط العامة (يوتيوب، تويتر، إلخ)
            def progress_hook_ytdlp(d):
                if cancel_event.is_set(): raise Exception("CANCELLED_BY_USER")
                if d['status'] == 'downloading':
                    now = time.time()
                    if status_msg.id in LAST_UPDATE_TIME and (now - LAST_UPDATE_TIME[status_msg.id]) < 2.0: return
                    LAST_UPDATE_TIME[status_msg.id] = now
                    p = d.get('_percent_str', '0%')
                    s = d.get('_speed_str', 'N/A')
                    text = f"📥 **جاري التحميل...**\n📊 النسبة: `{p}`\n🚀 السرعة: `{s}`"
                    asyncio.run_coroutine_threadsafe(status_msg.edit(text, buttons=[Button.inline("❌ إلغاء", data=f"cancel_{status_msg.id}")]), loop)

            ydl_opts = {
                'format': 'best',
                'quiet': True,
                'outtmpl': filename,
                'progress_hooks': [progress_hook_ytdlp],
            }
            def run_ytdlp():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

            await status_msg.edit("⏳ **بدء التحميل...**", buttons=[Button.inline("❌ إلغاء", data=f"cancel_{status_msg.id}")])
            await loop.run_in_executor(None, run_ytdlp)

        if cancel_event.is_set():
            raise Exception("CANCELLED_BY_USER")

        # 3. الرفع إلى تليجرام مع عداد الرفع المباشر
        if os.path.exists(filename):
            await status_msg.edit("📤 **جاري بدء الرفع...**", buttons=None)
            
            await bot.send_file(
                event.chat_id, 
                filename, 
                supports_streaming=True, 
                caption=f"✅ **تم التحميل والرفع بنجاح!**",
                progress_callback=lambda c, t: upload_progress_callback(c, t, status_msg, cancel_event)
            )
            await status_msg.delete()
        else:
            await status_msg.edit("❌ لم يتم العثور على الملف بعد التحميل.", buttons=None)

    except Exception as e:
        if "CANCELLED_BY_USER" in str(e):
            await status_msg.edit("🛑 **تم إلغاء العملية.**", buttons=None)
        else:
            await status_msg.edit(f"❌ خطأ: `{str(e)}`", buttons=None)
            
    finally:
        ACTIVE_DOWNLOADS.pop(status_msg.id, None)
        LAST_UPDATE_TIME.pop(status_msg.id, None)
        if os.path.exists(filename):
            try: os.remove(filename)
            except: pass

@bot.on(events.CallbackQuery(pattern=r"^cancel_"))
async def cancel_handler(event):
    msg_id = event.message_id
    if msg_id in ACTIVE_DOWNLOADS:
        ACTIVE_DOWNLOADS[msg_id].set()
        await event.answer("جاري الإلغاء...", alert=False)

async def main():
    os.makedirs("downloads", exist_ok=True)
    await bot.start(bot_token=BOT_TOKEN)
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
