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
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(MOJANG_API.format(username=username))
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        # Mojang API hiccup (429/5xx) — don't block a real applicant on our
        # dependency being flaky; let it through and let provisioning fail
        # loudly later if the name really is bad.
        logger.warning("mojang lookup returned %s for %s", resp.status_code, username)
        return True
    except httpx.HTTPError:
        logger.warning("mojang lookup failed for %s, allowing through", username)
        return True
