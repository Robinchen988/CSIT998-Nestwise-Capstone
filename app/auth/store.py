import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import psycopg2.errors

from app.core.db import get_connection

SESSION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    username       TEXT NOT NULL,
    username_key   TEXT NOT NULL UNIQUE,
    email          TEXT,
    email_key      TEXT UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
CREATE TABLE IF NOT EXISTS favorites (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    property_id  INTEGER NOT NULL,
    snapshot     JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, property_id)
);
"""


class Taken(ValueError):
    pass


def ensure_schema() -> None:
    with get_connection() as conn:
        conn.cursor().execute(SCHEMA)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def create_user(username: str, email: str | None, password_hash: str) -> dict:
    name_key, email_key = username.lower(), email.lower() if email else None
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT username_key, email_key FROM users WHERE username_key = %s OR email_key = %s",
                        (name_key, email_key))
            rows = cur.fetchall()
            if any(r[0] == name_key for r in rows):
                raise Taken('username')
            if rows:
                raise Taken('email')
            cur.execute("INSERT INTO users (username, username_key, email, email_key, password_hash) "
                        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                        (username, name_key, email, email_key, password_hash))
            return {'id': cur.fetchone()[0], 'username': username, 'email': email}
    except psycopg2.errors.UniqueViolation as exc:
        raise Taken('email' if 'email' in str(exc) else 'username') from exc


def find_for_login(login: str) -> dict | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, email, password_hash FROM users WHERE username_key = %s OR email_key = %s",
                    (login.lower(), login.lower()))
        row = cur.fetchone()
    return dict(zip(('id', 'username', 'email', 'password_hash'), row)) if row else None


def create_session(user_id: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE expires_at < now()")
        cur.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                    (_token_hash(token), user_id, expires))
    return token, expires


def user_for_session(token: str | None) -> dict | None:
    if not token or not token.isascii():
        return None
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT u.id, u.username, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE s.token_hash = %s AND s.expires_at > now()", (_token_hash(token),))
        row = cur.fetchone()
    return dict(zip(('id', 'username', 'email'), row)) if row else None


def delete_session(token: str | None) -> None:
    if token and token.isascii():
        with get_connection() as conn:
            conn.cursor().execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))


def delete_user(user_id: int) -> None:
    with get_connection() as conn:
        conn.cursor().execute("DELETE FROM users WHERE id = %s", (user_id,))
