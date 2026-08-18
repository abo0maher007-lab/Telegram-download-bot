import os
import time
import asyncio
import requests
import yt_dlp
import aiohttp
import subprocess
import threading
from telethon import Button
from telethon.tl.types import DocumentAttributeVideo

from config import BROWSER_HEADERS, X_COOKIES_FILE, INSTAGRAM_COOKIES_FILE, ACTIVE_CANCEL_EVENTS
from database import get_user_config
from utils import (
    clean_url, is_complex_url, is_x_url, is_instagram_url, is_tiktok_url,
    get_video_metadata_and_thumb, extract_9_frames, split_video_file,
    trim_video_clip, format_size, format_time
)

def download_with_ytdlp(url, task_dir, fmt='mp4', quality='best'):
    out_template = os.path.join(task_dir, '%(title).30s_%(id)s_%(autonumber)s.%(ext)s')
    ydl_opts = {
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'headers': BROWSER_HEADERS,
        'nocheckcertificate': True,
        'ignoreerrors': True,
    }
    url_lower = url.lower()

    if is_x_url(url) and os.path.exists(X_COOKIES_FILE):
        ydl_opts['cookiefile'] = X_COOKIES_FILE
    elif is_instagram_url(url) and os.path.exists(INSTAGRAM_COOKIES_FILE):
        ydl_opts['cookiefile'] = INSTAGRAM_COOKIES_FILE

    if 'tiktok.com' in url_lower:
        ydl_opts['format'] = 'best'
    elif fmt == 'mp3':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    elif quality != 'best':
        ydl_opts['format'] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
    else:
        ydl_opts['format'] = 'best/bestvideo+bestaudio'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"yt-dlp error: {e}")

async def download_direct_async(bot, chat_id, url, filepath, status_msg, cancel_event, task_id):
    async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
        async with session.get(url, ssl=False) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            start_time = time.time()
            last_update_time = 0

            cancel_btn = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]

            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(256 * 1024):
                    if cancel_event.is_set(): raise Exception("CANCELLED")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        current_time = time.time()
                        
                        if current_time - last_update_time > 1.8 or downloaded == total_size:
                            last_update_time = current_time
                            elapsed = current_time - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            percent = (downloaded / total_size * 100) if total_size > 0 else 0
                            filled = int(percent // 10)
                            bar = "█" * filled + "░" * (10 - filled)
                            rem_time = (total_size - downloaded) / speed if speed > 0 and total_size > 0 else 0
                            
                            text = (
                                f"📥 **جاري التنزيل المباشر...**\n"
                                f"[{bar}] {percent:.1f}%\n"
                                f"📦 الحجم: `{format_size(downloaded)}` / `{format_size(total_size)}`\n"
                                f"⚡ السرعة: `{format_size(speed)}/s`\n"
                                f"⏱️ الوقت المنقضي: `{format_time(elapsed)}`\n"
                                f"⏳ المتبقي تقريباً: `{format_time(rem_time)}`"
                            )
                            try: await status_msg.edit(text, buttons=cancel_btn)
                            except: pass

async def start_direct_execution(bot, chat_id, url, filename, as_doc=False, quality='best', target_fmt='mp4', status_msg=None, trim_times=None):
    task_id = f"task_{int(time.time() * 1000)}"
    cancel_event = threading.Event()
    ACTIVE_CANCEL_EVENTS[task_id] = cancel_event
    cancel_btn = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]

    if not status_msg:
        status_msg = await bot.send_message(chat_id, "⏳ **جاري تحضير الطلب...**", buttons=cancel_btn)
    else:
        await status_msg.edit("⏳ **جاري تحضير الطلب...**", buttons=cancel_btn)
        
    task_dir = os.path.join("downloads", task_id)
    os.makedirs(task_dir, exist_ok=True)
    user_config = get_user_config(chat_id)

    try:
        loop = asyncio.get_event_loop()
        target_url = clean_url(url) if is_complex_url(url) else url
        is_social = is_complex_url(target_url) if target_url else False

        if target_url and is_social:
            await loop.run_in_executor(None, download_with_ytdlp, target_url, task_dir, target_fmt, quality)
        elif target_url:
            filepath = os.path.join(task_dir, filename)
            await download_direct_async(bot, chat_id, target_url, filepath, status_msg, cancel_event, task_id)

        if cancel_event.is_set(): raise Exception("CANCELLED")

        # تجميع الملفات المحملة
        downloaded_files = []
        for root, _, files in os.walk(task_dir):
            for file in files:
                fpath = os.path.join(root, file)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 500:
                    downloaded_files.append(fpath)

        if not downloaded_files:
            raise Exception("تعذر الوصول إلى المحتوى. تأكد من صحة الرابط.")

        await status_msg.edit("📤 **جاري رفع المحتوى...**", buttons=cancel_btn)

        for vid in downloaded_files:
            if cancel_event.is_set(): raise Exception("CANCELLED")
            ext = os.path.splitext(vid)[1].lower()

            if ext in ['.mp4', '.mkv', '.avi', '.mov']:
                if trim_times:
                    vid = trim_video_clip(vid, trim_times[0], trim_times[1])

                split_vids = split_video_file(vid)

                for idx, part_vid in enumerate(split_vids, start=1):
                    duration, width, height, thumb_path = get_video_metadata_and_thumb(part_vid)
                    attr = [DocumentAttributeVideo(duration=duration, w=width, h=height, supports_streaming=True)]
                    
                    await bot.send_file(chat_id, part_vid, caption=f"🎥 **تم التنزيل بنجاح!**", force_document=as_doc, thumb=thumb_path, attributes=attr)
                    
                    # التقاط الصور المضمون (تم حل المشكلة)
                    if duration > 0 and user_config.get("snapshots", True) and idx == 1:
                        await status_msg.edit("📸 **جاري توليد ألبوم اللقطات الـ 9 للفيديو...**")
                        frames = extract_9_frames(part_vid, duration, chat_id=chat_id)
                        if frames:
                            await bot.send_file(chat_id, frames, caption="📸 **ألبوم اللقطات المصورة من الفيديو مع التوقيتات:**")
                            for fr in frames:
                                try: os.remove(fr)
                                except: pass

                    if thumb_path and os.path.exists(thumb_path):
                        try: os.remove(thumb_path)
                        except: pass

            elif ext in ['.mp3', '.wav', '.m4a']:
                await bot.send_file(chat_id, vid, caption="🎵 **تم تنزيل المقطع الصوتي:**")
            else:
                await bot.send_file(chat_id, vid, force_document=True)

        await status_msg.delete()

    except Exception as e:
        if str(e) == "CANCELLED":
            await status_msg.edit("🛑 **تم إلغاء العملية بناءً على طلبك.**", buttons=None)
        else:
            await status_msg.edit(f"❌ **خطأ:** `{str(e)}`", buttons=None)
    finally:
        ACTIVE_CANCEL_EVENTS.pop(task_id, None)
        if os.path.exists(task_dir):
            try: shutil.rmtree(task_dir)
            except: pass            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif quality != 'best':
        ydl_opts['format'] = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
    else:
        ydl_opts['format'] = 'best/bestvideo+bestaudio'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        print(f"yt-dlp error: {e}")

