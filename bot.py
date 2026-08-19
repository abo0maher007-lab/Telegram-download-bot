import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient

from config import API_ID, API_HASH, BOT_TOKEN, PORT
from utils import update_libraries, clean_download_folder
from database import init_db
from handlers import register_handlers

update_libraries()
init_db()
clean_download_folder()

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

bot = TelegramClient('bot_session', API_ID, API_HASH)

def main():
    register_handlers(bot)
    bot.start(bot_token=BOT_TOKEN)
    print("🤖 البوت يعمل بأعلى كفاءة مع الكود المقسم والمصلح!")
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()

if __name__ == '__main__':
    main()    setup_all_cookies()
    clean_download_folder()

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
  
