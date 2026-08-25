import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agent.watchdog import watch_containers
from app import billing
from app.config import settings
from app.db import init_db
from app.docker_ctl import ensure_network, get_client
from app.routes.admin import router as admin_router
from app.routes.auth import router as auth_router
from app.routes.servers import router as servers_router
from app.routes.session import router as session_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        ensure_network(get_client())
    except Exception:
        logger.exception("could not reach Docker daemon on startup — is docker.sock mounted?")

    # Owner actions live at /admin behind ADMIN_TOKEN rather than in a Discord
    # bot — there is no gateway connection to keep alive, and outbound
    # notifications still work because those go through a webhook.
    if not settings.admin_token:
        logger.warning("ADMIN_TOKEN not set — /admin endpoints will refuse requests")

    # Stops unpaid servers once their grace window passes. Runs regardless of
    # notification configuration — an unpaid server must not stay up just
    # because Discord happens to be unconfigured.
    sweeper_task = asyncio.create_task(billing.sweeper_loop())
    logger.info("payment sweeper running (grace = %s min)", settings.payment_grace_minutes)

    watchdog_task = asyncio.create_task(watch_containers())

    yield

    sweeper_task.cancel()
    watchdog_task.cancel()


app = FastAPI(title="Glistering Melon backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_allow_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)

app.include_router(servers_router)
app.include_router(session_router)
app.include_router(admin_router)
app.include_router(auth_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
