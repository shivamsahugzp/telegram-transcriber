import os
import tempfile
import logging
from telegram import Update
from telegram.ext import ContextTypes
from downloader import download_audio, is_supported_url
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
        await update.message.reply_text(
            "Hmm, I don't recognise that as a video link.\n\n"
            "Try sending a YouTube, Instagram, or Google Drive link — or just upload the video directly."
        )
        return

    status_msg = await update.message.reply_text("Got it! Downloading the video now...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = download_audio(url, tmp_dir)

            await status_msg.edit_text("Transcribing now... this usually takes 30–60 seconds ⏳")
            transcript = transcribe_file(audio_path)

        await status_msg.delete()
        await _send_transcript(update, transcript)

    except ValueError as e:
        await status_msg.edit_text(str(e))
    except Exception as e:
        logger.error("Error processing URL %s: %s", url, e)
        err = str(e).lower()

        if "instagram" in err and ("login" in err or "rate" in err or "cookie" in err or "credentials" in err):
            await status_msg.edit_text(
                "Instagram is blocking the download — it needs you to be logged in.\n\n"
                "The easiest fix: download the Reel on your phone and send the video file here directly. "
                "I'll transcribe it just the same! 🎙️"
            )
        elif "private" in err or "members only" in err:
            await status_msg.edit_text(
                "This looks like a private video — I can't access it.\n\n"
                "If you have the video saved, just send it as a file and I'll transcribe it."
            )
        elif "not available" in err or "removed" in err or "deleted" in err:
            await status_msg.edit_text(
                "Couldn't find that video — it may have been deleted or made private."
            )
        elif "unsupported url" in err:
            await status_msg.edit_text(
                "I couldn't download from that link. Try YouTube, Instagram, or Google Drive — "
                "or just upload the video file directly."
            )
        elif "network" in err or "connection" in err or "timeout" in err:
            await status_msg.edit_text(
                "Something went wrong with the connection. Please try again in a moment."
            )
        else:
            await status_msg.edit_text(
                f"Something went wrong:\n\n`{str(e)[:300]}`"
            )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    file_obj = message.video or message.audio or message.voice or message.document

    if not file_obj:
        await message.reply_text("Please send a video, audio, or voice message.")
        return

    status_msg = await message.reply_text("Got it! Give me a moment to process this...")

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

            await status_msg.edit_text("Transcribing now... this usually takes 30–60 seconds ⏳")

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
        err = str(e).lower()
        if "no speech" in err or "audio" in err:
            await status_msg.edit_text(
                "I couldn't detect any speech in that file. "
                "Make sure the video has audio and try again."
            )
        else:
            await status_msg.edit_text(
                "Something went wrong while processing your file. "
                "Please try again — if it keeps failing, try a different format (MP4 or MP3 works best)."
            )


async def _send_transcript(update: Update, transcript: str) -> None:
    """Send transcript, splitting into chunks if it exceeds Telegram's message limit."""
    if not transcript.strip():
        await update.message.reply_text(
            "I couldn't detect any speech in this video. "
            "Make sure the video has audio, then try again."
        )
        return

    header = "📝 *Here's your transcript:*\n\n"
    full_text = header + transcript

    if len(full_text) <= MAX_TELEGRAM_MSG_LEN:
        await update.message.reply_text(full_text, parse_mode="Markdown")
    else:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(transcript)
            tmp_path = f.name

        await update.message.reply_text(
            "The transcript is quite long, so I'm sending it as a text file."
        )
        with open(tmp_path, "rb") as f:
            await update.message.reply_document(document=f, filename="transcript.txt")
        os.unlink(tmp_path)
