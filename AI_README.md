# AI_README — Glistering Melon

Written for an AI agent picking this project up cold. Human-facing operational
commands are in [RUNNING.md](RUNNING.md); this file is the *why*, plus the
traps that already cost real debugging time.

---

## 1. What this is

A Minecraft server hosting service run by one person (KoPeng, kkwonsc@gmail.com)
out of their home in Changwon, South Korea. Pre-launch, no paying customers yet.

The product promise: **click a button, get a Minecraft server, manage it by
talking to an AI in plain Korean.** No console, no commands to memorise.

Two audiences, two agents:
- **Customers** — a management session page with an AI manager and server status.
- **The owner** — an admin page with payment confirmation and an ops agent.

Everything is in Korean for end users. Code and comments are in English.

---

## 2. Where things run

| Piece | Where | Notes |
|---|---|---|
| Landing page | Static, served locally (`python -m http.server 5599`) | `Landing.dc.html` at repo root |
| Backend | `192.168.45.100` (`dobby`), Docker | Ubuntu Server 26.04, i5-8500, 30GB RAM |
| Customer worlds | `/mnt/hdd/minecraft/worlds/` | 2.7TB HDD |
| SQLite DB | `/srv/minecraft/data/glistering_melon.db` | 300GB SSD LV |
| Repo | `github.com/KR-penguin/Glistering-Melon` (**private**) | SSH remote |

SSH: `ssh -i ~/.ssh/glistering_melon sean@192.168.45.100`. Passwordless sudo is
enabled for `sean`.

**The backend is LAN-only.** `192.168.45.100:8000` is unreachable from the
internet. This is the single biggest thing standing between the project and real
customers, and it is one of the two tasks this handoff is for.

---

## 3. Architecture

```
Landing.dc.html                  landing page (Design Component format, see §6)
backend/
  app/
    main.py                      FastAPI app, lifespan starts sweeper + watchdog
    config.py                    pydantic-settings, reads .env
    db.py                        SQLite schema + idempotent column migration
    capacity.py                  RAM/disk budget check before provisioning
    provisioning.py              the create-a-server flow
    docker_ctl.py                container lifecycle (the "outside the game" family)
    billing.py                   payment state + unpaid-server grace timer
    accounts.py                  customer accounts (Google identity, claim-a-server)
    google_oauth.py              the sign-in-with-Google dance
    naming.py                    how an unnamed server is referred to
    discord_notify.py            outbound webhook only (no bot)
    modpacks.py                  curated Modrinth catalog
    mojang.py                    username lookup (advisory, not a gate)
    ratelimit.py                 per-IP limit on server creation
    dbview.py                    read-only DB viewer CLI
    routes/{servers,session,admin,auth}.py
    static/{session,login,admin}.html + ds.css (vendored design system)
  agent/
    llm.py                       Gemini/Vertex tool-calling loop + defensive parsing
    customer_agent.py            CUSTOMER-facing agent (see §5 security note)
    ops_agent.py                 OWNER-facing agent
    tools.py                     owner tools (take server_id as an argument)
    rcon_client.py               Source RCON, hand-rolled + the lifecycle interlock
    routing.py                   Korean keyword pre-router
    watchdog.py                  non-LLM crash-restart notifier
    rag/
      ingest.py                  fetches command docs from minecraft.wiki
      snapshot.json              committed fetch result (26.2, 28 commands)
      store.py                   FTS5 index + retrieval
      docs/*.md                  legacy hand-written notes (older versions)
```

270 tests, 2 skipped (the skipped two need a live LLM). `cd backend && pytest`.

---

## 4. Customer flow

1. Landing page → "1분 만에 서버 열기". No form: the button IS the signup, and
   opens the cheapest tier. The pricing table's buttons each open their own.
2. `POST /api/servers` → capacity check → returns a `session_token` immediately,
   provisioning continues in the background (~50-70s).
