"""Customer accounts.

Until now a server was reachable only through the unguessable session token in
its URL. That works on the browser that opened it and nowhere else — clear the
site data or move to another device and the server is gone with no way back.
An account is the recovery path.

Registration is offered AFTER the server is running, not before: the founder's
whole flow is "click, and it opens", and a signup wall in front of that would
undo it. So an anonymous server exists first and gets claimed by the account
created moments later.

Passwords are hashed with scrypt from hashlib — a real memory-hard KDF in the
standard library, so there is no dependency to add or keep patched. Parameters
follow the usual interactive-login guidance (N=2^15, r=8, p=1).
"""

import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from app.db import get_connection

logger = logging.getLogger(__name__)

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64

SESSION_DAYS = 90
MIN_PASSWORD_LENGTH = 8

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    """Anything a customer should see a readable Korean message for."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalise_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str:
    email = normalise_email(email)
    if not _EMAIL_RE.match(email):
        raise AccountError("이메일 형식이 올바르지 않습니다.")
    return email


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.")
    if len(password) > 200:
        # scrypt on an unbounded input is a free CPU-exhaustion vector.
        raise AccountError("비밀번호가 너무 깁니다.")
    return password


def _maxmem_for(n: int, r: int, p: int) -> int:
    """scrypt needs 128*N*r*p bytes, and OpenSSL refuses past its own 32MiB
    default — which N=2^15, r=8 hits exactly. Ask for the real requirement
    plus headroom instead of inheriting a limit that rejects our parameters.
    """
    return 128 * n * r * p * 2


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=_maxmem_for(SCRYPT_N, SCRYPT_R, SCRYPT_P),
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, expected_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected_hex)),
            maxmem=_maxmem_for(int(n), int(r), int(p)),
        )
    except (ValueError, TypeError):
        return False
    # Constant-time: a timing side channel here leaks password guesses.
    return hmac.compare_digest(derived.hex(), expected_hex)


def _issue_auth_token(conn: sqlite3.Connection, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_tokens (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, _now().isoformat(), (_now() + timedelta(days=SESSION_DAYS)).isoformat()),
    )
    conn.commit()
    return token


def register(email: str, password: str, claim_session_token: str | None = None) -> dict:
    email = validate_email(email)
    validate_password(password)

    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AccountError("이미 가입된 이메일입니다. 로그인해주세요.")

        user_id = secrets.token_urlsafe(16)
        conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, hash_password(password), _now().isoformat()),
        )
        claimed = _claim_server(conn, user_id, claim_session_token)
        conn.commit()

        return {"token": _issue_auth_token(conn, user_id), "email": email, "claimed_server": claimed}
    finally:
        conn.close()


def _claim_server(conn: sqlite3.Connection, user_id: str, session_token: str | None) -> bool:
    """Attach the server the customer is currently looking at to their account.

    Only an UNCLAIMED server can be taken. Otherwise anyone who got hold of a
    session URL could bind someone else's server to their own account, which
    would be a far worse hole than the shareable-link exposure it replaces.
    """
    if not session_token:
        return False
    row = conn.execute(
        "SELECT id, user_id FROM servers WHERE session_token = ?", (session_token,)
    ).fetchone()
    if row is None or row["user_id"]:
        return False
    conn.execute("UPDATE servers SET user_id = ? WHERE id = ?", (user_id, row["id"]))
    return True


def login(email: str, password: str, claim_session_token: str | None = None) -> dict:
    email = normalise_email(email)
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        # Hash even when the account is missing, so a wrong email and a wrong
        # password take the same time and cannot be told apart.
        stored = row["password_hash"] if row else hash_password("dummy")
        if not verify_password(password, stored) or row is None:
            raise AccountError("이메일 또는 비밀번호가 올바르지 않습니다.")

        claimed = _claim_server(conn, row["id"], claim_session_token)
        conn.commit()
        return {"token": _issue_auth_token(conn, row["id"]), "email": email, "claimed_server": claimed}
    finally:
        conn.close()


def user_for_token(token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id
               WHERE t.token = ? AND t.expires_at > ?""",
            (token, _now().isoformat()),
        ).fetchone()
        return row
    finally:
        conn.close()


def logout(token: str | None) -> None:
    if not token:
        return
    conn = get_connection()
    try:
        conn.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def servers_for_user(user_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, nickname, tier, status, session_token, game_port, payment_status
               FROM servers WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def user_owns_session(user_id: str, session_token: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM servers WHERE session_token = ? AND user_id = ?",
            (session_token, user_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()
