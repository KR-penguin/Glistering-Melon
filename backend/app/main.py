import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db
from app.docker_ctl import ensure_network, get_client
from app.routes.applications import router as applications_router

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

    yield

    if bot_task:
        bot_task.cancel()


app = FastAPI(title="Glistering Melon backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_allow_origin],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

app.include_router(applications_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
