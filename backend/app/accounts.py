"""Customer accounts.

Until now a server was reachable only through the unguessable session token in
its URL. That works on the browser that opened it and nowhere else — clear the
site data or move to another device and the server is gone with no way back.
An account is the recovery path.

Signing in is offered AFTER the server is running, not before: the founder's
whole flow is "click, and it opens", and a signup wall in front of that would
undo it. So an anonymous server exists first and gets claimed by the account
created moments later.

There are no passwords. Identity comes from Google (app/google_oauth.py), so
this module never sees a credential — only a verified subject id and email.
That removed the scrypt work factor this file used to carry, and with it the
OpenSSL maxmem ceiling that made every login 500 until it was passed
explicitly. users.password_hash survives, nullable, so an account made before
the switch still resolves; nothing writes to it any more.
"""

import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from app.db import get_connection

logger = logging.getLogger(__name__)

SESSION_DAYS = 90


class AccountError(Exception):
    """Anything a customer should see a readable Korean message for."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalise_email(email: str) -> str:
    return email.strip().lower()


def _issue_auth_token(conn: sqlite3.Connection, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_tokens (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, _now().isoformat(), (_now() + timedelta(days=SESSION_DAYS)).isoformat()),
    )
    conn.commit()
    return token


def sign_in_with_google(
    google_sub: str,
    email: str,
    display_name: str | None = None,
    claim_session_token: str | None = None,
) -> dict:
    """Sign in (creating the account if this is the first time).

    Matching is by Google's subject id first and email second. The email
    fallback exists so an account made under the old password flow is adopted
    rather than colliding with users.email's UNIQUE constraint — the person
    proved control of that address to Google, which is a stronger claim than
    the password check it replaces.
    """
    email = normalise_email(email)

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE users SET google_sub = ? WHERE id = ?", (google_sub, row["id"])
                )

        if row is None:
            user_id = secrets.token_urlsafe(16)
            conn.execute(
                "INSERT INTO users (id, email, google_sub, created_at) VALUES (?, ?, ?, ?)",
                (user_id, email, google_sub, _now().isoformat()),
            )
        else:
            user_id = row["id"]
            if row["email"] != email:
                # A Google address can change, and this one is where the owner
                # writes when a deposit cannot be matched to a server — a stale
                # address is a customer they cannot reach.
                try:
                    conn.execute(
                        "UPDATE users SET email = ? WHERE id = ?", (email, user_id)
                    )
                except sqlite3.IntegrityError:
                    # users.email is UNIQUE and the new address already belongs
                    # to a different account. Merging two accounts would move
                    # servers between owners, so keep the old address and let a
                    # human sort it out.
                    logger.warning(
                        "user %s now signs in as an address held by another account", user_id
                    )

        claimed = _claim_server(conn, user_id, claim_session_token)
        if claimed and display_name:
            _name_unnamed_server(conn, claim_session_token, display_name)
        conn.commit()

        return {"token": _issue_auth_token(conn, user_id), "email": email, "claimed_server": claimed}
    finally:
        conn.close()


def _name_unnamed_server(conn: sqlite3.Connection, session_token: str, display_name: str) -> None:
    """Give the server the customer's name, now that we finally know it.

    Only fills a blank. Opening a server asks for nothing, so it starts
    nameless; but if a name was typed at some point it is the one the customer
    chose, and Google's profile name does not get to overwrite it.
    """
    conn.execute(
        "UPDATE servers SET nickname = ? WHERE session_token = ? AND (nickname IS NULL OR nickname = '')",
        (display_name[:20], session_token),
    )


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
