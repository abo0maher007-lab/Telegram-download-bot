#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import shutil
import asyncio
import logging
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yt_dlp
import uvicorn
from fastapi import FastAPI
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode, ChatAction

# تحميل متغيرات البيئة
load_dotenv()

# ============ الإعدادات ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
DOWNLOAD_PATH = Path(os.getenv("DOWNLOAD_PATH", "./downloads"))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024))  # 2GB
PORT = int(os.getenv("PORT", 8080))

# إنشاء مجلد التحميل
DOWNLOAD_PATH.mkdir(parents=True, exist_ok=True)

# ============ التسجيل ============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ البيانات المؤقتة ============
@dataclass
class DownloadTask:
    user_id: int
    url: str
    title: str
    duration: int
    status: str = "pending"
    progress: float = 0
    speed: str = "N/A"
    eta: str = "N/A"
    file_path: Optional[str] = None
    file_size: int = 0
    format_id: Optional[str] = None
    is_audio: bool = False
    created_at: datetime = field(default_factory=datetime.now)

active_tasks: Dict[int, DownloadTask] = {}
user_sessions: Dict[int, Dict[str, Any]] = {}
last_update_time: Dict[int, float] = {}

# ============ فئة التحميل ============
class MediaDownloader:
    def __init__(self):
        self.ydl_opts_base = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'retries': 3,
            'fragment_retries': 3,
            'file_access_retries': 3,
            'continuedl': True,
            'prefer_ffmpeg': True,
            'ffmpeg_location': '/usr/bin/ffmpeg' if os.path.exists('/usr/bin/ffmpeg') else 'ffmpeg',
        }
    
    async def extract_info(self, url: str) -> Dict[str, Any]:
        ydl_opts = {**self.ydl_opts_base, 'skip_download': True}
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, _extract)
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'formats': self._get_formats(info),
            }
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            raise ValueError(f"فشل استخراج المعلومات: {str(e)}")
    
    def _get_formats(self, info: Dict) -> List[Dict]:
        formats = []
        seen_heights = set()
        for f in info.get('formats', []):
            height = f.get('height')
            ext = f.get('ext')
            if height and height not in seen_heights and f.get('vcodec') != 'none':
                if ext in ['mp4', 'webm']:
                    seen_heights.add(height)
                    filesize = f.get('filesize') or f.get('filesize_approx', 0)
                    formats.append({
                        'format_id': f.get('format_id'),
                        'quality': f"{height}p",
                        'size_str': self._format_size(filesize),
                        'type': 'video'
                    })
        video_formats = sorted(formats, key=lambda x: int(x['quality'].replace('p', '')), reverse=True)
        audio_format = {'format_id': 'bestaudio', 'quality': '🎵 صوت فقط (MP3)', 'size_str': 'متغير', 'type': 'audio'}
        return video_formats + [audio_format]

    @staticmethod
    def _format_size(size: int) -> str:
        if size == 0: return "غير معروف"
        if size < 1024 * 1024: return f"{size / 1024:.0f}KB"
        if size < 1024 * 1024 * 1024: return f"{size / (1024 * 1024):.1f}MB"
        return f"{size / (1024 * 1024 * 1024):.1f}GB"

    async def download_media(self, url: str, format_id: str, is_audio: bool, task: DownloadTask, loop: asyncio.AbstractEventLoop, progress_callback=None) -> Tuple[Optional[str], Path]:
        temp_dir = Path(tempfile.mkdtemp(prefix="dl_"))
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent = float(d.get('_percent_str', '0%').replace('%', '').strip())
                except Exception:
                    percent = 0
                task.progress = percent
                task.speed = d.get('_speed_str', 'N/A').strip()
                task.eta = d.get('_eta_str', 'N/A').strip()
                if progress_callback and loop:
                    asyncio.run_coroutine_threadsafe(progress_callback(task), loop)

        ydl_opts = {
            **self.ydl_opts_base,
            'outtmpl': str(temp_dir / '%(title).80s.%(ext)s'),
            'progress_hooks': [progress_hook],
        }
        
        if is_audio:
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            })
        else:
            ydl_opts.update({'format': format_id, 'merge_output_format': 'mp4'})

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
                files = list(temp_dir.glob('*'))
                return str(files[0]) if files else None

        try:
            filepath = await loop.run_in_executor(None, _download)
            return filepath, temp_dir
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError(f"فشل التحميل: {str(e)}")

