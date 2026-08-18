import threading
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient

from config import API_ID, API_HASH, BOT_TOKEN, PORT, setup_all_cookies
from database import init_db
from utils import clean_download_folder
from handlers import register_handlers

def update_libraries():
    """تحديث مكتبات التنزيل الأساسية تلقائياً لمنع توقف الخدمة"""
    try:
        subprocess.run(
            ["pip", "install", "-U", "yt-dlp", "curl_cffi"], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        print("✅ تم تحديث مكتبات التنزيل الأساسية بنجاح.")
    except Exception as e:
        print(f"⚠️ فشل التحديث التلقائي: {e}")

class HealthCheckHandler(BaseHTTPRequestHandler):
    """سيرفر فحص الحياة (Health Check) لإبقاء البوت نشطاً على الاستضافة"""
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        
    def log_message(self, *args): 
        pass  # إخفاء سجلات الطلبات العادية للحفاظ على نظافة الـ Logs

def main():
    # 1. تهيئة البيئة وقواعد البيانات
    update_libraries()
    init_db()
    setup_all_cookies()
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
  
