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


def _pick_source_version(requested: str | None) -> tuple[str | None, bool]:
    """Return (version_to_search, is_exact_match)."""
    versions = available_versions()
    if not versions:
        return None, False
    if requested in versions:
        return requested, True
    if not requested:
        return versions[-1], False

    target = _version_tuple(requested)
    # Prefer the newest reference at or below the requested version — command
    # sets grow over time, so an older reference is less likely to describe
    # syntax that does not exist yet on the requested version.
    at_or_below = [v for v in versions if _version_tuple(v) <= target]
    if at_or_below:
        return at_or_below[-1], False
    return versions[0], False


def _fts_query(query: str) -> str:
    terms = [t for t in re.findall(r"\w+", query) if t]
    if not terms:
        return '""'
    return " OR ".join(terms)


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
        ORDER BY rank LIMIT ?
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
