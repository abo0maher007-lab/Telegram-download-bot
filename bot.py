import asyncio
import os
import re
import shutil
import subprocess
import time
import aiohttp
from bs4 import BeautifulSoup
from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import DocumentAttributeVideo

# --- قراءة المتغيرات الحساسة من إعدادات المنصة ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://shahidtv.net/",
}

bot = TelegramClient(
    None,
    API_ID,
    API_HASH,
    timeout=600,
    connection_retries=15,
    retry_delay=5,
)

ACTIVE_TASKS = {}
QUEUE = asyncio.Queue()
PROCESSED_MESSAGES = set()


def format_bytes(size):
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_time(seconds):
    if seconds <= 0 or seconds > 86400:
        return "جاري الحساب..."
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}س {m}د {s}ث" if h > 0 else f"{m}د {s}ث"


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


def get_video_metadata(video_path):
    """استخراج ميزات وأبعاد الفيديو"""
    width, height, duration = 1280, 720, 0
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        output = subprocess.check_output(cmd).decode("utf-8").strip().split("\n")
        if len(output) >= 2:
            width = int(output[0])
            height = int(output[1])
            if len(output) >= 3 and output[2] != "N/A":
                duration = int(float(output[2]))
    except Exception as e:
        print(f"Metadata extraction error: {e}")
    return width, height, duration


def generate_thumbnail(video_path, thumb_path):
    """استخراج صورة مصغرة من الفيديو عند الثانية 3"""
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:03",
            "-i",
            video_path,
            "-vframes",
            "1",
            "-vf",
            "scale=320:-1",
            thumb_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        print(f"Thumbnail error: {e}")
    return None


async def download_file_direct(url, output_path, cancel_event, status_msg):
    timeout_config = aiohttp.ClientTimeout(
        total=21600, connect=120, sock_read=300
    )
    async with aiohttp.ClientSession(
        timeout=timeout_config, headers=HEADERS
    ) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return False, f"رمز الاستجابة من السيرفر: {resp.status}"

            total_size = int(resp.headers.get("Content-Length", 0))
            if total_size > MAX_FILE_SIZE:
                return False, "يتجاوز حجم الملف الحد المسموح به (2GB)."

            downloaded = 0
            start_time = time.time()
            last_update = 0

            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    if cancel_event.is_set():
                        f.close()
                        if os.path.exists(output_path):
                            os.remove(output_path)
                        return False, "تم الإلغاء بواسطة المستخدم."

                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if now - last_update > 4:
                        last_update = now
                        elapsed = now - start_time
                        speed = downloaded / elapsed if elapsed > 0 else 1
                        percentage = (
                            (downloaded / total_size * 100)
                            if total_size > 0
                            else 0
                        )

                        progress_text = (
                            f"📥 **جاري التنزيل المباشر... v5.9**\n📦 `{output_path}`\n\n"
                            f"`{create_progress_bar(percentage)}` **{percentage:.1f}%**\n\n"
                            f"🚀 `{format_bytes(speed)}/s` | 📦 `{format_bytes(downloaded)}` / `{format_bytes(total_size)}`\n"
                            f"⏱️ المتبقي: `{format_time((total_size - downloaded) / speed if speed > 0 else 0)}`"
                        )
                        await safe_edit_message(status_msg, progress_text)

    return True, None


@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    welcome_text = (
        "🚀 **مرحباً بك في بوت التنزيل والرفع المباشر v5.9 Pro**\n\n"
        "💡 **ماذا يقدم هذا البوت؟**\n"
        "يقوم البوت بأخذ الروابط المباشرة للملفات من الإنترنت، ويدعم تحميلها ثم إعادة رفعها لك مباشرة داخل تلغرام بسرعة عالية جداً دون استهلاك بياناتك!\n\n"
        "📦 **المميزات والصيغ المدعومة:**\n"
        "• 📏 **الحد الأقصى:** ملفات بحجم يصل إلى **2 جيجابايت (2GB)**.\n"
        "• 🎬 **الفيديوهات والصوت:** `MP4`, `MKV`, `MP3`, `WEBM`...\n"
        "• 📦 **الملفات المضغوطة:** `ZIP`, `RAR`, `7Z`, `ISO`...\n"
        "• 📱 **التطبيقات:** `APK`.\n\n"
        "✍️ **كيفية الاستخدام:**\n"
        "قم بنسخ **الرابط المباشر** لأي ملف وأرسله هنا مباشرة في المحادثة!"
    )
    await event.respond(welcome_text)


