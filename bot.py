import asyncio
import os
import re
import subprocess
import time
import aiohttp
import yt_dlp
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5414125521"))

bot = TelegramClient(None, API_ID, API_HASH)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
}

def format_bytes(size):
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
    return f"{size / (1024 * 1024):.1f} MB"

async def safe_edit(msg, text):
    try:
        await msg.edit(text)
    except Exception:
        pass

# --- 1. التحميل الذكي بواسطة aiohttp للروابط المباشرة و CDN ---
async def download_direct(url, output_path, status_msg):
    req_headers = HEADERS.copy()
    match = re.match(r"https?://([^/]+)", url)
    if match:
        req_headers["Referer"] = f"{match.group(0)}/"

    timeout = aiohttp.ClientTimeout(total=21600, connect=60)
    async with aiohttp.ClientSession(headers=req_headers, timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return False, f"السيرفر أرجع خطأ (HTTP {resp.status})"

            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                return False, "الرابط أرجع صفحة ويب/حظر وليس ملف فيديو مباشر!"

            total_size = int(resp.headers.get("Content-Length", 0))
            
            # إذا كان الملف أقل من 100 كيلوبايت فهو بالتأكيد ليس فيديو
            if total_size > 0 and total_size < 100 * 1024:
                return False, "الملف الناتج صغير جداً (صفحة خطأ من السيرفر وليس فيديو)."

            downloaded = 0
            last_update = 0

            with open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_update > 3:
                        last_update = now
                        text = f"📥 **جاري التحميل المباشر...**\n📦 تم تحميل: `{format_bytes(downloaded)}`"
                        if total_size > 0:
                            p = (downloaded / total_size) * 100
                            text += f" / `{format_bytes(total_size)}` (`{p:.1f}%`)"
                        await safe_edit(status_msg, text)

            # فحص الحجم النهائي
            if os.path.getsize(output_path) < 100 * 1024:
                os.remove(output_path)
                return False, "فشل التحميل: الملف الناتج معطوب أو عبارة عن صفحة حظر (أقل من 100KB)."

            return True, None

# --- 2. التحميل بواسطة yt-dlp للروابط المعقدة ---
async def download_ytdlp(url, status_msg):
    loop = asyncio.get_event_loop()
    out_name = f"video_{int(time.time())}.mp4"

    ydl_opts = {
        'outtmpl': out_name,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'http_headers': HEADERS,
    }

    def run_dl():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return out_name if os.path.exists(out_name) else None

    try:
        await safe_edit(status_msg, "⏳ **جاري الفحص بالربط المتقدم (yt-dlp)...**")
        file_path = await loop.run_in_executor(None, run_dl)
        if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 100 * 1024:
            return True, file_path
    except Exception as e:
        print(f"yt-dlp error: {e}")

    return False, "فشل yt-dlp في استخراج الفيديو."

# --- دوال الميديا ---
def get_video_meta(path):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip().split("\n")
        w = int(out[0]) if len(out) > 0 else 1280
        h = int(out[1]) if len(out) > 1 else 720
        dur = int(float(out[2])) if len(out) > 2 and out[2] != "N/A" else 0
        return w, h, dur
    except Exception:
        return 1280, 720, 0

def make_thumb(path):
    thumb = f"{path}_thumb.jpg"
    try:
        cmd = ["ffmpeg", "-y", "-ss", "00:00:02", "-i", path, "-vframes", "1", "-vf", "scale=320:-1", thumb]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except Exception:
        pass
    return None

# --- المعالج الرئيسي ---
@bot.on(events.NewMessage(pattern=r"^https?://"))
async def handler(event):
    if event.sender_id != ADMIN_ID:
        return

    url = event.text.strip()
    status_msg = await event.respond("⏳ **جاري تحليل الرابط...**")

    # تحديد اسم الملف
    clean_url = url.split("?")[0]
    file_name = os.path.basename(clean_url) or "downloaded_video.mp4"
    if not file_name.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
        file_name += ".mp4"

    # المحاولة الأولى: تحميل مباشر مخصص للـ CDN
    success, err = await download_direct(url, file_name, status_msg)

    # المحاولة الثانية: إذا فشل التحميل المباشر نستخدم yt-dlp
    if not success:
        await safe_edit(status_msg, f"⚠️ التحميل المباشر لم ينجح (`{err}`).\n🔄 جاري المحاولة عبر محرك yt-dlp...")
        success, res_path = await download_ytdlp(url, status_msg)
        if success:
            file_name = res_path
        else:
            await safe_edit(status_msg, f"❌ **فشلت العملية:**\n`{err}`\n\n💡 *تأكد من أن السيرفر لا يطلب تسجيل دخول أو يحتوي على حماية Cloudflare Captcha.*")
            return

    # الرفع إلى تلغرام
    file_size = os.path.getsize(file_name)
    await safe_edit(status_msg, f"📤 **جاري الرفع إلى تلغرام...**\n📦 الحجم: `{format_bytes(file_size)}`")

    w, h, dur = get_video_meta(file_name)
    thumb = make_thumb(file_name)

    attributes = [
        DocumentAttributeVideo(
            duration=dur if dur > 0 else 1,
            w=w,
            h=h,
            supports_streaming=True
        )
    ]

    try:
        await bot.send_file(
            event.chat_id,
            file_name,
            caption=f"🎬 **اسم الملف:** `{file_name}`\n📦 **الحجم:** `{format_bytes(file_size)}`\n⚡ **تم الرفع بنجاح!**",
            thumb=thumb,
            attributes=attributes,
            supports_streaming=True,
            reply_to=event.id
        )
        await status_msg.delete()
    except Exception as e:
        await safe_edit(status_msg, f"❌ خطأ أثناء الرفع إلى تلغرام:\n`{str(e)}`")
    finally:
        if os.path.exists(file_name):
            os.remove(file_name)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("Bot is up and running!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
