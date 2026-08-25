"""Environment handed to each Minecraft container.

These are product decisions, not implementation details — a silent flip of
any of them changes who can play on a customer's server.
"""

import pytest

from app.docker_ctl import _build_env


def env_for(**overrides):
    kwargs = dict(
        ram_mb=2048,
        mc_version="1.21.1",
        mc_type="PAPER",
        nickname="Steve",
        mc_username="Steve",
        rcon_password="secret",
        modpack_key=None,
    )
    kwargs.update(overrides)
    return _build_env(**kwargs)


def test_server_is_open_to_everyone_by_default():
    """The founder asked for no whitelist — anyone can join."""
    assert env_for()["ENABLE_WHITELIST"] == "false"
    assert "WHITELIST" not in env_for()


def test_a_real_java_account_is_made_operator():
    """With the whitelist off this is the only thing that distinguishes the
    person paying for the server from any other player."""
    assert env_for(nickname="Notch", mc_username="Notch")["OPS"] == "Notch"


def test_a_display_name_that_is_not_a_java_account_is_not_opped():
    """The field is "이름 (닉네임 가능)" now, so it is often something like
    "김철수". Opping a name nobody owns would just confuse the server."""
    env = env_for(nickname="김철수", mc_username=None)
    assert "OPS" not in env


def test_ops_uses_the_verified_account_not_the_display_name():
    env = env_for(nickname="멜론이", mc_username="jeb_")
    assert env["OPS"] == "jeb_"


def test_eula_accepted_and_rcon_enabled():
    env = env_for()
    assert env["EULA"] == "TRUE"
    assert env["ENABLE_RCON"] == "true"
    assert env["RCON_PASSWORD"] == "secret"


def test_memory_matches_the_tier():
    assert env_for(ram_mb=6144)["MEMORY"] == "6144M"


def test_plain_server_uses_the_configured_type():
    env = env_for(mc_type="PAPER")
    assert env["TYPE"] == "PAPER"
    assert "MODRINTH_MODPACK" not in env


def test_modpack_switches_type_and_pins_the_project():
    env = env_for(modpack_key="cobblemon")
    assert env["TYPE"] == "MODRINTH"
    assert env["MODRINTH_MODPACK"] == "cobblemon-fabric"
    # VERSION still narrows which Minecraft version the pack resolves to.
    assert env["VERSION"] == "1.21.1"


def test_unknown_modpack_key_raises_rather_than_booting_a_wrong_server():
    with pytest.raises(KeyError):
        env_for(modpack_key="does-not-exist")
