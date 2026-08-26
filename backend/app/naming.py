"""How a server is referred to when nobody has given a name.

Opening a server asks for nothing, so `nickname` is usually empty until the
customer signs in with Google. Every Korean string that used to read
"{nickname}님" would otherwise render as "님" or "undefined님", which looks
like a bug to the one person who reads all of them — the owner, in Discord.

Two audiences need different fallbacks, which is why this is not one constant:
the owner needs to tell servers apart, and the customer already knows which
server they are looking at.
"""

import sqlite3

# Deliberately not "손님": these get interpolated in front of 님, and "손님님"
# reads worse than no name at all.
UNNAMED_OWNER_LABEL = "이름 미등록"
UNNAMED_CUSTOMER_LABEL = "내 서버"


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
