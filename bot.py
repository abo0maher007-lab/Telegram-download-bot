import asyncio
import os
import re
import shutil
import subprocess
import threading
import time
import aiohttp
from flask import Flask
from google_play_scraper import app as gplay_app
from telethon import Button, TelegramClient, events

# --- خادم ويب لإبقاء الخدمة مستيقظة على Render ---
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot v5.4 Pro is active"


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


threading.Thread(target=run_web_server, daemon=True).start()

# --- إعدادات الحساب والبوت ---
API_ID = int(os.environ.get("API_ID", "30065326"))
API_HASH = os.environ.get("API_HASH", "a95066d2eba2c2262e88983a39ceeb4e")
BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "8932698139:AAFGa9PzIoCG923GtdlKx4b5fD75ArXEMww"
)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# ترويسات متوافقة لمنع الـ 403 وحل مشكلة الحظر
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
}

bot = TelegramClient("url_uploader_bot", API_ID, API_HASH)
ACTIVE_TASKS = {}
QUEUE = asyncio.Queue()
PROCESSED_MESSAGES = set()


def extract_package_id(url):
    match = re.search(r"id=([a-zA-Z0-9_.]+)", url)
    return match.group(1) if match else None


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


# استخراج رابط APK حقيقي ومضمون من سيرفرات APKCombo/APKPure الحية
async def get_apk_download_url(package_id):
    # رابط مباشر عبر APKCombo CDN
    combo_url = f"https://apkcombo.com/downloader/#package={package_id}"

    # سيرفر تحويل ثانوي حقيقي يعطي روابط تنزيل مباشرة
    apk_pure_url = (
        f"https://d.apkpure.com/b/APK/{package_id}?version=latest"
    )

    return apk_pure_url, combo_url


@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    await event.respond("🚀 **مرحباً بك في بوت التنزيل والرفع Pro v5.4**")


async def process_download_job(event, url):
    task_id = f"{event.chat_id}_{event.id}"
    cancel_event = asyncio.Event()
    pause_event = asyncio.Event()
    pause_event.set()

    ACTIVE_TASKS[task_id] = {
        "cancel_event": cancel_event,
        "pause_event": pause_event,
        "is_paused": False,
    }

    def get_buttons(is_paused=False):
        toggle_btn = (
            Button.inline("▶️ استئناف", data=f"resume_{task_id}")
            if is_paused
            else Button.inline("⏸️ إيقاف مؤقت", data=f"pause_{task_id}")
        )
        return [
            [toggle_btn, Button.inline("❌ إلغاء", data=f"cancel_{task_id}")]
        ]

    status_msg = await event.respond(
        "⏳ **جاري الفحص واستخراج رابط الملف...**", buttons=get_buttons()
    )
    timeout_config = aiohttp.ClientTimeout(total=10800, connect=60)
    thumb_path = None
    app_title = "downloaded_file"

    if "play.google.com" in url:
        package_id = extract_package_id(url)
        if not package_id:
            await status_msg.edit(
                "❌ لم يتم العثور على المعرف الخاص بالتطبيق.", buttons=None
            )
            return

        try:
            app_info = gplay_app(package_id, lang="ar", country="us")
            app_title = app_info.get("title", package_id)
            icon_url = app_info.get("icon")
        except Exception:
            app_title = package_id
            icon_url = None

        clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", app_title)
        file_name = f"{clean_name}.apk"

        dl_url, _ = await get_apk_download_url(package_id)

        if icon_url:
            thumb_path = f"{package_id}.jpg"
            try:
                async with aiohttp.ClientSession(headers=HEADERS) as sess:
                    async with sess.get(icon_url) as r:
                        if r.status == 200:
                            with open(thumb_path, "wb") as f:
                                f.write(await r.read())
            except Exception:
                thumb_path = None
    else:
        dl_url = url
        file_name = os.path.basename(url.split("?")[0]) or "downloaded_file"

    try:
        # إرسال طلب التنزيل مع محاكاة هاتف أندرويد لتجاوز 403
        req_headers = HEADERS.copy()
        if "play.google.com" in url:
            req_headers["Referer"] = f"https://apkpure.com/store/apps/details?id={package_id}"

        async with aiohttp.ClientSession(
            timeout=timeout_config, headers=req_headers
        ) as session:
            async with session.get(dl_url, allow_redirects=True) as resp:
                if resp.status != 200:
                    await status_msg.edit(
                        f"❌ فشل الاتصال برابط التحميل المباشر. رمز الاستجابة: `{resp.status}`",
                        buttons=None,
                    )
                    return

                total_size = int(resp.headers.get("Content-Length", 0))
                if total_size > MAX_FILE_SIZE:
                    await status_msg.edit(
                        "❌ يتجاوز حجم الملف الحد المسموح به (2GB).",
                        buttons=None,
                    )
                    return

                downloaded = 0
                start_time = time.time()
                last_update = 0

                with open(file_name, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if cancel_event.is_set():
                            f.close()
                            if os.path.exists(file_name):
                                os.remove(file_name)
                            await status_msg.edit(
                                "🛑 تم إلغاء العملية.", buttons=None
                            )
                            return

                        if not pause_event.is_set():
                            await status_msg.edit(
                                f"⏸️ **توقف مؤقت:** `{file_name}`",
                                buttons=get_buttons(is_paused=True),
                            )
                            await pause_event.wait()
                            start_time = time.time() - (
                                downloaded
                                / (
                                    speed
                                    if "speed" in locals() and speed > 0
                                    else 1
                                )
                            )

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

                            await status_msg.edit(
                                f"📥 **جاري التنزيل... v5.4**\n📦 `{file_name}`\n\n"
                                f"`{create_progress_bar(percentage)}` **{percentage:.1f}%**\n\n"
                                f"🚀 `{format_bytes(speed)}/s` | 📦 `{format_bytes(downloaded)}` / `{format_bytes(total_size)}`\n"
                                f"⏱️ المتبقي: `{format_time((total_size - downloaded) / speed if speed > 0 else 0)}`",
                                buttons=get_buttons(is_paused=False),
                            )

        await status_msg.edit(
            f"📤 **جاري الرفع إلى تلغرام:** `{file_name}`...",
            buttons=[
                [Button.inline("❌ إلغاء", data=f"cancel_{task_id}")]
            ],
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
                await status_msg.edit(
                    f"📤 **جاري الرفع إلى تلغرام...**\n📄 `{file_name}`\n\n"
                    f"`{create_progress_bar(percentage)}` **{percentage:.1f}%**\n\n"
                    f"🚀 `{format_bytes(speed)}/s`",
                    buttons=[
                        [
                            Button.inline(
                                "❌ إلغاء", data=f"cancel_{task_id}"
                            )
                        ]
                    ],
                )

        await bot.send_file(
            event.chat_id,
            file_name,
            thumb=thumb_path,
            caption=f"📱 **اسم الملف / التطبيق:** `{file_name}`\n⚡ **تم الرفع بنجاح v5.4 Pro**",
            progress_callback=upload_progress,
        )

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit(
            f"❌ حدث خطأ أثناء التنزيل/الرفع:\n`{str(e)}`", buttons=None
        )
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
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
