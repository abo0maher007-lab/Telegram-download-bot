#!/bin/bash

# إنشاء ملف إعدادات البروكسي من المتغيرات الثابتة والمضمونة
cat <<EOF > wireproxy.conf
[Interface]
PrivateKey = ${WARP_PRIVATE_KEY}
Address = 172.16.0.2/32, 2606:4700:110:8413:1f72:a87e:b2ee:a1f6/128

[Peer]
PublicKey = ${WARP_PUBLIC_KEY}
Endpoint = engage.cloudflareclient.com:2408

[Socks5]
BindAddress = 127.0.0.1:40001
EOF

echo "[+] wireproxy.conf has been generated from Environment Variables."

# تشغيل أداة wireproxy في الخلفية
wireproxy -c wireproxy.conf &

# الانتظار لثلاث ثوانٍ لضمان استقرار وفتح منفذ البروكسي
sleep 3

# تشغيل البوت الرئيسي الخاص بك
python bot.py
