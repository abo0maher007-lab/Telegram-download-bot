import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient

from config import API_ID, API_HASH, BOT_TOKEN, PORT, setup_all_cookies
from utils import update_libraries, clean_download_folder
from database import init_db
from handlers import register_handlers

# 1. إعداد البيئة وقاعدة البيانات
setup_all_cookies()
update_libraries()
init_db()
clean_download_folder()

# 2. خادم صحة الاستضافة (Railway Health Check Server)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

# 3. إنشاء كائن البوت
bot = TelegramClient('bot_session', API_ID, API_HASH)

async def main():
    register_handlers(bot)
    # تشغيل البوت باستخدام التوكن الممرر من config.py المربوط بمتغيرات المنصة
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 تم التشغيل بنجاح والربط مع متغيرات منصة Railway!")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())    loop.run_until_complete(main())    clean_download_folder()

    # 2. تشغيل سيرفر الـ Health Check في المسار الخلفي (Background Thread)
    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), 
        daemon=True
    ).start()

    # 3. تشغيل بوت تيليجرام وتسجيل المعالجات
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    register_handlers(bot)

    print("🤖 تم تشغيل البوت بنجاح بالهيكلية المقسّمة الجديدة عبر bot.py!")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()
  
