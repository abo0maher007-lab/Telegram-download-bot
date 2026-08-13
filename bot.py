import asyncio
import os
import threading
import time
import yt_dlp
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient, events

# --- الإعدادات ---
VERSION = "v8.9"
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))

# --- سيرفر وهمي للـ Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

bot = TelegramClient('bot_session', int(API_ID), API_HASH)

# --- وظيفة تحديث العداد (Progress Hook) ---
def progress_hook(d, status_msg, loop):
    if d['status'] == 'downloading':
        try:
            p = d.get('_percent_str', '0%')
            s = d.get('_speed_str', 'N/A')
            e = d.get('_eta_str', 'N/A')
            text = f"📥 **جاري التحميل ({VERSION})...**\n📊 النسبة: `{p}`\n🚀 السرعة: `{s}`\n⏱ الوقت المتبقي: `{e}`"
            # تشغيل تحديث الرسالة في حلقة الأحداث (asyncio)
            asyncio.run_coroutine_threadsafe(status_msg.edit(text), loop)
        except: pass

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def download_handler(event):
    url = event.text.strip()
    status_msg = await event.respond("🔍 **جاري تحليل الرابط...**")
    
    loop = asyncio.get_event_loop()
    
    # تعريف الـ hook ليستخدم الـ status_msg الخاص بالرسالة الحالية
    def hook(d): progress_hook(d, status_msg, loop)

    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': '%(title)s.%(ext)s',
        'progress_hooks': [hook], # تفعيل العداد
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await status_msg.edit("⏳ **بدء التحميل...**")
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        
        if os.path.exists(filename):
            await status_msg.edit("📤 **جاري الرفع إلى تليجرام...**")
            await bot.send_file(event.chat_id, filename, caption=f"✅ **تم التحميل والرفع بنجاح!**\nاسم الملف: `{os.path.basename(filename)}`")
            await status_msg.delete()
            os.remove(filename)
        else:
            await status_msg.edit("❌ فشل تحميل الملف.")
            
    except Exception as e:
        await status_msg.edit(f"❌ خطأ: `{str(e)}`")

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
