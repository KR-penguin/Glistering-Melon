"""Per-version Minecraft command reference, retrieved via SQLite FTS5.

Deliberately not a vector store for v1: commands are short, keyword-heavy
text, and FTS5 needs zero new infrastructure (same sqlite3 stdlib module
already used everywhere else). Docs are curated offline in docs/<version>.md,
not scraped live, so retrieval stays deterministic. Upgrade path (not v1):
swap for embeddings if keyword search proves too brittle for phrasing.

Version handling is the load-bearing part here. Minecraft command syntax
changes between versions, so every result carries explicit provenance: which
version's reference it actually came from, and whether that was an exact
match for the version requested. A near-miss is reported as a near-miss
rather than silently passed off as authoritative — the agent is instructed
to verify before running anything destructive on an inexact match.

To add a version: drop a `docs/<version>.md` file using `## <command>`
headings. No code change needed.
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
        entries.append((command, text[start:end].strip()))
    return entries


def _build_index() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE VIRTUAL TABLE command_docs USING fts5(mc_version, command, body)")
    for doc_path in sorted(DOCS_DIR.glob("*.md")):
        for command, body in _parse_doc(doc_path.read_text(encoding="utf-8")):
            conn.execute(
                "INSERT INTO command_docs (mc_version, command, body) VALUES (?, ?, ?)",
                (doc_path.stem, command, body),
            )
    conn.commit()
    return conn


def get_index() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _build_index()
    return _conn


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
        SELECT command, body FROM command_docs
        WHERE mc_version = ? AND command_docs MATCH ?
        ORDER BY rank LIMIT ?
        """,
        (source_version, _fts_query(query), limit),
    ).fetchall()

    if exact:
        note = f"Exact reference for {source_version}."
    else:
        note = (
            f"No bundled reference for {mc_version!r}. These entries come from "
            f"{source_version} and may be wrong for the running version. "
            f"Verify syntax before running anything destructive. "
            f"Available references: {', '.join(available_versions())}."
        )

    return {
        "requested_version": mc_version,
        "source_version": source_version,
        "exact_version_match": exact,
        "note": note,
        "results": [{"command": r[0], "body": r[1]} for r in rows],
    }
