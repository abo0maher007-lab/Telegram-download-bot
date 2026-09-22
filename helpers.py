from curl_cffi import requests
from typing import Dict, Any, Optional

# قائمة بعناوين User-Agent الحديثة لمتصفحات حقيقية
DEFAULT_USER_AGENTS = {
    "chrome": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "firefox": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "safari": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15"
}

def get_impersonated_headers(browser: str = "chrome") -> Dict[str, str]:
    """
    إنشاء رؤوس طلبات (Headers) مموهة تبدو وكأنها صادرة من متصفح حقيقي.
    """
    user_agent = DEFAULT_USER_AGENTS.get(browser.lower(), DEFAULT_USER_AGENTS["chrome"])
    
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

def get_ytdlp_impersonate_opts(browser: str = "chrome") -> Dict[str, Any]:
    """
    خيارات جاهزة للـ yt-dlp تستخدم التمويه وتعديل الرؤوس لتجاوز حظر البروكسي/الشبكة.
    """
    headers = get_impersonated_headers(browser)
    
    return {
        # تمويه الطلبات عبر خيار impersonate المدمج في yt-dlp (يستخدم curl_cffi تلقائياً إذا كانت مثبتة)
        'impersonate': browser,
        
        # تعديل الـ User-Agent و HTTP Headers المخصصة
        'http_headers': headers,
        
        # خيارات إضافية لتفادي الحظر والقيود
        'nocheckcertificate': True,
        'prefer_insecure': False,
        'geo_bypass': True,
        'quiet': True,
        'no_warnings': True,
    }

def fetch_with_curl_cffi(url: str, impersonate: str = "chrome120") -> Optional[str]:
    """
    دالة برمجية لإرسال طلب HTTP مموه بالكامل باستخدام curl-cffi مباشرة 
    (تُستخدم للروابط المحمية جداً أو للحصول على بيانات الصفحات التي تحظر yt-dlp).
    """
    try:
        response = requests.get(
            url,
            impersonate=impersonate,
            headers=get_impersonated_headers("chrome"),
            timeout=15
        )
        if response.status_code == 200:
            return response.text
        else:
            print(f"[!] Request failed with status code: {response.status_code}")
            return None
    except Exception as e:
        print(f"[!] Error using curl_cffi: {e}")
        return None
