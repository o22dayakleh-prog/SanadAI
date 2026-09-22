import os
import logging
import tempfile
import asyncio
import time
from datetime import datetime, timezone, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CopyTextButton,
)
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
    increment_questions_used,
    get_user_stats,
    deactivate_expired_subscription,
    create_payment,
    get_payment_by_txid,
    update_payment_status,
    activate_subscription,
)

from payment import verify_payment, PaymentVerificationError


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
    "3",
)

SUBSCRIPTION_DAYS = os.getenv(
    "SUBSCRIPTION_DAYS",
    "30",
)

OWNER_TELEGRAM_ID = int(
    os.getenv(
        "OWNER_TELEGRAM_ID",
        "0",
    )
)


# ============================================================
# إعداد الأسئلة المجانية
# ============================================================

FREE_QUESTIONS = 3


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
# إعداد Telegram Webhook على Render
# ============================================================

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    "https://sanadai-bot.onrender.com",
).rstrip("/")

WEBHOOK_PATH = "/telegram"

WEBHOOK_URL = (
    f"{RENDER_EXTERNAL_URL}"
    f"{WEBHOOK_PATH}"
)


# ============================================================
# تسجيل / تحديث المستخدم
# ============================================================


def register_user(update: Update):

    if not update.effective_user:
        return None

    telegram_user = update.effective_user

    try:

        user = create_or_update_user(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
        )

        logger.info(
            "User registered/updated: %s",
            telegram_user.id,
        )

        return user

    except Exception:

        logger.exception(
            "Could not register/update user"
        )

        return None


# ============================================================
# فحص إمكانية استخدام سؤال
# ============================================================

def can_use_service(telegram_id):

    try:

        user = get_user(
            telegram_id
        )

        if not user:
            return False, None

        if telegram_id == OWNER_TELEGRAM_ID:
            return True, user

        subscription_active = user.get(
            "subscription_active",
            False,
        )

        subscription_expires_at = user.get(
            "subscription_expires_at"
        )

        if subscription_active:

            if (
                subscription_expires_at
                and
                subscription_expires_at <= datetime.now(timezone.utc)
            ):

                deactivate_expired_subscription(
                    telegram_id
                )

                subscription_active = False

        if subscription_active:

            return True, user

        questions_used = user.get(
            "questions_used",
            0,
        )

        if questions_used < FREE_QUESTIONS:

            return True, user

        return False, user

    except Exception:

        logger.exception(
            "Could not check user usage"
        )

        return False, None


# ============================================================
# تسجيل استخدام سؤال
# ============================================================

def consume_question(telegram_id):

    if telegram_id == OWNER_TELEGRAM_ID:
        return 0

    try:

        return increment_questions_used(
            telegram_id
        )

    except Exception:

        logger.exception(
            "Could not increment question counter"
        )

        return None


# ============================================================
# رسالة الاشتراك
# ============================================================

async def send_subscription_message(
    update: Update,
):

    wallet = (
        PAYMENT_WALLET
        or
        "عنوان الدفع غير مضبوط حاليًا."
    )

    message = (
        "💳 اشتراك SanadAI\n\n"
        f"💰 السعر: {SUBSCRIPTION_PRICE_USDT} USDT\n"
        f"📅 المدة: {SUBSCRIPTION_DAYS} يومًا\n"
        "🌐 الشبكة: TRON (TRC20)\n"
        "🪙 العملة: USDT\n\n"
        "📥 عنوان الدفع:\n"
        f"<code>{wallet}</code>\n\n"
        "👆 اضغط على عنوان المحفظة لنسخه بسهولة.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "بعد إتمام التحويل، أرسل رقم المعاملة TXID بهذا الشكل:\n\n"
        "/pay TXID\n\n"
        "🔎 سيتم التحقق من المعاملة على شبكة TRON قبل تفعيل الاشتراك.\n"
        "🔐 لا ترسل أبدًا المفتاح الخاص لمحفظتك."
    )

    reply_markup = None

    if PAYMENT_WALLET:
        reply_markup = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "📋 نسخ عنوان الدفع",
                    copy_text=CopyTextButton(
                        text=PAYMENT_WALLET
                    ),
                )
            ]]
        )

    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


# ============================================================
# أمر الاشتراك
# ============================================================

async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    await send_subscription_message(
        update
    )


