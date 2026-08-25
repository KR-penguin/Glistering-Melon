"""Free, unauthenticated Mojang username lookup.

Catches a typo'd Java username at signup. The server is open to everyone, so
a bad name no longer locks anybody out — but it is still what makes the
customer the operator of their own server, and what identifies them to the
operator, so a silent typo is worth one GET to avoid.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

MOJANG_API = "https://api.mojang.com/users/profiles/minecraft/{username}"


async def is_valid_java_username(username: str) -> bool:
    """True only when this is definitely a real Java Edition account.

    Fails CLOSED on purpose. The answer no longer gates signup — it only
    decides whether to make the customer an operator — so an uncertain answer
    should mean "skip the bonus", never "op a name that may not exist".
    """
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(MOJANG_API.format(username=username))
        if resp.status_code == 200:
            return True
        if resp.status_code != 404:
            logger.warning("mojang lookup returned %s for %r", resp.status_code, username)
        return False
    except httpx.HTTPError:
        logger.warning("mojang lookup failed for %r", username)
        return False
