import asyncio
import os
import shutil
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import aiohttp
import gdown
from mega import Mega
from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError

# --- خادم HTTP خفيف لإرضاء Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")

    def log_message(self, format, *args):
        return

def start_health_check_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=start_health_check_server, daemon=True).start()

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ADMIN_ID = 5414125521
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

bot = TelegramClient(None, API_ID, API_HASH, timeout=600, connection_retries=15, retry_delay=5)

try:
    mega = Mega()
    m = mega.login()
except Exception as e:
    print(f"Mega login warning: {e}")
    m = None

QUEUE = asyncio.Queue()
PROCESSED_MESSAGES = set()
CANCEL_EVENTS = {}  # قاموس لمتابعة أحداث الإلغاء

# --- الدوال المساعدة ---
def format_bytes(size):
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"

def format_time(seconds):
    if seconds <= 0 or seconds > 86400:
        return "جاري الحساب..."
    m_val, s = divmod(int(seconds), 60)
    h, m_val = divmod(m_val, 60)
    return f"{h}س {m_val}د {s}ث" if h > 0 else f"{m_val}د {s}ث"

def create_progress_bar(percentage):
    completed = int(percentage // 10)
    return f"[{'█' * completed}{'░' * (10 - completed)}]"

async def safe_edit_message(msg, text, buttons=None):
    try:
        await msg.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        print(f"Edit msg error: {e}")

# --- معالجة التنزيل ---
async def download_file_direct(url, output_path, status_msg, cancel_event, task_id):
    timeout_config = aiohttp.ClientTimeout(total=21600, connect=120, sock_read=300)
    loop = asyncio.get_running_loop()
    cancel_button = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]

    # 1. Google Drive
    if "drive.google.com" in url:
        await safe_edit_message(status_msg, "📥 **جاري التنزيل من Google Drive...**", buttons=cancel_button)
        try:
            download_task = loop.run_in_executor(None, lambda: gdown.download(url, output_path, quiet=True))
            while not download_task.done():
                if cancel_event.is_set():
                    return False, "تم إلغاء التنزيل بواسطة المستخدم."
                await asyncio.sleep(1)
            if os.path.exists(output_path):
                return True, None
            return False, "فشل تنزيل الملف من Drive."
        except Exception as e:
            return False, f"خطأ Drive: {str(e)}"

    # 2. Mega
    if "mega.nz" in url:
        if not m:
            return False, "خدمة Mega غير مفعلة حالياً."
        await safe_edit_message(status_msg, "📥 **جاري التنزيل من Mega...**\n⏳ يرجى الانتظار...", buttons=cancel_button)
        try:
            download_task = loop.run_in_executor(None, lambda: m.download_url(url))
            while not download_task.done():
                if cancel_event.is_set():
                    return False, "تم إلغاء التنزيل بواسطة المستخدم."
                await asyncio.sleep(1)
            downloaded_path = await download_task
            if downloaded_path and os.path.exists(downloaded_path):
                if downloaded_path != output_path:
                    shutil.move(downloaded_path, output_path)
                return True, None
            return False, "فشل تنزيل ملف Mega."
        except Exception as e:
            return False, f"خطأ Mega: {str(e)}"

    # 3. التحميل المباشر
    async with aiohttp.ClientSession(timeout=timeout_config, headers=HEADERS) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return False, f"خطأ السيرفر: {resp.status}"
            total_size = int(resp.headers.get("Content-Length", 0))
            if total_size > MAX_FILE_SIZE:
                return False, "حجم الملف يتجاوز الحد المسموح (2GB)."
            
            downloaded = 0
            start_time = time.time()
            last_edit = start_time
            
            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if cancel_event.is_set():
                        return False, "تم إلغاء التنزيل بواسطة المستخدم."
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    now = time.time()
                    if now - last_edit >= 3 and total_size > 0:
                        speed = downloaded / (now - start_time)
                        eta = (total_size - downloaded) / speed if speed > 0 else 0
                        percent = (downloaded / total_size) * 100
                        text = (
                            f"📥 **جاري التحميل المباشر...**\n"
                            f"{create_progress_bar(percent)} {percent:.1f}%\n"
                            f"⚡ **السرعة:** {format_bytes(speed)}/ثانية\n"
                            f"📦 **الحجم:** {format_bytes(downloaded)} / {format_bytes(total_size)}\n"
                            f"⏱ **الوقت المتبقي:** {format_time(eta)}"
                        )
                        await safe_edit_message(status_msg, text, buttons=cancel_button)
                        last_edit = now
            return True, None