# ============================================================
# استقبال TXID والتحقق من الدفع
# الاستخدام: /pay TXID
# ============================================================

async def pay(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    if not update.effective_user:
        return

    telegram_id = update.effective_user.id

    if not context.args:

        await update.message.reply_text(
            "🧾 لإرسال رقم المعاملة استخدم:\n\n"
            "/pay TXID\n\n"
            "مثال:\n"
            "/pay 64_character_transaction_hash"
        )

        return

    if len(context.args) != 1:

        await update.message.reply_text(
            "⚠️ أرسل TXID واحدًا فقط.\n\n"
            "الاستخدام الصحيح:\n"
            "/pay TXID"
        )

        return

    txid = context.args[0].strip()

    if len(txid) != 64:

        await update.message.reply_text(
            "⚠️ يبدو أن TXID غير صحيح.\n\n"
            "TXID الخاص بمعاملات TRON يجب أن يكون بطول 64 حرفًا."
        )

        return

    try:

        int(
            txid,
            16
        )

    except ValueError:

        await update.message.reply_text(
            "⚠️ TXID غير صالح.\n\n"
            "يجب أن يحتوي TXID على أحرف وأرقام سداسية عشرية فقط."
        )

        return

    try:

        # ----------------------------------------------------
        # منع إعادة استخدام TXID
        # ----------------------------------------------------

        existing_payment = await asyncio.to_thread(
            get_payment_by_txid,
            txid,
        )

        if existing_payment:

            existing_user_id = existing_payment.get(
                "telegram_id"
            )

            status = existing_payment.get(
                "status"
            )

            if (
                existing_user_id == telegram_id
                and status == "verified"
            ):

                await update.message.reply_text(
                    "ℹ️ هذه المعاملة تم التحقق منها وتفعيلها سابقًا.\n\n"
                    f"🧾 TXID:\n{txid}"
                )

            elif existing_user_id == telegram_id and status in ("pending", "rejected"):

                await update.message.reply_text(
                    "🔄 هذه المعاملة مسجلة لديك ولكنها لم تعتمد بعد.\n"
                    "🔎 سأعيد التحقق منها على شبكة TRON...\n"
                    "⏳ قد يستغرق الفحص بضع ثوانٍ."
                )

            elif existing_user_id == telegram_id:

                await update.message.reply_text(
                    "ℹ️ هذه المعاملة مسجلة لديك بالفعل.\n\n"
                    f"🧾 TXID:\n{txid}\n"
                    f"⏳ الحالة الحالية: {status}"
                )
                return

            else:

                await update.message.reply_text(
                    "⚠️ هذه المعاملة مسجلة مسبقًا في النظام."
                )
                return

        else:
            # ----------------------------------------------------
            # إعلام المستخدم ببدء التحقق لمعاملة جديدة
            # ----------------------------------------------------

            await update.message.reply_text(
                "🔎 جارٍ التحقق من المعاملة على شبكة TRON...\n"
                "⏳ قد يستغرق الفحص بضع ثوانٍ."
            )

        # ----------------------------------------------------
        # التحقق الحقيقي من البلوكشين
        # ----------------------------------------------------

        verified = await asyncio.to_thread(
            verify_payment,
            txid,
        )

        if not verified.get("verified"):

            raise PaymentVerificationError(
                "Transaction verification failed."
            )

        actual_amount = verified.get(
            "amount_usdt"
        )

        sender_address = verified.get(
            "sender_address"
        )

        recipient_address = verified.get(
            "recipient_address"
        )

        # ----------------------------------------------------
        # تسجيل المعاملة بعد نجاح التحقق
        # ----------------------------------------------------

        payment = await asyncio.to_thread(
            create_payment,
            telegram_id,
            txid,
            str(actual_amount),
            recipient_address or PAYMENT_WALLET or "",
            "TRON",
            "USDT",
        )

        if not payment:

            # حماية إضافية من سباق الطلبات
            existing_payment = await asyncio.to_thread(
                get_payment_by_txid,
                txid,
            )

            if existing_payment:

                await update.message.reply_text(
                    "ℹ️ هذه المعاملة تم تسجيلها مسبقًا."
                )

                return

            raise PaymentVerificationError(
                "Could not save verified payment."
            )

        # ----------------------------------------------------
        # تحديث حالة الدفع إلى verified
        # ----------------------------------------------------

        transaction_time = None

        block_timestamp = verified.get(
            "block_timestamp"
        )

        if block_timestamp:

            try:

                transaction_time = datetime.fromtimestamp(
                    int(block_timestamp) / 1000,
                    tz=timezone.utc,
                )

            except Exception:

                transaction_time = None

        await asyncio.to_thread(
            update_payment_status,
            txid,
            "verified",
            1,
            transaction_time,
        )

        # ----------------------------------------------------
        # تفعيل الاشتراك
        # ----------------------------------------------------

        user = await asyncio.to_thread(
            get_user,
            telegram_id,
        )

        now = datetime.now(timezone.utc)

        current_expiry = (
            user.get("subscription_expires_at")
            if user
            else None
        )

        if (
            current_expiry
            and
            current_expiry > now
        ):

            base_date = current_expiry

        else:

            base_date = now

        expires_at = (
            base_date
            +
            timedelta(
                days=int(SUBSCRIPTION_DAYS)
            )
        )

        await asyncio.to_thread(
            activate_subscription,
            telegram_id,
            expires_at,
        )

        # ----------------------------------------------------
        # رسالة نجاح الدفع
        # ----------------------------------------------------

        sender_text = (
            sender_address
            or
            "غير متاح"
        )

        await update.message.reply_text(
            "🎉 تم التحقق من الدفع بنجاح!\n\n"
            "✅ المعاملة مؤكدة على شبكة TRON\n"
            "🪙 العملة: USDT TRC20\n"
            f"💰 المبلغ المستلم: {actual_amount} USDT\n"
            f"📅 الاشتراك فعال حتى:\n{expires_at}\n\n"
            f"🧾 TXID:\n{txid}\n\n"
            f"📤 عنوان المرسل:\n{sender_text}\n\n"
            "🚀 يمكنك الآن استخدام SanadAI بدون استهلاك الأسئلة المجانية."
        )

        logger.info(
            "Payment verified and subscription activated. "
            "User=%s TXID=%s Amount=%s Expires=%s",
            telegram_id,
            txid,
            actual_amount,
            expires_at,
        )

    except PaymentVerificationError as exc:

        logger.warning(
            "Payment verification failed. User=%s TXID=%s Error=%s",
            telegram_id,
            txid,
            exc,
        )

        await update.message.reply_text(
            "❌ لم يتم تفعيل الاشتراك.\n\n"
            f"السبب:\n{exc}\n\n"
            "تأكد من أن:\n"
            "• المعاملة على شبكة TRON (TRC20)\n"
            "• العملة هي USDT\n"
            "• المبلغ يساوي أو يتجاوز سعر الاشتراك\n"
            "• التحويل وصل إلى عنوان SanadAI الصحيح\n"
            "• المعاملة مؤكدة على الشبكة\n\n"
            "ثم يمكنك المحاولة مرة أخرى."
        )

    except Exception:

        logger.exception(
            "Payment processing error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ غير متوقع أثناء التحقق من الدفع.\n"
            "لم يتم تفعيل الاشتراك.\n"
            "حاول مرة أخرى لاحقًا."
        )


# ============================================================
# فحص الاستخدام قبل معالجة الطلب
# ============================================================

async def check_and_consume(
    update: Update,
):

    if not update.effective_user:
        return False

    telegram_id = update.effective_user.id

    if telegram_id == OWNER_TELEGRAM_ID:

        logger.info(
            "Owner access granted for user %s",
            telegram_id,
        )

        return True

    allowed, user = await asyncio.to_thread(
        can_use_service,
        telegram_id,
    )

    if not allowed:

        await send_subscription_message(
            update
        )

        return False

    subscription_active = (
        user.get(
            "subscription_active",
            False,
        )
        if user
        else False
    )

    if subscription_active:

        return True

    new_count = await asyncio.to_thread(
        consume_question,
        telegram_id,
    )

    if new_count is None:

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء تسجيل استخدام السؤال.\n"
            "حاول مرة أخرى."
        )

        return False

    logger.info(
        "Question consumed for user %s. New count: %s",
        telegram_id,
        new_count,
    )

    return True


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
        "🎁 لديك 3 أسئلة مجانية.\n\n"
        "💳 للاشتراك:\n"
        "/subscribe\n\n"
        "أرسل ما تريد تحليله وسأحاول مساعدتك."
    )

    await update.message.reply_text(
        message
    )


