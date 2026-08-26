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

    # Gemini on Vertex AI. Vertex authenticates with Application Default
    # Credentials, not an API key — GOOGLE_APPLICATION_CREDENTIALS points at a
    # service-account file, so nothing secret belongs in these three values.
    #
    # The Gemini Developer API would also reach these models with a single key
    # and no cloud project. It is deliberately not wired up: billing has to
    # land on the GCP invoice for the business, and a second way in would be a
    # second way to end up on a personal card by accident.
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    gemini_model: str = "gemini-3.7-flash"

    # Sign in with Google. Accounts have no passwords — see app/accounts.py.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""

    # Where Google sends the browser back. Separate from public_base_url
    # because Google only accepts public https origins and http://localhost —
    # a LAN address like http://192.168.45.100:8000 is refused at registration
    # time. Blank means "use public_base_url", which is correct once a real
    # domain is in place. Until then this points at localhost.
    oauth_redirect_base: str = ""

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

    # LATEST so new servers track the current release without a code change.
    # itzg resolves it at container start; the actually-running version is read
    # back from the logs by agent.tools.get_server_version, because this value
    # is only a provisioning-time request.
    default_mc_version: str = "LATEST"
    default_mc_type: str = "PAPER"
    mc_port_range_start: int = 25565
    mc_port_range_end: int = 25664

    data_root: str = "./data"

    # World data lives apart from the SQLite database so the two can sit on
    # different disks: worlds are large and go on the HDD, while the database
    # is tiny and stays on the SSD. Empty means "{data_root}/worlds".
    #
    # Must be an absolute HOST path when set, and docker-compose has to mount
    # it at the identical path inside the container — this backend asks the
    # host's Docker daemon to bind-mount world directories, and the daemon
    # resolves those paths on the host.
    worlds_root_override: str = ""
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
        return Path(self.worlds_root_override or (Path(self.data_root) / "worlds"))


settings = Settings()
