import os
import logging
import threading
import tempfile
import asyncio

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

from database import (
    init_database,
    create_or_update_user,
    get_user,
)


# ============================================================
# إعداد التسجيل
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# مفاتيح التشغيل
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

PAYMENT_WALLET = os.getenv("PAYMENT_WALLET")
SUBSCRIPTION_PRICE_USDT = os.getenv(
    "SUBSCRIPTION_PRICE_USDT",
    "3"
)
SUBSCRIPTION_DAYS = os.getenv(
    "SUBSCRIPTION_DAYS",
    "30"
)


# ============================================================
# نموذج Gemini
# ============================================================

GEMINI_MODEL = "gemini-3.6-flash"

gemini_client = None

if GEMINI_API_KEY:
    gemini_client = genai.Client(
        api_key=GEMINI_API_KEY
    )


# ============================================================
# خادم الصحة الخاص بـ Render
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.end_headers()

        self.wfile.write(
            b"SanadAI is running."
        )

    def log_message(self, format, *args):
        return


def start_health_server():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    logger.info(
        "Health server started on port %s",
        port,
    )

    server.serve_forever()


# ============================================================
# تسجيل / تحديث مستخدم Telegram
# ============================================================

def register_user(update: Update):

    if not update.effective_user:
        return

    telegram_user = update.effective_user

    try:

        create_or_update_user(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
        )

        logger.info(
            "User registered/updated: %s",
            telegram_user.id,
        )

    except Exception:

        logger.exception(
            "Could not register/update user"
        )


# ============================================================
# أمر البداية
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    message = (
        "🤖 أهلاً بك في SanadAI\n\n"
        "أنا مساعد ذكاء اصطناعي متعدد الاستخدامات.\n\n"
        "يمكنك إرسال:\n"
        "💬 نص\n"
        "🖼️ صورة\n"
        "📄 PDF\n"
        "📝 Word\n"
        "📊 Excel\n"
        "📋 CSV\n"
        "📃 TXT\n\n"
        "أرسل ما تريد تحليله وسأحاول مساعدتك."
    )

    await update.message.reply_text(
        message
    )


# ============================================================
# فحص قاعدة البيانات - مؤقت
# ============================================================

async def mydb(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    try:

        telegram_id = update.effective_user.id

        user = await asyncio.to_thread(
            get_user,
            telegram_id
        )

        if not user:

            await update.message.reply_text(
                "⚠️ لم يتم العثور على حسابك في قاعدة البيانات."
            )

            return

        username = user.get("username") or "غير موجود"
        first_name = user.get("first_name") or "غير موجود"
        questions_used = user.get("questions_used", 0)
        subscription_active = user.get(
            "subscription_active",
            False
        )
        subscription_expires_at = user.get(
            "subscription_expires_at"
        )
        created_at = user.get(
            "created_at"
        )

        if subscription_expires_at:

            expires_text = str(
                subscription_expires_at
            )

        else:

            expires_text = "لا يوجد"

        if created_at:

            created_text = str(
                created_at
            )

        else:

            created_text = "غير معروف"

        status = (
            "🟢 مفعّل"
            if subscription_active
            else
            "⚪ غير مفعّل"
        )

        message = (
            "🗄️ بيانات حسابك في SanadAI\n\n"
            f"🆔 Telegram ID: {telegram_id}\n"
            f"👤 الاسم: {first_name}\n"
            f"🔹 Username: @{username if username != 'غير موجود' else username}\n"
            f"🔢 الأسئلة المستخدمة: {questions_used}\n"
            f"💳 الاشتراك: {status}\n"
            f"📅 انتهاء الاشتراك: {expires_text}\n"
            f"🕐 تاريخ إنشاء الحساب: {created_text}\n\n"
            "✅ قاعدة البيانات متصلة وحسابك مسجل."
        )

        await update.message.reply_text(
            message
        )

    except Exception:

        logger.exception(
            "Database check error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء فحص قاعدة البيانات."
        )


# ============================================================
# تشغيل Gemini بطريقة لا توقف البوت
# ============================================================

async def run_gemini(contents):

    if gemini_client is None:

        raise RuntimeError(
            "GEMINI_API_KEY غير موجود."
        )

    def generate():

        return gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
        )

    return await asyncio.to_thread(
        generate
    )


# ============================================================
# الرسائل النصية
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    try:

        user_text = update.message.text

        if not user_text:
            return

        response = await run_gemini(
            user_text
        )

        answer = response.text

        if not answer:

            answer = (
                "⚠️ لم أستطع الحصول على إجابة."
            )

        await update.message.reply_text(
            answer
        )

    except Exception:

        logger.exception(
            "Text processing error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء معالجة رسالتك."
        )


# ============================================================
# الصور
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    try:

        await update.message.reply_text(
            "🖼️ تم استلام الصورة.\n"
            "⏳ جارٍ تحليلها..."
        )

        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        image_bytes = (
            await telegram_file.download_as_bytearray()
        )

        user_text = (
            update.message.caption
            or
            "حلل هذه الصورة واشرح لي ما تحتويه بالتفصيل."
        )

        contents = [

            types.Part.from_bytes(
                data=bytes(image_bytes),
                mime_type="image/jpeg",
            ),

            user_text,
        ]

        response = await run_gemini(
            contents
        )

        answer = response.text

        if not answer:

            answer = (
                "⚠️ لم أستطع تحليل الصورة."
            )

        await update.message.reply_text(
            answer
        )

    except Exception:

        logger.exception(
            "Image processing error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء تحليل الصورة.\n"
            "حاول إرسالها مرة أخرى."
        )


