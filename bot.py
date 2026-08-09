import os
import asyncio
import time
import re
import shutil
import subprocess
import aiohttp
from urllib.parse import unquote
from google_play_scraper import app as gplay_app
from telethon import TelegramClient, events, Button

# إعدادات الحساب والبوت
API_ID = int(os.environ.get("API_ID", "30065326"))
API_HASH = os.environ.get("API_HASH", "a95066d2eba2c2262e88983a39ceeb4e")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8932698139:AAFGa9PzIoCG923GtdlKx4b5fD75ArXEMww")

# معرف الأدمن (0 للجميع، أو ضع ID الخاص بك)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

ALLOWED_EXTENSIONS = ('.mp4', '.mp3', '.apk', '.zip', '.xapk')
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

# ترويسة متصفح موحدة لمنع الحظر وبلاغات 403
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
}

bot = TelegramClient('url_uploader_bot', API_ID, API_HASH)

ACTIVE_TASKS = {}
QUEUE = asyncio.Queue()

def generate_thumbnail(video_path, thumb_path):
    try:
        cmd = [
            'ffmpeg', '-ss', '00:00:02', '-i', video_path,
            '-vframes', '1', '-q:v', '2', thumb_path, '-y'
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        return thumb_path if os.path.exists(thumb_path) else None
    except Exception:
        return None

def extract_package_id(url):
    """استخراج Package ID من رابط Google Play"""
    match = re.search(r'id=([a-zA-Z0-9_.]+)', url)
    return match.group(1) if match else None

def create_progress_bar(percentage):
    completed = int(percentage // 10)
    remaining = 10 - completed
    return f"[{'█' * completed}{'░' * remaining}]"

def format_time(seconds):
    if seconds <= 0 or seconds > 86400:
        return "جاري الحساب..."
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}س {m}د {s}ث"
    if m > 0:
        return f"{m}د {s}ث"
    return f"{s}ث"

def format_bytes(size):
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"

@bot.on(events.NewMessage(pattern=r'^/start$'))
async def start_handler(event):
    await event.respond(
        "🚀 **مرحباً بك في بوت التنزيل والرفع المتقدم v5.1 Pro**\n\n"
        "أرسل رابطاً مباشراً للملفات أو رابط تطبيق من **Google Play Store**.\n\n"
        "✨ **المميزات:**\n"
        "• تنزيل تطبيقات وألعاب Google Play بصيغة APK مباشرة وتجاوز الحظر\n"
        "• استخراج المعاينات والأيقونات تلقائياً\n"
        "• تحكم كامل بالعمليات (إيقاف/استئناف/إلغاء)\n"
        "• فحص السيرفر عبر `/stats`"
    )

@bot.on(events.NewMessage(pattern=r'^/stats$'))
async def stats_handler(event):
    total, used, free = shutil.disk_usage("/")
    active_count = len(ACTIVE_TASKS)
    queue_count = QUEUE.qsize()
    
    await event.respond(
        f"📊 **إحصائيات النظام v5.1 Pro:**\n\n"
        f"⚙️ **العمليات النشطة:** `{active_count}`\n"
        f"⏳ **قائمة الانتظار:** `{queue_count}`\n"
        f"💾 **المساحة المتاحة:** `{format_bytes(free)}`\n"
        f"📁 **المساحة المستخدمة:** `{format_bytes(used)}` / `{format_bytes(total)}`"
    )

@bot.on(events.CallbackQuery(pattern=r'^(pause|resume|cancel)_'))
async def control_buttons_handler(event):
    action, task_id = event.data.decode('utf-8').split('_', 1)
    
    if task_id not in ACTIVE_TASKS:
        await event.answer("⚠️ العملية غير موجودة أو انتهت بالفعل.", alert=True)
        return

    task_data = ACTIVE_TASKS[task_id]

    if action == "cancel":
        task_data['cancel_event'].set()
        await event.answer("❌ جاري إلغاء العملية...", alert=True)
    elif action == "pause":
        if not task_data['is_paused']:
            task_data['is_paused'] = True
            task_data['pause_event'].clear()
            await event.answer("⏸️ تم الإيقاف المؤقت", alert=True)
    elif action == "resume":
        if task_data['is_paused']:
            task_data['is_paused'] = False
            task_data['pause_event'].set()
            await event.answer("▶️ تم الاستئناف", alert=True)

async def download_gplay_apk(package_id, status_msg):
    """جلب معلومات التطبيق ورابط تنزيل موثوق"""
    await status_msg.edit(f"🔍 **جاري جلب معلومات التطبيق:** `{package_id}`...")
    
    try:
        app_info = gplay_app(package_id, lang='ar', country='us')
        app_title = app_info.get('title', package_id)
        icon_url = app_info.get('icon')
    except Exception:
        app_title = package_id
        icon_url = None

    download_url = f"https://d.apkpure.com/b/APK/{package_id}?version=latest"
    file_name = f"{re.sub(r'[^a-zA-Z0-9_]', '_', app_title)}.apk"
    
    return download_url, file_name, icon_url

async def process_download_job(event, url):
    task_id = f"{event.chat_id}_{event.id}"
    cancel_event = asyncio.Event()
    pause_event = asyncio.Event()
    pause_event.set()
    
    ACTIVE_TASKS[task_id] = {
        'cancel_event': cancel_event,
        'pause_event': pause_event,
        'is_paused': False
    }

    def get_buttons(is_paused=False):
        toggle_btn = Button.inline("▶️ استئناف", data=f"resume_{task_id}") if is_paused else Button.inline("⏸️ إيقاف مؤقت", data=f"pause_{task_id}")
        return [[toggle_btn, Button.inline("❌ إلغاء", data=f"cancel_{task_id}")]]

    status_msg = await event.respond("⏳ **جاري فحص الرابط ومصادر الخدمة...**", buttons=get_buttons())
    timeout_config = aiohttp.ClientTimeout(total=10800, connect=60)
    
    thumb_path = None
    icon_temp_path = None

    if "play.google.com" in url:
        package_id = extract_package_id(url)
        if not package_id:
            await status_msg.edit("❌ لم يتم العثور على المعرف الخاص بالتطبيق في رابط Google Play.", buttons=None)
            return
        
        url, file_name, icon_url = await download_gplay_apk(package_id, status_msg)
        
        if icon_url:
            icon_temp_path = f"{package_id}.jpg"
            try:
                async with aiohttp.ClientSession(headers=HEADERS) as sess:
                    async with sess.get(icon_url) as resp:
                        if resp.status == 200:
                            with open(icon_temp_path, 'wb') as f:
                                f.write(await resp.read())
                            thumb_path = icon_temp_path
            except Exception:
                thumb_path = None
    else:
        file_name = os.path.basename(url.split('?')[0]) or "downloaded_file"

    try:
        # إرسال ترويسة المتصفح لمنع خطأ 403
        async with aiohttp.ClientSession(timeout=timeout_config, headers=HEADERS) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    await status_msg.edit(f"❌ فشل الاتصال بالسيرفر. كود الاستجابة: `{response.status}`", buttons=None)
                    return

                total_size = int(response.headers.get('Content-Length', 0))
                if total_size > MAX_FILE_SIZE:
                    await status_msg.edit("❌ يتجاوز حجم الملف الحد المسموح به (2 جيجابايت).", buttons=None)
                    return

                downloaded = 0
                start_time = time.time()
                last_update_time = 0
                
                with open(file_name, 'wb') as f:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        if cancel_event.is_set():
                            f.close()
                            if os.path.exists(file_name):
                                os.remove(file_name)
                            await status_msg.edit("🛑 تم إلغاء العملية بنجاح.", buttons=None)
                            return

                        if not pause_event.is_set():
                            await status_msg.edit(f"⏸️ **التنزيل موقوف مؤقتاً:** `{file_name}`", buttons=get_buttons(is_paused=True))
                            await pause_event.wait()
                            start_time = time.time() - (downloaded / (speed if 'speed' in locals() and speed > 0 else 1))

                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        if now - last_update_time > 4:
                            last_update_time = now
                            elapsed = now - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 1
                            percentage = (downloaded / total_size * 100) if total_size > 0 else 0
                            progress_bar = create_progress_bar(percentage)
                            
                            status_text = (
                                f"📥 **جاري تنزيل APK / الملف... v5.1**\n"
                                f"📦 الملف: `{file_name}`\n\n"
                                f"`{progress_bar}` **{percentage:.1f}%**\n\n"
                                f"🚀 **السرعة:** `{format_bytes(speed)}/s`\n"
                                f"📦 **المُنزّل:** `{format_bytes(downloaded)}` / `{format_bytes(total_size)}`\n"
                                f"⏱️ **المتبقي:** `{format_time((total_size - downloaded) / speed if speed > 0 else 0)}`"
                            )
                            try:
                                await status_msg.edit(status_text, buttons=get_buttons(is_paused=False))
                            except Exception:
                                pass

        if file_name.lower().endswith('.mp4') and not thumb_path:
            thumb_path = generate_thumbnail(file_name, f"{file_name}.jpg")

        await status_msg.edit(f"📤 **جاري رفع:** `{file_name}` إلى تلغرام...", buttons=[[Button.inline("❌ إلغاء", data=f"cancel_{task_id}")]])
        
        upload_start_time = time.time()
        last_upload_update = [0]

        async def upload_progress(current, total):
            if cancel_event.is_set():
                raise asyncio.CancelledError("تم إلغاء الرفع بواسطة المستخدم.")

            now = time.time()
            if now - last_upload_update[0] > 4:
                last_upload_update[0] = now
                elapsed = now - upload_start_time
                speed = current / elapsed if elapsed > 0 else 1
                eta = (total - current) / speed if speed > 0 else 0
                percentage = current * 100 / total if total > 0 else 0
                progress_bar = create_progress_bar(percentage)
                
                status_text = (
                    f"📤 **جاري الرفع إلى تلغرام... v5.1**\n"
                    f"📄 الملف: `{file_name}`\n\n"
                    f"`{progress_bar}` **{percentage:.1f}%**\n\n"
                    f"🚀 **السرعة:** `{format_bytes(speed)}/s`\n"
                    f"📦 **المرفوع:** `{format_bytes(current)}` / `{format_bytes(total)}`\n"
                    f"⏱️ **المتبقي:** `{format_time(eta)}`"
                )
                try:
                    await status_msg.edit(status_text, buttons=[[Button.inline("❌ إلغاء", data=f"cancel_{task_id}")]])
                except Exception:
                    pass

        await bot.send_file(
            event.chat_id,
            file_name,
            thumb=thumb_path,
            caption=f"📱 **اسم الملف / التطبيق:** `{file_name}`\n⚡ **تم الرفع بواسطة البوت v5.1 Pro**",
            progress_callback=upload_progress
        )

        await status_msg.delete()

    except asyncio.CancelledError:
        await status_msg.edit("🛑 تم إلغاء عملية الرفع بنجاح.", buttons=None)
    except Exception as e:
        await status_msg.edit(f"❌ حدث خطأ أثناء المعالجة:\n`{str(e)}`", buttons=None)
    finally:
        ACTIVE_TASKS.pop(task_id, None)
        if os.path.exists(file_name):
            os.remove(file_name)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)

async def queue_worker():
    while True:
        event, url = await QUEUE.get()
        try:
            await process_download_job(event, url)
        except Exception as e:
            print(f"Error handling task: {e}")
        finally:
            QUEUE.task_done()

@bot.on(events.NewMessage(pattern=r'^https?://'))
async def queue_handler(event):
    if ADMIN_ID != 0 and event.sender_id != ADMIN_ID:
        await event.respond("⛔ البوت مخصص للاستخدام الخاص فقط.")
        return

    url = event.text.strip()
    await QUEUE.put((event, url))
    
    qsize = QUEUE.qsize()
    if qsize > 1:
        await event.respond(f"⏳ **تمت إضافة العملية لطابور الانتظار.**\nترتيبك: `{qsize - 1}`")

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot v5.1 Pro is running successfully with Telethon...")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
