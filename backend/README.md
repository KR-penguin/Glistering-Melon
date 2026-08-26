# Glistering Melon backend

Deterministic provisioning pipeline + a Minecraft ops AI agent, meant to run
as a single Docker Compose stack on one box — your home Ubuntu Server,
reached over SSH for deploy/admin. Nothing in this backend talks to Docker
over SSH; it runs *on* the box and uses the local `docker.sock`.

## What this does

- `POST /api/applications` (nickname, email, tier, optional modpack) checks
  spare RAM/storage against a configured budget, and either provisions a
  real `itzg/minecraft-server` container or tells the applicant + your
  Discord there's no room right now.
- A Discord bot in one ops channel lets you type things like
  "srv-1 서버 껐다 켜줘" or "철수 밴 시켜줘" — Claude looks up the right
  command for that server's Minecraft version and runs it. Restart/stop/
  start go through Docker (works even if the game is hung); everything
  else goes through RCON. No confirmation step — it acts immediately.
- Crash auto-restart is plain Docker (`restart: unless-stopped`), not the
  AI — free, instant, and a plain Discord message tells you when it fires.

## Not built yet (see the project plan for the full list)

Hourly backups, plugin-conflict rollback, automatic RAM tuning, CurseForge
modpacks (Modrinth only for now), per-customer Discord threads, an admin
web UI, TLS/reverse proxy. The landing page (`public/index.html`) still posts straight to the
Discord webhook — it is **not** wired to this backend yet; do that only
once this is deployed and reachable (see the plan doc).

## Deploy on the Ubuntu box

Prerequisites: Docker Engine + the Compose plugin (`docker compose version`
should work). If not installed:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out/in after this
```

Then, over SSH:

```bash
git clone https://github.com/KR-penguin/Glistering-Melon.git
cd Glistering-Melon/backend
cp .env.example .env
nano .env   # fill in the credentials below
docker compose up -d --build
```

Credentials you need before it's fully live (the API + provisioning work
without all of them, but each missing one disables its feature and logs a
warning on startup rather than crashing):

- **ANTHROPIC_API_KEY** — console.anthropic.com. This is the one part of
  the pipeline with a real per-use cost (see `.env.example`).
- **DISCORD_WEBHOOK_URL** — the same one already in the landing page.
- **DISCORD_BOT_TOKEN** + **DISCORD_OPS_CHANNEL_ID** — Discord Developer
  Portal → New Application → Bot → copy token; invite it to your server
  with "Send Messages" + "Read Message History"; right-click the ops
  channel → Copy Channel ID (enable Developer Mode in Discord settings
  first) for the ID.
- **GMAIL_ADDRESS** + **GMAIL_APP_PASSWORD** — enable 2FA on the Google
  account, then myaccount.google.com → Security → App passwords.

## Verify it's actually working

```bash
curl -X POST localhost:8000/api/applications \
  -H "Content-Type: application/json" \
  -d '{"nickname":"testuser","email":"you@example.com","tier":"small"}'

docker ps                      # a new gm-<id> container should appear
docker logs -f gm-<id>         # watch for EULA accept -> "Done"
```

Then in the Discord ops channel: `list_servers`-style natural language like
"지금 서버 목록 보여줘", then try an ops command against the test server.

## Running the tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Most tests mock Docker/RCON and need no live server. The two tests in
`tests/test_agent_tools.py` marked as needing `ANTHROPIC_API_KEY` are the
real acceptance check for "the agent must act from outside the server" —
they assert a restart request never goes through RCON and a ban request
never touches Docker. They're skipped automatically without a key.
