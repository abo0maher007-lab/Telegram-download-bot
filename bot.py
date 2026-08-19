import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient

from config import API_ID, API_HASH, BOT_TOKEN, PORT
import handlers  # تسجيل الأحداث والأوامر

# --- Health Check لسيرفر Railway ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

# --- إنشاء كائن البوت ---
bot = TelegramClient('bot_session', API_ID, API_HASH)

# تسجيل الـ Handlers مع البوت
handlers.register_handlers(bot)

# --- تشغيل البوت بنظام Async المباشر ---
async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 البوت يعمل بأعلى كفاءة في النظام المقسّم!")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())if __name__ == '__main__':
    asyncio.run(main())
