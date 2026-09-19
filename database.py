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
    """Create the users table if it does not already exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
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

        conn.commit()


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


def create_or_update_user(telegram_id, username=None, first_name=None):
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
                (telegram_id, username, first_name)
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


def activate_subscription(telegram_id, expires_at):
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
                (expires_at, telegram_id)
            )

        conn.commit()


def deactivate_expired_subscription(telegram_id):
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