# ============================================================
# أمر إدارة المستخدمين - للمالك فقط
# ============================================================

async def users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    telegram_id = update.effective_user.id

    if telegram_id != OWNER_TELEGRAM_ID:

        logger.warning(
            "Unauthorized /users attempt by user %s",
            telegram_id,
        )

        await update.message.reply_text(
            "⛔ هذا الأمر غير متاح."
        )

        return

    try:

        stats = await asyncio.to_thread(
            get_user_stats
        )

        if not stats:

            await update.message.reply_text(
                "⚠️ لم أستطع الحصول على إحصائيات المستخدمين."
            )

            return

        total_users = stats.get(
            "total_users",
            0,
        )

        active_subscribers = stats.get(
            "active_subscribers",
            0,
        )

        expired_subscriptions = stats.get(
            "expired_subscriptions",
            0,
        )

        free_users_remaining = stats.get(
            "free_users_remaining",
            0,
        )

        total_questions_used = stats.get(
            "total_questions_used",
            0,
        )

        message = (
            "👑 لوحة إدارة مستخدمي SanadAI\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"🎁 لديهم أسئلة مجانية: {free_users_remaining}\n"
            f"💳 الاشتراكات الفعالة: {active_subscribers}\n"
            f"⏰ الاشتراكات المنتهية: {expired_subscriptions}\n"
            f"🔢 إجمالي الأسئلة المستخدمة: {total_questions_used}\n\n"
            "✅ قاعدة البيانات تعمل بشكل صحيح."
        )

        await update.message.reply_text(
            message
        )

    except Exception:

        logger.exception(
            "Users statistics error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء الحصول على إحصائيات المستخدمين."
        )


