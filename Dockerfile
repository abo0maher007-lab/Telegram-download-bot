FROM python:3.11-slim

# تثبيت المتطلبات النظامية
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# إنشاء مجلد العمل
WORKDIR /app

# نسخ المتطلبات
COPY requirements.txt .

# تثبيت المتطلبات
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# نسخ الكود
COPY . .

# إنشاء مجلد التحميلات
RUN mkdir -p downloads

# المنفذ
EXPOSE 8080

# تشغيل البوت
CMD ["python", "bot.py"]