3. Browser redirects to `{backend}/session/{token}` — status, price, deposit
   info, countdown, AI chat. No console, no logs (deliberate — the owner asked).
4. Once running, the page offers Google sign-in, which **claims** the server
   and fills in the name it was opened without.
5. Payment is a bank transfer with a 4-digit code in the depositor name. The
   owner confirms at `/admin`.
6. Unpaid after the grace window → container **stopped** (never deleted).
   Confirming payment restarts it.

---

## 5. Decisions that are not obvious

Change these only deliberately; each exists because of a specific failure.

### The RCON lifecycle interlock — the one hard guarantee
`agent/rcon_client.py::assert_allowed` refuses `stop`, `restart`, `reload`,
`kill`, `shutdown`, etc. at the socket, before connecting.

"Restart the server" must ALWAYS go through Docker, because that is what works
when the game is hung — which is exactly when it is needed. This cannot live in
a prompt: Korean "서버 정지" reads as both `/stop` and `docker stop`, and models
misroute. The refusal names the right tool, so a misroute self-corrects on the
next turn instead of stopping a customer's server. Covered by
`tests/test_rcon_interlock.py`.

### Customer agent tools close over `server_id`
`agent/customer_agent.py` builds its tools bound to one server; **no tool schema
accepts a server identifier**. `agent/tools.py` (owner-facing) does take
`server_id`, which is why they are separate modules. If the customer agent used
the owner's tools, cross-tenant access would be one prompt injection away.
Covered by `tests/test_customer_agent.py`.

### Opening a server asks for nothing
The button is the whole signup — no name, no email, no plan chooser. So
`applications.nickname` and `.email` are nullable, `servers.nickname` stores
empty rather than null (that table has live data and was not worth
rebuilding), and `app/naming.py` decides how an unnamed server reads: the
owner sees the container name so they can tell servers apart in Discord and
/admin, the customer just sees "내 서버". A tier-less request opens the
cheapest plan, because one click with no confirmation must not be able to
commit someone to the 49,000원 tier.

### Identity comes from Google, not from a password
There is no password anywhere now. `app/google_oauth.py` runs the
authorization-code flow server-side and `accounts.sign_in_with_google` matches
on Google's subject id first and email second. Subject-first because a Google
address can change and must not fork the account away from its servers;
email-second so an account made under the old password flow is adopted rather
than colliding with `users.email`'s UNIQUE constraint. The callback is where a
session token gets claimed, which makes CSRF there account takeover — hence
single-use, expiring `state` and a *verified* ID token, not a decoded one.
Covered by `tests/test_google_oauth.py`.

### Claiming a server only works if unclaimed
Session tokens travel in URLs. If holding one could re-bind an already-owned
server, sharing a link would hand over the account. `accounts.py::_claim_server`
refuses owned servers; there is a test that attempts the theft.

### RAG is retrieve-then-generate, not an optional tool
`rag/store.py::reference_block` is injected into the prompt every turn. As a
tool the model only consulted it when it judged itself unsure — precisely when
a model is least reliable. The prompt tells it to believe the reference over
its own memory.

### The command reference is fetched, not written
`rag/ingest.py` pulls from minecraft.wiki. Hand-written docs came from a model's
memory and were wrong in exactly the way the reference exists to prevent: between
1.21.1 and 26.2, `/time` was rebuilt around world clocks, `/team modify color`
stopped accepting `darkpurple`, and `/worldborder`'s time argument became ticks.
Refresh with `python -m agent.rag.ingest --version <ver>` and review the diff.

### Payment: 1-hour grace, stop not delete
The owner asked for 30 minutes; research showed that would kill paying customers
(지연이체 delays a minimum of 3 hours, bank maintenance clusters 23:30-00:30,
student accounts hit daily caps). Presented with that, they chose 1 hour. It is
survivable only because expiry **stops** rather than deletes, late payment
auto-restarts, and the customer can self-serve one +2h extension.