# --- معالجة المهام ورفع التلغرام ---
async def process_download_job(event, url, status_msg, task_id):
    filename = f"file_{int(time.time())}.tmp"
    output_path = os.path.join(os.getcwd(), filename)
    cancel_event = asyncio.Event()
    CANCEL_EVENTS[task_id] = cancel_event
    cancel_button = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]
    
    try:
        success, error = await download_file_direct(url, output_path, status_msg, cancel_event, task_id)
        if not success:
            await safe_edit_message(status_msg, f"❌ **توقفت العملية:**\n`{error}`", buttons=None)
            return
            
        if cancel_event.is_set():
            await safe_edit_message(status_msg, "❌ **تم إلغاء العملية بواسطة المستخدم.**", buttons=None)
            return

        await safe_edit_message(status_msg, "📤 **اكتمل التنزيل! جاري الرفع إلى تلغرام...**", buttons=cancel_button)
        
        last_edit = [time.time()]
        start_time = time.time()
        
        async def upload_callback(current, total):
            if cancel_event.is_set():
                raise asyncio.CancelledError("Upload cancelled by user")
            now = time.time()
            if now - last_edit[0] >= 3:
                speed = current / (now - start_time)
                eta = (total - current) / speed if speed > 0 else 0
                percent = (current / total) * 100
                text = (
                    f"📤 **جاري الرفع إلى تلغرام...**\n"
                    f"{create_progress_bar(percent)} {percent:.1f}%\n"
                    f"⚡ **السرعة:** {format_bytes(speed)}/ثانية\n"
                    f"📦 **الحجم:** {format_bytes(current)} / {format_bytes(total)}\n"
                    f"⏱ **الوقت المتبقي:** {format_time(eta)}"
                )
                await safe_edit_message(status_msg, text, buttons=cancel_button)
                last_edit[0] = now

        await bot.send_file(
            event.chat_id,
            output_path,
            caption="✅ **تم الرفع بنجاح!**",
            progress_callback=upload_callback,
            supports_streaming=True
        )
        await safe_edit_message(status_msg, "🎉 **تمت العملية بنجاح!**", buttons=None)
        
    except asyncio.CancelledError:
        await safe_edit_message(status_msg, "❌ **تم إلغاء العملية أثناء الرفع.**", buttons=None)
    except Exception as e:
        await safe_edit_message(status_msg, f"❌ **خطأ غير متوقع:** {str(e)}", buttons=None)
    finally:
        CANCEL_EVENTS.pop(task_id, None)
        if os.path.exists(output_path):
            os.remove(output_path)

async def queue_worker():
    while True:
        event, url, status_msg, task_id = await QUEUE.get()
        try:
            await process_download_job(event, url, status_msg, task_id)
        except Exception as e:
            print(f"Queue worker error: {e}")
        finally:
            QUEUE.task_done()

# --- الأحداث والأوامر ---
@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    
    welcome_text = (
        "مرحباً بك في بوت التنزيل والرفع المباشر v5.9 Pro 🚀\n\n"
        "💡 ماذا يقدم هذا البوت؟\n"
        "يقوم البوت بأخذ الروابط المباشرة للملفات من الإنترنت، ويدعم تحميلها ثم إعادة رفعها لك مباشرة داخل تلغرام بسرعة عالية جداً دون استهلاك بياناتك!\n\n"
        "📦 المميزات والصيغ المدعومة:\n"
        "• 📏 الحد الأقصى: ملفات بحجم يصل إلى 2 جيجابايت (2GB).\n"
        "• 🎬 الفيديوهات والصوت: MP4, MKV, MP3, WEBM...\n"
        "• 📦 الملفات المضغوطة: ZIP, RAR, 7Z, ISO...\n"
        "• 📱 التطبيقات وروابط Mega وجوجل درايف: APK وغيرها.\n\n"
        "✍️ كيفية الاستخدام:\n"
        "قم بنسخ الرابط المباشر لأي ملف وأرسله هنا مباشرة في المحادثة!"
    )
    await event.respond(welcome_text)

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def queue_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    if event.text.startswith("/"):
        return
    if event.id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(event.id)
    
    task_id = str(event.id)
    status_msg = await event.respond("⏳ **تم استلام الرابط، جاري بدء العملية...**")
    await QUEUE.put((event, event.text.strip(), status_msg, task_id))

@bot.on(events.CallbackQuery(pattern=r"^cancel_"))
async def cancel_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    task_id = event.data.decode("utf-8").replace("cancel_", "")
    if task_id in CANCEL_EVENTS:
        CANCEL_EVENTS[task_id].set()
        await event.answer("⚠️ جاري إلغاء العملية...", alert=True)
    else:
        await event.answer("⚠️ العملية غير موجودة أو اكتملت بالفعل.", alert=True)

# --- نقطة الدخول الرئيسية ---
async def main():
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot started successfully!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
