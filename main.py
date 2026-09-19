import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from google import genai
from google.genai import types


# =========================
# إعداد التسجيل
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================
# مفاتيح التشغيل
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# =========================
# إعدادات الاشتراك
# =========================

PAYMENT_WALLET = os.getenv("PAYMENT_WALLET")
SUBSCRIPTION_PRICE_USDT = os.getenv("SUBSCRIPTION_PRICE_USDT", "3")
SUBSCRIPTION_DAYS = os.getenv("SUBSCRIPTION_DAYS", "30")


# =========================
# Gemini
# =========================

gemini_client = None

if GEMINI_API_KEY:
    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY
    )


# =========================
# خادم HTTP لـ Render
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(
            b"SanadAI is running"
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.environ.get("PORT", 10000)
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    logger.info(
        f"Health server running on port {port}"
    )

    server.serve_forever()


# =========================
# أمر البداية
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    name = (
        user.first_name
        if user
        else "صديقي"
    )

    text = f"""
مرحبًا {name} 👋

أنا SanadAI 🤖

مساعدك الذكي للدراسة والعمل والحياة اليومية.

يمكنك أن تسألني عن:

🎓 الدراسة والجامعة
🔬 العلوم والفيزياء والكيمياء
⚙️ الهندسة والتقنيات
💻 البرمجة وعلوم الحاسوب
📚 الشرح والتلخيص
📝 الكتابة والترجمة
🧠 المعلومات العامة
🖼️ تحليل الصور

أرسل سؤالك أو صورة الآن.
"""

    await update.message.reply_text(
        text
    )


# =========================
# فحص جاهزية Gemini
# =========================

async def check_gemini(
    update: Update
):

    if not gemini_client:

        await update.message.reply_text(
            "⚠️ خدمة الذكاء الاصطناعي غير مفعّلة حاليًا."
        )

        return False

    return True


# =========================
# معالجة الرسائل النصية
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.message
        or not update.message.text
    ):
        return

    user_text = update.message.text.strip()

    if not await check_gemini(update):
        return

    try:

        response = (
            gemini_client.models.generate_content(
                model="gemini-3.6-flash",
                contents=user_text,
            )
        )

        answer = response.text

        if not answer:

            answer = (
                "لم أتمكن من الحصول على إجابة."
            )

        await update.message.reply_text(
            answer
        )

    except Exception:

        logger.exception(
            "Gemini text error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء معالجة طلبك. "
            "حاول مرة أخرى."
        )


# =========================
# معالجة الصور
# =========================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.message
        or not update.message.photo
    ):
        return

    if not await check_gemini(update):
        return

    try:

        # الحصول على أعلى دقة للصورة
        photo = update.message.photo[-1]

        # تحميل الصورة من Telegram
        telegram_file = (
            await context.bot.get_file(
                photo.file_id
            )
        )

        image_bytes = (
            await telegram_file.download_as_bytearray()
        )

        # النص المرفق بالصورة
        user_text = (
            update.message.caption
            or
            "حلل هذه الصورة واشرح لي ما تحتويه بالتفصيل."
        )

        # إرسال الصورة + السؤال إلى Gemini
        response = (
            gemini_client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    types.Part.from_bytes(
                        data=bytes(image_bytes),
                        mime_type="image/jpeg",
                    ),
                    user_text,
                ],
            )
        )

        answer = response.text

        if not answer:

            answer = (
                "لم أتمكن من تحليل الصورة."
            )

        await update.message.reply_text(
            answer
        )

    except Exception:

        logger.exception(
            "Gemini image error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء تحليل الصورة. "
            "حاول مرة أخرى."
        )


# =========================
# تشغيل البوت
# =========================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN غير موجود"
        )

    # تشغيل خادم Render
    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

    # إنشاء تطبيق Telegram
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # أمر البداية
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # الصور
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo
        )
    )

    # الرسائل النصية
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info(
        "SanadAI is running..."
    )

    application.run_polling()


# =========================
# بداية البرنامج
# =========================

if __name__ == "__main__":
    main()
