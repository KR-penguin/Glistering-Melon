"""Pull the command reference from minecraft.wiki instead of writing it by hand.

Hand-written docs were the weak point: they came from a model's memory, so they
could be wrong in exactly the way the reference exists to prevent, and they
froze at whatever version they were written for. Between 1.21.1 and 26.2 that
mattered — /time was rebuilt around world clocks, /team modify colour stopped
accepting `darkpurple`, and /worldborder's time argument switched from seconds
to ticks. A reference written from memory would have confidently served all
three wrong.

minecraft.wiki's command pages render the CURRENT release, so a fetch is a
snapshot of live syntax attributed to a real URL. The fetched snapshot is
committed to the repo, which means:

  * no network call on the request path — retrieval stays deterministic and
    fast, and the agent works when the wiki is down,
  * refreshing is a deliberate act (`python -m agent.rag.ingest`) that shows up
    as a reviewable diff rather than silently changing what the agent believes.

Run it after a Minecraft release, and read the diff before committing.
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

WIKI_API = "https://minecraft.wiki/api.php"
SNAPSHOT_PATH = Path(__file__).parent / "snapshot.json"

# Commands an ops agent actually issues. Deliberately a fixed list rather than
# scraping every command on the wiki: the agent should not be tempted into
# /fill or /setblock on a customer's world, and a smaller tool surface measurably
# improves small-model routing.
OPS_COMMANDS = [
    "ban", "ban-ip", "pardon", "pardon-ip", "kick", "op", "deop",
    "whitelist", "list", "say", "tell", "gamerule", "difficulty",
    "time", "weather", "save-all", "stop", "teleport", "gamemode",
    "give", "effect", "kill", "setworldspawn", "seed", "defaultgamemode",
    "fetchprofile", "worldborder", "team",
]

# Several commands are documented as a section of another command's page, or
# under an alias. Verified against the wiki — a wrong title here silently drops
# a command from the reference, which is how the agent ends up guessing.
PAGE_OVERRIDES = {
    "ban-ip": "Commands/ban",
    "pardon-ip": "Commands/pardon",
    "tell": "Commands/msg",        # /msg is the canonical page; /tell and /w alias it
    "save-all": "Commands/save",   # covers save-all, save-off and save-on
}

USER_AGENT = "GlisteringMelon/1.0 (Minecraft hosting ops agent; contact kkwonsc@gmail.com)"

_SECTION_RE = re.compile(r"^={2,4}\s*(.+?)\s*={2,4}\s*$", re.MULTILINE)


def _page_title(command: str) -> str:
    return PAGE_OVERRIDES.get(command, f"Commands/{command}")


def fetch_extract(client: httpx.Client, title: str) -> str | None:
    resp = client.get(
        WIKI_API,
        params={
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "titles": title,
            "format": "json",
            "formatversion": 2,
        },
    )
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    return pages[0].get("extract") or None


def split_sections(text: str) -> list[tuple[str, str]]:
    """Split a plain-text wiki extract into (heading, body) pairs."""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return [("", text.strip())]

    sections = [("", text[: matches[0].start()].strip())]
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1), text[m.end() : end].strip()))
    return [(h, b) for h, b in sections if b]


def extract_syntax(text: str) -> str:
    """Collect every Syntax block on the page.

    A page like Commands/ban documents several commands (ban, ban-ip), each
    with its own Syntax section, so all of them are kept.
    """
    blocks = [body for heading, body in split_sections(text) if heading.lower() == "syntax"]
    return "\n".join(blocks).strip()


def build_entry(command: str, text: str, title: str) -> dict:
    sections = split_sections(text)
    lead = next((body for heading, body in sections if not heading), "")
    return {
        "command": command,
        "syntax": extract_syntax(text),
        "description": " ".join(lead.split())[:1200],
        # Full text kept so full-text search can match on argument names and
        # caveats, not just the syntax line.
        "body": text[:12000],
        "source": f"https://minecraft.wiki/w/{title.replace(' ', '_')}",
    }


def ingest(version: str, commands: list[str] | None = None, delay: float = 0.4) -> dict:
    commands = commands or OPS_COMMANDS
    entries, failed = [], []

    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for command in commands:
            title = _page_title(command)
            try:
                text = fetch_extract(client, title)
            except httpx.HTTPError as exc:
                logger.warning("fetch failed for %s: %s", command, exc)
                failed.append(command)
                continue

            if not text:
                logger.warning("no page for %s (%s)", command, title)
                failed.append(command)
                continue

            entries.append(build_entry(command, text, title))
            # The wiki is a volunteer-run service; do not hammer it.
            time.sleep(delay)

    return {
        "mc_version": version,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "minecraft.wiki",
        "note": (
            "Fetched from minecraft.wiki, which documents the CURRENT release. "
            "Re-run agent.rag.ingest after a Minecraft release and review the diff."
        ),
        "failed": failed,
        "commands": entries,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Fetch the Minecraft command reference.")
    parser.add_argument("--version", required=True, help="Minecraft version this snapshot documents, e.g. 26.2")
    parser.add_argument("--out", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--command", action="append", help="Fetch only these commands (repeatable)")
    args = parser.parse_args()

    data = ingest(args.version, args.command)
    if not data["commands"]:
        print("ERROR: nothing fetched; leaving the existing snapshot alone.", file=sys.stderr)
        return 1

    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}: {len(data['commands'])} commands for {args.version}")
    if data["failed"]:
        print(f"FAILED: {', '.join(data['failed'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
