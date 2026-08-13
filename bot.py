import asyncio
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import aiohttp
import yt_dlp
from bs4 import BeautifulSoup
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo

# --- التحديث التلقائي للإصدار ---
VERSION = "v8.4"

# --- الإعدادات ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5414125521"))
PORT = int(os.environ.get("PORT", 8080))

bot = TelegramClient(None, API_ID, API_HASH)

# هيدرز لمحاكاة متصفح Chrome حقيقي وتجاوز الحظر
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Not)A;Brand";v="99", "Google Chrome";v="127", "Chromium";v="127"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

# --- سيرفر وهمي لمنع إغلاق الخدمة في Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running healthily!")

    def log_message(self, format, *args):
        return

def run_health_check_server():
    try:
        server_address = ('', PORT)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        httpd.serve_forever()
    except Exception as e:
        print(f"Health check server error: {e}")

threading.Thread(target=run_health_check_server, daemon=True).start()

def format_bytes(size):
    return f"{size / (1024 * 1024 * 1024):.2f} GB" if size >= 1024*1024*1024 else f"{size / (1024 * 1024):.1f} MB"

async def safe_edit(msg, text):
    try:
        await msg.edit(text)
    except Exception:
        pass

# --- مستخرج رابط MediaFire المباشر ---
async def extract_mediafire_url(url):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    download_btn = soup.find("a", id="downloadButton")
                    if download_btn and "href" in download_btn.attrs:
                        return download_btn["href"]
    except Exception as e:
        print(f"Mediafire extract error: {e}")
    return None

