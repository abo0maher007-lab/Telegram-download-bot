import asyncio
import os
import re
import shutil
import subprocess
import threading
import time
import aiohttp
from bs4 import BeautifulSoup
from flask import Flask
from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError

# --- خادم ويب لإبقاء الخدمة مستيقظة على Render ---
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot v5.9 Pro is active"


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


# --- إعدادات الحساب والبوت ---
API_ID = int(os.environ.get("API_ID", "30065326"))
API_HASH = os.environ.get("API_HASH", "a95066d2eba2c2262e88983a39ceeb4e")
BOT_TOKEN = os.environ.get(
    "BOT_TOKEN", "8932698139:AAFGa9PzIoCG923GtdlKx4b5fD75ArXEMww"
)
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

# جلسة بدون حفظ ملف لتفادي قفل الجلسات (session lock) على Render
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


async def resolve_apkmirror_link(session, url):
    try:
        async with session.get(url, headers=HEADERS) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        download_btn = soup.find("a", class_=re.compile(r"downloadButton"))
        if download_btn and "href" in download_btn.attrs:
            step2_url = "https://www.apkmirror.com" + download_btn["href"]
            async with session.get(step2_url, headers=HEADERS) as resp2:
                if resp2.status != 200:
                    return None
                html = await resp2.text()
            soup = BeautifulSoup(html, "html.parser")

        direct_anchor = soup.find(
            "a", href=re.compile(r"/wp-content/themes/APKMirror/redirect\.php")
        )
        if direct_anchor and "href" in direct_anchor.attrs:
            return "https://www.apkmirror.com" + direct_anchor["href"]

        fallback_anchor = soup.find(
            "a",
            rel="nofollow",
            data_google_v3_recaptcha=re.compile(r".*"),
        )
        if fallback_anchor and "href" in fallback_anchor.attrs:
            return "https://www.apkmirror.com" + fallback_anchor["href"]
    except Exception as e:
        print(f"APKMirror extraction error: {e}")
    return None


async def download_file_direct(url, output_path, cancel_event, status_msg):
    timeout_config = aiohttp.ClientTimeout(
        total=21600, connect=120, sock_read=300
    )
    async with aiohttp.ClientSession(
        timeout=timeout_config, headers=HEADERS
    ) as session:
        if "apkmirror.com" in url:
            await safe_edit_message(
                status_msg,
                "🔍 **جاري فك حماية APKMirror واستخراج الرابط المباشر...**",
            )
            real_url = await resolve_apkmirror_link(session, url)
            if real_url:
                url = real_url
            else:
                return False, "فشل استخراج رابط التنزيل المباشر من APKMirror."

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
    await event.respond("🚀 **مرحباً بك في بوت التنزيل والرفع Pro v5.9**")


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
    if "apkmirror.com" in url and not file_name.endswith(".apk"):
        file_name = "application_file.apk"

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

        await bot.send_file(
            event.chat_id,
            file_name,
            caption=f"📱 **اسم الملف:** `{file_name}`\n⚡ **تم الرفع بنجاح v5.9 Pro**",
            progress_callback=upload_progress,
        )

        await status_msg.delete()

    except Exception as e:
        await safe_edit_message(status_msg, f"❌ حدث خطأ أثناء العملية:\n`{str(e)}`")
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
    threading.Thread(target=run_web_server, daemon=True).start()
    await bot.start(bot_token=BOT_TOKEN)
    asyncio.create_task(queue_worker())
    print("Bot started successfully!")
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