async def process_download_job(event, url):
    task_id = f"{event.chat_id}_{event.id}"
    cancel_event = asyncio.Event()

    ACTIVE_TASKS[task_id] = {
        "cancel_event": cancel_event,
    }

    def get_buttons():
        return [[Button.inline("❌ إلغاء", data=f"cancel_{task_id}")]]

    status_msg = await event.respond(
        "⏳ **جاري معالجة الرابط...**", buttons=get_buttons()
    )

    file_name = os.path.basename(url.split("?")[0]) or "downloaded_file"

    try:
        success, err = await download_file_direct(
            url, file_name, cancel_event, status_msg
        )

        if cancel_event.is_set():
            await safe_edit_message(status_msg, "🛑 تم إلغاء العملية.")
            return

        if not success:
            await safe_edit_message(status_msg, f"❌ فشل التنزيل:\n`{err}`")
            return

        file_size = os.path.getsize(file_name)
        await safe_edit_message(
            status_msg,
            f"📤 **جاري الرفع إلى تلغرام:** `{file_name}`\n📦 الحجم: `{format_bytes(file_size)}`",
            buttons=get_buttons(),
        )

        upload_start = time.time()
        last_up_update = [0]

        async def upload_progress(current, total):
            if cancel_event.is_set():
                raise asyncio.CancelledError()
            now = time.time()
            if now - last_up_update[0] > 4:
                last_up_update[0] = now
                elapsed = now - upload_start
                speed = current / elapsed if elapsed > 0 else 1
                percentage = current * 100 / total if total > 0 else 0
                up_text = (
                    f"📤 **جاري الرفع إلى تلغرام...**\n📄 `{file_name}`\n\n"
                    f"`{create_progress_bar(percentage)}` **{percentage:.1f}%**\n\n"
                    f"🚀 `{format_bytes(speed)}/s`"
                )
                await safe_edit_message(
                    status_msg, up_text, buttons=get_buttons()
                )

        video_extensions = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")
        is_video = file_name.lower().endswith(video_extensions)

        thumb = None
        attributes = []

        if is_video:
            w, h, duration = get_video_metadata(file_name)
            thumb_path = f"{file_name}_thumb.jpg"
            thumb = generate_thumbnail(file_name, thumb_path)
            
            attributes = [
                DocumentAttributeVideo(
                    duration=duration if duration > 0 else 1,
                    w=w if w > 0 else 1280,
                    h=h if h > 0 else 720,
                    supports_streaming=True,
                )
            ]

        # رفع الملف باختيارات إظهار الفيديو والصورة
        await bot.send_file(
            event.chat_id,
            file_name,
            caption=f"🎬 **اسم الفيديو:** `{file_name}`\n⚡ **تم الرفع بنجاح v5.9 Pro**",
            progress_callback=upload_progress,
            thumb=thumb,
            attributes=attributes if is_video else None,
            supports_streaming=is_video,
            force_document=not is_video,  # إجبار الرفع كفيديو إذا كان فيديو
        )

        if thumb and os.path.exists(thumb):
            os.remove(thumb)

        await status_msg.delete()

    except Exception as e:
        await safe_edit_message(
            status_msg, f"❌ حدث خطأ أثناء العملية:\n`{str(e)}`"
        )
    finally:
        ACTIVE_TASKS.pop(task_id, None)
        if os.path.exists(file_name):
            os.remove(file_name)


async def queue_worker():
    while True:
        event, url = await QUEUE.get()
        try:
            await process_download_job(event, url)
        except Exception:
            pass
        finally:
            QUEUE.task_done()


@bot.on(events.NewMessage(pattern=r"^https?://"))
async def queue_handler(event):
    if ADMIN_ID != 0 and event.sender_id != ADMIN_ID:
        return
    if event.id in PROCESSED_MESSAGES:
        return
    PROCESSED_MESSAGES.add(event.id)
    await QUEUE.put((event, event.text.strip()))


async def main():
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot started successfully!")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
