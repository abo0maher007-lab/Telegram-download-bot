import threading
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient

from config import API_ID, API_HASH, BOT_TOKEN, PORT, setup_all_cookies
from database import init_db
from utils import clean_download_folder
from handlers import register_handlers

def update_libraries():
    try:
        subprocess.run(["pip", "install", "-U", "yt-dlp", "curl_cffi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("✅ تم تحديث مكتبات التنزيل الأساسية.")
    except Exception as e:
        print(f"⚠️ فشل التحديث: {e}")

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

def main():
    update_libraries()
    init_db()
    setup_all_cookies()
    clean_download_folder()

    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

    bot = TelegramClient('bot_session', API_ID, API_HASH)
    register_handlers(bot)

    print("🤖 البوت يعمل بالهيكلية المقسمة الخالية من المشاكل!")
    bot.start(bot_token=BOT_TOKEN)
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
  
