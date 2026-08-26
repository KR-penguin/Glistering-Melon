"""How a server is referred to when nobody has given a name.

Opening a server asks for nothing, so `nickname` is usually empty until the
customer signs in with Google. Every Korean string that used to read
"{nickname}님" would otherwise render as "님" or "undefined님", which looks
like a bug to the one person who reads all of them — the owner, in Discord.

Two audiences need different fallbacks, which is why this is not one constant:
the owner needs to tell servers apart, and the customer already knows which
server they are looking at.
"""

import secrets
import sqlite3

from app.config import settings

# Deliberately not "손님": these get interpolated in front of 님, and "손님님"
# reads worse than no name at all.
UNNAMED_OWNER_LABEL = "이름 미등록"
UNNAMED_CUSTOMER_LABEL = "내 서버"

# No 0/o, 1/l/i — this gets read off a screen and typed into Minecraft by
# someone's friend, often from a screenshot in a chat app.
SLUG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SLUG_LENGTH = 6


def subject_for_name(nickname: str | None) -> str:
    """When there is a name but no server row yet — capacity alerts fire
    before a container is allocated."""
    return f"{nickname}님" if nickname else UNNAMED_OWNER_LABEL


def owner_label(row: sqlite3.Row) -> str:
    """For Discord notifications and /admin — must identify WHICH server."""
    nickname = row["nickname"] if "nickname" in row.keys() else None
    if nickname:
        return f"{nickname}님"
    return f"{UNNAMED_OWNER_LABEL}({row['container_name']})"


def customer_heading(nickname: str | None) -> str:
    """For the session page heading — the customer knows whose server it is."""
    return f"{nickname}님의" if nickname else UNNAMED_CUSTOMER_LABEL


def generate_slug(conn: sqlite3.Connection) -> str:
    """A short name for one server, used as its subdomain.

    Random rather than derived from the customer: opening a server asks for no
    name, so most of the time there is nothing to derive from, and a Korean
    nickname is not a valid hostname anyway. Fixed at provisioning and never
    changed afterwards — customers hand this address to their friends, and
    renaming it later would break a link that is already out in the world.
    """
    taken = {
        row["mc_slug"]
        for row in conn.execute("SELECT mc_slug FROM servers WHERE mc_slug IS NOT NULL")
    }
    for _ in range(200):
        slug = "".join(secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH))
        if slug not in taken:
            return slug
    raise RuntimeError("could not allocate a free server slug")


def server_address(row: sqlite3.Row) -> str:
    """What a player types into Minecraft.

    Falls back to the bare host whenever there is no domain configured or the
    row predates slugs, so development on the LAN keeps working unchanged.
    """
    slug = row["mc_slug"] if "mc_slug" in row.keys() else None
    host = settings.minecraft_public_host
    if settings.minecraft_domain:
        domain = settings.minecraft_domain.strip().strip(".")
        host = f"{slug}.{domain}" if slug else domain
    return f"{host}:{row['game_port']}"
