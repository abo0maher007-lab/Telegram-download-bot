#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shadow Mode V99 - Elite Telegram Downloader Bot
Complete Multi-Engine Video Extraction System
"""

import os
import re
import asyncio
import logging
import json
import time
import uuid
import hashlib
import mimetypes
import subprocess
import tempfile
from typing import Optional, List, Dict, Any, Tuple, Union
from urllib.parse import urlparse, parse_qs, unquote, urljoin
from datetime import datetime

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, ConversationHandler, filters
)
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError, RetryAfter

# HTTP & Parsing
import aiohttp
import requests
from bs4 import BeautifulSoup
import cloudscraper
from fake_useragent import UserAgent

# Video Processing
import yt_dlp
import ffmpeg
from yt_dlp.utils import DownloadError, ExtractorError

# Configuration from Railway.com Environment Variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()]
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "2097152000"))  # 2GB default
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "/tmp/downloads")
ENABLE_DEBUG = os.getenv("ENABLE_DEBUG", "false").lower() == "true"
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
PROGRESS_UPDATE_INTERVAL = int(os.getenv("PROGRESS_UPDATE_INTERVAL", "5"))

# Ensure download directory exists
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if ENABLE_DEBUG else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class VideoExtractor:
    """Multi-engine video extraction system with advanced techniques"""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.scraper: Optional[cloudscraper.CloudScraper] = None
        self.ua = UserAgent()
        self.download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self.cache = {}
        
        # Enhanced yt-dlp options
        self.ytdl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'force_generic_extractor': False,
            'noplaylist': True,
            'retries': 10,
            'fragment_retries': 10,
            'socket_timeout': 30,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'logtostderr': False,
            'no_color': True,
            'geo_bypass': True,
            'cachedir': False,
            'writethumbnail': False,
            'verbose': False,
            'progress_hooks': [],
        }
    
    async def init_sessions(self):
        """Initialize HTTP sessions with anti-detection"""
        if not self.session or self.session.closed:
            # Advanced session with rotating user agents
            conn = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=10,
                ttl_dns_cache=300,
                ssl=False,
                force_close=False,
                enable_cleanup_closed=True
            )
            
            timeout = aiohttp.ClientTimeout(
                total=300,
                connect=30,
                sock_connect=30,
                sock_read=300
            )
            
            self.session = aiohttp.ClientSession(
                connector=conn,
                timeout=timeout,
                headers={
                    'User-Agent': self.ua.random,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Cache-Control': 'max-age=0',
                },
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                trust_env=True
            )
        
        if not self.scraper:
            # Cloudflare bypass scraper
            self.scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True,
                    'mobile': False
                },
                delay=10,
                interpreter='nodejs',
                sess=None,
                doubleDown=True
            )
    
    async def extract_video(self, url: str, progress_callback=None) -> Optional[Dict[str, Any]]:
        """Main extraction orchestrator"""
        await self.init_sessions()
        
        # Check cache
        cache_key = hashlib.md5(url.encode()).hexdigest()
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if time.time() - cached['timestamp'] < 300:  # 5 minutes cache
                return cached['data']
        
        # Try multiple extraction methods
        methods = [
            self._try_ytdlp,
            self._try_direct_download,
            self._try_cloudscraper_extraction,
            self._try_page_analysis,
            self._try_redirect_following,
            self._try_iframe_extraction,
            self._try_json_ld_extraction,
            self._try_social_media_extraction,
            self._try_stream_detection,
            self._try_playlist_extraction
        ]
        
        for i, method in enumerate(methods):
            if progress_callback:
                await progress_callback(f"🔍 محاولة {i+1}/{len(methods)}: {method.__name__}")
            
            try:
                result = await method(url)
                if result and result.get('url'):
                    # Cache successful result
                    self.cache[cache_key] = {
                        'data': result,
                        'timestamp': time.time()
                    }
                    return result
            except Exception as e:
                logger.error(f"Method {method.__name__} failed: {e}")
                continue
        
        return None
    
    async def _try_ytdlp(self, url: str) -> Optional[Dict[str, Any]]:
        """yt-dlp extraction - supports 1000+ sites"""
        try:
            def extract_sync():
                with yt_dlp.YoutubeDL(self.ytdl_opts) as ydl:
                    # Extract info without downloading
                    info = ydl.extract_info(url, download=False)
                    
                    if info:
                        formats = info.get('formats', [])
                        direct_url = None
                        
                        # Priority: MP4 with audio
                        for f in formats:
                            if f.get('protocol') in ['https', 'http']:
                                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                                    if f.get('ext') == 'mp4':
                                        direct_url = f.get('url')
                                        break
                        
                        # Fallback: Any video format
                        if not direct_url:
                            for f in formats:
                                if f.get('protocol') in ['https', 'http']:
                                    if f.get('vcodec') != 'none':
                                        direct_url = f.get('url')
                                        break
                        
                        # Fallback: Any format
                        if not direct_url and formats:
                            direct_url = formats[-1].get('url')
                        
                        return {
                            'url': direct_url or info.get('webpage_url'),
                            'title': info.get('title', 'video'),
                            'ext': info.get('ext', 'mp4'),
                            'thumbnail': info.get('thumbnail'),
                            'duration': info.get('duration'),
                            'filesize': info.get('filesize_approx') or info.get('filesize'),
                            'site': info.get('extractor_key', 'unknown'),
                            'description': info.get('description', ''),
                            'uploader': info.get('uploader', ''),
                            'view_count': info.get('view_count'),
                            'like_count': info.get('like_count'),
                        }
                return None
            
            # Run in thread pool for async
            result = await asyncio.get_event_loop().run_in_executor(None, extract_sync)
            return result
            
        except Exception as e:
            logger.error(f"yt-dlp extraction failed: {e}")
            return None
    
    async def _try_direct_download(self, url: str) -> Optional[Dict[str, Any]]:
        """Check if URL is a direct video file"""
        try:
            # Check if URL ends with video extension
            video_extensions = ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.3gp']
            
            parsed_url = urlparse(url)
            path = parsed_url.path.lower()
            
            if any(path.endswith(ext) for ext in video_extensions):
                # Verify with HEAD request
                async with self.session.head(url, allow_redirects=True) as response:
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '')
                        content_length = response.headers.get('Content-Length', '0')
                        
                        if 'video' in content_type or int(content_length) > 0:
                            return {
                                'url': url,
                                'title': os.path.basename(parsed_url.path) or 'video',
                                'ext': os.path.splitext(path)[1][1:] or 'mp4',
                                'filesize': int(content_length) if content_length.isdigit() else None,
                                'site': 'direct',
                                'content_type': content_type
                            }
            
            return None
            
        except Exception as e:
            logger.error(f"Direct download check failed: {e}")
            return None
    
    async def _try_cloudscraper_extraction(self, url: str) -> Optional[Dict[str, Any]]:
        """Cloudflare bypass extraction"""
        try:
            def extract_sync():
                # Use cloudscraper to bypass protection
                response = self.scraper.get(url)
                
                if response.status_code == 200:
                    # Parse HTML for video tags
                    soup = BeautifulSoup(response.text, 'lxml')
                    
                    # Look for video elements
                    video_tags = soup.find_all(['video', 'source'])
                    
                    for video in video_tags:
                        if video.name == 'source':
                            src = video.get('src') or video.get('data-src')
                        else:
                            src = video.get('src') or video.get('data-src')
                            if not src:
                                source_tag = video.find('source')
                                if source_tag:
                                    src = source_tag.get('src') or source_tag.get('data-src')
                        
                        if src and not src.startswith('data:') and not src.startswith('blob:'):
                            return {
                                'url': urljoin(url, src) if not src.startswith('http') else src,
                                'title': f"video_{int(time.time())}",
                                'ext': self._detect_extension(src),
                                'site': 'cloudscraper'
                            }
                    
                    # Look for meta tags
                    for meta_prop in ['og:video', 'og:video:url', 'twitter:player:stream']:
                        meta = soup.find('meta', property=meta_prop) or soup.find('meta', attrs={'name': meta_prop})
                        if meta and meta.get('content'):
                            content = meta.get('content')
                            if content and not content.startswith('data:'):
                                return {
                                    'url': content,
                                    'title': f"video_{int(time.time())}",
                                    'ext': 'mp4',
                                    'site': 'meta'
                                }
                    
                    # Look for video URLs in script tags
                    scripts = soup.find_all('script')
                    for script in scripts:
                        if script.string:
                            video_urls = re.findall(r'https?://[^\s"\']+\.(?:mp4|webm|m3u8)(?:\?[^\s"\']*)?', script.string)
                            if video_urls:
                                return {
                                    'url': video_urls[0],
                                    'title': f"video_{int(time.time())}",
                                    'ext': self._detect_extension(video_urls[0]),
                                    'site': 'script'
                                }
                
                return None
            
            result = await asyncio.get_event_loop().run_in_executor(None, extract_sync)
            return result
            
        except Exception as e:
            logger.error(f"Cloudscraper extraction failed: {e}")
            return None
    
    async def _try_page_analysis(self, url: str) -> Optional[Dict[str, Any]]:
        """Advanced page analysis with pattern matching"""
        try:
            async with self.session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                
                # Multiple pattern matching for video URLs
                patterns = [
                    # Direct video URLs
                    r'https?://[^\s"\'\<\>]+\.(?:mp4|webm|m3u8|mkv|avi|mov)(?:\?[^\s"\'\<\>]*)?',
                    # Quoted URLs
                    r'(?:"|\')([^"\']*\.(?:mp4|webm|m3u8)[^"\']*)(?:"|\')',
                    # src attributes
                    r'src=["\']([^"\']*video[^"\']*)["\']',
                    # data attributes
                    r'data-(?:src|url|video)=["\']([^"\']*\.(?:mp4|webm)[^"\']*)["\']',
                    # Stream URLs
                    r'stream(?:Url|URL|_url)?["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    # videoUrl variables
                    r'videoUrl\s*=\s*["\']([^"\']+)["\']',
                    # JSON video objects
                    r'"url"\s*:\s*"([^"]*\.(?:mp4|webm|m3u8)[^"]*)"',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, html, re.IGNORECASE)
                    if matches:
                        video_url = matches[0]
                        if isinstance(video_url, tuple):
                            video_url = video_url[0]
                        
                        # Clean URL
                        video_url = video_url.strip()
                        if video_url.startswith('//'):
                            video_url = 'https:' + video_url
                        
                        return {
                            'url': video_url,
                            'title': f"video_{int(time.time())}",
                            'ext': self._detect_extension(video_url),
                            'site': 'pattern'
                        }
                
                # Try to find JSON data
                json_patterns = [
                    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                ]
                
                for json_pattern in json_patterns:
                    json_matches = re.findall(json_pattern, html, re.DOTALL)
                    for json_str in json_matches:
                        try:
                            data = json.loads(json_str)
                            video_url = self._extract_video_from_json(data)
                            if video_url:
                                return {
                                    'url': video_url,
                                    'title': f"video_{int(time.time())}",
                                    'ext': self._detect_extension(video_url),
                                    'site': 'json'
                                }
                        except:
                            continue
                
                return None
                
        except Exception as e:
            logger.error(f"Page analysis failed: {e}")
            return None
    
    async def _try_redirect_following(self, url: str) -> Optional[Dict[str, Any]]:
        """Follow redirects to find actual video URL"""
        try:
            async with self.session.get(url, allow_redirects=True) as response:
                final_url = str(response.url)
                
                # Check if final URL is a video
                if self._is_video_url(final_url):
                    return {
                        'url': final_url,
                        'title': f"video_{int(time.time())}",
                        'ext': self._detect_extension(final_url),
                        'site': 'redirect'
                    }
                
                # Check headers for video content
                content_type = response.headers.get('Content-Type', '')
                if 'video' in content_type:
                    return {
                        'url': final_url,
                        'title': f"video_{int(time.time())}",
                        'ext': content_type.split('/')[-1].split(';')[0],
                        'site': 'header'
                    }
                
                return None
                
        except Exception as e:
            logger.error(f"Redirect following failed: {e}")
            return None
    
    async def _try_iframe_extraction(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract video from iframes"""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'lxml')
                
                # Find iframes
                iframes = soup.find_all('iframe')
                
                for iframe in iframes:
                    src = iframe.get('src') or iframe.get('data-src')
                    if src:
                        # Resolve relative URLs
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif not src.startswith('http'):
                            src = urljoin(url, src)
                        
                        # Recursively try to extract from iframe
                        result = await self.extract_video(src)
                        if result:
                            return result
                
                return None
                
        except Exception as e:
            logger.error(f"Iframe extraction failed: {e}")
            return None
    
    async def _try_json_ld_extraction(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract from JSON-LD structured data"""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'lxml')
                
                # Find JSON-LD scripts
                json_ld_scripts = soup.find_all('script', type='application/ld+json')
                
                for script in json_ld_scripts:
                    try:
                        data = json.loads(script.string)
                        
                        # Handle different JSON-LD formats
                        if isinstance(data, list):
                            for item in data:
                                video_url = self._extract_video_from_json_ld(item)
                                if video_url:
                                    return video_url
                        else:
                            video_url = self._extract_video_from_json_ld(data)
                            if video_url:
                                return video_url
                                
                    except:
                        continue
                
                return None
                
        except Exception as e:
            logger.error(f"JSON-LD extraction failed: {e}")
            return None
    
    async def _try_social_media_extraction(self, url: str) -> Optional[Dict[str, Any]]:
        """Special extraction for social media platforms"""
        try:
            # TikTok
            if 'tiktok.com' in url:
                return await self._extract_tiktok(url)
            
            # Instagram
            elif 'instagram.com' in url:
                return await self._extract_instagram(url)
            
            # Twitter/X
            elif 'twitter.com' in url or 'x.com' in url:
                return await self._extract_twitter(url)
            
            # Facebook
            elif 'facebook.com' in url or 'fb.watch' in url:
                return await self._extract_facebook(url)
            
            return None
            
        except Exception as e:
            logger.error(f"Social media extraction failed: {e}")
            return None
    
    async def _try_stream_detection(self, url: str) -> Optional[Dict[str, Any]]:
        """Detect streaming URLs"""
        try:
            # Check for HLS streams
            if '.m3u8' in url:
                return {
                    'url': url,
                    'title': f"stream_{int(time.time())}",
                    'ext': 'm3u8',
                    'site': 'hls'
                }
            
            # Check for DASH streams
            if '.mpd' in url:
                return {
                    'url': url,
                    'title': f"stream_{int(time.time())}",
                    'ext': 'mpd',
                    'site': 'dash'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Stream detection failed: {e}")
            return None
    
    async def _try_playlist_extraction(self, url: str) -> Optional[Dict[str, Any]]:
        """Extract from playlist files"""
        try:
            # Check if URL is a playlist
            if url.endswith(('.m3u8', '.m3u', '.pls', '.txt')):
                async with self.session.get(url) as response:
                    if response.status == 200:
                        content = await response.text()
                        
                        # Parse m3u8 playlist
                        if '.m3u8' in url or '#EXTM3U' in content:
                            lines = content.split('\n')
                            for line in lines:
                                if line and not line.startswith('#'):
                                    if line.startswith('http'):
                                        return {
                                            'url': line,
                                            'title': f"stream_{int(time.time())}",
                                            'ext': self._detect_extension(line),
                                            'site': 'playlist'
                                        }
            
            return None
            
        except Exception as e:
            logger.error(f"Playlist extraction failed: {e}")
            return None
    
    def _extract_video_from_json(self, data: Any) -> Optional[str]:
        """Recursively extract video URL from JSON data"""
        if isinstance(data, dict):
            for key, value in data.items():
                if key.lower() in ['video', 'videourl', 'video_url', 'contenturl', 'content_url', 'url']:
                    if isinstance(value, str) and self._is_video_url(value):
                        return value
                elif isinstance(value, (dict, list)):
                    result = self._extract_video_from_json(value)
                    if result:
                        return result
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    result = self._extract_video_from_json(item)
                    if result:
                        return result
        return None
    
    def _extract_video_from_json_ld(self, data: Dict) -> Optional[Dict[str, Any]]:
        """Extract video from JSON-LD data"""
        video_url = None
        
        # Check for VideoObject
        if data.get('@type') == 'VideoObject':
            video_url = data.get('contentUrl') or data.get('embedUrl')
        elif 'video' in data:
            video_data = data['video']
            if isinstance(video_data, dict):
                video_url = video_data.get('contentUrl') or video_data.get('embedUrl')
            elif isinstance(video_data, str):
                video_url = video_data
        
        if video_url:
            return {
                'url': video_url,
                'title': data.get('name', f"video_{int(time.time())}"),
                'ext': self._detect_extension(video_url),
                'site': 'json-ld',
                'description': data.get('description', ''),
                'thumbnail': data.get('thumbnailUrl')
            }
        
        return None
    
    async def _extract_tiktok(self, url: str) -> Optional[Dict[str, Any]]:
        """Special TikTok extraction"""
        # Use yt-dlp for TikTok
        return await self._try_ytdlp(url)
    
    async def _extract_instagram(self, url: str) -> Optional[Dict[str, Any]]:
        """Special Instagram extraction"""
        # Use yt-dlp for Instagram
        return await self._try_ytdlp(url)
    
    async def _extract_twitter(self, url: str) -> Optional[Dict[str, Any]]:
        """Special Twitter/X extraction"""
        # Use yt-dlp for Twitter
        return await self._try_ytdlp(url)
    
    async def _extract_facebook(self, url: str) -> Optional[Dict[str, Any]]:
        """Special Facebook extraction"""
        # Use yt-dlp for Facebook
        return await self._try_ytdlp(url)
    
    def _detect_extension(self, url: str) -> str:
        """Detect file extension from URL"""
        url_clean = url.split('?')[0].split('#')[0]
        ext = os.path.splitext(url_clean)[-1].lower()
        
        if ext in ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.3gp']:
            return ext[1:]  # Remove dot
        
        if '.m3u8' in url:
            return 'm3u8'
        
        if '.mpd' in url:
            return 'mpd'
        
        return 'mp4'
    
    def _is_video_url(self, url: str) -> bool:
        """Check if URL points to video"""
        if not url:
            return False
        
        video_extensions = ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.3gp', '.m3u8', '.mpd']
        url_lower = url.lower()
        return any(ext in url_lower for ext in video_extensions)
    
    async def download_video(self, url: str, title: str, ext: str, progress_callback=None) -> Optional[str]:
        """Download video with progress tracking"""
        await self.init_sessions()
        
        # Sanitize filename
        safe_title = re.sub(r'[^\w\-_. ]', '', title)[:100]
        if not safe_title:
            safe_title = f"video_{int(time.time())}"
        
        filepath = os.path.join(DOWNLOAD_PATH, f"{safe_title}_{uuid.uuid4().hex[:8]}.{ext}")
        
        try:
            async with self.download_semaphore:
                if ext == 'm3u8':
                    # Use ffmpeg for HLS streams
                    await self._download_m3u8(url, filepath, progress_callback)
                elif ext == 'mpd':
                    # Use ffmpeg for DASH streams
                    await self._download_mpd(url, filepath, progress_callback)
                else:
                    # Direct download
                    await self._download_direct(url, filepath, progress_callback)
            
            # Check if file exists and has size
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return filepath
            
            return None
            
        except Exception as e:
            logger.error(f"Download failed: {e}")
            if os.path.exists(filepath):
                os.remove(filepath)
            return None
    
    async def _download_direct(self, url: str, filepath: str, progress_callback=None):
        """Direct HTTP download with progress"""
        async with self.session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                raise Exception(f"Download failed with status {response.status}")
            
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            
            with open(filepath, 'wb') as f:
                async for chunk in response.content.iter_chunked(1024 * 1024):  # 1MB chunks
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if progress_callback and total_size > 0:
                        percent = (downloaded / total_size) * 100
                        await progress_callback(f"⬇️ التحميل: {percent:.1f}% ({downloaded / 1024 / 1024:.1f} MB / {total_size / 1024 / 1024:.1f} MB)")
    
    async def _download_m3u8(self, url: str, filepath: str, progress_callback=None):
        """Download HLS stream using ffmpeg"""
        def download_sync():
            try:
                # Use ffmpeg to download and convert HLS
                cmd = [
                    'ffmpeg',
                    '-i', url,
                    '-c', 'copy',
                    '-y',
                    '-loglevel', 'error',
                    filepath
                ]
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                process.wait()
                
                if process.returncode != 0:
                    stderr = process.stderr.read().decode()
                    raise Exception(f"ffmpeg error: {stderr}")
                    
            except Exception as e:
                logger.error(f"ffmpeg error: {e}")
                raise
        
        if progress_callback:
            await progress_callback("⬇️ تحميل البث المباشر (HLS)...")
        
        await asyncio.get_event_loop().run_in_executor(None, download_sync)
    
    async def _download_mpd(self, url: str, filepath: str, progress_callback=None):
        """Download DASH stream using ffmpeg"""
        def download_sync():
            try:
                cmd = [
                    'ffmpeg',
                    '-i', url,
                    '-c', 'copy',
                    '-y',
                    '-loglevel', 'error',
                    filepath
                ]
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                process.wait()
                
                if process.returncode != 0:
                    stderr = process.stderr.read().decode()
                    raise Exception(f"ffmpeg error: {stderr}")
                    
            except Exception as e:
                logger.error(f"ffmpeg error: {e}")
                raise
        
        if progress_callback:
            await progress_callback("⬇️ تحميل البث المتكيف (DASH)...")
        
        await asyncio.get_event_loop().run_in_executor(None, download_sync)
    
    async def cleanup(self):
        """Cleanup sessions"""
        if self.session and not self.session.closed:
            await self.session.close()

class ShadowBot:
    """Elite Telegram Downloader Bot"""
    
    def __init__(self):
        self.extractor = VideoExtractor()
        self.user_states = {}
        self.active_downloads = {}
        self.stats = {
            'total_downloads': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'start_time': time.time()
        }
        
        # States for conversation handler
        self.WAITING_FOR_URL = 1
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        if not self._is_authorized(update):
            await update.message.reply_text("⛔️ غير مصرح لك باستخدام هذا البوت")
            return
        
        user = update.effective_user
        welcome_text = f"""
🎯 **Shadow Mode V99 - Elite Downloader**

مرحباً {user.first_name}! 👋

أنا نظام تحميل متطور يدعم:
✅ YouTube, TikTok, Instagram, Twitter
✅ المواقع المحمية بـ Cloudflare
✅ الروابط المباشرة وغير المباشرة
✅ استخراج الفيديو من iframes
✅ HLS Streams (m3u8)
✅ DASH Streams (mpd)
✅ تخطي الحماية تلقائياً

📤 **أرسل رابط الفيديو وسأقوم بالباقي**

📊 **الأوامر المتاحة:**
/start - بدء البوت
/help - المساعدة
/stats - الإحصائيات
/cancel - إلغاء العملية
        """
        
        keyboard = [
            [InlineKeyboardButton("ℹ️ معلومات النظام", callback_data='info'),
             InlineKeyboardButton("⚙️ الإعدادات", callback_data='settings')],
            [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats'),
             InlineKeyboardButton("❓ المساعدة", callback_data='help')]
        ]
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        if not self._is_authorized(update):
            return
        
        help_text = """
📚 **دليل الاستخدام**

**الاستخدام الأساسي:**
1. أرسل رابط الفيديو مباشرة
2. انتظر حتى يتم التحليل والتحميل
3. استلم الفيديو

**المواقع المدعومة:**
• YouTube, Vimeo, Dailymotion
• TikTok, Instagram Reels
• Twitter/X, Facebook
• +1000 موقع آخر

**المميزات:**
• تحميل حتى 2GB
• جودة عالية
• سرعة تحميل محسنة
• تخطي الحماية

**الأوامر:**
/start - بدء البوت
/help - هذه الرسالة
/stats - إحصائيات الاستخدام
/cancel - إلغاء العملية الحالية
        """
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        if not self._is_authorized(update):
            return
        
        uptime = int(time.time() - self.stats['start_time'])
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60
        seconds = uptime % 60
        
        stats_text = f"""
📊 **إحصائيات البوت**

⏱️ **وقت التشغيل:** {hours}h {minutes}m {seconds}s

📥 **التحميلات:**
• الإجمالي: {self.stats['total_downloads']}
• الناجحة: {self.stats['successful_downloads']}
• الفاشلة: {self.stats['failed_downloads']}
• نسبة النجاح: {(self.stats['successful_downloads'] / max(1, self.stats['total_downloads']) * 100):.1f}%

👥 **المستخدمون النشطون:** {len(self.active_downloads)}
        """
        
        await update.message.reply_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel command"""
        if not self._is_authorized(update):
            return
        
        user_id = update.effective_user.id
        if user_id in self.active_downloads:
            self.active_downloads[user_id]['cancelled'] = True
            await update.message.reply_text("❌ تم إلغاء العملية الحالية")
        else:
            await update.message.reply_text("ℹ️ لا توجد عملية جارية للإلغاء")
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle URLs from messages"""
        if not self._is_authorized(update):
            return
        
        user_id = update.effective_user.id
        
        # Check if user already has active download        if user_id in self.active_downloads and not self.active_downloads[user_id].get('cancelled', False):
            await update.message.reply_text("⚠️ لديك عملية تحميل جارية بالفعل. انتظر حتى تكتمل أو استخدم /cancel")
            return
        
        # Extract URLs from message
        urls = re.findall(r'https?://[^\s]+', update.message.text)
        
        if not urls:
            await update.message.reply_text("⚠️ من فضلك أرسل رابط صحيح")
            return
        
        url = urls[0]
        
        # Initialize download state
        self.active_downloads[user_id] = {
            'cancelled': False,
            'start_time': time.time()
        }
        
        # Send processing message
        processing_msg = await update.message.reply_text(
            "🔍 **جاري تحليل الرابط...**\n"
            "⏳ هذا قد يستغرق بضع ثواني",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Show typing action
        await update.message.chat.send_action(ChatAction.TYPING)
        
        try:
            # Progress callback
            async def progress_callback(message):
                if self.active_downloads.get(user_id, {}).get('cancelled', False):
                    raise Exception("Download cancelled by user")
                
                try:
                    await processing_msg.edit_text(
                        f"{message}\n\n"
                        f"⏱️ الوقت المنقضي: {int(time.time() - self.active_downloads[user_id]['start_time'])} ثانية",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass
            
            # Extract video info
            video_info = await self.extractor.extract_video(url, progress_callback)
            
            if not video_info:
                self.stats['failed_downloads'] += 1
                await processing_msg.edit_text(
                    "❌ **فشل استخراج الفيديو**\n"
                    "الرابط قد يكون محمي أو غير مدعوم\n\n"
                    "💡 جرب:\n"
                    "• التأكد من صحة الرابط\n"
                    "• استخدام رابط مباشر للفيديو",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Update stats
            self.stats['total_downloads'] += 1
            
            # Update progress
            await processing_msg.edit_text(
                f"✅ **تم استخراج الفيديو**\n"
                f"📹 العنوان: {video_info.get('title', 'Video')}\n"
                f"🌐 المصدر: {video_info.get('site', 'unknown')}\n"
                f"📦 الصيغة: {video_info.get('ext', 'mp4')}\n\n"
                f"⬇️ **جاري التحميل...**",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Show upload action
            await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
            
            # Download video
            filepath = await self.extractor.download_video(
                video_info['url'],
                video_info['title'],
                video_info['ext'],
                progress_callback
            )
            
            if not filepath:
                self.stats['failed_downloads'] += 1
                await processing_msg.edit_text(
                    "❌ **فشل التحميل**\n"
                    "حدث خطأ أثناء تحميل الفيديو",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Check file size
            file_size = os.path.getsize(filepath)
            if file_size > MAX_FILE_SIZE:
                await processing_msg.edit_text(
                    f"❌ **الملف كبير جداً**\n"
                    f"الحجم: {file_size / 1024 / 1024:.1f} MB\n"
                    f"الحد الأقصى: {MAX_FILE_SIZE / 1024 / 1024:.1f} MB",
                    parse_mode=ParseMode.MARKDOWN
                )
                os.remove(filepath)
                return
            
            # Send video
            await processing_msg.edit_text(
                f"📤 **جاري الإرسال إلى تيليجرام...**\n"
                f"📦 حجم الملف: {file_size / 1024 / 1024:.1f} MB",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Prepare caption
            caption = f"""
✅ **تم التحميل بنجاح**

📹 **العنوان:** {video_info.get('title', 'Video')}
🌐 **المصدر:** {video_info.get('site', 'unknown')}
📦 **الصيغة:** {video_info.get('ext', 'mp4').upper()}
📏 **الحجم:** {file_size / 1024 / 1024:.1f} MB
⏱️ **الوقت:** {int(time.time() - self.active_downloads[user_id]['start_time'])} ثانية

⚡️ **Shadow Mode V99**
"""
            
            # Send video with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with open(filepath, 'rb') as video_file:
                        await update.message.reply_video(
                            video_file,
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            supports_streaming=True,
                            timeout=300,
                            write_timeout=300,
                            connect_timeout=30,
                            read_timeout=300
                        )
                    break
                except RetryAfter as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(e.retry_after)
                    else:
                        raise
                except TelegramError as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(5)
                    else:
                        raise
            
            # Update stats
            self.stats['successful_downloads'] += 1
            
            # Cleanup
            os.remove(filepath)
            await processing_msg.delete()
            
            # Remove from active downloads
            del self.active_downloads[user_id]
            
        except Exception as e:
            logger.error(f"Error processing URL: {e}")
            self.stats['failed_downloads'] += 1
            
            error_msg = str(e)
            if "cancelled" in error_msg.lower():
                await processing_msg.edit_text(
                    "❌ **تم إلغاء العملية**",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await processing_msg.edit_text(
                    f"❌ **حدث خطأ غير متوقع**\n"
                    f"`{error_msg[:100]}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            
            # Remove from active downloads
            if user_id in self.active_downloads:
                del self.active_downloads[user_id]
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'info':
            info_text = """
📊 **معلومات النظام**
━━━━━━━━━━━━━━━
🔧 **المحركات:**
• yt-dlp (1000+ موقع)
• Cloudscraper (تخطي Cloudflare)
• BeautifulSoup (تحليل الصفحات)
• FFmpeg (معالجة الفيديو)
• JSON-LD (البيانات المنظمة)

🎯 **المميزات:**
• استخراج من iframes
• دعم HLS Streams
• دعم DASH Streams
• تخطي الحماية المتقدمة
• متابعة redirects
• تحليل متعدد الأنماط
• اكتشاف البث المباشر

⚡️ **الإصدار:** V99
            """
            await query.edit_message_text(
                info_text,
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif query.data == 'settings':
            await query.edit_message_text(
                "⚙️ **الإعدادات الحالية:**\n"
                f"📏 الحد الأقصى: {MAX_FILE_SIZE / 1024 / 1024:.0f} MB\n"
                f"👥 المستخدمين المصرحين: {len(ALLOWED_USER_IDS) if ALLOWED_USER_IDS else 'الجميع'}\n"
                f"🐛 وضع التصحيح: {'✅' if ENABLE_DEBUG else '❌'}\n"
                f"⬇️ التحميلات المتزامنة: {MAX_CONCURRENT_DOWNLOADS}",
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif query.data == 'stats':
            uptime = int(time.time() - self.stats['start_time'])
            hours = uptime // 3600
            minutes = (uptime % 3600) // 60
            
            await query.edit_message_text(
                f"📊 **إحصائيات سريعة:**\n"
                f"⏱️ وقت التشغيل: {hours}h {minutes}m\n"
                f"📥 التحميلات: {self.stats['total_downloads']}\n"
                f"✅ الناجحة: {self.stats['successful_downloads']}\n"
                f"❌ الفاشلة: {self.stats['failed_downloads']}",
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif query.data == 'help':
            await query.edit_message_text(
                "❓ **للمساعدة:**\n"
                "أرسل رابط الفيديو مباشرة\n"
                "أو استخدم /help للأوامر الكاملة",
                parse_mode=ParseMode.MARKDOWN
            )
    
    def _is_authorized(self, update: Update) -> bool:
        """Check if user is authorized"""
        if not ALLOWED_USER_IDS:  # If empty, allow everyone
            return True
        
        user_id = update.effective_user.id
        return user_id in ALLOWED_USER_IDS
    
    async def run(self):
        """Run the bot"""
        if not TOKEN:
            logger.error("TELEGRAM_BOT_TOKEN not set in environment variables")
            return
        
        try:
            # Build application
            app = ApplicationBuilder().token(TOKEN).build()
            
            # Add handlers
            app.add_handler(CommandHandler("start", self.start))
            app.add_handler(CommandHandler("help", self.help))
            app.add_handler(CommandHandler("stats", self.stats))
            app.add_handler(CommandHandler("cancel", self.cancel))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
            app.add_handler(CallbackQueryHandler(self.handle_callback))
            
            # Add error handler
            app.add_error_handler(self._error_handler)
            
            logger.info("Shadow Mode V99 bot started successfully")
            logger.info(f"Download path: {DOWNLOAD_PATH}")
            logger.info(f"Max file size: {MAX_FILE_SIZE / 1024 / 1024:.0f} MB")
            logger.info(f"Authorized users: {len(ALLOWED_USER_IDS) if ALLOWED_USER_IDS else 'All'}")
            
            # Start polling
            await app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                close_loop=False
            )
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            raise
    
    async def _error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ حدث خطأ في معالجة طلبك. حاول مرة أخرى."
                )
            except:
                pass
    
    async def cleanup(self):
        """Cleanup resources"""
        await self.extractor.cleanup()

# Global bot instance
bot = None

def main():
    """Main function to run the bot"""
    global bot
    
    # Create bot instance
    bot = ShadowBot()
    
    # Run the bot
    try:
        # Use asyncio to run the bot
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Create task for bot
        task = loop.create_task(bot.run())
        
        # Run until disconnected
        loop.run_until_complete(task)
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        # Cleanup
        if bot:
            loop.run_until_complete(bot.cleanup())
        loop.close()

# For Telegram bot to run until disconnected
def run_bot():
    """Run bot until disconnected"""
    global bot
    
    if bot is None:
        bot = ShadowBot()
    
    # Run the bot
    bot.run_until_disconnected()

# Add run_until_disconnected method to ShadowBot
def _run_until_disconnected(self):
    """Run bot until disconnected"""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment variables")
        return
    
    try:
        # Build application
        app = ApplicationBuilder().token(TOKEN).build()
        
        # Add handlers
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.help))
        app.add_handler(CommandHandler("stats", self.stats))
        app.add_handler(CommandHandler("cancel", self.cancel))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Add error handler
        app.add_error_handler(self._error_handler)
        
        logger.info("Shadow Mode V99 bot started successfully")
        
        # Run until disconnected
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

# Add method to ShadowBot class
ShadowBot.run_until_disconnected = _run_until_disconnected

# Run the bot
if __name__ == '__main__':
    main()