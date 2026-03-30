import os
import tempfile
import logging
from telegram import Update
from telegram.ext import ContextTypes
from downloader import download_audio, save_telegram_file, extract_audio_from_video, is_supported_url
from transcriber import transcribe_file

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MSG_LEN = 4096


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Video Transcriber Bot*\n\n"
        "Send me:\n"
        "• A YouTube / video URL\n"
        "• A video or audio file\n\n"
        "I'll transcribe it for you — Hindi & English supported!\n\n"
        "Just paste a link or forward a video to get started.",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Supported sources:*\n"
        "• YouTube\n"
        "• Google Drive (public links)\n"
        "• Instagram, Twitter/X, Facebook\n"
        "• Direct video/audio URLs\n"
        "• Telegram video/audio files (upload directly)\n\n"
        "*Languages:* Auto-detected (Hindi, English, and 97 more)\n\n"
        "*Commands:*\n"
        "/start — Welcome message\n"
        "/help — This help message",
        parse_mode="Markdown"
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text.strip()

    if not is_supported_url(url):
        await update.message.reply_text("Please send a valid video URL or upload a video file.")
        return

    status_msg = await update.message.reply_text("⏳ Downloading video...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            await status_msg.edit_text("⏳ Downloading audio...")
            audio_path = download_audio(url, tmp_dir)

            await status_msg.edit_text("🔤 Transcribing... (this may take a minute)")
            transcript = transcribe_file(audio_path)

        await status_msg.delete()
        await _send_transcript(update, transcript)

    except Exception as e:
        logger.error("Error processing URL %s: %s", url, e)
        await status_msg.edit_text(f"❌ Failed to process video.\n\nError: {str(e)}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    file_obj = message.video or message.audio or message.voice or message.document

    if not file_obj:
        await message.reply_text("Please send a video, audio, or voice message.")
        return

    status_msg = await message.reply_text("⏳ Downloading file...")

    try:
        file = await context.bot.get_file(file_obj.file_id)

        with tempfile.TemporaryDirectory() as tmp_dir:
            extension = "mp4"
            if message.audio or message.voice:
                extension = "mp3"
            elif message.document:
                name = getattr(file_obj, "file_name", "file.mp4")
                extension = name.rsplit(".", 1)[-1] if "." in name else "mp4"

            file_path = os.path.join(tmp_dir, f"input.{extension}")
            await file.download_to_drive(file_path)

            await status_msg.edit_text("🔤 Transcribing... (this may take a minute)")

            if extension in ("mp3", "ogg", "m4a", "wav"):
                audio_path = file_path
            else:
                audio_path = os.path.join(tmp_dir, "audio.mp3")
                import subprocess
                subprocess.run([
                    "ffmpeg", "-y", "-i", file_path,
                    "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path
                ], capture_output=True)

            transcript = transcribe_file(audio_path)

        await status_msg.delete()
        await _send_transcript(update, transcript)

    except Exception as e:
        logger.error("Error processing file: %s", e)
        await status_msg.edit_text(f"❌ Failed to transcribe file.\n\nError: {str(e)}")


async def _send_transcript(update: Update, transcript: str) -> None:
    """Send transcript, splitting into chunks if it exceeds Telegram's message limit."""
    if not transcript.strip():
        await update.message.reply_text("⚠️ No speech detected in the audio.")
        return

    header = "📝 *Transcript:*\n\n"
    full_text = header + transcript

    if len(full_text) <= MAX_TELEGRAM_MSG_LEN:
        await update.message.reply_text(full_text, parse_mode="Markdown")
    else:
        # Send as a text file for long transcripts
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(transcript)
            tmp_path = f.name

        await update.message.reply_text(
            f"📝 Transcript is long ({len(transcript)} characters). Sending as a file."
        )
        with open(tmp_path, "rb") as f:
            await update.message.reply_document(document=f, filename="transcript.txt")
        os.unlink(tmp_path)
