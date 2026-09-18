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
# Gemini
# =========================

gemini_client = None

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# =========================
# خادم HTTP لـ Render
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"SanadAI is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 10000))

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    name = user.first_name if user else "صديقي"

    text = f"""
مرحبًا {name} 👋

أنا SanadAI 🤖

مساعدك الذكي للدراسة والعمل والحياة اليومية.

يمكنك أن تسألني عن:

🎓 الدراسة والجامعة
🔧 الميكانيك والفنيّات
🏗️ الهندسة
💼 العمل الحر
📝 الكتابة والترجمة
📚 التلخيص والشرح
🧠 المعلومات العامة

أرسل سؤالك الآن.
"""

    await update.message.reply_text(text)


# =========================
# معالجة الرسائل
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message or not update.message.text:
        return

    user_text = update.message.text.strip()

    if not gemini_client:

        await update.message.reply_text(
            "⚠️ خدمة الذكاء الاصطناعي غير مفعّلة حاليًا."
        )

        return

    try:

        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=user_text,
        )

        answer = response.text

        if not answer:
            answer = "لم أتمكن من الحصول على إجابة."

        await update.message.reply_text(answer)

    except Exception:

        logger.exception("Gemini error")

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء معالجة طلبك. حاول مرة أخرى."
        )


# =========================
# تشغيل البوت
# =========================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN غير موجود"
        )

    # تشغيل خادم Render في الخلفية
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

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("SanadAI is running...")

    application.run_polling()


# =========================
# بداية البرنامج
# =========================

if __name__ == "__main__":
    main()