def tiktok_multi_engine(url, task_dir):
    try:
        cmd = ["gallery-dl", "--directory", task_dir, "--filename", "tt_{id}_{num}.{ext}", url]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        if any(os.path.isfile(os.path.join(task_dir, f)) for f in os.listdir(task_dir)):
            return
    except Exception as e:
        print(f"TikTok gallery-dl error: {e}")

    try:
        api_url = "https://api.cobalt.tools/api/json"
        headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": BROWSER_HEADERS['User-Agent']}
        res = requests.post(api_url, json={"url": url}, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            urls_to_dl = [data.get("url")] if data.get("status") in ["stream", "redirect"] else [x.get("url") for x in data.get("picker", [])]
            for idx, media_url in enumerate(urls_to_dl, start=1):
                if not media_url: continue
                r = requests.get(media_url, stream=True, headers=BROWSER_HEADERS, timeout=30)
                if r.status_code == 200:
                    ext = "mp4" if ".mp4" in media_url or "video" in r.headers.get("content-type", "") else "jpg"
                    with open(os.path.join(task_dir, f"tt_photo_{idx}.{ext}"), 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk: f.write(chunk)
    except Exception as e:
        print(f"TikTok Cobalt error: {e}")

def instagram_carousel_and_photo_engine(url, task_dir):
    try:
        cmd = ["gallery-dl", "--directory", task_dir, "--filename", "ig_{id}_{num}.{ext}", "--option", "extractor.instagram.post-quality=max", "--user-agent", BROWSER_HEADERS['User-Agent']]
        if os.path.exists(INSTAGRAM_COOKIES_FILE): cmd.extend(["--cookies", INSTAGRAM_COOKIES_FILE])
        cmd.append(url)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        if any(os.path.isfile(os.path.join(task_dir, f)) for f in os.listdir(task_dir)): return

        ydl_opts = {'outtmpl': os.path.join(task_dir, 'ig_hd_%(id)s_%(autonumber)s.%(ext)s'), 'quiet': True, 'headers': BROWSER_HEADERS, 'format': 'best'}
        if os.path.exists(INSTAGRAM_COOKIES_FILE): ydl_opts['cookiefile'] = INSTAGRAM_COOKIES_FILE
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
    except Exception as e:
        print(f"Instagram engine error: {e}")

# --- التحميل المباشر ---
async def download_direct_async(client, chat_id, url, filepath, status_msg, cancel_event, task_id):
    async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
        async with session.get(url, ssl=False) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            
            downloaded = 0
            start_time = time.time()
            last_update_time = 0
            chunk_size = 256 * 1024
            cancel_btn = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]

            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(chunk_size):
                    if cancel_event.is_set(): raise Exception("CANCELLED")
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        current_time = time.time()
                        
                        if current_time - last_update_time > 1.8 or downloaded == total_size:
                            last_update_time = current_time
                            elapsed = current_time - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            percent = (downloaded / total_size * 100) if total_size > 0 else 0
                            filled = int(percent // 10)
                            bar = "█" * filled + "░" * (10 - filled)
                            rem_time = (total_size - downloaded) / speed if speed > 0 and total_size > 0 else 0
                            
                            text = (
                                f"📥 **جاري التنزيل المباشر...**\n"
                                f"[{bar}] {percent:.1f}%\n"
                                f"📦 الحجم: `{format_size(downloaded)}` / `{format_size(total_size)}`\n"
                                f"⚡ السرعة: `{format_size(speed)}/s`\n"
                                f"⏱️ الوقت المنقضي: `{format_time(elapsed)}`\n"
                                f"⏳ المتبقي تقريباً: `{format_time(rem_time)}`"
                            )
                            try: await status_msg.edit(text, buttons=cancel_btn)
                            except: pass
                            
