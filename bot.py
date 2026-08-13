import asyncio
import os
import sys
import threading
import time
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo

# --- الإعدادات ---
VERSION = "v8.7"
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))

# --- التحقق من المتغيرات ---
if not all([API_ID, API_HASH, BOT_TOKEN]):
    print("❌ ERROR: يرجى التأكد من إضافة API_ID, API_HASH, و BOT_TOKEN في إعدادات Environment في Render!")
    sys.exit(1)

bot = TelegramClient('bot_session', int(API_ID), API_HASH)

# --- سيرفر وهمي (لإبقاء الخدمة مستيقظة) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active")
    def log_message(self, format, *args): pass

def run_server():
    httpd = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    print(f"🌐 HealthCheck Server started on port {PORT}")
    httpd.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# --- وظائف مساعدة ---
def format_bytes(size):
    return f"{size / (1024 * 1024 * 1024):.2f} GB" if size >= 1024*1024*1024 else f"{size / (1024 * 1024):.1f} MB"

async def safe_edit(msg, text):
    try: await msg.edit(text)
    except: pass

# --- الأوامر ---
@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    await event.respond(f"🚀 **البوت يعمل بنجاح (v{VERSION})**\nأرسل رابط الملف للتحميل!")

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def download_handler(event):
    url = event.text.strip()
    status_msg = await event.respond("⏳ **جاري البدء...**")
    
    # استخدام أمر curl لتحميل الملف (أكثر استقراراً في البيئات المحدودة)
    file_name = f"download_{int(time.time())}"
    
    try:
        # تحميل بسيط باستخدام curl
        cmd = ["curl", "-L", "-o", file_name, url]
        subprocess.run(cmd, check=True)
        
        if os.path.exists(file_name) and os.path.getsize(file_name) > 100:
            await safe_edit(status_msg, "📤 **جاري الرفع...**")
            await bot.send_file(event.chat_id, file_name, caption="✅ **تم التحميل والرفع بنجاح!**")
            await status_msg.delete()
        else:
            await safe_edit(status_msg, "❌ فشل تحميل الملف.")
            
    except Exception as e:
        await safe_edit(status_msg, f"❌ خطأ: {str(e)}")
    finally:
        if os.path.exists(file_name): os.remove(file_name)

async def main():
    print(f"🤖 Bot v{VERSION} starting...")
    await bot.start(bot_token=BOT_TOKEN)
    print("✅ Bot is polling for messages...")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
