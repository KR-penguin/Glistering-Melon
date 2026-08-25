"""Per-version Minecraft command reference, retrieved via SQLite FTS5.

Two sources, and the difference between them is reported to the agent:

  * `snapshot.json` — fetched from minecraft.wiki by agent/rag/ingest.py.
    This is the authoritative one. It carries real syntax attributed to a real
    URL, so it does not drift from what the game actually accepts.
  * `docs/<version>.md` — hand-written notes for older versions. Kept only
    because nothing else covers them, and marked as hand-written so the agent
    can weigh them accordingly.

That distinction is not cosmetic. Between 1.21.1 and 26.2 the game rebuilt
`/time` around world clocks, made `/team modify color` reject `darkpurple`,
and switched `/worldborder`'s time argument from seconds to ticks. A reference
written from memory would have served all three confidently and wrongly.

Version handling is the other load-bearing part: every result says which
version's reference answered and whether that exactly matched the version
asked for, so an approximate answer is never mistaken for an authoritative
one. Note that 26.x sorts NEWER than 1.21.x despite the smaller major number —
the calendar scheme (year.drop) began in 2026 and replaced 1.22.

Refresh after a Minecraft release:
    python -m agent.rag.ingest --version 26.3
"""

import json
import re
import sqlite3
from pathlib import Path

DOCS_DIR = Path(__file__).parent / "docs"
SNAPSHOT_PATH = Path(__file__).parent / "snapshot.json"

WIKI = "minecraft.wiki"
HANDWRITTEN = "hand-written"

_conn: sqlite3.Connection | None = None

_ENTRY_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _parse_doc(text: str) -> list[tuple[str, str]]:
    """Split a hand-written doc into (command, body) pairs on '## ' headings."""
    entries = []
    matches = list(_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries.append((m.group(1).strip(), text[m.end() : end].strip()))
    return entries


def _load_snapshot() -> tuple[str, list[dict]] | None:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not data.get("commands"):
        return None
    return data.get("mc_version", "unknown"), data["commands"]


def _build_index() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        "CREATE VIRTUAL TABLE command_docs USING fts5(mc_version, command, body, source)"
    )

    snapshot = _load_snapshot()
    if snapshot:
        version, commands = snapshot
        for entry in commands:
            body = "\n".join(
                part for part in (
                    f"Syntax:\n{entry.get('syntax', '')}".strip(),
                    entry.get("description", ""),
                    entry.get("body", ""),
                ) if part
            )
            conn.execute(
                "INSERT INTO command_docs (mc_version, command, body, source) VALUES (?, ?, ?, ?)",
                (version, entry["command"], body, entry.get("source") or WIKI),
            )

    for doc_path in sorted(DOCS_DIR.glob("*.md")):
        for command, body in _parse_doc(doc_path.read_text(encoding="utf-8")):
            conn.execute(
                "INSERT INTO command_docs (mc_version, command, body, source) VALUES (?, ?, ?, ?)",
                (doc_path.stem, command, body, HANDWRITTEN),
            )

    conn.commit()
    return conn


def get_index() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _build_index()
    return _conn


def reset_index() -> None:
    """Test hook — forces a rebuild so fixtures take effect."""
    global _conn
    _conn = None


def available_versions() -> list[str]:
    rows = get_index().execute("SELECT DISTINCT mc_version FROM command_docs").fetchall()
    return sorted((r[0] for r in rows), key=_version_tuple)


def _version_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(v).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _is_concrete_version(v: str | None) -> bool:
    """Does this name a specific version, rather than a moving pointer?

    Servers are provisioned with VERSION=LATEST, which is not a version at
    all. Treating it as one parsed to (0,) and selected the OLDEST reference —
    so a brand-new 26.2 server would have been answered from 1.20.1.
    """
    return bool(v) and any(c.isdigit() for c in v)


def _pick_source_version(requested: str | None) -> tuple[str | None, bool]:
    """Return (version_to_search, is_exact_match)."""
    versions = available_versions()
    if not versions:
        return None, False
    if requested in versions:
        return requested, True
    if not _is_concrete_version(requested):
        # "LATEST", "", None — the newest reference is the best guess, and it
        # is reported as inexact so the agent knows it is not pinned.
        return versions[-1], False

    target = _version_tuple(requested)
    # Prefer the newest reference at or below the requested version — command
    # sets grow over time, so an older reference is less likely to describe
    # syntax that does not exist yet on the requested version.
    at_or_below = [v for v in versions if _version_tuple(v) <= target]
    if at_or_below:
        return at_or_below[-1], False
    # Requested version predates every reference we have.
    return versions[0], False


