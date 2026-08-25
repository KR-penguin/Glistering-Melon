"""The command reference is fetched documentation, not remembered syntax.

Between 1.21.1 and 26.2 the game rebuilt /time around world clocks, made
/team modify color reject `darkpurple`, and switched /worldborder's time
argument from seconds to ticks. A reference written from memory would have
served all three wrong, which is the whole reason the agent consults one.
"""

import json

import pytest

from agent.rag import ingest, store


@pytest.fixture(autouse=True)
def fresh_index():
    store.reset_index()
    yield
    store.reset_index()


# --- the committed snapshot ------------------------------------------------


def snapshot() -> dict:
    return json.loads(store.SNAPSHOT_PATH.read_text(encoding="utf-8"))


def test_snapshot_exists_and_covers_the_ops_commands():
    data = snapshot()
    fetched = {c["command"] for c in data["commands"]}
    missing = set(ingest.OPS_COMMANDS) - fetched
    assert not missing, f"missing from the snapshot: {sorted(missing)}"


def test_snapshot_records_no_fetch_failures():
    assert snapshot()["failed"] == [], "a failed page silently drops a command"


def test_every_entry_is_attributed_to_a_real_wiki_url():
    for entry in snapshot()["commands"]:
        assert entry["source"].startswith("https://minecraft.wiki/w/"), entry["command"]


def test_entries_carry_actual_syntax():
    for entry in snapshot()["commands"]:
        assert entry["syntax"].strip(), f"{entry['command']} has no syntax block"


# --- the changes a from-memory reference would have got wrong --------------


def test_time_reference_has_the_26_x_clock_syntax():
    """/time was rebuilt in 26.1 around world clocks."""
    out = store.search_commands("26.2", "time set clock")
    body = " ".join(r["body"] for r in out["results"])
    assert "of <clock>" in body


def test_worldborder_reference_is_present():
    out = store.search_commands("26.2", "worldborder size time")
    assert any(r["command"] == "worldborder" for r in out["results"])


def test_team_reference_is_present():
    out = store.search_commands("26.2", "team modify color")
    assert any(r["command"] == "team" for r in out["results"])


# --- source attribution ----------------------------------------------------


def test_fetched_entries_are_marked_as_wiki_sourced():
    out = store.search_commands("26.2", "ban player")
    assert out["results"]
    assert all(r["source"].startswith("https://minecraft.wiki") for r in out["results"])


def test_handwritten_entries_are_flagged_as_lower_confidence():
    out = store.search_commands("1.21.1", "ban player")
    assert out["results"]
    assert all(r["source"] == store.HANDWRITTEN for r in out["results"])
    assert "hand-written" in out["note"]


def test_wiki_sourced_results_are_not_flagged_as_handwritten():
    out = store.search_commands("26.2", "ban player")
    assert "hand-written" not in out["note"]


# --- version ordering under the calendar scheme ----------------------------


def test_calendar_versions_sort_newer_than_1_21():
    """26.x replaced what would have been 1.22, so it is NEWER despite the
    smaller-looking major number. Getting this backwards would make the agent
    answer a 26.2 server from a 1.20 reference."""
    versions = store.available_versions()
    assert versions.index("26.2") > versions.index("1.21.1")
    assert store._version_tuple("26.2") > store._version_tuple("1.21.11")


def test_26_2_is_an_exact_match():
    out = store.search_commands("26.2", "ban player")
    assert out["exact_version_match"] is True
    assert out["source_version"] == "26.2"


def test_an_unbundled_26_x_version_falls_back_and_says_so():
    out = store.search_commands("26.3", "ban player")
    assert out["exact_version_match"] is False
    assert out["source_version"] == "26.2", "should use the newest at or below"
    assert "26.3" in out["note"]


# --- parsing ---------------------------------------------------------------


def test_split_sections_handles_wiki_headings():
    text = "lead text\n\n== ban ==\n\nintro\n\n=== Syntax ===\nban <targets>\n"
    sections = dict(ingest.split_sections(text))
    assert sections[""] == "lead text"
    assert sections["Syntax"] == "ban <targets>"


def test_extract_syntax_collects_every_syntax_block():
    """Commands/ban documents ban and ban-ip, each with its own Syntax."""
    text = "=== Syntax ===\nban <targets>\n\n== ban-ip ==\n=== Syntax ===\nban-ip <target>\n"
    out = ingest.extract_syntax(text)
    assert "ban <targets>" in out
    assert "ban-ip <target>" in out


def test_page_title_overrides_are_used():
    assert ingest._page_title("tell") == "Commands/msg"
    assert ingest._page_title("save-all") == "Commands/save"
    assert ingest._page_title("ban") == "Commands/ban"
