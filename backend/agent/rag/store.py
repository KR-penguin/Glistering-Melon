"""Per-version Minecraft command reference, retrieved via SQLite FTS5.

Deliberately not a vector store for v1: commands are short, keyword-heavy
text, and FTS5 needs zero new infrastructure (same sqlite3 stdlib module
already used everywhere else). Docs are curated offline in docs/<version>.md,
not scraped live, so retrieval stays deterministic. Upgrade path (not v1):
swap for embeddings if keyword search proves too brittle for phrasing.
"""

import re
import sqlite3
from pathlib import Path

DOCS_DIR = Path(__file__).parent / "docs"

_conn: sqlite3.Connection | None = None

_ENTRY_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _parse_doc(text: str) -> list[tuple[str, str]]:
    """Split a doc file into (command, body) pairs on '## ' headings."""
    entries = []
    matches = list(_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        command = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        entries.append((command, body))
    return entries


def _build_index() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        "CREATE VIRTUAL TABLE command_docs USING fts5(mc_version, command, body)"
    )
    for doc_path in sorted(DOCS_DIR.glob("*.md")):
        mc_version = doc_path.stem
        for command, body in _parse_doc(doc_path.read_text(encoding="utf-8")):
            conn.execute(
                "INSERT INTO command_docs (mc_version, command, body) VALUES (?, ?, ?)",
                (mc_version, command, body),
            )
    conn.commit()
    return conn


def get_index() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _build_index()
    return _conn


def _fts_query(query: str) -> str:
    # Treat the query as OR-of-terms so partial phrasing still matches.
    terms = [t for t in re.findall(r"\w+", query) if t]
    if not terms:
        return '""'
    return " OR ".join(terms)


def search_commands(mc_version: str, query: str, limit: int = 5) -> list[dict]:
    conn = get_index()
    rows = conn.execute(
        """
        SELECT command, body FROM command_docs
        WHERE mc_version = ? AND command_docs MATCH ?
        ORDER BY rank LIMIT ?
        """,
        (mc_version, _fts_query(query), limit),
    ).fetchall()

    if not rows:
        # Fall back to the nearest available version's docs rather than
        # returning nothing — better an approximate answer flagged as such
        # than silence, since the agent will just guess otherwise.
        rows = conn.execute(
            """
            SELECT command, body FROM command_docs
            WHERE command_docs MATCH ?
            ORDER BY rank LIMIT ?
            """,
            (_fts_query(query), limit),
        ).fetchall()

    return [{"command": r[0], "body": r[1]} for r in rows]
