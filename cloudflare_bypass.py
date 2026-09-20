import asyncio
from curl_cffi import requests
from playwright.async_api import async_playwright

def fetch_with_curl_cffi(url: str, cookies: dict = None) -> str:
    """
    محاكاة متصفح Chrome حديث لتخطي حظر Cloudflare 403
    """
    session = requests.Session(impersonate="chrome120")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = session.get(url, headers=headers, cookies=cookies, timeout=15)
    return response.text

async def get_cookies_and_html_via_playwright(url: str):
    """
    تخطي تحديات Cloudflare المتقدمة (Turnstile / JS Challenge) باستعمال متصفح خفي
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")
        
        # الانتظار في حال وجود تحدي Turnstile / Cloudflare
        await asyncio.sleep(5) 
        
        cookies = await context.cookies()
        content = await page.content()
        await browser.close()
        
        cookie_dict = {c['name']: c['value'] for c in cookies}
        return cookie_dict, content
