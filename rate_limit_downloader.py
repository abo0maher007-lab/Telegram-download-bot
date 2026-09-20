import time
import random
import yt_dlp

def download_with_retry(url, custom_opts=None, max_retries=5, base_delay=10):
    """
    دالة مخصصة لتنزيل الروابط التي تواجه خطأ HTTP 429 (Too Many Requests).
     تقوم بإعادة المحاولة تلقائياً مع زيادة فترة الانتظار بين كل محاولة والأخرى.
    """
    
    # إعدادات متقدمة لتجاوز الحظر والتأخير
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        # إضافة تأخير عشوائي بين الطلبات لمنع اكتشاف الأتمتة
        'sleep_interval': 5,
        'max_sleep_interval': 15,
        # رأس الطلب المخصص لتفادي الكشف
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9,tr;q=0.8',
        }
    }

    # دمج أي إعدادات إضافية يمررها المستخدم
    if custom_opts:
        ydl_opts.update(custom_opts)

    for attempt in range(1, max_retries + 1):
        try:
            print(f"🔄 المحاولة [{attempt}/{max_retries}] لتنزيل الرابط...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            print("✅ تم التنزيل بنجاح!")
            return True

        except Exception as e:
            error_message = str(e)
            
            # التحقق مما إذا كان الخطأ هو 429 (تجاوز حد الطلبات)
            if "429" in error_message or "Too Many Requests" in error_message:
                # حساب وقت الانتظار المتزايد + عشوائية صغيرة
                sleep_time = (base_delay * (2 ** (attempt - 1))) + random.uniform(1, 5)
                print(f"⚠️ خطأ 429: تم تجاوز حد الطلبات المقبولة.")
                print(f"⏳ جاري الانتظار لمدة {int(sleep_time)} ثانية قبل إعادة المحاولة...")
                time.sleep(sleep_time)
            else:
                # إذا كان الخطأ مختلفاً، يتم طباعته والتوقف أو الاستمرار حسب الرغبة
                print(f"❌ حدث خطأ مختلف: {error_message}")
                break

    print("❌ فشلت جميع المحاولات بسبب قيود الخادم (Rate Limit).")
    return False
