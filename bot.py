import asyncio
import os
import threading
import time
import yt_dlp
from http.server import BaseHTTPRequestHandler, HTTPServer
from telethon import TelegramClient, events, Button

# --- الإعدادات ---
VERSION = "v8.9-CancelSupported"
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 8080))

# قاموس لتتبع عمليات التحميل النشطة وإلغائها
ACTIVE_DOWNLOADS = {}

# --- سيرفر وهمي للـ Render / Railway ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self): 
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever(), daemon=True).start()

bot = TelegramClient('bot_session', int(API_ID), API_HASH)

# --- وظيفة تحديث العداد وضبط الإلغاء ---
def progress_hook(d, status_msg, loop, cancel_event):
    # التحقق من طلب الإلغاء
    if cancel_event.is_set():
        raise Exception("CANCELLED_BY_USER")

    if d['status'] == 'downloading':
        try:
            p = d.get('_percent_str', '0%')
            s = d.get('_speed_str', 'N/A')
            e = d.get('_eta_str', 'N/A')
            text = f"📥 **جاري التحميل ({VERSION})...**\n📊 النسبة: `{p}`\n🚀 السرعة: `{s}`\n⏱ الوقت المتبقي: `{e}`"
            
            # زر الإلغاء
            buttons = [Button.inline("❌ إلغاء التحميل", data=f"cancel_{status_msg.id}")]
            
            asyncio.run_coroutine_threadsafe(status_msg.edit(text, buttons=buttons), loop)
        except Exception:
            pass

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def download_handler(event):
    url = event.text.strip()
    status_msg = await event.respond(
        "🔍 **جاري تحليل الرابط...**", 
        buttons=[Button.inline("❌ إلغاء", data="cancel_init")]
    )
    
    # ربط زر الإلغاء بمعرف الرسالة
    cancel_event = threading.Event()
    ACTIVE_DOWNLOADS[status_msg.id] = cancel_event
    
    loop = asyncio.get_event_loop()

    def hook(d): progress_hook(d, status_msg, loop, cancel_event)

    ydl_opts = {
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': f'downloads/{status_msg.id}_%(title)s.%(ext)s',
        'progress_hooks': [hook],
    }
    
    filename = None
    try:
        # تشغيل yt_dlp في منفصل لتجنب حظر الـ asyncio loop
        def run_dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        await status_msg.edit("⏳ **بدء التحميل...**", buttons=[Button.inline("❌ إلغاء", data=f"cancel_{status_msg.id}")])
        filename = await loop.run_in_executor(None, run_dl)
        
        # التأكد من عدم إلغاء العملية أثناء الانتظار
        if cancel_event.is_set():
            raise Exception("CANCELLED_BY_USER")

        if filename and os.path.exists(filename):
            await status_msg.edit("📤 **جاري الرفع إلى تليجرام...**", buttons=None)
            await bot.send_file(
                event.chat_id, 
                filename, 
                caption=f"✅ **تم التحميل والرفع بنجاح!**\nاسم الملف: `{os.path.basename(filename)}`"
            )
            await status_msg.delete()
        else:
            await status_msg.edit("❌ فشل تحميل الملف.", buttons=None)
            
    except Exception as e:
        if "CANCELLED_BY_USER" in str(e):
            await status_msg.edit("🛑 **تم إلغاء عملية التحميل بنجاح.**", buttons=None)
        else:
            await status_msg.edit(f"❌ خطأ: `{str(e)}`", buttons=None)
            
    finally:
        # تنظيف الملفات والأحداث
        ACTIVE_DOWNLOADS.pop(status_msg.id, None)
        if filename and os.path.exists(filename):
            try: os.remove(filename)
            except: pass

# --- التعامل مع ضغط زر الإلغاء ---
@bot.on(events.CallbackQuery(pattern=r"^cancel_"))
async def cancel_handler(event):
    msg_id = event.message_id
    if msg_id in ACTIVE_DOWNLOADS:
        ACTIVE_DOWNLOADS[msg_id].set()  # إرسال إشارة الإلغاء
        await event.answer("جاري إلغاء العملية...", alert=False)
    else:
        await event.answer("هذه العملية غير نشطة أو انتهت بالفعل.", alert=True)

async def main():
    os.makedirs("downloads", exist_ok=True)
    await bot.start(bot_token=BOT_TOKEN)
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
