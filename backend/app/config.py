from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator(
        "host_ram_mb_budget",
        "host_storage_gb_budget",
        "max_concurrent_servers",
        "mc_port_range_start",
        "mc_port_range_end",
        "payment_grace_minutes",
        mode="before",
    )
    @classmethod
    def _blank_means_default(cls, v, info: ValidationInfo):
        """Treat an unset key in .env as "use the default", not a parse error.

        A fresh deploy legitimately leaves optional credentials blank (e.g.
        DISCORD_OPS_CHANNEL_ID= before the bot exists). Pydantic would
        otherwise reject "" as an invalid int and crash-loop the container,
        taking the whole provisioning pipeline down over a feature that is
        supposed to degrade gracefully.
        """
        if v is None or (isinstance(v, str) and not v.strip()):
            return cls.model_fields[info.field_name].default
        return v

    # "local" (OpenAI-compatible server, no per-call cost) or "anthropic".
    llm_provider: str = "local"
    local_llm_base_url: str = "http://gm-llm:8080/v1"
    local_llm_model: str = "Qwen3.5-4B-Q4_K_M"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    discord_webhook_url: str = ""

    # Shared secret for /admin. One operator, one token — replace with real
    # accounts if a second person ever needs access.
    admin_token: str = ""

    # Public invite to the support server. Shown to customers wherever payment
    # can go wrong — deposits arrive without the code often enough (customer
    # forgets, a parent sends instead, the bank truncates the name) that there
    # has to be somewhere to ask. Blank hides every link rather than shipping
    # a dead one.
    discord_invite_url: str = ""

    host_ram_mb_budget: int = 24576
    host_storage_gb_budget: int = 400
    max_concurrent_servers: int = 6

    default_mc_version: str = "1.21.1"
    default_mc_type: str = "PAPER"
    mc_port_range_start: int = 25565
    mc_port_range_end: int = 25664

    data_root: str = "./data"
    cors_allow_origin: str = "*"

    # Minutes an unpaid server stays up before the sweeper stops it (never
    # deletes). See app/billing.py for why the failure mode matters more than
    # the length. Env-tunable so it can be changed without a redeploy.
    payment_grace_minutes: int = 60

    # How the backend refers to itself in links sent to customers. Must become
    # the real public URL once the box is reachable from outside the LAN.
    public_base_url: str = "http://192.168.45.100:8000"

    # Host/port players use to connect. Defaults to the LAN address for now.
    minecraft_public_host: str = "192.168.45.100"

    @property
    def db_path(self) -> str:
        return str(Path(self.data_root) / "glistering_melon.db")

    @property
    def worlds_root(self) -> Path:
        return Path(self.data_root) / "worlds"


settings = Settings()
