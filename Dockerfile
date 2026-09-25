FROM python:3.11-slim

# تثبيت FFmpeg والتحديثات الأساسية للتحميل والأدوات
RUN apt-get update && \
    apt-get install -y ffmpeg wget curl chmod && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# تحميل أداة wireproxy المخصصة لـ Linux 64-bit المتوافقة مع Railway
RUN wget https://github.com -O /usr/local/bin/wireproxy && \
    chmod +x /usr/local/bin/wireproxy

WORKDIR /app

# نسخ الملفات وتثبيت المكتبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# تشغيل البوت مباشرة
CMD ["python", "bot.py"]
