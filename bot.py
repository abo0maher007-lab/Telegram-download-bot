import math
import os
from threading import Thread
from flask import Flask
import requests
import telebot

# --- 1. سيرفر وهمي لإبقاء Render مستيقظاً ---
app = Flask("")


@app.route("/")
def home():
    return "Bot is Alive & Running!"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


Thread(target=run_flask, daemon=True).start()

# --- 2. إعدادات البوت والـ Owner ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "5414125521"))

if not BOT_TOKEN:
    raise ValueError("⚠️ لم يتم العثور على BOT_TOKEN في متغيرات البيئة!")

bot = telebot.TeleBot(BOT_TOKEN)

# الحد الأقصى المسموح به من تلغرام (50 ميجابايت)
MAX_FILE_SIZE = 50 * 1024 * 1024


def log_activity(user, activity):
    if OWNER_ID != 0:
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        username = f"@{user.username}" if user.username else "بدون يوزر"
        log_msg = (
            f"🔔 **نشاط جديد:**\n"
            f"👤 **المستخدم:** {full_name}\n"
            f"🏷️ **اليوزر:** {username}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📝 **العملية:** {activity}"
        )
        try:
            bot.send_message(OWNER_ID, log_msg, parse_mode="Markdown")
        except Exception:
            pass


def format_size(size_in_bytes):
    if size_in_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_in_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_in_bytes / p, 2)
    return f"{s} {size_name[i]}"


# --- 3. معالجات البوت ---
@bot.message_handler(commands=["start"])
def send_welcome(message):
    log_activity(message.from_user, "قام ببدء البوت (/start)")
    bot.reply_to(
        message,
        "👋 أهلاً بك! أرسل لي أي رابط مباشر وسأقوم بتحميله ورفعه لك فوراً.\n\n⚠️ **ملاحظة:** الحد الأقصى لحجم الملف هو 50 ميجابايت (قيود تلغرام للبوتات).",
    )


@bot.message_handler(func=lambda message: True)
def process_link(message):
    url = message.text.strip()

    if not (url.startswith("http://") or url.startswith("https://")):
        return

    log_activity(message.from_user, f"طلب تحميل رابط: {url}")

    status_msg = bot.reply_to(message, "⏳ جاري فحص الرابط والتجهيز...")

    try:
        file_name = url.split("/")[-1].split("?")[0]
        if not file_name or "." not in file_name:
            file_name = "downloaded_file"

        # فحص رأس الملف لمعرفة الحجم قبل التنزيل
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))

        # التحقق من الحد الأقصى للحجم
        if total_size > MAX_FILE_SIZE:
            bot.edit_message_text(
                f"⚠️ **عذراً، حجم الملف كبير جداً!**\n\n"
                f"📄 **الملف:** `{file_name}`\n"
                f"📊 **الحجم:** {format_size(total_size)}\n"
                f"❌ **الحد الأقصى المسموح به للبوتات:** 50 MB",
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                parse_mode="Markdown",
            )
            return

        bot.edit_message_text(
            f"📥 جاري تنزيل الملف...\n📄 **الملف:** `{file_name}`\n📊 **الحجم:** {format_size(total_size)}",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )

        local_path = os.path.join(".", file_name)
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        bot.edit_message_text(
            f"📤 جاري الرفع إلى تلغرام...\n📄 **الملف:** `{file_name}`",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )

        with open(local_path, "rb") as doc:
            bot.send_document(
                chat_id=message.chat.id,
                document=doc,
                caption=f"✅ تم الرفع بنجاح: `{file_name}`",
                parse_mode="Markdown",
            )

        if os.path.exists(local_path):
            os.remove(local_path)

        bot.delete_message(
            chat_id=message.chat.id, message_id=status_msg.message_id
        )

    except Exception as e:
        bot.edit_message_text(
            f"❌ حدث خطأ أثناء المعالجة:\n`{str(e)}`",
            chat_id=message.chat.id,
            message_id=status_msg.message_id,
            parse_mode="Markdown",
        )
        if "local_path" in locals() and os.path.exists(local_path):
            os.remove(local_path)


if __name__ == "__main__":
    print("🤖 البوت وسيرفر الويب يعملان بنجاح...")
    bot.infinity_polling()
