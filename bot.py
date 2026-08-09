import asyncio
import os
import re
import yt_dlp
from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError

# قراءة البيانات من متغيرات البيئة بدون قيم افتراضية مكشوفة
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# إعداد المجلد المحلي المؤقت لحفظ الملفات والجلسة على Render
DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = TelegramClient('savevy_render_session', API_ID, API_HASH)

ACTIVE_TASKS = {}
PENDING_URLS = {}

def format_bytes(size):
    if not size: return "N/A"
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"

def create_progress_bar(percentage):
    completed = int(percentage // 10)
    return f"[{'█' * completed}{'░' * (10 - completed)}]"

async def safe_edit(msg, text, buttons=None):
    try:
        await msg.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        print(f"Edit Error: {e}")

def extract_youtube_qualities(url):
    ydl_opts = {'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get('formats', [])
        
        qualities = set()
        for f in formats:
            height = f.get('height')
            if height and f.get('vcodec') != 'none':
                if height >= 1080: qualities.add('1080p')
                elif height >= 720: qualities.add('720p')
                elif height >= 480: qualities.add('480p')
                elif height >= 360: qualities.add('360p')
        
        sorted_qualities = sorted(list(qualities), key=lambda x: int(x.replace('p', '')), reverse=True)
        return sorted_qualities, info.get('title', 'فيديو يوتيوب')

async def download_and_send(url, format_str, chat_id, status_msg):
    task_id = f"{chat_id}_{status_msg.id}"
    cancel_event = asyncio.Event()
    ACTIVE_TASKS[task_id] = cancel_event

    output_template = os.path.join(DOWNLOAD_DIR, f"{chat_id}_%(id)s.%(ext)s")
    
    if format_str == "audio":
        y_format = "bestaudio/best"
    elif format_str == "best":
        y_format = "bestvideo+bestaudio/best"
    else:
        res = format_str.replace('p', '')
        y_format = f"bestvideo[height<={res}]+bestaudio/best[height<={res}]/best"

    loop = asyncio.get_running_loop()
    last_update = [0]

    def thread_safe_update(text, buttons):
        asyncio.run_coroutine_threadsafe(safe_edit(status_msg, text, buttons), loop)

    def progress_hook(d):
        if cancel_event.is_set():
            raise Exception("CANCELLED")

        if d['status'] == 'downloading':
            now = loop.time()
            if now - last_update[0] > 3:
                last_update[0] = now
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0)
                
                pct = (downloaded / total * 100) if total > 0 else 0
                
                text = (
                    f"📥 **جاري تنزيل الفيديو...**\n\n"
                    f"`{create_progress_bar(pct)}` **{pct:.1f}%**\n\n"
                    f"🚀 السرعة: `{format_bytes(speed)}/s`\n"
                    f"📦 الحجم: `{format_bytes(downloaded)}` / `{format_bytes(total)}`"
                )
                
                buttons = [[Button.inline("❌ إلغاء العملية", data=f"cancel_{status_msg.id}")]]
                loop.call_soon_threadsafe(thread_safe_update, text, buttons)

    ydl_opts = {
        'format': y_format,
        'outtmpl': output_template,
        'progress_hooks': [progress_hook],
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4'
    }

    filename = None

    try:
        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        filename = await loop.run_in_executor(None, _download)

        if not os.path.exists(filename):
            base_fn = os.path.splitext(filename)[0]
            if os.path.exists(f"{base_fn}.mp4"):
                filename = f"{base_fn}.mp4"

        await safe_edit(status_msg, "📤 **جاري الرفع إلى تيليجرام...**")
        
        file_size = os.path.getsize(filename)
        await bot.send_file(
            chat_id,
            filename,
            caption=f"✅ **تم التحميل بنجاح!**\n📦 **الحجم:** `{format_bytes(file_size)}`",
            supports_streaming=True
        )
        await status_msg.delete()

    except Exception as e:
        if cancel_event.is_set() or "CANCELLED" in str(e):
            await safe_edit(status_msg, "⚠️ **تم إلغاء العملية بنجاح.**")
        else:
            await safe_edit(status_msg, f"❌ **حدث خطأ أثناء التنزيل:** {str(e)[:100]}")
    finally:
        ACTIVE_TASKS.pop(task_id, None)
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(str(chat_id)):
                try: os.remove(os.path.join(DOWNLOAD_DIR, f))
                except: pass

@bot.on(events.NewMessage(pattern=r'^(https?://[^\s]+)'))
async def link_handler(event):
    url = event.text.strip()
    chat_id = event.chat_id

    is_youtube = re.search(r'(youtube\.com|youtu\.be)', url)

    if is_youtube:
        msg = await event.respond("🔍 **جاري فحص جودات الفيديو المتاحة...**")
        loop = asyncio.get_running_loop()
        try:
            qualities, title = await loop.run_in_executor(None, lambda: extract_youtube_qualities(url))
            
            if not qualities:
                qualities = ['720p', '360p']

            url_key = f"{chat_id}_{msg.id}"
            PENDING_URLS[url_key] = url

            buttons = []
            row = []
            for q in qualities:
                row.append(Button.inline(f"🎬 {q}", data=f"q_{q}_{msg.id}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row: buttons.append(row)
            
            buttons.append([Button.inline("🎵 صوت فقط (MP3)", data=f"q_audio_{msg.id}")])

            await safe_edit(
                msg, 
                f"🎥 **{title[:50]}**\n\nاختر جودة التحميل المطلوبة:", 
                buttons=buttons
            )
        except Exception as e:
            await safe_edit(msg, "⚡ **جاري بدء التحميل بأفضل جودة متميزة...**")
            await download_and_send(url, "best", chat_id, msg)
    else:
        msg = await event.respond("⚡ **جاري بدء التحميل...**", buttons=[[Button.inline("❌ إلغاء العملية", data=f"cancel_{event.id}")]])
        await download_and_send(url, "best", chat_id, msg)

@bot.on(events.CallbackQuery(pattern=r'^q_(.+)_(\d+)$'))
async def quality_callback(event):
    quality = event.pattern_match.group(1).decode('utf-8')
    msg_id = int(event.pattern_match.group(2).decode('utf-8'))
    chat_id = event.chat_id
    
    url_key = f"{chat_id}_{msg_id}"
    url = PENDING_URLS.pop(url_key, None)

    if not url:
        await event.answer("⚠️ انتهت صلاحية هذا الطلب، أرسل الرابط مجدداً.", alert=True)
        return

    await event.answer(f"تم اختيار جودة {quality}، جاري البدء...", alert=False)
    status_msg = await event.get_message()
    await safe_edit(status_msg, "⏳ **جاري تحضير ملف التنزيل...**")
    
    asyncio.create_task(download_and_send(url, quality, chat_id, status_msg))

@bot.on(events.CallbackQuery(pattern=r'^cancel_(\d+)$'))
async def cancel_callback(event):
    msg_id = int(event.pattern_match.group(1).decode('utf-8'))
    chat_id = event.chat_id
    task_id = f"{chat_id}_{msg_id}"

    if task_id in ACTIVE_TASKS:
        ACTIVE_TASKS[task_id].set()
        await event.answer("🛑 جاري إلغاء العملية...", alert=True)
    else:
        await event.answer("⚠️ لا توجد عملية جارية لإلغائها.", alert=True)

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond(
        "🚀 **مرحباً بك في بوت التنزيل!**\n\n"
        "• أرسل أي رابط من **YouTube** لاختيار الجودة بدقة.\n"
        "• أرسل روابط **TikTok, Instagram, Twitter** للتحميل المباشر بأعلى جودة."
    )

print("البوت يعمل الآن على Render...")
bot.start(bot_token=BOT_TOKEN)
bot.run_until_disconnected()
