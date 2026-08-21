import os
import re
import glob
import time
import asyncio
import logging
import subprocess
import requests
from bs4 import BeautifulSoup
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DownloaderBot-v3")

# ----------------------------------------------------
# 🚂 الإعداد والمتغيرات
# ----------------------------------------------------
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not API_ID or not API_HASH or not BOT_TOKEN:
    logger.critical("❌ خطأ: المتغيرات غير معرفة!")
    exit(1)

app = Client("SmartDownloaderBot_v3", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN)

ACTIVE_TASKS = {}
CANCELLED_TASKS = set()

class ProcessCancelledException(Exception):
    pass

# ----------------------------------------------------
# 📊 أدوات v3 للمسح، القياس، والتنظيف الشامل
# ----------------------------------------------------

def cleanup_temp_files(task_id: str):
    """تنظيف كافة الملفات المؤقتة المرتبطة بالمهمة لمنع امتلاء المساحة"""
    patterns = [
        f"downloads/{task_id}*",
        f"downloads/thumb_{task_id}*"
    ]
    for pattern in patterns:
        for file_path in glob.glob(pattern):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

def get_video_duration(video_path: str) -> int:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode().strip()
        return int(float(output))
    except Exception:
        return 0

def scrape_direct_video_link_v3(url: str) -> str:
    """محرك v3 المطور للقشط والتقاط بث الـ HLS والـ MP4 المباشر"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # 1. البحث في وسوم الفيديو
            for video in soup.find_all('video'):
                if video.get('src'):
                    return video['src']
                for source in video.find_all('source'):
                    if source.get('src'):
                        return source['src']
            
            # 2. فحص الوسوم التوضيحية v3
            for meta in soup.find_all('meta'):
                prop = meta.get('property', '') or meta.get('name', '')
                if prop in ['og:video', 'og:video:secure_url', 'twitter:player:stream']:
                    if meta.get('content'):
                        return meta['content']

            # 3. التقاط روابط m3u8 و mp4 عبر Regex
            matches = re.findall(r'https?://[^\s\'"]+\.(?:m3u8|mp4|webm|mov)', resp.text)
            if matches:
                return matches[0]
    except Exception as e:
        logger.warning(f"v3 Scraper failed: {e}")
    return None

def format_eta(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes:02d}m {secs:02d}s"
    return f"{secs:02d}s"

def get_cancel_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ إلغاء العملية (v3 Engine)", callback_data=f"cancel_{task_id}")
    ]])

def render_progress_bar(percentage: float) -> str:
    filled = int(percentage // 10)
    return "█" * filled + "░" * (10 - filled)

async def update_status_ui(message: Message, status_text: str, current: int, total: int, start_time: float, task_id: str):
    if task_id in CANCELLED_TASKS:
        raise ProcessCancelledException("CANCELLED_BY_USER")

    now = time.time()
    diff = now - start_time
    if diff <= 0:
        return

    percentage = (current / total) * 100 if total > 0 else 0
    speed = current / diff
    eta_seconds = round((total - current) / speed) if speed > 0 else 0

    text = (
        f"🚀 **[v3 Ultimate] {status_text}**\n\n"
        f"[{render_progress_bar(percentage)}] `{percentage:.1f}%`\n"
        f"📦 **الحجم:** `{current / (1024*1024):.1f}MB` / `{total / (1024*1024):.1f}MB`\n"
        f"⚡ **السرعة:** `{speed / (1024*1024):.2f} MB/s`\n"
        f"⏱️ **المتبقي:** `{format_eta(eta_seconds)}`"
    )

    try:
        await message.edit_text(text, reply_markup=get_cancel_keyboard(task_id))
    except Exception:
        pass

def generate_ffmpeg_thumbnail(video_path: str, thumb_path: str) -> bool:
    try:
        cmd = f'ffmpeg -y -i "{video_path}" -ss 00:00:02 -vframes 1 "{thumb_path}"'
        os.system(cmd)
        return os.path.exists(thumb_path)
    except Exception:
        return False

# ----------------------------------------------------
# 🧠 محرك الاستخراج - v3 Core Engine
# ----------------------------------------------------

def make_ytdl_opts(base_opts: dict, status_msg: Message, loop: asyncio.AbstractEventLoop, task_id: str, action_name: str):
    start_time = time.time()
    last_update = [0]

    def hook(d):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")

        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            
            if total > 0 and (time.time() - last_update[0] > 2):
                last_update[0] = time.time()
                asyncio.run_coroutine_threadsafe(
                    update_status_ui(status_msg, action_name, downloaded, total, start_time, task_id),
                    loop
                )

    opts = base_opts.copy()
    opts["progress_hooks"] = [hook]
    # تسريع التحميل المتوازي في v3
    opts["concurrent_fragment_downloads"] = 5
    return opts

async def smart_extractor_v3(url: str, status_msg: Message, task_id: str):
    loop = asyncio.get_event_loop()

    thumb_opts = {
        "writethumbnail": True,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}]
    }

    strategies = [
        ({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": f"downloads/{task_id}_s1.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True,
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            **thumb_opts
        }, "1/18: v3 Parallel Turbo HQ"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s2.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
            "referer": "https://www.instagram.com/",
            **thumb_opts
        }, "2/18: iOS Safari Engine"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s3.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "headers": {"Accept-Language": "en-US,en;q=0.9"},
            **thumb_opts
        }, "3/18: Googlebot Bypass"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s4.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36",
            "headers": {"Sec-Fetch-Mode": "navigate"},
            **thumb_opts
        }, "4/18: Android Client Engine"),

        ({
            "format": "bv*+ba/b",
            "hls_prefer_native": True,
            "outtmpl": f"downloads/{task_id}_s5.%(ext)s",
            "quiet": True, "nocheckcertificate": True, "allow_unplayable_formats": True,
            **thumb_opts
        }, "5/18: HLS/DASH Stream Sniffer"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s6.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/125.0",
                "Referer": url, "Origin": f"{url.split('/')[0]}//{url.split('/')[2]}"
            },
            **thumb_opts
        }, "6/18: Direct Server Bypass"),

        ({
            "format": "worstvideo+worstaudio/worst",
            "outtmpl": f"downloads/{task_id}_s7.%(ext)s",
            "quiet": True, "geo_bypass": True, "nocheckcertificate": True, "legacy_server_connect": True,
            **thumb_opts
        }, "7/18: Geo Legacy Connect"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s8.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 7.0) AppleWebKit/537.36 SamsungBrowser/5.0 TV Safari/537.36",
            **thumb_opts
        }, "8/18: Smart TV Appliance"),

        ({
            "format": "best/worst",
            "outtmpl": f"downloads/{task_id}_s9.%(ext)s",
            "quiet": True, "force_generic_extractor": True, "nocheckcertificate": True,
            **thumb_opts
        }, "9/18: Generic Extractor"),

        ({
            "format": "mp4/best",
            "outtmpl": f"downloads/{task_id}_s10.%(ext)s",
            "quiet": True, "nocheckcertificate": True, "prefer_insecure": True,
            **thumb_opts
        }, "10/18: Fallback MP4 Protocol"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s11.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            **thumb_opts
        }, "11/18: FB External Scraper"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s12.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Twitterbot/1.0",
            **thumb_opts
        }, "12/18: TwitterBot Engine"),

        ({
            "format": "all/best",
            "hls_use_mpegts": True,
            "outtmpl": f"downloads/{task_id}_s13.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            **thumb_opts
        }, "13/18: MPEG-TS Stream"),

        ({
            "format": "worst",
            "outtmpl": f"downloads/{task_id}_s14.%(ext)s",
            "quiet": True, "nocheckcertificate": True, "no_check_certificates": True,
            **thumb_opts
        }, "14/18: Insecure SSL Bypass"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s15.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "AppleTV11,1/11.1",
            **thumb_opts
        }, "15/18: Apple TV Spoof"),

        ({
            "format": "best",
            "outtmpl": f"downloads/{task_id}_s16.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Edg/124.0.0.0",
            **thumb_opts
        }, "16/18: macOS Edge Spoof"),

        ({
            "format": "b/best",
            "outtmpl": f"downloads/{task_id}_s17.%(ext)s",
            "quiet": True, "force_generic_extractor": True, "nocheckcertificate": True, "ignoreerrors": True,
            **thumb_opts
        }, "17/18: Deep Generic Raw"),

        ({
            "format": "all",
            "outtmpl": f"downloads/{task_id}_s18.%(ext)s",
            "quiet": True, "nocheckcertificate": True,
            **thumb_opts
        }, "18/18: Force Any Stream")
    ]

    for idx, (st_opts, st_name) in enumerate(strategies, 1):
        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")
        try:
            await update_status_ui(status_msg, f"فحص v3 ({idx}/18)...", 0, 100, time.time(), task_id)
            
            opts = make_ytdl_opts(st_opts, status_msg, loop, task_id, f"تحميل الفيديو ({idx}/18)")
            def run_yt(target_url):
                with YoutubeDL(opts) as ytdl:
                    info = ytdl.extract_info(target_url, download=True)
                    return ytdl.prepare_filename(info), info.get("title", "Video"), info.get("duration", 0)

            file_path, title, duration = await loop.run_in_executor(None, run_yt, url)
            if file_path and os.path.exists(file_path):
                return file_path, title, duration, st_name
        except ProcessCancelledException:
            raise
        except Exception as e:
            logger.warning(f"المحاولة {idx} لم تكتمل: {e}")
            continue

    # 🚀 خطة التعزيز المتقدمة v3
    try:
        await update_status_ui(status_msg, "⚡ تشغيل خطة Stream Scraper v3...", 0, 100, time.time(), task_id)
        scraped_url = await loop.run_in_executor(None, scrape_direct_video_link_v3, url)
        if scraped_url:
            opts = make_ytdl_opts(strategies[8][0], status_msg, loop, task_id, "تحميل Stream v3")
            file_path, title, duration = await loop.run_in_executor(None, run_yt, scraped_url)
            if file_path and os.path.exists(file_path):
                return file_path, title, duration, "⚡ Direct Stream Scraper v3"
    except Exception as e:
        logger.error(f"فشلت خطة التعزيز v3: {e}")

    return None, None, 0, None

# ----------------------------------------------------
# 📡 معالجة الأحداث والرسائل
# ----------------------------------------------------

@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_handler(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[1]
    CANCELLED_TASKS.add(task_id)
    
    if task_id in ACTIVE_TASKS:
        task = ACTIVE_TASKS[task_id]
        if not task.done():
            task.cancel()

    await callback.answer("🛑 تم إلغاء العملية وتنظيف المساحة!", show_alert=True)
    cleanup_temp_files(task_id)
    try:
        await callback.message.edit_text("❌ **تم إلغاء العملية وحذف كافة الملفات المؤقتة بنجاح.**")
    except Exception:
        pass

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text("⚡ **أهلاً بك في البوت (الإصدار v3 Ultimate)**\n\nأرسل رابط الفيديو الآن لبدء التحميل بسرعة عالية وتدفق متوازي.")

async def process_download(client: Client, message: Message, task_id: str, url: str):
    status_msg = await message.reply_text(
        "⏳ **[v3] جاري تشغيل المحرك وفحص الرابط...**",
        reply_markup=get_cancel_keyboard(task_id)
    )

    file_path = None
    thumb_path = None

    try:
        file_path, title, duration, used_method = await smart_extractor_v3(url, status_msg, task_id)

        if task_id in CANCELLED_TASKS:
            raise ProcessCancelledException("CANCELLED_BY_USER")

        if file_path and os.path.exists(file_path):
            if not duration or duration <= 0:
                duration = get_video_duration(file_path)

            base_path = os.path.splitext(file_path)[0]
            possible_thumbs = [f"{base_path}.jpg", f"{base_path}.png", f"{base_path}.webp"]
            
            for p in possible_thumbs:
                if os.path.exists(p):
                    thumb_path = p
                    break

            if not thumb_path or not os.path.exists(thumb_path):
                gen_thumb = f"downloads/thumb_{task_id}.jpg"
                if generate_ffmpeg_thumbnail(file_path, gen_thumb):
                    thumb_path = gen_thumb

            upload_start = time.time()
            last_up_update = [0]

            async def upload_progress(current, total):
                if task_id in CANCELLED_TASKS:
                    raise ProcessCancelledException("CANCELLED_BY_USER")
                if time.time() - last_up_update[0] > 2:
                    last_up_update[0] = time.time()
                    await update_status_ui(status_msg, "رفع الفيديو إلى تلجرام", current, total, upload_start, task_id)

            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                duration=int(duration) if duration else 0,
                caption=f"🎬 **{title}**\n⚡ **الطريقة:** `{used_method}`\n🛠️ **Engine:** `v3 Ultimate`",
                progress=upload_progress
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ **فشلت كافة محاولات v3 المتقدمة لاستخراج هذا الفيديو.**")

    except (asyncio.CancelledError, ProcessCancelledException):
        logger.info(f"🛑 تم إلغاء المهمة وتفريغ الذاكرة: {task_id}")
    except Exception as e:
        logger.error(f"خطأ أثناء المعالجة: {e}")
        try:
            await status_msg.edit_text(f"❌ حدث خطأ غير متوقع: `{str(e)[:150]}`")
        except Exception:
            pass
    finally:
        # v3 تنظيف شامل وتلقائي لجميع الملفات المتعلقة بالعملية
        cleanup_temp_files(task_id)
        CANCELLED_TASKS.discard(task_id)
        ACTIVE_TASKS.pop(task_id, None)

@app.on_message(filters.text & filters.private & ~filters.forwarded)
async def handle_download(client: Client, message: Message):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        return

    task_id = f"{message.from_user.id}_{int(time.time())}"
    task = asyncio.create_task(process_download(client, message, task_id, url))
    ACTIVE_TASKS[task_id] = task

if __name__ == "__main__":
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    
    logger.info("🚀 تم تشغيل محرك v3 Ultimate بنجاح...")
    app.run()