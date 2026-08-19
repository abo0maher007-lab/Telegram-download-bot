import os
import time
import asyncio
import shutil
import threading
import requests
import yt_dlp
import subprocess
import mimetypes
import aiohttp
from telethon import Button
from telethon.tl.types import DocumentAttributeVideo

from config import (
    BROWSER_HEADERS, X_COOKIES_FILE, INSTAGRAM_COOKIES_FILE, ACTIVE_CANCEL_EVENTS
)
from database import get_user_config
from utils import (
    clean_url, is_complex_url, is_x_url, is_instagram_url, is_tiktok_url,
    get_video_metadata_and_thumb, extract_9_frames, split_video_file,
    trim_video_clip, deep_sanitize_image, format_size, format_time
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
        'writethumbnails': True,
        'allow_playlist_files': True,
    }
    url_lower = url.lower()

    if is_x_url(url) and os.path.exists(X_COOKIES_FILE):
        ydl_opts['cookiefile'] = X_COOKIES_FILE
    elif is_instagram_url(url) and os.path.exists(INSTAGRAM_COOKIES_FILE):
        ydl_opts['cookiefile'] = INSTAGRAM_COOKIES_FILE

    if 'tiktok.com' in url_lower:
        ydl_opts['format'] = 'best'
    elif fmt in ['mp3', 'audio_mp3']:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
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
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": BROWSER_HEADERS['User-Agent']
        }
        res = requests.post(api_url, json={"url": url}, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            status = data.get("status")
            urls_to_dl = []
            if status in ["stream", "redirect"]:
                urls_to_dl.append(data.get("url"))
            elif status == "picker":
                for item in data.get("picker", []):
                    urls_to_dl.append(item.get("url"))

            for idx, media_url in enumerate(urls_to_dl, start=1):
                if not media_url: continue
                r = requests.get(media_url, stream=True, headers=BROWSER_HEADERS, timeout=30)
                if r.status_code == 200:
                    is_vid = ".mp4" in media_url or "video" in r.headers.get("content-type", "")
                    ext = "mp4" if is_vid else "jpg"
                    filepath = os.path.join(task_dir, f"tt_photo_{idx}.{ext}")
                    with open(filepath, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk: f.write(chunk)
    except Exception as e:
        print(f"TikTok Cobalt API error: {e}")

def instagram_carousel_and_photo_engine(url, task_dir):
    try:
        cmd = [
            "gallery-dl",
            "--directory", task_dir,
            "--filename", "ig_{id}_{num}.{ext}",
            "--option", "extractor.instagram.post-quality=max",
            "--option", "extractor.instagram.include=posts",
            "--user-agent", BROWSER_HEADERS['User-Agent']
        ]
        if os.path.exists(INSTAGRAM_COOKIES_FILE):
            cmd.extend(["--cookies", INSTAGRAM_COOKIES_FILE])
        cmd.append(url)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        
        if any(os.path.isfile(os.path.join(task_dir, f)) for f in os.listdir(task_dir)):
            return

        ydl_opts = {
            'outtmpl': os.path.join(task_dir, 'ig_hd_%(id)s_%(autonumber)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'headers': BROWSER_HEADERS,
            'writethumbnails': True,
            'format': 'best',
        }
        if os.path.exists(INSTAGRAM_COOKIES_FILE):
            ydl_opts['cookiefile'] = INSTAGRAM_COOKIES_FILE

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if any(os.path.isfile(os.path.join(task_dir, f)) for f in os.listdir(task_dir)):
            return

        api_url = "https://api.cobalt.tools/api/json"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": BROWSER_HEADERS['User-Agent']
        }
        res = requests.post(api_url, json={"url": url, "downloadMode": "max"}, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            status = data.get("status")
            urls_to_dl = []
            if status in ["stream", "redirect"]:
                urls_to_dl.append(data.get("url"))
            elif status == "picker":
                for item in data.get("picker", []):
                    urls_to_dl.append(item.get("url"))

            for idx, media_url in enumerate(urls_to_dl, start=1):
                if not media_url: continue
                r = requests.get(media_url, stream=True, headers=BROWSER_HEADERS, timeout=30)
                if r.status_code == 200:
                    is_vid = ".mp4" in media_url or "video" in r.headers.get("content-type", "")
                    ext = "mp4" if is_vid else "jpg"
                    filepath = os.path.join(task_dir, f"ig_photo_{idx}.{ext}")
                    with open(filepath, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk: f.write(chunk)
    except Exception as e:
        print(f"Instagram engine error: {e}")

async def download_direct_async(bot, chat_id, url, filepath, status_msg, cancel_event, task_id):
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
                            try:
                                await status_msg.edit(text, buttons=cancel_btn)
                            except: pass

async def start_direct_execution(bot, chat_id, url, filename, as_doc=False, quality='best', media_msg=None, target_fmt='mp4', status_msg=None, trim_times=None):
    task_id = f"task_{int(time.time() * 1000)}"
    cancel_event = threading.Event()
    ACTIVE_CANCEL_EVENTS[task_id] = cancel_event

    cancel_btn = [Button.inline("❌ إلغاء العملية", data=f"cancel_{task_id}")]

    if not status_msg:
        status_msg = await bot.send_message(chat_id, "⏳ **جاري تحضير الطلب...**", buttons=cancel_btn)
    else:
        try:
            await status_msg.edit("⏳ **جاري تحضير الطلب...**", buttons=cancel_btn)
        except Exception:
            status_msg = await bot.send_message(chat_id, "⏳ **جاري تحضير الطلب...**", buttons=cancel_btn)
        
    task_dir = os.path.join("downloads", task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    user_config = get_user_config(chat_id)

    try:
        loop = asyncio.get_event_loop()
        target_url = clean_url(url) if is_complex_url(url) else url
        is_social = is_complex_url(target_url) if target_url else False
        
        if cancel_event.is_set(): raise Exception("CANCELLED")

        if target_url and is_social:
            await loop.run_in_executor(None, download_with_ytdlp, target_url, task_dir, target_fmt, quality)
            if cancel_event.is_set(): raise Exception("CANCELLED")

            if is_instagram_url(target_url):
                downloaded = [f for f in os.listdir(task_dir) if os.path.getsize(os.path.join(task_dir, f)) > 0]
                if not downloaded:
                    await loop.run_in_executor(None, instagram_carousel_and_photo_engine, target_url, task_dir)

            if is_tiktok_url(target_url):
                downloaded = [f for f in os.listdir(task_dir) if os.path.getsize(os.path.join(task_dir, f)) > 0]
                if not downloaded:
                    await loop.run_in_executor(None, tiktok_multi_engine, target_url, task_dir)

        elif target_url:
            filepath = os.path.join(task_dir, filename)
            
            upload_start_time = time.time()
            last_upload_update = 0

            async def upload_progress_callback(current, total):
                if cancel_event.is_set(): raise Exception("CANCELLED")
                nonlocal last_upload_update
                now = time.time()
                if now - last_upload_update > 2.0 or current == total:
                    last_upload_update = now
                    elapsed = now - upload_start_time
                    speed = current / elapsed if elapsed > 0 else 0
                    percent = (current / total * 100) if total > 0 else 0
                    filled = int(percent // 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    rem_time = (total - current) / speed if speed > 0 and total > 0 else 0
                    
                    text = (
                        f"📤 **جاري رفع الملف إلى تيليجرام...**\n"
                        f"[{bar}] {percent:.1f}%\n"
                        f"📦 الحجم: `{format_size(current)}` / `{format_size(total)}`\n"
                        f"⚡ السرعة: `{format_size(speed)}/s`\n"
                        f"⏱️ الوقت المنقضي: `{format_time(elapsed)}`\n"
                        f"⏳ المتبقي: `{format_time(rem_time)}`"
                    )
                    try:
                        await status_msg.edit(text, buttons=cancel_btn)
                    except: pass

            await download_direct_async(bot, chat_id, target_url, filepath, status_msg, cancel_event, task_id)
            
            if cancel_event.is_set(): raise Exception("CANCELLED")
            
            if trim_times:
                await status_msg.edit("✂️ **جاري قص المقطع المباشر...**")
                filepath = trim_video_clip(filepath, trim_times[0], trim_times[1])

            await status_msg.edit("📤 **جاري تجهيز الرفع...**", buttons=cancel_btn)
            
            if target_fmt == 'mp3':
                base, _ = os.path.splitext(filepath)
                mp3_path = base + ".mp3"
                subprocess.run(['ffmpeg', '-y', '-i', filepath, '-vn', '-ab', '192k', mp3_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(mp3_path):
                    filepath = mp3_path
            
            video_files = split_video_file(filepath)

            for idx, vid_file in enumerate(video_files, start=1):
                part_caption = f" (Part {idx}/{len(video_files)})" if len(video_files) > 1 else ""
                
                if as_doc:
                    await bot.send_file(chat_id, vid_file, force_document=True, caption=part_caption, progress_callback=upload_progress_callback)
                else:
                    ext = os.path.splitext(vid_file)[1].lower()
                    if ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm']:
                        duration, width, height, thumb_path = get_video_metadata_and_thumb(vid_file)
                        attr = [DocumentAttributeVideo(duration=duration, w=width, h=height, supports_streaming=True)]
                        await bot.send_file(chat_id, vid_file, caption=part_caption, thumb=thumb_path, attributes=attr, progress_callback=upload_progress_callback)
                        
                        should_take_snaps = user_config.get("snapshots", True) and (not is_social or user_config.get("social_snapshots", False))
                        
                        if duration > 0 and should_take_snaps and idx == 1:
                            await status_msg.edit("📸 **جاري التقاط 9 صور من الفيديو وتجهيز الألبوم...**")
                            frames = extract_9_frames(vid_file, duration, chat_id=chat_id)
                            if frames:
                                await bot.send_file(chat_id, frames, caption="📸 **ألبوم اللقطات المصورة من الفيديو مع التوقيتات:**")
                                for fr in frames:
                                    try: os.remove(fr)
                                    except: pass

                        if thumb_path and os.path.exists(thumb_path):
                            try: os.remove(thumb_path)
                            except: pass
                    elif ext in ['.mp3', '.wav', '.m4a', '.aac']:
                        await bot.send_file(chat_id, vid_file, caption=part_caption, progress_callback=upload_progress_callback)
                    else:
                        await bot.send_file(chat_id, vid_file, caption=part_caption, force_document=True, progress_callback=upload_progress_callback)
            
            await status_msg.delete()
            return

        elif media_msg:
            filepath = os.path.join(task_dir, filename)
            await bot.download_media(media_msg, file=filepath)

        if cancel_event.is_set(): raise Exception("CANCELLED")

        downloaded_files = []
        for root, _, files in os.walk(task_dir):
            for file in files:
                fpath = os.path.join(root, file)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 500 and not file.endswith('_thumb.jpg') and not file.endswith('_clean.jpg') and not file.endswith('_final.jpg') and not file.startswith('frame_'):
                    
                    base_name, current_ext = os.path.splitext(file)
                    current_ext = current_ext.lower()
                    
                    if "none" in file.lower() or current_ext not in ['.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mkv', '.mov', '.avi']:
                        mime_type, _ = mimetypes.guess_type(fpath)
                        new_ext = ".jpg"
                        if mime_type:
                            guessed_ext = mimetypes.guess_extension(mime_type)
                            if guessed_ext:
                                new_ext = '.jpg' if guessed_ext == '.jpe' else guessed_ext
                        
                        clean_base = base_name.replace("None", "media").replace("none", "media")
                        if not clean_base or clean_base == "media":
                            clean_base = f"media_{int(time.time()*1000)}"
                            
                        new_fpath = os.path.join(root, f"{clean_base}{new_ext}")
                        os.rename(fpath, new_fpath)
                        fpath = new_fpath
                        current_ext = os.path.splitext(fpath)[1].lower()

                    if current_ext in ['.jpg', '.jpeg', '.png', '.webp']:
                        fpath = await loop.run_in_executor(None, deep_sanitize_image, fpath)

                    downloaded_files.append(fpath)

        if not downloaded_files:
            raise Exception("تعذر الوصول إلى المحتوى. تأكد من صحة الرابط.")

        await status_msg.edit(f"📤 **جاري رفع المحتوى ({len(downloaded_files)} عنصر)...**", buttons=cancel_btn)

        photos, videos, other_files = [], [], []
        video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.webm')
        image_extensions = ('.jpg', '.jpeg', '.png')

        for fpath in sorted(downloaded_files):
            ext = os.path.splitext(fpath)[1].lower()
            if ext in image_extensions:
                photos.append(fpath)
            elif ext in video_extensions:
                videos.append(fpath)
            else:
                other_files.append(fpath)

        if photos:
            sent_as_album = False
            if len(photos) > 1 and not as_doc:
                try:
                    uploaded_handles = []
                    for p in photos[:10]:
                        uploaded_file = await bot.upload_file(p)
                        uploaded_handles.append(uploaded_file)
                    await bot.send_file(chat_id, uploaded_handles, caption=f"📸 **تم تنزيل ألبوم الصور ({len(photos)} صورة):**")
                    sent_as_album = True
                except Exception as album_err:
                    print(f"Album upload failed: {album_err}")

            if not sent_as_album:
                for idx, p in enumerate(photos, start=1):
                    try:
                        uploaded_single = await bot.upload_file(p)
                        cap = f"📸 **صورة ({idx}/{len(photos)}):**" if len(photos) > 1 else "📸 **تم تنزيل الصورة بنجاح!**"
                        await bot.send_file(chat_id, uploaded_single, caption=cap, force_document=as_doc)
                    except Exception as single_err:
                        await bot.send_file(chat_id, p, force_document=True)

        for vid in videos:
            if cancel_event.is_set(): raise Exception("CANCELLED")
            
            if trim_times:
                vid = trim_video_clip(vid, trim_times[0], trim_times[1])

            split_vids = split_video_file(vid)

            for idx, part_vid in enumerate(split_vids, start=1):
                part_caption = f" (Part {idx}/{len(split_vids)})" if len(split_vids) > 1 else ""
                duration, width, height, thum    if 'tiktok.com' in url_lower:
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
                            
