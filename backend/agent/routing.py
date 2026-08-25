"""Deterministic Korean intent hints, applied before the model sees a message.

The safety guarantee lives in agent/rcon_client.assert_allowed — a lifecycle
verb cannot reach RCON no matter what the model decides. This module is the
cheaper layer in front of it: for phrasings that are unambiguous in Korean, say
so outright rather than paying a slow local-model turn to discover it and a
second turn to recover from the refusal.

Only genuinely unambiguous phrases belong here. Anything that could reasonably
mean either family (리로드 — plugin reload or container restart? 서버 정지 —
`/stop` or `docker stop`?) is deliberately left out, because forcing a guess on
those is worse than letting the model ask.
"""

import re

LIFECYCLE = "lifecycle"
INGAME = "ingame"

# Container-level: turning the server off/on, recovering an unresponsive one.
_LIFECYCLE_RE = re.compile(
    r"(재시작|재부팅|리스타트|껐다\s*켜|끄고\s*켜|다시\s*켜|다시\s*시작"
    r"|restart|reboot|컨테이너|도커|docker)",
    re.IGNORECASE,
)

# In-game console: things only the running game can do.
_INGAME_RE = re.compile(
    r"(밴|차단|추방|킥|화이트리스트|화리|옵\s*줘|op\s|관리자\s*권한"
    r"|게임룰|난이도|시간\s*설정|날씨"
    r"|ban|kick|whitelist|gamerule|difficulty)",
    re.IGNORECASE,
)

_HINTS = {
    LIFECYCLE: (
        "[라우팅 힌트] 이 요청은 서버를 껐다 켜는 컨테이너 단위 작업입니다. "
        "restart_my_server / start_my_server / stop_my_server 중 하나를 쓰세요. "
        "RCON 명령으로는 처리할 수 없습니다."
    ),
    INGAME: (
        "[라우팅 힌트] 이 요청은 게임 안에서 실행하는 콘솔 명령입니다. "
        "명령어 문법을 확인한 뒤 RCON으로 실행하세요."
    ),
}

_OPS_HINTS = {
    LIFECYCLE: (
        "[routing hint] This is a container-level lifecycle request. Use "
        "start_server / stop_server / restart_server, never an RCON command."
    ),
    INGAME: (
        "[routing hint] This is an in-game console command. Look up the syntax "
        "for the server's actual version, then run it via run_rcon_command."
    ),
}


def classify(message: str) -> str | None:
    """Return LIFECYCLE, INGAME, or None when the phrasing is not decisive."""
    lifecycle = bool(_LIFECYCLE_RE.search(message))
    ingame = bool(_INGAME_RE.search(message))
    if lifecycle and not ingame:
        return LIFECYCLE
    if ingame and not lifecycle:
        return INGAME
    # Both or neither: genuinely ambiguous, let the model work it out.
    return None


def hint_for(message: str, *, owner: bool = False) -> str:
    """A prefix to prepend to the model's input, or '' when undecidable."""
    family = classify(message)
    if family is None:
        return ""
    return (_OPS_HINTS if owner else _HINTS)[family] + "\n\n"