# ============================================================
# أنواع الملفات المدعومة
# ============================================================

SUPPORTED_DOCUMENTS = {

    ".pdf":
        "application/pdf",

    ".docx":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",

    ".xlsx":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

    ".xls":
        "application/vnd.ms-excel",

    ".csv":
        "text/csv",

    ".txt":
        "text/plain",
}


# ============================================================
# معالجة الملفات
# ============================================================

async def handle_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    temp_path = None
    uploaded_file = None

    try:

        document = update.message.document

        if document is None:
            return

        file_name = (
            document.file_name
            or
            "file"
        )

        extension = os.path.splitext(
            file_name
        )[1].lower()

        mime_type = document.mime_type

        if extension not in SUPPORTED_DOCUMENTS:

            await update.message.reply_text(
                "⚠️ هذا النوع من الملفات غير مدعوم حاليًا.\n\n"
                "الأنواع المدعومة:\n"
                "📄 PDF\n"
                "📝 Word (.docx)\n"
                "📊 Excel (.xlsx / .xls)\n"
                "📋 CSV\n"
                "📃 TXT"
            )

            return

        if not mime_type:

            mime_type = SUPPORTED_DOCUMENTS[
                extension
            ]

        if extension == ".pdf":

            icon = "📄"

        elif extension == ".docx":

            icon = "📝"

        elif extension in [
            ".xlsx",
            ".xls"
        ]:

            icon = "📊"

        elif extension == ".csv":

            icon = "📋"

        else:

            icon = "📃"

        await update.message.reply_text(
            f"{icon} تم استلام الملف:\n"
            f"{file_name}\n\n"
            "⏳ جارٍ قراءته وتحليله..."
        )

        telegram_file = await context.bot.get_file(
            document.file_id
        )

        suffix = (
            extension
            if extension
            else
            ".tmp"
        )

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp_file:

            temp_path = temp_file.name

        await telegram_file.download_to_drive(
            custom_path=temp_path
        )

        # ----------------------------------------------------
        # رفع الملف إلى Gemini
        # ----------------------------------------------------

        def upload_file():

            return gemini_client.files.upload(
                file=temp_path
            )

        uploaded_file = await asyncio.to_thread(
            upload_file
        )

        user_prompt = (
            update.message.caption
            or
            "اقرأ هذا الملف بالكامل ثم حلله بدقة. "
            "استخرج أهم المعلومات والحقائق والأرقام، "
            "وأجب عن أي سؤال مرتبط بمحتواه. "
            "إذا كان هناك تناقض أو خطأ واضح في الملف، "
            "اذكره بوضوح. "
            "إذا كان الملف جدولًا، فحلل البيانات الموجودة فيه. "
            "فرّق بين الحقائق والحسابات والاستنتاجات. "
            "لا تفترض سببًا أو معلومة غير موجودة في الملف. "
            "قدّم الإجابة باللغة العربية ما لم يطلب المستخدم لغة أخرى."
        )

        # ----------------------------------------------------
        # إرسال الملف مع الطلب إلى Gemini
        # ----------------------------------------------------

        file_part = types.Part.from_uri(
            file_uri=uploaded_file.uri,
            mime_type=(
                uploaded_file.mime_type
                or
                mime_type
            ),
        )

        response = await run_gemini(
            [
                file_part,
                user_prompt,
            ]
        )

        answer = response.text

        if not answer:

            answer = (
                "⚠️ تم استلام الملف، "
                "لكن لم أستطع استخراج إجابة منه."
            )

        await update.message.reply_text(
            answer
        )

    except Exception:

        logger.exception(
            "Document processing error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء قراءة الملف.\n"
            "تأكد من أن الملف سليم وحاول مرة أخرى."
        )

    finally:

        if temp_path:

            try:

                if os.path.exists(
                    temp_path
                ):

                    os.remove(
                        temp_path
                    )

            except Exception:

                logger.warning(
                    "Could not remove temporary file"
                )


# ============================================================
# التشغيل الرئيسي
# ============================================================

def main():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN غير موجود."
        )

    if not GEMINI_API_KEY:

        raise RuntimeError(
            "GEMINI_API_KEY غير موجود."
        )

    # --------------------------------------------------------
    # تهيئة قاعدة البيانات
    # --------------------------------------------------------

    try:

        init_database()

        logger.info(
            "Database initialized successfully."
        )

    except Exception:

        logger.exception(
            "Database initialization failed."
        )

        raise

    # --------------------------------------------------------
    # تشغيل خادم Render في الخلفية
    # --------------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )

    health_thread.start()

    # --------------------------------------------------------
    # إنشاء تطبيق Telegram
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # الأوامر
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "mydb",
            mydb,
        )
    )

    # --------------------------------------------------------
    # الصور
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    )

    # --------------------------------------------------------
    # جميع الملفات
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    # --------------------------------------------------------
    # الرسائل النصية
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info(
        "SanadAI bot is starting..."
    )

    application.run_polling()


# ============================================================
# نقطة البداية
# ============================================================

if __name__ == "__main__":

    main()                

    
