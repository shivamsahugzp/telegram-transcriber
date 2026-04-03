import os
import logging
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


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    groq_key = os.environ.get("GROQ_API_KEY")
    if not groq_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")

    auth.init()
    vocab.init()

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

    # URL messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    # Video / audio / voice / document files
    app.add_handler(MessageHandler(
        filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL,
        handle_video
    ))

    logger.info("Bot started. Polling for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
