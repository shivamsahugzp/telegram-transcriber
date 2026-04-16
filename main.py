import os
import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from handlers import (
    start_command, help_command,
    setlang_command, setformat_command,
    approve_command, deny_command,
    correct_command, vocab_command, forget_command,
    handle_url, handle_video,
)
import auth
import vocab

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

_ptb_app: Application | None = None


async def telegram_webhook(request: web.Request) -> web.Response:
    data = await request.json()
    update = Update.de_json(data, _ptb_app.bot)
    await _ptb_app.process_update(update)
    return web.Response(text="OK")


async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def _build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setlang", setlang_command))
    app.add_handler(CommandHandler("setformat", setformat_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("deny", deny_command))
    app.add_handler(CommandHandler("correct", correct_command))
    app.add_handler(CommandHandler("vocab", vocab_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL,
        handle_video
    ))
    return app


async def run_webhook(token: str, webhook_url: str, port: int) -> None:
    global _ptb_app
    _ptb_app = _build_app(token)

    full_webhook_url = f"{webhook_url}/webhook/{token}"
    await _ptb_app.bot.set_webhook(url=full_webhook_url, drop_pending_updates=True)
    logger.info("Webhook set to %s", full_webhook_url)

    web_app = web.Application()
    web_app.router.add_post(f"/webhook/{token}", telegram_webhook)
    web_app.router.add_get("/", health_check)

    async with _ptb_app:
        await _ptb_app.start()

        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("Server listening on port %d", port)

        await asyncio.Event().wait()  # run forever

        await _ptb_app.stop()


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")

    auth.init()
    vocab.init()

    webhook_url = os.environ.get("WEBHOOK_URL", "").rstrip("/")
    port = int(os.environ.get("PORT", 8080))

    if webhook_url:
        logger.info("Starting in webhook mode on port %d", port)
        asyncio.run(run_webhook(token, webhook_url, port))
    else:
        logger.info("WEBHOOK_URL not set — starting in polling mode (local dev)")
        app = _build_app(token)
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
