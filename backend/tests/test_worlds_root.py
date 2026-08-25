"""Worlds live on their own disk, separate from the database.

Worlds are large and go on the HDD; the SQLite database is tiny and stays on
the SSD. Getting this wrong is quiet and expensive — worlds land on the wrong
volume and nobody notices until it fills up.
"""

from pathlib import Path

from app.config import Settings


def test_defaults_to_a_worlds_dir_under_data_root():
    """Unset override keeps the original layout, so an existing deploy that
    has not set the new variable does not silently relocate its worlds."""
    s = Settings(_env_file=None, data_root="/srv/minecraft/data", worlds_root_override="")
    assert s.worlds_root == Path("/srv/minecraft/data/worlds")


def test_override_moves_worlds_off_the_database_disk():
    s = Settings(
        _env_file=None,
        data_root="/srv/minecraft/data",
        worlds_root_override="/mnt/hdd/minecraft/worlds",
    )
    assert s.worlds_root == Path("/mnt/hdd/minecraft/worlds")
    # The database must NOT follow the worlds onto the HDD.
    assert s.db_path == str(Path("/srv/minecraft/data") / "glistering_melon.db")


def test_override_reads_from_env(monkeypatch):
    monkeypatch.setenv("WORLDS_ROOT_OVERRIDE", "/mnt/hdd/minecraft/worlds")
    assert Settings(_env_file=None).worlds_root == Path("/mnt/hdd/minecraft/worlds")


def test_a_blank_override_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("WORLDS_ROOT_OVERRIDE", "")
    s = Settings(_env_file=None, data_root="/srv/minecraft/data")
    assert s.worlds_root == Path("/srv/minecraft/data/worlds")
