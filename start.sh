#!/bin/bash

# إنشاء ملف الإعدادات للبروكسي باستخدام المتغيرات الثابتة والمضمونة
cat <<EOF > wireproxy.conf
[Interface]
PrivateKey = ${WARP_PRIVATE_KEY}
Address = 172.16.0.2/32, fd00::5/128

[Peer]
PublicKey = ${WARP_PUBLIC_KEY}
Endpoint = ://cloudflareclient.com

[Socks5]
BindAddress = 127.0.0.1:40001
EOF

echo "[+] wireproxy.conf has been generated from Environment Variables."

# تشغيل أداة wireproxy في الخلفية بنجاح
wireproxy -c wireproxy.conf &

# الانتظار 3 ثوانٍ لضمان استقرار وفتح منفذ البروكسي
sleep 3

# تشغيل البوت الرئيسي الخاص بك
python bot.py
