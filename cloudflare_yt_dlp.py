"""Hardened yt-dlp configuration for Cloudflare-protected generic URLs.

This module centralizes yt-dlp options so the bot can use browser impersonation,
modern headers, cookies, proxy support, and a conservative retry strategy.
It does not bypass authentication or solve interactive CAPTCHAs.
"""
from __future__ import annotations

import os
import random
from typing import Any, Dict, Optional

import yt_dlp


USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)


def cloudflare_ydl_opts(
    *,
    outtmpl: Optional[str] = None,
    format_selector: str = "bestvideo*+bestaudio/best",
    cookiefile: Optional[str] = None,
    proxy: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    progress_hooks: Optional[list] = None,
    download: bool = True,
) -> Dict[str, Any]:
    """Return resilient yt-dlp options for generic/Cloudflare endpoints.

    ``impersonate`` is intentionally configured under the generic extractor,
    matching yt-dlp's command-line recommendation for this error.
    """
    user_agent = random.choice(USER_AGENTS)
    request_headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if headers:
        request_headers.update(headers)

    options: Dict[str, Any] = {
        "format": format_selector,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "skip_download": not download,
        "check_formats": False,
        "geo_bypass": True,
        "retries": 10,
        "fragment_retries": 10,
        "retry_sleep_functions": {"http": lambda n: min(30, 2 ** min(n, 4))},
        "sleep_interval": 1,
        "max_sleep_interval": 5,
        "http_headers": request_headers,
        "user_agent": user_agent,
        "extractor_args": {
            "generic": {"impersonate": ["chrome"]},
        },
    }

    if outtmpl:
        options["outtmpl"] = outtmpl
    if cookiefile and os.path.isfile(cookiefile):
        options["cookiefile"] = cookiefile
    if proxy:
        options["proxy"] = proxy
    if progress_hooks:
        options["progress_hooks"] = progress_hooks
    return options


def extract_or_download(url: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """Run yt-dlp and retry once without generic impersonation when unsupported."""
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=not options.get("skip_download", False))
    except Exception as first_error:
        message = str(first_error).lower()
        if "impersonat" not in message and "curl_cffi" not in message:
            raise

        fallback = dict(options)
        fallback["extractor_args"] = {
            k: v for k, v in options.get("extractor_args", {}).items() if k != "generic"
        }
        with yt_dlp.YoutubeDL(fallback) as ydl:
            return ydl.extract_info(url, download=not fallback.get("skip_download", False))
