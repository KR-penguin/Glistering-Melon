import logging

import discord

from agent.claude_client import handle_message
from agent.watchdog import watch_containers
from app.config import settings

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    logger.info("ops bot logged in as %s", client.user)
    client.loop.create_task(watch_containers())


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != settings.discord_ops_channel_id:
        return
    if not message.content.strip():
        return

    async with message.channel.typing():
        try:
            reply = await handle_message(message.content)
        except Exception:
            logger.exception("agent failed to handle message")
            reply = "요청을 처리하는 중 오류가 발생했어요. 로그를 확인해주세요."
    await message.reply(reply, mention_author=False)


async def run_bot() -> None:
    await client.start(settings.discord_bot_token)