# ============================================================
# عرض مستخدم محدد - للمالك فقط
# الاستخدام: /user Telegram_ID
# ============================================================

async def user_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    owner_id = update.effective_user.id

    if owner_id != OWNER_TELEGRAM_ID:

        logger.warning(
            "Unauthorized /user attempt by user %s",
            owner_id,
        )

        await update.message.reply_text(
            "⛔ هذا الأمر غير متاح."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "ℹ️ استخدم الأمر بهذا الشكل:\n\n"
            "/user Telegram_ID\n\n"
            "مثال:\n"
            "/user 8840372128"
        )

        return

    if len(context.args) != 1:

        await update.message.reply_text(
            "⚠️ يجب إدخال Telegram ID واحد فقط.\n\n"
            "مثال:\n"
            "/user 8840372128"
        )

        return

    target_id_text = context.args[0]

    try:

        target_telegram_id = int(
            target_id_text
        )

    except ValueError:

        await update.message.reply_text(
            "⚠️ Telegram ID يجب أن يكون رقمًا فقط.\n\n"
            "مثال:\n"
            "/user 8840372128"
        )

        return

    try:

        user = await asyncio.to_thread(
            get_user,
            target_telegram_id,
        )

        if not user:

            await update.message.reply_text(
                "🔍 لم يتم العثور على هذا المستخدم "
                "في قاعدة البيانات.\n\n"
                f"Telegram ID: {target_telegram_id}"
            )

            return

        username = user.get(
            "username"
        )

        first_name = user.get(
            "first_name"
        )

        questions_used = user.get(
            "questions_used",
            0,
        )

        subscription_active = user.get(
            "subscription_active",
            False,
        )

        subscription_expires_at = user.get(
            "subscription_expires_at"
        )

        created_at = user.get(
            "created_at"
        )

        actual_subscription_active = False

        if subscription_active:

            if (
                subscription_expires_at
                and
                subscription_expires_at > datetime.now(timezone.utc)
            ):

                actual_subscription_active = True

            elif subscription_expires_at:

                await asyncio.to_thread(
                    deactivate_expired_subscription,
                    target_telegram_id,
                )

        if actual_subscription_active:

            subscription_status = "🟢 مفعّل"

        else:

            subscription_status = "⚪ غير مفعّل"

        remaining_free = max(
            FREE_QUESTIONS - questions_used,
            0,
        )

        if username:

            username_text = f"@{username}"

        else:

            username_text = "غير موجود"

        if first_name:

            name_text = first_name

        else:

            name_text = "غير موجود"

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

        message = (
            "👤 بيانات المستخدم\n\n"
            f"🆔 Telegram ID: {target_telegram_id}\n"
            f"👤 الاسم: {name_text}\n"
            f"🔹 Username: {username_text}\n"
            f"🔢 الأسئلة المستخدمة: {questions_used}\n"
            f"🎁 الأسئلة المجانية المتبقية: {remaining_free}\n"
            f"💳 الاشتراك: {subscription_status}\n"
            f"📅 انتهاء الاشتراك: {expires_text}\n"
            f"🕐 تاريخ إنشاء الحساب: {created_text}"
        )

        await update.message.reply_text(
            message
        )

    except Exception:

        logger.exception(
            "User details error"
        )

        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء الحصول على بيانات المستخدم."
        )