### Servers are open to everyone; the customer is opped
No whitelist by default (owner's call). The name field is "이름 (닉네임 가능)" —
a display name, so it may be Korean. Mojang lookup is advisory: if the name
happens to be a real Java account, that account is opped; otherwise no OPS is
set. Never let an unverified name become `OPS`.

### No Discord bot, no customer email
The bot was removed in favour of `/admin` behind `ADMIN_TOKEN`. The Discord
**webhook** remains for outbound notifications. Any notification that asks the
owner to *do* something must carry the admin URL — a stale "reply in Discord"
instruction shipped once and silently did nothing.

---

## 6. Traps

Every one of these cost real debugging time.

| Trap | Symptom | Fix |
|---|---|---|
| **`--build` is mandatory** | Files land on the server, behaviour unchanged | Code is COPYied at image build. `--force-recreate` alone reruns the OLD image. |
| **Bind-mount path identity** | World data appears in the wrong place | The backend runs in a container but asks the HOST daemon to bind-mount. Host paths must be mounted at the *identical* path inside the backend container. See `docker-compose.yml`. |
| **CORS `allow_headers`** | Request never reaches the server; nothing in server logs | The landing page is a different origin. Every custom header (`X-Auth-Token`, `X-Admin-Token`) must be listed. Only visible in the browser console. |
| **Gemini rejects an empty tool schema** | The whole tool set 400s, so the agent answers nothing | A parameterless tool must OMIT `parameters_json_schema`, not send `{"type":"object","properties":{}}`. Most customer tools take no arguments. `agent/llm.py::_function_declarations`. |
| **Gemini function results ride a `user` turn** | 400 on every tool call | Not `role="tool"` — the SDK README's example says tool, the SDK's own loop uses user, and the API agrees with the loop. |
| **Never bind-mount the credentials file directly** | ADC authenticates against a *directory* | Docker creates a directory at a bind-mount source that does not exist. The key therefore lives at `${HOST_DATA_ROOT}/gcp-credentials.json`, inside a tree that is already mounted, where a missing file stays a missing file. |
| **Google rejects a LAN redirect URI** | Cannot even register the OAuth client | Only public https origins and `http://localhost`. Hence `OAUTH_REDIRECT_BASE` separate from `PUBLIC_BASE_URL`; tunnel with `ssh -L 8000:localhost:8000` until a domain exists. |
| **`mcrcon` + threads** | "signal only works in main thread" | It arms SIGALRM. All RCON runs in `asyncio.to_thread`. Replaced with a hand-rolled client; do not reintroduce a signal-based library. |
| **Blank env vars** | Container crash-loops on boot | Pydantic rejects `""` for an int. `config.py` has a validator turning blanks into defaults. |
| **`VERSION=LATEST` is not a version** | 26.2 server answered from 1.20.1 docs | Parsed to `(0,)` and picked the OLDEST reference. Non-numeric pointers now fall back to NEWEST, and provisioning writes the real version back after boot. |
| **26.x sorts NEWER than 1.21.x** | Version comparison backwards | Calendar versioning began 2026; 26.1 replaced what would have been 1.22. |
| **Korean queries vs English docs** | RAG silently never fires | `store.py::_KO_TERMS` bridges Korean→English, substring-matched because Korean agglutinates particles. Without it every real customer message retrieved nothing. |
| **Private repo** | Server cannot `git pull` | Deploy via `git archive` + `scp`. A GitHub deploy key would fix this properly. |
| **Design Component template engine** | `{{ }}` expressions silently do nothing | `Landing.dc.html` uses a restricted resolver: dotted path lookups only. No function calls, no arrow functions, no expressions. Compute in `renderVals()`. |

---

## 7. Open items

**Blocking launch:**

1. **Google credentials are not set yet.** The code is written and tested, but
   nothing here has ever run against real Google infrastructure.

   The agent reaches Gemini either way, and the choice is one env var:
   `GEMINI_API_KEY` (Gemini Developer API — one key from AI Studio, no cloud
   project, free tier) or `GOOGLE_CLOUD_PROJECT` (Vertex AI — needs a GCP
   project with billing plus a service account at
   `/srv/minecraft/data/gcp-credentials.json`, and bills through GCP, which is
   why a registered business wants it). The key wins if both are set. Sign-in
   separately needs `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET`.

   Until then the agent returns its "not configured" message and sign-in 503s;
   provisioning, the payment timer and /admin all keep working. **`GEMINI_MODEL`
   defaults to `gemini-3.7-flash` and has not been confirmed available — check
   it first, it is one env var.**
2. **Port forwarding / domain / HTTPS.** The owner is doing this themselves.
   Backend is still LAN-only. `Landing.dc.html` hardcodes
   `BACKEND_URL = 'http://192.168.45.100:8000'`; `.env` has `PUBLIC_BASE_URL`,
   `MINECRAFT_PUBLIC_HOST` and `OAUTH_REDIRECT_BASE`. All become real
   addresses, and `CORS_ALLOW_ORIGIN` tightens from `*` at that point.

**What the AI manager actually did when reproduced** (2026-08-26, on the
Claude provider it has since moved off):

It worked. It answered in Korean, called its tools, ran RCON commands with the
correct 26.2 world-clock syntax the RAG supplies, and restarted the container
through Docker rather than RCON. Two real faults surfaced:

- Opening a server without an email was a **500** — `applications.email` was
  NOT NULL while the API called it optional. Fixed; that path is now the
  default one.
- The agent **invents payment policy.** Asked "what happens if I don't pay",
  it deflected to Discord; asked for status on a provisioning server it
  announced the server was not starting *because* payment was unconfirmed,
  which is backwards — servers run through the whole grace window. The cause
  is that `billing.payment_summary` hands the model `status: "awaiting"` and a
  bare `seconds_remaining` with nothing saying what either means, and the
  system prompt never states the policy. **Still open**, and it is the most
  customer-visible thing left: every customer asks this question.

**Also open:**
- Landing page still claims 시간별 백업 / 플러그인 충돌 롤백 / 램 자동 조정.
  None are built. (모드팩 *is* real now.)
- No path for deleting long-abandoned suspended servers; disk is reclaimed by hand.
- `MODPACK_CATALOG` has one entry (Cobblemon).
- Community tier is 150GB — fine on the 2.7TB HDD, but never load-tested.
- Minecraft on HDD is slower than SSD for chunk I/O. Owner chose HDD knowing this.

**Security notes:**
- **Never run a command that dumps resolved config** — no `docker compose
  config`, no `cat .env`. An Anthropic key leaked into a transcript that way
  and had to be rotated. That provider is gone, but the habit is what matters:
  `${HOST_DATA_ROOT}/gcp-credentials.json` is now the credential, and Vertex has no
  API-key alternative to fall back on if it leaks.
- The owner's account password and `ADMIN_TOKEN` also appeared in transcripts.
- `ADMIN_TOKEN` is a single shared secret — fine for one operator, replace with
  real accounts if a second person needs access.
- Mounting `docker.sock` gives the backend container root-equivalent host access.

---

## 8. Working conventions

- **Deploy:** `git archive` → `scp` → `tar xzf` → `docker compose up -d --build`.
- **Test before deploying.** `cd backend && pytest`. Add a test for every bug
  fixed — most traps in §6 have one.
- **Clean up after testing.** Remove test containers, world dirs and DB rows;
  the owner tests on the same box and stale state confuses them.
- **The owner tests things themselves.** Do not run exploratory tests on their
  behalf after they have said they will — set up FE/BE and stop.
- **Never print secrets.** No `docker compose config`, no `cat .env`, no echoing
  tokens. Grep for a key's *presence*, not its value.
- **Ask before destroying.** Check whether a running container is something the
  owner just created before deleting it.
- Commit messages: what changed and *why*, in prose. Not bullet lists.
