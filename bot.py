#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import shutil
import asyncio
import logging
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yt_dlp
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
from telegram.error import TelegramError

# تحميل متغيرات البيئة
load_dotenv()

# ============ الإعدادات ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
DOWNLOAD_PATH = Path(os.getenv("DOWNLOAD_PATH", "./downloads"))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024))  # 2GB
PORT = int(os.getenv("PORT", 8080))
RAILWAY_ENV = os.getenv("RAILWAY_ENVIRONMENT", "false").lower() == "true"

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
    """مهمة تحميل"""
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
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

# تخزين المهام النشطة
active_tasks: Dict[int, DownloadTask] = {}
user_sessions: Dict[int, Dict[str, Any]] = {}
last_update_time: Dict[int, float] = {}

# ============ فئة التحميل ============
class MediaDownloader:
    """محمل الوسائط"""
    
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
        """استخراج معلومات الرابط"""
        ydl_opts = {
            **self.ydl_opts_base,
            'skip_download': True,
        }
        
        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, _extract)
            
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail'),
                'uploader': info.get('uploader'),
                'formats': self._get_formats(info),
            }
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            raise ValueError(f"فشل استخراج المعلومات: {str(e)}")
    
    def _get_formats(self, info: Dict) -> List[Dict]:
        """الحصول على الصيغ المتاحة"""
        formats = []
        seen_heights = set()
        
        # تنسيقات الفيديو
        for f in info.get('formats', []):
            height = f.get('height')
            ext = f.get('ext')
            
            if height and height not in seen_heights and f.get('vcodec') != 'none':
                if ext in ['mp4', 'webm']:
                    seen_heights.add(height)
                    filesize = f.get('filesize') or f.get('filesize_approx', 0)
                    
                    formats.append({
                        'format_id': f.get('format_id'),
                        'height': height,
                        'quality': f"{height}p",
                        'ext': ext,
                        'filesize': filesize,
                        'size_str': self._format_size(filesize),
                        'type': 'video'
                    })
        
        # ترتيب تنازلي حسب الجودة
        video_formats = sorted(
            [f for f in formats if f['type'] == 'video'],
            key=lambda x: x['height'],
            reverse=True
        )
        
        # إضافة خيار الصوت
        audio_format = {
            'format_id': 'bestaudio',
            'quality': '🎵 صوت فقط (MP3)',
            'ext': 'mp3',
            'filesize': 0,
            'size_str': 'متغير',
            'type': 'audio'
        }
        
        return video_formats + [audio_format]
    
    @staticmethod
    def _format_size(size: int) -> str:
        """تنسيق حجم الملف"""
        if size == 0:
            return "غير معروف"
        elif size < 1024 * 1024:
            return f"{size / 1024:.0f}KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f}GB"
    
    async def download_video(
        self,
        url: str,
        format_id: str,
        task: DownloadTask,
        loop: asyncio.AbstractEventLoop,
        progress_callback=None
    ) -> Tuple[Optional[str], Path]:
        """تحميل الفيديو"""
        temp_dir = Path(tempfile.mkdtemp(prefix="dl_"))
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                percent_str = d.get('_percent_str', '0%').replace('%', '').replace('\x1b[0m', '').strip()
                try:
                    percent = float(percent_str)
                except Exception:
                    percent = 0
                
                task.progress = percent
                task.speed = d.get('_speed_str', 'N/A').replace('\x1b[0m', '').strip()
                task.eta = d.get('_eta_str', 'N/A').replace('\x1b[0m', '').strip()
                
                if progress_callback and loop:
                    asyncio.run_coroutine_threadsafe(progress_callback(task), loop)
            elif d['status'] == 'finished':
                task.progress = 100
                if progress_callback and loop:
                    asyncio.run_coroutine_threadsafe(progress_callback(task), loop)
        
        ydl_opts = {
            **self.ydl_opts_base,
            'format': format_id,
            'outtmpl': str(temp_dir / '%(title).80s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'merge_output_format': 'mp4',
            'concurrent_fragment_downloads': 4,
        }
        
        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
                if os.path.exists(filename):
                    return filename
                
                files = list(temp_dir.glob('*'))
                if files:
                    return str(files[0])
                
                return None
        
        try:
            filepath = await loop.run_in_executor(None, _download)
            return filepath, temp_dir
        except Exception as e:
            logger.error(f"Download error: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError(f"فشل التحميل: {str(e)}")
    
    async def download_audio(
        self,
        url: str,
        task: DownloadTask,
        loop: asyncio.AbstractEventLoop,
        progress_callback=None
    ) -> Tuple[Optional[str], Path]:
        """تحميل الصوت"""
        temp_dir = Path(tempfile.mkdtemp(prefix="audio_"))
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                percent_str = d.get('_percent_str', '0%').replace('%', '').replace('\x1b[0m', '').strip()
                try:
                    percent = float(percent_str)
                except Exception:
                    percent = 0
                
                task.progress = percent
                task.speed = d.get('_speed_str', 'N/A').replace('\x1b[0m', '').strip()
                task.eta = d.get('_eta_str', 'N/A').replace('\x1b[0m', '').strip()
                
                if progress_callback and loop:
                    asyncio.run_coroutine_threadsafe(progress_callback(task), loop)
        
        ydl_opts = {
            **self.ydl_opts_base,
            'format': 'bestaudio/best',
            'outtmpl': str(temp_dir / '%(title).80s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        
        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
                
                mp3_files = list(temp_dir.glob('*.mp3'))
                if mp3_files:
                    return str(mp3_files[0])
                
                audio_files = list(temp_dir.glob('*'))
                if audio_files:
                    return str(audio_files[0])
                
                return None
        
        try:
            filepath = await loop.run_in_executor(None, _download)
            return filepath, temp_dir
        except Exception as e:
            logger.error(f"Audio download error: {e}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError(f"فشل تحميل الصوت: {str(e)}")

# إنشاء المحمل
downloader = MediaDownloader()

# ============ دوال مساعدة ============
def is_valid_url(url: str) -> bool:
    """التحقق من صحة الرابط"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def detect_platform(url: str) -> Optional[str]:
    """تحديد المنصة من الرابط"""
    platforms = {
        'youtube': ['youtube.com', 'youtu.be'],
        'facebook': ['facebook.com', 'fb.watch'],
        'instagram': ['instagram.com'],
        'twitter': ['twitter.com', 'x.com'],
        'tiktok': ['tiktok.com'],
        'reddit': ['reddit.com'],
        'vimeo': ['vimeo.com'],
        'telegram': ['t.me'],
    }
    
    domain = urlparse(url).netloc.lower()
    
    for platform, domains in platforms.items():
        for d in domains:
            if d in domain:
                return platform
    
    return None

def create_progress_bar(percent: float, length: int = 20) -> str:
    """إنشاء شريط تقدم"""
    filled = int(length * percent / 100)
    bar = '█' * filled + '░' * (length - filled)
    return f"[{bar}]"

def format_duration(seconds: int) -> str:
    """تنسيق المدة الزمنية"""
    if not seconds:
        return "غير معروف"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"

# ============ معالجات البوت ============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البداية"""
    user = update.effective_user
    
    welcome_text = f"""
👋 *مرحباً {user.first_name}!*

أنا بوت تحميل الفيديوهات والصوتيات من منصات متعددة.

📥 *المنصات المدعومة:*
• YouTube
• Facebook
• Instagram
• TikTok
• Twitter/X
• Reddit
• Vimeo

🎯 *كيفية الاستخدام:*
1️⃣ أرسل رابط الفيديو
2️⃣ اختر الجودة المطلوبة
3️⃣ انتظر اكتمال التحميل

📊 *مميزات:*
• اختيار جودة متعددة
• تحميل الصوت فقط
• شريط تقدم مباشر
• دعم ملفات كبيرة

⚠️ *ملاحظة:* الحد الأقصى لحجم الملف {MAX_FILE_SIZE // (1024*1024*1024)}GB

❓ للمساعدة أرسل /help
"""
    
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة"""
    help_text = """
📚 *دليل الاستخدام*

*الأوامر المتاحة:*
/start - بدء البوت
/help - عرض المساعدة
/status - حالة التحميلات النشطة
/cancel - إلغاء التحميل الحالي

*طريقة الاستخدام:*
1. أرسل رابط الفيديو
2. اختر الجودة من القائمة
3. انتظر حتى يكتمل التحميل
4. استلم الملف مباشرة

*نصائح:*
• الروابط القصيرة مدعومة
• يمكن تحميل الصوت فقط
• التحميل المتوازي مدعوم

*الاستخدام العادل:*
• احترم حقوق النشر
• لا تستخدم لتحميل محتوى محمي
• استخدم للأغراض الشخصية فقط

*للمساعدة التقنية:*
تواصل مع @admin
"""
    
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر حالة التحميلات"""
    user_id = update.effective_user.id
    
    if user_id not in active_tasks:
        await update.message.reply_text(
            "📊 *لا توجد تحميلات نشطة*\n\n"
            "أرسل رابط فيديو للبدء!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    task = active_tasks[user_id]
    
    status_text = f"""
📊 *حالة التحميل*

*العنوان:* {task.title[:50]}
*الحالة:* {task.status}
*التقدم:* {task.progress:.1f}%
*السرعة:* {task.speed}
*الوقت المتبقي:* {task.eta}
"""
    
    await update.message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر إلغاء التحميل"""
    user_id = update.effective_user.id
    
    if user_id in active_tasks:
        task = active_tasks[user_id]
        task.status = "cancelled"
        del active_tasks[user_id]
        if user_id in last_update_time:
            del last_update_time[user_id]
        
        await update.message.reply_text(
            "✅ *تم إلغاء التحميل*",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "❌ *لا يوجد تحميل نشط للإلغاء*",
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل"""
    user = update.effective_user
    text = update.message.text.strip()
    
    if not is_valid_url(text):
        await update.message.reply_text(
            "❌ *الرجاء إرسال رابط صحيح*\n\n"
            "مثال: https://youtube.com/watch?v=...",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    platform = detect_platform(text)
    if not platform:
        await update.message.reply_text(
            "❌ *المنصة غير مدعومة*\n\n"
            "المنصات المدعومة:\n"
            "YouTube, Facebook, Instagram, TikTok, Twitter, Reddit, Vimeo",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    await update.message.chat.send_action(ChatAction.TYPING)
    status_msg = await update.message.reply_text(
        "🔍 *جاري تحليل الرابط...*\n\n"
        f"المنصة: {platform.capitalize()}",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        info = await downloader.extract_info(text)
        
        user_sessions[user.id] = {
            'url': text,
            'title': info['title'],
            'duration': info['duration'],
            'formats': info['formats'],
        }
        
        keyboard = []
        for fmt in info['formats'][:8]:
            if fmt['type'] == 'video':
                callback_data = f"dl:video:{fmt['format_id']}"
                button_text = f"🎥 {fmt['quality']} ({fmt['size_str']})"
            else:
                callback_data = f"dl:audio:{fmt['format_id']}"
                button_text = fmt['quality']
            
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        info_text = f"""
📹 *معلومات الفيديو*

*العنوان:* {info['title'][:100]}
*المدة:* {format_duration(info['duration'])}
*المنصة:* {platform.capitalize()}

اختر الجودة المطلوبة للتحميل:
"""
        
        await status_msg.edit_text(
            info_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error processing {text}: {e}")
        await status_msg.edit_text(
            "❌ *فشل معالجة الرابط*\n\n"
            "تأكد من أن الرابط صحيح ويمكن الوصول إليه.",
            parse_mode=ParseMode.MARKDOWN
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار الاختيار"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    if not data.startswith('dl:'):
        return
    
    # تفكيك الآمن عبر التنسيق dl:type:format_id
    parts = data.split(':', 2)
    media_type = parts[1]
    format_id = parts[2]
    
    if user.id not in user_sessions:
        await query.edit_message_text(
            "❌ *انتهت الجلسة*\n\n"
            "أرسل الرابط مرة أخرى.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    session = user_sessions[user.id]
    
    task = DownloadTask(
        user_id=user.id,
        url=session['url'],
        title=session['title'],
        duration=session['duration'],
        format_id=format_id,
        is_audio=(media_type == 'audio'),
        status="downloading"
    )
    
    active_tasks[user.id] = task
    
    await query.edit_message_text(
        "⬇️ *جاري التحميل...*\n\n"
        f"العنوان: {task.title[:50]}\n"
        f"التقدم: {create_progress_bar(0)} 0%",
        parse_mode=ParseMode.MARKDOWN
    )
    
    async def progress_callback(task: DownloadTask):
        """تحديث التقدم المباشر مع تفادي الـ Rate Limit"""
        now = time.time()
        uid = task.user_id
        
        if task.status == "cancelled":
            return

        # التحكم بالتحديث: كل 3 ثوانٍ على الأقل أو عند اكتمال التحميل
        if uid in last_update_time and (now - last_update_time[uid] < 3.0) and task.progress < 100:
            return

        last_update_time[uid] = now
        progress_bar = create_progress_bar(task.progress)
        
        message_text = f"""
⬇️ *جاري التحميل...*

*العنوان:* {task.title[:50]}
*الحالة:* {task.status}

{progress_bar} {task.progress:.1f}%

⚡ *السرعة:* {task.speed}
⏳ *الوقت المتبقي:* {task.eta}
"""
        try:
            await query.edit_message_text(
                message_text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
    
    loop = asyncio.get_running_loop()
    filepath = None
    temp_dir = None
    
    try:
        if task.is_audio:
            filepath, temp_dir = await downloader.download_audio(
                task.url,
                task,
                loop,
                progress_callback
            )
        else:
            filepath, temp_dir = await downloader.download_video(
                task.url,
                task.format_id,
                task,
                loop,
                progress_callback
            )
        
        if not filepath or not os.path.exists(filepath):
            raise ValueError("الملف غير موجود بعد التحميل")
        
        task.file_path = filepath
        task.file_size = os.path.getsize(filepath)
        task.status = "uploading"
        
        if task.file_size > MAX_FILE_SIZE:
            await query.edit_message_text(
                "❌ *حجم الملف كبير جداً*\n\n"
                f"الحجم: {task.file_size / (1024**3):.1f}GB\n"
                f"الحد الأقصى: {MAX_FILE_SIZE / (1024**3):.1f}GB",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        await query.edit_message_text(
            "📤 *جاري رفع الملف إلى تليجرام...*\n\n"
            f"الحجم: {task.file_size / (1024**2):.1f}MB",
            parse_mode=ParseMode.MARKDOWN
        )
        
        await query.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
        
        with open(filepath, 'rb') as file:
            if task.is_audio:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=file,
                    title=task.title[:100],
                    performer="Downloaded via Bot",
                    caption=f"✅ *تم التحميل بنجاح!*\n\n"
                            f"🎵 {task.title[:100]}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=file,
                    supports_streaming=True,
                    caption=f"✅ *تم التحميل بنجاح!*\n\n"
                            f"🎥 {task.title[:100]}",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        await query.delete_message()
        task.status = "completed"
        task.completed_at = datetime.now()

    except Exception as e:
        logger.error(f"Download failed for {task.url}: {e}")
        task.status = "failed"
        task.error = str(e)
        
        try:
            await query.edit_message_text(
                "❌ *فشل التحميل*\n\n"
                f"الخطأ: {str(e)[:200]}\n\n"
                "حاول مرة أخرى أو جرب جودة مختلفة.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
            
    finally:
        # ضمان تنظيف المجلدات المؤقتة والذاكرة
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        if user.id in active_tasks:
            del active_tasks[user.id]
        if user.id in user_sessions:
            del user_sessions[user.id]
        if user.id in last_update_time:
            del last_update_time[user.id]

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ *حدث خطأ غير متوقع*\n\n"
                "يرجى المحاولة مرة أخرى لاحقاً.",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception:
        pass

# ============ خادم صحي لـ Railway ============
def create_health_server():
    """إنشاء خادم فحص الصحة"""
    from fastapi import FastAPI
    
    app = FastAPI()
    
    @app.get("/")
    async def root():
        return {"status": "running", "bot": "Telegram Downloader"}
    
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "active_downloads": len(active_tasks)
        }
    
    return app

# ============ الدالة الرئيسية ============
def main():
    """تشغيل البوت"""
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        print("❌ خطأ: لم يتم تعيين BOT_TOKEN")
        print("أضف التوكن في ملف .env أو متغيرات البيئة")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    if RAILWAY_ENV:
        import uvicorn
        from threading import Thread
        
        health_app = create_health_server()
        
        def run_health_server():
            uvicorn.run(health_app, host="0.0.0.0", port=PORT, log_level="info")
        
        Thread(target=run_health_server, daemon=True).start()
        logger.info(f"Health server running on port {PORT}")
    
    logger.info("Bot starting...")
    print("🤖 Bot is running...")
    print("Press Ctrl+C to stop")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
