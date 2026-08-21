import os
import time
import random
import logging
import requests
from typing import Optional, Dict, Any, Callable
from bs4 import BeautifulSoup
import yt_dlp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception,
    before_sleep_log,
    after_log
)

# إعدادات التسجيل لمتابعة الأداء والمحاولات
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s"
)
logger = logging.getLogger("Engine_v12")


class CircuitBreakerOpenException(Exception):
    """تستثنى عند فتح قاطع الدائرة لتجنب استنزاف الموارد عند الحظر"""
    pass


class UniversalEngineV12:
    def __init__(self, proxy_pool: Optional[list[str]] = None, user_agents: Optional[list[str]] = None):
        self.proxy_pool = proxy_pool or []
        self.user_agents = user_agents or [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ]
        self.consecutive_failures = 0
        self.max_circuit_failures = 5
        self.circuit_open_until = 0

    def _get_random_headers(self) -> Dict[str, str]:
        """إنشاء هيدر مطابق للمتصفحات الحديثة"""
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="132"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1"
        }

    def _get_random_proxy(self) -> Optional[str]:
        """اختيار بروكسي عشوائي"""
        if not self.proxy_pool:
            return None
        return random.choice(self.proxy_pool)

    @staticmethod
    def _is_retryable_exception(exception: Exception) -> bool:
        """تصفية الاستثناءات لإعادة المحاولة عند الأخطاء المؤقتة فقط"""
        if isinstance(exception, requests.exceptions.RequestException):
            response = getattr(exception, 'response', None)
            if response is not None:
                if response.status_code in [408, 429, 500, 502, 503, 504]:
                    return True
                if response.status_code in [400, 401, 403, 404]:
                    return False
            return True
        return False

    # =========================================================================
    # 1️⃣ قسم كشط HTML واستخراج البيانات (HTML Scraper)
    # =========================================================================
    def fetch_html(self, url: str) -> Optional[BeautifulSoup]:
        """جلب وتحليل صفحات الـ HTML بأسلوب المحاولات المتقدم v11/v12"""
        if time.time() < self.circuit_open_until:
            raise CircuitBreakerOpenException("Circuit Breaker Active: Target Domain Cooldown.")

        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential_jitter(initial=2, max=30, jitter=1.5),
            retry=retry_if_exception(self._is_retryable_exception),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
        def _execute_fetch():
            headers = self._get_random_headers()
            proxy = self._get_random_proxy()
            proxies = {"http": proxy, "https": proxy} if proxy else None

            logger.info(f"Fetching HTML from: {url}")
            response = requests.get(url, headers=headers, proxies=proxies, timeout=12)

            if response.status_code == 429 and "Retry-After" in response.headers:
                try:
                    wait_time = int(response.headers.get("Retry-After", 5))
                except ValueError:
                    wait_time = 5
                time.sleep(wait_time)

            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")

        try:
            soup = _execute_fetch()
            self.consecutive_failures = 0
            return soup
        except Exception as e:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_circuit_failures:
                self.circuit_open_until = time.time() + 60
                logger.critical("Circuit Breaker Tripped! Pausing Scraper for 60 seconds.")
            raise e

    # =========================================================================
    # 2️⃣ قسم تحميل الملفات المباشرة (Direct Download: MP4, Zip, PDF...)
    # =========================================================================
    def download_direct_file(
        self, 
        url: str, 
        output_path: str, 
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> str:
        """تحميل الملفات المباشرة بأسلوب تجزيئي (Chunked) لمنع استهلاك الذكاء العشوائية (RAM)"""
        headers = self._get_random_headers()
        proxy = self._get_random_proxy()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        logger.info(f"Starting direct file download: {url}")
        
        with requests.get(url, headers=headers, proxies=proxies, stream=True, timeout=30) as response:
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0

            with open(output_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded_size, total_size)

        logger.info(f"Direct download complete: {output_path}")
        return output_path

    # =========================================================================
    # 3️⃣ قسم الكشط والتحميل المتقدم للروابط غير المباشرة (Indirect / Streaming Media)
    # =========================================================================
    def download_indirect_media(
        self, 
        url: str, 
        output_dir: str = "./downloads", 
        max_quality: str = "1080"
    ) -> Dict[str, Any]:
        """
        استخراج وتحميل الفيديو/الصوت من مواقع السلسلة والمنصات غير المباشرة 
        (YouTube, TikTok, Facebook, Instagram, Twitter, Rumble, الخ...)
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        proxy = self._get_random_proxy()

        # إعدادات yt-dlp الذكية والمتوافقة مع الحظر
        ydl_opts = {
            'format': f'bestvideo[height<={max_quality}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': random.choice(self.user_agents),
            'retries': 5,
            'fragment_retries': 5,
            'geo_bypass': True,
        }

        if proxy:
            ydl_opts['proxy'] = proxy

        logger.info(f"Processing indirect media link with yt-dlp: {url}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 1. استخراج معلومات الفيديو أولاً بدون تحميل للتأكد
            info_dict = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info_dict)

            # معالجة تغيير الامتداد بعد الدمج تلقائياً إلى mp4
            base, _ = os.path.splitext(filename)
            final_file_path = f"{base}.mp4" if not filename.endswith('.mp4') else filename

            return {
                "status": "success",
                "title": info_dict.get('title', 'Unknown Title'),
                "duration": info_dict.get('duration', 0),
                "uploader": info_dict.get('uploader', 'Unknown'),
                "file_path": final_file_path if os.path.exists(final_file_path) else filename,
                "file_size_bytes": os.path.getsize(final_file_path) if os.path.exists(final_file_path) else 0
            }


# =========================================================================
# 🧪 أمثلة وتجارب التشغيل المباشرة (Execution Test)
# =========================================================================
if __name__ == "__main__":
    engine = UniversalEngineV12()

    print("\n--- 1. تجربة كشط صفحة (Scraping HTML) ---")
    try:
        soup = engine.fetch_html("https://httpbin.org/html")
        heading = soup.find("h1")
        print(f"H1 Title Extracted: {heading.text if heading else 'No H1'}")
    except Exception as e:
        print(f"HTML Scrape Error: {e}")

    print("\n--- 2. تجربة تحميل ملف مباشر (Direct File Download) ---")
    try:
        def my_progress(current, total):
            percent = (current / total) * 100
            print(f"\rDownloading: {percent:.1f}% ({current}/{total} bytes)", end="")

        direct_url = "https://raw.githubusercontent.com/python/cpython/main/README.rst"
        saved_file = engine.download_direct_file(direct_url, "README_test.rst", progress_callback=my_progress)
        print(f"\nFile Saved to: {saved_file}")
    except Exception as e:
        print(f"Direct Download Error: {e}")

    print("\n--- 3. تجربة تحميل فيديو غير مباشر (Indirect Video Download) ---")
    try:
        # يمكنك وضع أي رابط غير مباشر (مثل فيديو يوتيوب أو تيك توك قصير)
        sample_indirect_url = "https://www.youtube.com/watch?v=qyrcmzCkd0w"
        result = engine.download_indirect_media(sample_indirect_url, output_dir="./downloads")
        print("Media Download Result:")
        print(f"Title: {result['title']}")
        print(f"Saved Path: {result['file_path']}")
        print(f"Size: {result['file_size_bytes'] / (1024*1024):.2f} MB")
    except Exception as e:
        print(f"Indirect Download Error: {e}")