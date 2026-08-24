import sqlite3
from typing import NamedTuple

from app.config import settings

TIER_SPECS = {
    "small": {"ram_mb": 2048, "storage_gb": 15},
    "friends": {"ram_mb": 6144, "storage_gb": 50},
    "community": {"ram_mb": 16384, "storage_gb": 150},
}

ACTIVE_STATUSES = ("provisioning", "running")


class CapacityResult(NamedTuple):
    ok: bool
    tier: str
    used_ram_mb: int
    used_storage_gb: int
    ram_budget_mb: int
    storage_budget_gb: int
    active_count: int
    max_concurrent: int


def check_capacity(tier: str, conn: sqlite3.Connection) -> CapacityResult:
    if tier not in TIER_SPECS:
        raise ValueError(f"unknown tier: {tier}")

    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(ram_mb), 0) AS ram, COALESCE(SUM(storage_gb), 0) AS storage,
               COUNT(*) AS active_count
        FROM servers WHERE status IN ({placeholders})
        """,
        ACTIVE_STATUSES,
    ).fetchone()

    used_ram = row["ram"]
    used_storage = row["storage"]
    active_count = row["active_count"]

    spec = TIER_SPECS[tier]
    fits_ram = used_ram + spec["ram_mb"] <= settings.host_ram_mb_budget
    fits_storage = used_storage + spec["storage_gb"] <= settings.host_storage_gb_budget
    fits_count = active_count < settings.max_concurrent_servers

    return CapacityResult(
        ok=fits_ram and fits_storage and fits_count,
        tier=tier,
        used_ram_mb=used_ram,
        used_storage_gb=used_storage,
        ram_budget_mb=settings.host_ram_mb_budget,
        storage_budget_gb=settings.host_storage_gb_budget,
        active_count=active_count,
        max_concurrent=settings.max_concurrent_servers,
    )
