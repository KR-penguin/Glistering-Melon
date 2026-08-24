"""Curated modpack catalog.

Deliberately NOT free-text: an applicant's raw input never reaches
docker_ctl's env vars directly. Add entries here as you decide which
modpacks to actually offer — each key becomes a <select> option on the
site once the Landing.dc.html diff ships.

Modrinth only for v1 (no CurseForge API key). `project` is the Modrinth
project slug, `loader` is optional (most modpacks imply their own loader
from the .mrpack manifest — only set it if a specific slug needs it).
"""

from typing import NamedTuple


class ModpackSpec(NamedTuple):
    name: str
    project: str
    version_type: str = "release"  # release | beta | alpha
    loader: str | None = None


MODPACK_CATALOG: dict[str, ModpackSpec] = {
    # Verified real, actively maintained Modrinth modpack — seeded as a
    # working example. Add more entries the same way; each needs its
    # Modrinth project slug (from the modpack's modrinth.com/modpack/<slug>
    # URL), nothing else is fabricated here.
    "cobblemon": ModpackSpec(name="Cobblemon", project="cobblemon-fabric"),
}


def is_valid_modpack_key(key: str | None) -> bool:
    return key is None or key in MODPACK_CATALOG
