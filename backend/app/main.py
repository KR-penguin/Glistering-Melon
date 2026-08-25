import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import billing
from app.config import settings
from app.db import init_db
from app.docker_ctl import ensure_network, get_client
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

    bot_task = None
    if settings.discord_bot_token:
        from agent.bot import run_bot

        bot_task = asyncio.create_task(run_bot())
    else:
        logger.warning("DISCORD_BOT_TOKEN not set — ops agent bot not started")

    # Stops unpaid servers once their grace window passes. Runs regardless of
    # whether the Discord bot is configured — an unpaid server must not stay up
    # just because notifications happen to be off.
    sweeper_task = asyncio.create_task(billing.sweeper_loop())
    logger.info("payment sweeper running (grace = %s min)", settings.payment_grace_minutes)

    yield

    sweeper_task.cancel()
    if bot_task:
        bot_task.cancel()


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


@app.get("/healthz")
async def healthz():
    return {"ok": True}
