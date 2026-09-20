import os
import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    """Create and return a PostgreSQL database connection."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def init_database():
    """Create the database tables if they do not already exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:

            # ====================================================
            # جدول المستخدمين
            # ====================================================

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    questions_used INTEGER NOT NULL DEFAULT 0,
                    subscription_active BOOLEAN NOT NULL DEFAULT FALSE,
                    subscription_expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_users_telegram_id
                ON users (telegram_id);
                """
            )

            # ====================================================
            # جدول عمليات الدفع
            # ====================================================

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,

                    telegram_id BIGINT NOT NULL,

                    txid TEXT UNIQUE NOT NULL,

                    amount_usdt NUMERIC(20, 6) NOT NULL,

                    recipient_address TEXT NOT NULL,

                    network TEXT NOT NULL DEFAULT 'TRON',

                    token TEXT NOT NULL DEFAULT 'USDT',

                    status TEXT NOT NULL DEFAULT 'pending',

                    confirmations INTEGER NOT NULL DEFAULT 0,

                    transaction_time TIMESTAMPTZ,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    verified_at TIMESTAMPTZ
                );
                """
            )

            # ====================================================
            # فهارس جدول الدفع
            # ====================================================

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_telegram_id
                ON payments (telegram_id);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_status
                ON payments (status);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_txid
                ON payments (txid);
                """
            )

        conn.commit()


# ============================================================
# المستخدمون
# ============================================================

def get_user(telegram_id):
    """Return a user by Telegram ID, or None if the user does not exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM users
                WHERE telegram_id = %s;
                """,
                (telegram_id,)
            )

            return cur.fetchone()


def create_or_update_user(
    telegram_id,
    username=None,
    first_name=None
):
    """Create a user if needed, otherwise update their profile information."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (
                    telegram_id,
                    username,
                    first_name
                )
                VALUES (%s, %s, %s)

                ON CONFLICT (telegram_id)

                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    updated_at = NOW()

                RETURNING *;
                """,
                (
                    telegram_id,
                    username,
                    first_name
                )
            )

            user = cur.fetchone()

        conn.commit()

        return user


def increment_questions_used(telegram_id):
    """Increase the user's question counter by one."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    questions_used = questions_used + 1,
                    updated_at = NOW()
                WHERE telegram_id = %s

                RETURNING questions_used;
                """,
                (telegram_id,)
            )

            result = cur.fetchone()

        conn.commit()

        return result["questions_used"] if result else None


def reset_questions_used(telegram_id):
    """Reset the user's question counter."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    questions_used = 0,
                    updated_at = NOW()
                WHERE telegram_id = %s;
                """,
                (telegram_id,)
            )

        conn.commit()


def activate_subscription(
    telegram_id,
    expires_at
):
    """Activate or extend a user's subscription."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    subscription_active = TRUE,
                    subscription_expires_at = %s,
                    updated_at = NOW()
                WHERE telegram_id = %s;
                """,
                (
                    expires_at,
                    telegram_id
                )
            )

        conn.commit()


def deactivate_expired_subscription(
    telegram_id
):
    """Deactivate a subscription that has expired."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET
                    subscription_active = FALSE,
                    updated_at = NOW()
                WHERE telegram_id = %s
                  AND subscription_expires_at IS NOT NULL
                  AND subscription_expires_at <= NOW();
                """,
                (telegram_id,)
            )

        conn.commit()


def get_user_stats():
    """Return overall user statistics."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_users,

                    COUNT(*) FILTER (
                        WHERE subscription_active = TRUE
                          AND subscription_expires_at IS NOT NULL
                          AND subscription_expires_at > NOW()
                    ) AS active_subscribers,

                    COUNT(*) FILTER (
                        WHERE subscription_expires_at IS NOT NULL
                          AND subscription_expires_at <= NOW()
                    ) AS expired_subscriptions,

                    COUNT(*) FILTER (
                        WHERE subscription_active = FALSE
                          AND questions_used < 3
                    ) AS free_users_remaining,

                    COALESCE(
                        SUM(questions_used),
                        0
                    ) AS total_questions_used

                FROM users;
                """
            )

            return cur.fetchone()


# ============================================================
# عمليات الدفع
# ============================================================

def create_payment(
    telegram_id,
    txid,
    amount_usdt,
    recipient_address,
    network="TRON",
    token="USDT"
):
    """
    Create a payment record.

    TXID is unique, so the same transaction
    cannot be registered twice.
    """

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO payments (
                    telegram_id,
                    txid,
                    amount_usdt,
                    recipient_address,
                    network,
                    token
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )

                ON CONFLICT (txid)
                DO NOTHING

                RETURNING *;
                """,
                (
                    telegram_id,
                    txid,
                    amount_usdt,
                    recipient_address,
                    network,
                    token
                )
            )

            payment = cur.fetchone()

        conn.commit()

        return payment


def get_payment_by_txid(txid):
    """Return a payment by TXID."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM payments
                WHERE txid = %s;
                """,
                (txid,)
            )

            return cur.fetchone()


def get_user_payments(telegram_id):
    """Return all payments belonging to a user."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM payments
                WHERE telegram_id = %s
                ORDER BY created_at DESC;
                """,
                (telegram_id,)
            )

            return cur.fetchall()


def update_payment_status(
    txid,
    status,
    confirmations=0,
    transaction_time=None
):
    """Update payment verification status."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE payments
                SET
                    status = %s,
                    confirmations = %s,
                    transaction_time = %s,
                    verified_at = CASE
                        WHEN %s = 'verified'
                        THEN NOW()
                        ELSE verified_at
                    END
                WHERE txid = %s;
                """,
                (
                    status,
                    confirmations,
                    transaction_time,
                    status,
                    txid
                )
            )

        conn.commit()


def get_payment_stats():
    """Return payment statistics."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_payments,

                    COUNT(*) FILTER (
                        WHERE status = 'verified'
                    ) AS verified_payments,

                    COUNT(*) FILTER (
                        WHERE status = 'pending'
                    ) AS pending_payments,

                    COUNT(*) FILTER (
                        WHERE status = 'rejected'
                    ) AS rejected_payments,

                    COALESCE(
                        SUM(amount_usdt)
                        FILTER (
                            WHERE status = 'verified'
                        ),
                        0
                    ) AS verified_amount_usdt

                FROM payments;
                """
            )

            return cur.fetchone()
