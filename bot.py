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
    """تستثنى هذه الفئة عندما تكون الخدمة متوقفة أو جدار الحماية حظر الكشط تماماً"""
    pass

class ScraperEngineV11:
    def __init__(self, proxy_pool: list[str], user_agents: list[str]):
        self.proxy_pool = proxy_pool
        self.user_agents = user_agents
        self.consecutive_failures = 0
        self.max_circuit_failures = 5
        self.circuit_open_until = 0

    def _get_random_headers(() -> Dict[str, str]:
        """توليد رؤوس طلبات حديثة تشبه المتصفح الحقيقي v11"""
        return {
            "User-Agent": random.choice(self.user_agents) if self.user_agents else "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Not A(Brand";v="8", "Chromium";v="132"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1"
        }

    def _get_random_proxy(self) -> Optional[Dict[str, str]]:
        """اختيار بروكسي عشوائي من المسبح (Proxy Pool)"""
        if not self.proxy_pool:
            return None
        proxy = random.choice(self.proxy_pool)
        return {"http": proxy, "https": proxy}

    @staticmethod
    def _is_retryable_exception(exception: Exception) -> bool:
        """تحديد هل الخطأ يستحق إعادة المحاولة أم لا"""
        if isinstance(exception, requests.exceptions.RequestException):
            response = getattr(exception, 'response', None)
            if response is not None:
                # أخطاء الحظر أو السيرفر المقبولة للمحاولة
                if response.status_code in [408, 429, 500, 502, 503, 504]:
                    return True
                # عدم إعادة المحاولة في حال الأخطاء الدائمة
                if response.status_code in [400, 401, 403, 404]:
                    return False
            # أخطاء الاتصال والانقطاع (Network Drop / Timeout)
            return True
        return False

    def fetch(self, url: str) -> Optional[requests.Response]:
        """الدالة الرئيسية لجلب الصفحات مع آلية v11 للسلامة والمحاولات"""
        
        # 1. التحقق من حالة Circuit Breaker
        if time.time() < self.circuit_open_until:
            raise CircuitBreakerOpenException(f"Circuit open for target domain. Cooldown active.")

        # 2. إعداد شروط Tenacity للمحاولات
        @retry(
            stop=stop_after_attempt(5),  # الحد الأقصى للمحاولات: 5
            wait=wait_exponential_jitter(initial=2, max=30, jitter=1.5), # انتظار أسي مع القفزات العشوائية
            retry=retry_if_exception(_is_retryable_exception),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            after=after_log(logger, logging.INFO),
            reraise=True
        )
        def _execute_request():
            headers = self._get_random_headers()
            proxies = self._get_random_proxy()

            logger.info(f"Attempting fetch: {url} | Proxy: {proxies.get('http') if proxies else 'Direct'}")
            
            response = requests.get(url, headers=headers, proxies=proxies, timeout=10)
            
            # التحقق من هيدر Retry-After في حالات 429
            if response.status_code == 429 and "Retry-After" in response.headers:
                retry_after = int(response.headers.get("Retry-After", 5))
                logger.warning(f"Rate limited (429). Respecting Retry-After header: {retry_after}s")
                time.sleep(retry_after)
            
            response.raise_for_status()
            return response

        try:
            res = _execute_request()
            self.consecutive_failures = 0  # تصفير الفشل عند النجاح
            return res
        except Exception as e:
            self.consecutive_failures += 1
            logger.error(f"Failed to fetch {url}. Total consecutive failures: {self.consecutive_failures}")
            
            # فتح قاطع الدائرة عند تجاوز حد الفشل المتتالي
            if self.consecutive_failures >= self.max_circuit_failures:
                self.circuit_open_until = time.time() + 60  # فترة تبريد لمدة 60 ثانية
                logger.critical("Circuit Breaker Tripped! Pausing scraper for 60 seconds.")
            
            raise e


# --- مثال للاستخدام والتجربة ---
if __name__ == "__main__":
    proxies = [
        "http://proxy1.example.com:8080",
        "http://proxy2.example.com:8080"
    ]
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
    ]

    scraper = ScraperEngineV11(proxy_pool=proxies, user_agents=user_agents)

    try:
        response = scraper.fetch("https://httpbin.org/status/503,200") # رابط للتجربة ينشئ أخطاء عشوائية
        print(f"Success! Status Code: {response.status_code}")
    except Exception as err:
        print(f"Scraping Failed completely after retries: {err}")