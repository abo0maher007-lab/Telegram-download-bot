import asyncio
import os
import sys
import threading
import time
import yt_dlp
from telethon import TelegramClient, events

# --- الإعدادات ---
VERSION = "v8.8"
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))

# تشغيل سيرفر وهمي للـ Render
from http.server import BaseHTTPRequestHandler, HTTPServer
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

bot = TelegramClient('bot_session', int(API_ID), API_HASH)

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def download_handler(event):
    url = event.text.strip()
    status_msg = await event.respond("🔍 **جاري تحليل الرابط واستخراج الملف الحقيقي...**")
    
    # إعدادات yt-dlp للتعامل مع MediaFire والمواقع المشابهة
    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': '%(title)s.%(ext)s',
    }
    
    try:
        # استخراج معلومات الملف وتحميله
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        
        if os.path.exists(filename):
            await status_msg.edit("📤 **جاري الرفع إلى تليجرام...**")
            await bot.send_file(event.chat_id, filename, caption=f"✅ **تم التحميل بنجاح!**\nاسم الملف: `{os.path.basename(filename)}`")
            await status_msg.delete()
            os.remove(filename) # تنظيف الملف بعد الرفع
        else:
            await status_msg.edit("❌ فشل تحميل الملف الحقيقي.")
            
    except Exception as e:
        await status_msg.edit(f"❌ خطأ أثناء التحميل: `{str(e)}`\n\n💡 *ملاحظة: تأكد أن الرابط عام وليس خاصاً.*")

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