# ============================================================
# فحص قاعدة البيانات - للمالك فقط
# ============================================================

async def mydb(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_user:
        return

    telegram_id = update.effective_user.id

    if telegram_id != OWNER_TELEGRAM_ID:

        logger.warning(
            "Unauthorized /mydb attempt by user %s",
            telegram_id,
        )

        await update.message.reply_text(
            "⛔ هذا الأمر غير متاح."
        )

        return

    register_user(update)

    try:

        user = await asyncio.to_thread(
            get_user,
            telegram_id,
        )

        if not user:

            await update.message.reply_text(
                "⚠️ لم يتم العثور على حسابك في قاعدة البيانات."
            )

            return

        username = (
            user.get("username")
            or
            "غير موجود"
        )

        first_name = (
            user.get("first_name")
            or
            "غير موجود"
        )

        questions_used = user.get(
            "questions_used",
            0,
        )

        subscription_active = user.get(
            "subscription_active",
            False,
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

        remaining = max(
            FREE_QUESTIONS - questions_used,
            0,
        )

        message = (
            "🗄️ بيانات حسابك في SanadAI\n\n"
            f"🆔 Telegram ID: {telegram_id}\n"
            f"👤 الاسم: {first_name}\n"
            f"🔹 Username: @{username if username != 'غير موجود' else username}\n"
            f"🔢 الأسئلة المستخدمة: {questions_used}\n"
            f"🎁 الأسئلة المجانية المتبقية: {remaining}\n"
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
# تشغيل Gemini
# ============================================================

async def run_gemini(contents):

    if gemini_client is None:

        raise RuntimeError(
            "GEMINI_API_KEY غير موجود."
        )

    start_time = time.perf_counter()

    def generate():

        return gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
        )

    try:

        response = await asyncio.to_thread(
            generate
        )

        elapsed = time.perf_counter() - start_time

        logger.info(
            "Gemini request completed in %.2f seconds",
            elapsed,
        )

        return response

    except Exception:

        elapsed = time.perf_counter() - start_time

        logger.exception(
            "Gemini request failed after %.2f seconds",
            elapsed,
        )

        raise


# ============================================================
# الرسائل النصية
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    allowed = await check_and_consume(
        update
    )

    if not allowed:
        return

    try:

        user_text = update.message.text

        if not user_text:
            return

        await update.message.reply_text(
            "⏳ جارٍ معالجة سؤالك..."
        )

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

    allowed = await check_and_consume(
        update
    )

    if not allowed:
        return

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

    allowed = await check_and_consume(
        update
    )

    if not allowed:
        return

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
            ".xls",
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

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe,
        )
    )

    application.add_handler(
        CommandHandler(
            "pay",
            pay,
        )
    )

    application.add_handler(
        CommandHandler(
            "mydb",
            mydb,
        )
    )

    application.add_handler(
        CommandHandler(
            "users",
            users,
        )
    )

    application.add_handler(
        CommandHandler(
            "user",
            user_details,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info(
        "SanadAI bot is starting with Telegram webhook..."
    )

    logger.info(
        "Telegram webhook URL: %s",
        WEBHOOK_URL,
    )

    port = int(
        os.environ.get(
            "PORT",
            "10000",
        )
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=WEBHOOK_PATH.lstrip("/"),
        webhook_url=WEBHOOK_URL,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


# ============================================================
# نقطة البداية
# ============================================================

if __name__ == "__main__":

    main()

