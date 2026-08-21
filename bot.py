import time
import random
import logging
import requests
from typing import Optional, Dict, Any
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception,
    before_sleep_log,
    after_log
)

# إعداد السجلات لمتابعة سلوك المحاولات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Scraper_v11")

class CircuitBreakerOpenException(Exception):
    """استثناء يطرح عند تفعيل قاطع الدائرة لحماية النظام"""
    pass

class ScraperEngineV11:
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
        """توليد رؤوس طلبات حديثة تشبه المتصفح الحقيقي v11"""
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="132"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1"
        }

    def _get_random_proxy(self) -> Optional[Dict[str, str]]:
        """اختيار بروكسي عشوائي من المسبح"""
        if not self.proxy_pool:
            return None
        proxy = random.choice(self.proxy_pool)
        return {"http": proxy, "https": proxy}

    @staticmethod
    def _is_retryable_exception(exception: Exception) -> bool:
        """تحديد أخطاء الاتصال والسيرفر المقبولة لإعادة المحاولة"""
        if isinstance(exception, requests.exceptions.RequestException):
            response = getattr(exception, 'response', None)
            if response is not None:
                # أخطاء الضغط، الحظر المؤقت، والسيرفر المقبولة للمحاولة
                if response.status_code in [408, 429, 500, 502, 503, 504]:
                    return True
                # أخطاء دائمة تمنع المحاولة مجدداً
                if response.status_code in [400, 401, 403, 404]:
                    return False
            # أخطاء انقطاع الشبكة والـ Timeout
            return True
        return False

    def fetch(self, url: str) -> Optional[requests.Response]:
        """الدالة الرئيسية لجلب البيانات بأسلوب المحاولات v11"""
        
        # 1. التحقق من حالة Circuit Breaker
        if time.time() < self.circuit_open_until:
            raise CircuitBreakerOpenException("Circuit open for target domain. Cooldown active.")

        # 2. إعداد آلية المحاولات Tenacity
        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential_jitter(initial=2, max=30, jitter=1.5),
            retry=retry_if_exception(self._is_retryable_exception),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            after=after_log(logger, logging.INFO),
            reraise=True
        )
        def _execute_request():
            headers = self._get_random_headers()
            proxies = self._get_random_proxy()

            logger.info(f"Attempting fetch: {url} | Proxy: {proxies.get('http') if proxies else 'Direct'}")
            
            response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
            
            # التعامل مع هيدر Retry-After عند وجود 429
            if response.status_code == 429 and "Retry-After" in response.headers:
                try:
                    retry_after = int(response.headers.get("Retry-After", 5))
                except ValueError:
                    retry_after = 5
                logger.warning(f"Rate limited (429). Respecting Retry-After header: {retry_after}s")
                time.sleep(retry_after)
            
            response.raise_for_status()
            return response

        try:
            res = _execute_request()
            self.consecutive_failures = 0  # تصفير الأخطاء عند النجاح
            return res
        except Exception as e:
            self.consecutive_failures += 1
            logger.error(f"Failed to fetch {url}. Total consecutive failures: {self.consecutive_failures}")
            
            # تفعيل قاطع الدائرة عند استمرار الفشل
            if self.consecutive_failures >= self.max_circuit_failures:
                self.circuit_open_until = time.time() + 60
                logger.critical("Circuit Breaker Tripped! Pausing scraper for 60 seconds.")
            
            raise e


# --- التشغيل والتجربة ---
if __name__ == "__main__":
    scraper = ScraperEngineV11()
    
    test_url = "https://httpbin.org/get"
    
    try:
        logger.info(f"Starting test request to {test_url}")
        res = scraper.fetch(test_url)
        print("\n--- Response Received ---")
        print(f"Status Code: {res.status_code}")
        print(res.text[:200])
    except Exception as err:
        logger.error(f"Final Failure: {err}")