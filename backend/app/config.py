from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    discord_webhook_url: str = ""
    discord_bot_token: str = ""
    discord_ops_channel_id: int = 0

    gmail_address: str = ""
    gmail_app_password: str = ""

    host_ram_mb_budget: int = 24576
    host_storage_gb_budget: int = 400
    max_concurrent_servers: int = 6

    default_mc_version: str = "1.21.1"
    default_mc_type: str = "PAPER"
    mc_port_range_start: int = 25565
    mc_port_range_end: int = 25664

    data_root: str = "./data"
    cors_allow_origin: str = "*"

    @property
    def db_path(self) -> str:
        return str(Path(self.data_root) / "glistering_melon.db")

    @property
    def worlds_root(self) -> Path:
        return Path(self.data_root) / "worlds"


settings = Settings()
