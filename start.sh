#!/bin/bash

# 1. الاتصال بـ Cloudflare وتوليد حساب ومفاتيح 1.1.1.1 مجاناً من داخل السيرفر
python3 -c "
import requests, os
try:
    r = requests.post('https://cloudflareclient.com', headers={'User-Agent': 'okhttp/3.12.1'}, timeout=10).json()
    config = f'[Interface]\nPrivateKey = {r[\"config\"][\"interface\"][\"account\"][\"private_key\"]}\nAddress = 172.16.0.2/32, fd00::5/128\n\n[Peer]\nPublicKey = {r[\"config\"][\"peers\"][\"public_key\"]}\nEndpoint = ://cloudflareclient.com\n\n[Socks5]\nBindAddress = 127.0.0.1:40001\n'
    with open('wireproxy.conf', 'w') as f: f.write(config)
    print('[+] WARP config generated successfully.')
except Exception as e:
    print('[-] Failed to generate WARP config:', e)
"

# 2. تشغيل أداة wireproxy في الخلفية لبدء نفق الاتصال الآمن
wireproxy -c wireproxy.conf &

# 3. الانتظار لثانيتين حتى يستقر اتصال البروكسي المشفر
sleep 2

# 4. تشغيل ملف البوت الرئيسي الخاص بك ببيئة العمل الجديدة
python bot.py