# --- محرك التحميل المباشر مع فحص حظر الـ HTML ---
async def download_direct(url, output_path, status_msg):
    req_headers = HEADERS.copy()
    match = re.match(r"https?://([^/]+)", url)
    if match:
        req_headers["Referer"] = f"{match.group(0)}/"
    
    timeout = aiohttp.ClientTimeout(total=21600, connect=60)
    try:
        async with aiohttp.ClientSession(headers=req_headers, timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return False, f"HTTP {resp.status}"
                
                content_type = resp.headers.get("Content-Type", "").lower()
                
                # إذا كانت الاستجابة صفحة HTML فهناك حظر أو تحويل لصفحة ويب
                if "text/html" in content_type:
                    return False, "IS_HTML_PAGE"

                total_size = int(resp.headers.get("Content-Length", 0))
                if total_size > 0 and total_size < 100 * 1024:
                    return False, "IS_HTML_PAGE"

                downloaded = 0
                last_update = 0

                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_update > 4:
                            last_update = now
                            progress = f"📥 **جاري التحميل المباشر ({VERSION})...**\n📦 تم تنزيل: `{format_bytes(downloaded)}`"
                            if total_size > 0:
                                p = (downloaded / total_size) * 100
                                progress += f" / `{format_bytes(total_size)}` (`{p:.1f}%`)"
                            await safe_edit(status_msg, progress)

                if os.path.getsize(output_path) < 100 * 1024:
                    os.remove(output_path)
                    return False, "IS_HTML_PAGE"

                return True, None
    except Exception as e:
        return False, str(e)

# --- محرك yt-dlp الذكي للالتفاف على الحظر واستخراج الفيديو ---
async def download_ytdlp(url, status_msg):
    loop = asyncio.get_event_loop()
    out_name = f"video_{int(time.time())}.mp4"
    
    ydl_opts = {
        'outtmpl': out_name,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'http_headers': HEADERS,
        'user_agent': HEADERS["User-Agent"]
    }
    
    def run_dl():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return out_name if os.path.exists(out_name) else None
    
    try:
        file_path = await loop.run_in_executor(None, run_dl)
        if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 100 * 1024:
            return True, file_path
    except Exception as e:
        print(f"yt-dlp error: {e}")
        
    return False, "فشل استخراج الفيديو عبر المحرك المتقدم (قد تطلب الصفحة حماية Captcha)."

# --- استخراج معلومات الفيديو ---
def get_video_meta(path):
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,duration", "-of", "default=noprint_wrappers=1:nokey=1", path]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip().split("\n")
        return int(out[0]), int(out[1]), int(float(out[2]))
    except Exception:
        return 1280, 720, 0

def make_thumb(path):
    thumb = f"{path}_thumb.jpg"
    try:
        cmd = ["ffmpeg", "-y", "-ss", "00:00:02", "-i", path, "-vframes", "1", "-vf", "scale=320:-1", thumb]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return thumb if os.path.exists(thumb) else None
    except Exception:
        return None

# --- الأوامر ---
@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    welcome_text = (
        f"مرحباً بك في بوت التنزيل والرفع المباشر Pro {VERSION} 🚀\n\n"
        "💡 **ماذا يقدم هذا البوت؟**\n"
        "يقوم البوت بأخذ الروابط المباشرة للملفات وروابط Mega وجوجل درايف، ويدعم تحميلها ثم إعادة رفعها لك مباشرة داخل تلغرام بسرعة عالية جداً دون استهلاك بياناتك!\n\n"
        "📦 **المميزات والصيغ المدعومة:**\n"
        "• 📏 الحد الأقصى: ملفات بحجم يصل إلى 2 جيجابايت (2GB).\n"
        "• 🎬 الفيديوهات والصوت: MP4, MKV, MP3, WEBM...\n"
        "• 📸 استخراج لقطات معاينة تلقائية وصورة مصغرة للفيديوهات.\n\n"
        "✍️ **كيفية الاستخدام:**\n"
        "قم بنسخ الرابط المباشر لأي ملف وأرسله هنا مباشرة في المحادثة!"
    )
    await event.respond(welcome_text)

@bot.on(events.NewMessage(pattern=r"^https?://"))
async def handler(event):
    if event.sender_id != ADMIN_ID:
        return
    
    url = event.text.strip()
    status_msg = await event.respond("⏳ **جاري تحليل الرابط...**")

    if "mediafire.com" in url:
        await safe_edit(status_msg, "🔍 **جاري استخراج رابط التحميل المباشر من MediaFire...**")
        direct_mf = await extract_mediafire_url(url)
        if direct_mf:
            url = direct_mf
        else:
            await safe_edit(status_msg, "❌ **فشل استخراج الرابط المباشر من صفحة MediaFire.**")
            return

    clean_url = url.split("?")[0]
    file_name = os.path.basename(clean_url) or f"download_{int(time.time())}.mp4"
    if not os.path.splitext(file_name)[1]:
        file_name += ".mp4"

    # 1. محاولة التحميل المباشر
    success, err = await download_direct(url, file_name, status_msg)
    
    # 2. إذا كشف البوت صفحة HTML أو حظر، يتحول تلقائياً إلى محرك استخراج الروابط
    if not success:
        await safe_edit(status_msg, "🛡️ **الرابط محمي أو صفحة ويب. جاري التجاوز واستخراج الفيديو عبر yt-dlp...**")
        success, res_file = await download_ytdlp(url, status_msg)
        if success:
            file_name = res_file
        else:
            await safe_edit(status_msg, f"❌ **فشلت العملية:**\n`{res_file}`")
            return

    file_size = os.path.getsize(file_name)
    await safe_edit(status_msg, f"📤 **جاري الرفع إلى تلغرام ({VERSION})...**\n📦 الحجم: `{format_bytes(file_size)}`")

    is_video = file_name.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm'))
    thumb = None
    attributes = None

    if is_video:
        w, h, dur = get_video_meta(file_name)
        thumb = make_thumb(file_name)
        attributes = [DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True)]

    try:
        await bot.send_file(
            event.chat_id,
            file_name,
            caption=f"🎬 **اسم الملف:** `{file_name}`\n📦 **الحجم:** `{format_bytes(file_size)}`\n⚡ **تم الرفع بنجاح عبر الاصدار {VERSION}!**",
            thumb=thumb,
            attributes=attributes,
            supports_streaming=is_video,
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
    print(f"Bot {VERSION} is running!")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