downloader = MediaDownloader()

# ============ دوال المساعدة ============
def is_valid_url(url: str) -> bool:
    try: return all([urlparse(url).scheme, urlparse(url).netloc])
    except Exception: return False

def create_progress_bar(percent: float) -> str:
    filled = int(20 * percent / 100)
    return f"[{'█' * filled}{'░' * (20 - filled)}]"

# ============ معالجات البوت ============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 مرحباً بك! أرسل رابط الفيديو للبدء بالتحميل.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not is_valid_url(text):
        await update.message.reply_text("❌ الرجاء إرسال رابط صحيح.")
        return
    
    status_msg = await update.message.reply_text("🔍 جاري تحليل الرابط...")
    try:
        info = await downloader.extract_info(text)
        user_sessions[update.effective_user.id] = {'url': text, 'title': info['title']}
        
        keyboard = []
        for fmt in info['formats'][:8]:
            cb_data = f"dl:{fmt['type']}:{fmt['format_id']}"
            keyboard.append([InlineKeyboardButton(f"{fmt['quality']} ({fmt['size_str']})", callback_data=cb_data)])
        
        await status_msg.edit_text(f"📹 *{info['title'][:80]}*\n\nاختر الجودة المطلوب تحميلها:", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await status_msg.edit_text("❌ فشل معالجة الرابط، تأكد من صحته.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(':', 2)
    is_audio = (parts[1] == 'audio')
    format_id = parts[2]
    user_id = query.from_user.id
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ انتهت الجلسة، أرسل الرابط مجدداً.")
        return
        
    session = user_sessions[user_id]
    task = DownloadTask(user_id=user_id, url=session['url'], title=session['title'], format_id=format_id, is_audio=is_audio, status="downloading")
    active_tasks[user_id] = task

    async def progress_callback(t: DownloadTask):
        now = time.time()
        if user_id in last_update_time and (now - last_update_time[user_id] < 3.0) and t.progress < 100:
            return
        last_update_time[user_id] = now
        try:
            await query.edit_message_text(f"⬇️ *جاري التحميل...*\n\n{create_progress_bar(t.progress)} {t.progress:.1f}%\n⚡ السرعة: {t.speed}", parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

    temp_dir = None
    try:
        filepath, temp_dir = await downloader.download_media(task.url, task.format_id, task.is_audio, task, asyncio.get_running_loop(), progress_callback)
        await query.edit_message_text("📤 جاري الرفع إلى تلجرام...")
        
        with open(filepath, 'rb') as f:
            if is_audio:
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, title=task.title)
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=f, supports_streaming=True)
        await query.delete_message()
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ أثناء التحميل: {str(e)[:100]}")
    finally:
        if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)
        active_tasks.pop(user_id, None)
        user_sessions.pop(user_id, None)

# ============ خادم الصحة (FastAPI) ============
web_app = FastAPI()

@web_app.get("/")
@web_app.get("/health")
async def health_check():
    return {"status": "ok", "active_tasks": len(active_tasks)}

# ============ تشغيل التطبيق المزدوج ============
async def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN غير موجود!")
        return

    # إعداد البوت
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    bot_app.add_handler(CallbackQueryHandler(button_callback))

    # إعداد خادم Uvicorn
    config = uvicorn.Config(app=web_app, host="0.0.0.0", port=PORT, log_level="error")
    server = uvicorn.Server(config)

    # تشغيل البوت والـ Healthcheck بالتوازي في نفس Event Loop
    async with bot_app:
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info(f"🚀 Server & Bot running on port {PORT}")
        await server.serve()
        await bot_app.updater.stop()
        await bot_app.stop()

if __name__ == "__main__":
    asyncio.run(main())