# The reference is in English; customers write Korean. Without this bridge a
# message like "화이트리스트에 친구 추가해줘" matches nothing and retrieval
# silently never fires — the feature would look implemented and do nothing.
# Substring matching on purpose, since Korean agglutinates particles onto the
# noun ("화이트리스트에", "밴을").
_KO_TERMS: list[tuple[str, str]] = [
    ("화이트리스트", "whitelist"), ("화리", "whitelist"), ("허용목록", "whitelist"),
    ("밴", "ban"), ("차단", "ban"), ("정지시", "ban"),
    ("언밴", "pardon"), ("밴해제", "pardon"), ("차단해제", "pardon"),
    ("킥", "kick"), ("추방", "kick"), ("내보내", "kick"),
    ("옵", "op"), ("관리자", "op"), ("운영자", "op"), ("권한", "op deop"),
    ("게임룰", "gamerule"), ("규칙", "gamerule"),
    ("난이도", "difficulty"),
    ("시간", "time"), ("낮", "time"), ("밤", "time"), ("새벽", "time"),
    ("날씨", "weather"), ("비", "weather"), ("맑", "weather"), ("천둥", "weather"),
    ("텔레포트", "teleport"), ("순간이동", "teleport"), ("이동시", "teleport"),
    ("게임모드", "gamemode"), ("크리에이티브", "gamemode creative"),
    ("서바이벌", "gamemode survival"), ("관전", "gamemode spectator"),
    ("아이템", "give"), ("지급", "give"), ("주기", "give"),
    ("효과", "effect"), ("포션", "effect"),
    ("저장", "save"), ("백업", "save"),
    ("접속", "list"), ("몇명", "list"), ("누가", "list"), ("목록", "list"),
    ("스폰", "setworldspawn"), ("시작지점", "setworldspawn"),
    ("시드", "seed"),
    ("월드보더", "worldborder"), ("경계", "worldborder"),
    ("팀", "team"),
    ("귓속말", "tell msg"), ("공지", "say"), ("전체메시지", "say"),
    ("프로필", "fetchprofile"), ("유", "fetchprofile"),
]


def _expand_query(query: str) -> list[str]:
    """Original terms plus English keywords implied by any Korean in them."""
    terms = [t for t in re.findall(r"\w+", query) if t]
    for korean, english in _KO_TERMS:
        if korean in query:
            terms.extend(english.split())
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(terms))


def _fts_query(query: str) -> str:
    terms = _expand_query(query)
    if not terms:
        return '""'
    # Quote each term: Korean fragments can otherwise be read as FTS5 syntax.
    return " OR ".join(f'"{t}"' for t in terms)


def reference_block(mc_version: str | None, query: str, limit: int = 3) -> str:
    """Reference text to inject into the model's context for this message.

    Retrieve-then-generate rather than a tool the model may call. As an
    optional tool the reference was only consulted when the model judged
    itself unsure — which is exactly when a model is least reliable, and a
    confident wrong answer skips the lookup entirely. Putting the documentation
    in front of it every turn removes that decision.

    Returns "" when nothing matches, so an unrelated message ("입금했어요")
    costs no context.
    """
    out = search_commands(mc_version, query, limit=limit)
    if not out["results"]:
        return ""

    lines = [f"[명령어 레퍼런스 — {out['source_version']} 기준]"]
    if not out["exact_version_match"]:
        lines.append(f"주의: {out['note']}")
    for r in out["results"]:
        lines.append(f"\n### {r['command']}  (출처: {r['source']})")
        lines.append(r["body"][:1800].strip())
    lines.append(
        "\n위 문법을 그대로 쓰세요. 여기에 없는 명령이 필요하면 lookup_command 로 더 찾아보고, "
        "그래도 확실하지 않으면 추측하지 말고 손님에게 물어보세요."
    )
    return "\n".join(lines) + "\n\n"


def search_commands(mc_version: str | None, query: str, limit: int = 5) -> dict:
    conn = get_index()
    source_version, exact = _pick_source_version(mc_version)

    if source_version is None:
        return {
            "requested_version": mc_version,
            "source_version": None,
            "exact_version_match": False,
            "note": "No command reference is bundled at all. Do not guess syntax.",
            "results": [],
        }

    rows = conn.execute(
        """
        SELECT command, body, source FROM command_docs
        WHERE mc_version = ? AND command_docs MATCH ?
        -- Weight the command NAME far above the body: /pardon's page discusses
        -- the ban list at length, so an unweighted search for "ban" ranked
        -- pardon above ban.  Columns: mc_version, command, body, source.
        ORDER BY bm25(command_docs, 0.0, 20.0, 1.0, 0.0) LIMIT ?
        """,
        (source_version, _fts_query(query), limit),
    ).fetchall()

    if exact:
        note = f"Reference for {source_version}."
    else:
        note = (
            f"No bundled reference for {mc_version!r}. These entries come from "
            f"{source_version} and may be wrong for the running version. "
            f"Verify before running anything destructive. "
            f"Available: {', '.join(available_versions())}."
        )

    results = [{"command": r[0], "body": r[1][:4000], "source": r[2]} for r in rows]
    if any(r["source"] == HANDWRITTEN for r in results):
        note += (
            " Some entries are hand-written notes rather than fetched documentation — "
            "treat their syntax as lower confidence."
        )

    return {
        "requested_version": mc_version,
        "source_version": source_version,
        "exact_version_match": exact,
        "note": note,
        "results": results,
    }
