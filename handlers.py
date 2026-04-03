import os
import tempfile
import logging
from telegram import Update
from telegram.ext import ContextTypes
from downloader import download_audio, is_supported_url
from transcriber import transcribe_file
import auth

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MSG_LEN = 4096

# Per-user language preference for Whisper input (None = default Hindi)
_user_lang: dict[int, str | None] = {}

# Per-user output format preference
_user_format: dict[int, str] = {}

SUPPORTED_LANGS = {
    "hi": "Hindi", "en": "English", "auto": "Auto-detect",
    "ur": "Urdu", "mr": "Marathi", "ta": "Tamil", "te": "Telugu",
    "gu": "Gujarati", "bn": "Bengali", "pa": "Punjabi",
}

SUPPORTED_FORMATS = {
    "hi": "Hindi (Devanagari script)",
    "en": "English (translated)",
    "hinglish": "Hinglish (Roman script, Hindi-English mix)",
}

DEFAULT_FORMAT = os.environ.get("DEFAULT_FORMAT", "hi")


async def _check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if user is allowed. If not, handle request flow and return False."""
    # If owner hasn't configured their ID yet, allow everyone
    if not auth.is_configured():
        return True

    user = update.effective_user
    user_id = user.id

    if auth.is_approved(user_id):
        return True

    owner_id = auth.is_owner.__wrapped__(user_id) if hasattr(auth.is_owner, "__wrapped__") else None
    # owner is always approved (already added in auth.init), so reaching here means not approved

    if not auth.is_pending(user_id):
        name = user.full_name or "Unknown"
        username = f"@{user.username}" if user.username else "no username"
        auth.add_pending(user_id, name, user.username)

        await update.message.reply_text(
            "This bot is private.\n\n"
            "Your access request has been sent to the owner. "
            "You'll be notified once approved."
        )

        # Notify owner
        owner = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
        if owner:
            await context.bot.send_message(
                chat_id=owner,
                text=(
                    f"*Access request*\n\n"
                    f"Name: {name}\n"
                    f"Username: {username}\n"
                    f"ID: `{user_id}`\n\n"
                    f"Reply `/approve {user_id}` to grant access\n"
                    f"or `/deny {user_id}` to reject."
                ),
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            "Your request is still pending. The owner will approve it soon."
        )

    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not await _check_access(update, context):
        return

    await update.message.reply_text(
        f"👋 *Video Transcriber Bot*\n\n"
        f"Your ID: `{user.id}`\n\n"
        "Send me:\n"
        "• A YouTube / Instagram / video URL\n"
        "• A video or audio file\n\n"
        "I'll transcribe it — Hindi, English, or Hinglish!\n\n"
        "Use /help to see all commands.",
        parse_mode="Markdown"
    )


async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth.is_owner(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: `/approve <user_id>`", parse_mode="Markdown")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    info = auth.pending_info(target_id)
    was_pending = auth.approve(target_id)
    name = info["name"] if info else str(target_id)

    await update.message.reply_text(f"✅ Approved *{name}* (`{target_id}`).", parse_mode="Markdown")

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="✅ Your access has been approved! Send me a video link or file to get started.",
        )
    except Exception:
        pass  # User may not have started the bot yet


async def deny_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth.is_owner(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: `/deny <user_id>`", parse_mode="Markdown")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    info = auth.pending_info(target_id)
    auth.deny(target_id)
    name = info["name"] if info else str(target_id)

    await update.message.reply_text(f"❌ Denied *{name}* (`{target_id}`).", parse_mode="Markdown")

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="Your access request was not approved.",
        )
    except Exception:
        pass


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update, context):
        return
    await update.message.reply_text(
        "*Supported sources:*\n"
        "• YouTube, Instagram, Twitter/X, Facebook\n"
        "• Google Drive (public links)\n"
        "• Direct video/audio URLs\n"
        "• Telegram video/audio files (upload directly)\n\n"
        "*Commands:*\n"
        "/setformat hi — Output in Hindi (Devanagari)\n"
        "/setformat en — Output translated to English\n"
        "/setformat hinglish — Output in Hinglish (Roman script)\n"
        "/setlang — Change Whisper input language\n"
        "/help — This message",
        parse_mode="Markdown"
    )


async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update, context):
        return
    user_id = update.effective_user.id
    args = context.args

    if not args:
        current = _user_lang.get(user_id)
        current_name = SUPPORTED_LANGS.get(current, "Auto-detect") if current else "Auto-detect"
        lang_list = "\n".join(f"  `/setlang {code}` — {name}" for code, name in SUPPORTED_LANGS.items())
        await update.message.reply_text(
            f"Current language: *{current_name}*\n\n"
            f"Available options:\n{lang_list}",
            parse_mode="Markdown"
        )
        return

    lang = args[0].lower().strip()
    if lang == "auto":
        _user_lang[user_id] = "auto"
        await update.message.reply_text("Language set to *Auto-detect*.", parse_mode="Markdown")
    elif lang in SUPPORTED_LANGS:
        _user_lang[user_id] = lang
        await update.message.reply_text(
            f"Language pinned to *{SUPPORTED_LANGS[lang]}*. All transcripts will use this now.\n"
            "Use `/setlang auto` to switch back to auto-detect.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"Unknown language code `{lang}`.\n"
            "Try `/setlang hi` for Hindi, `/setlang en` for English, or `/setlang auto` for auto-detect.",
            parse_mode="Markdown"
        )


async def setformat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update, context):
        return
    user_id = update.effective_user.id
    args = context.args

    if not args:
        current = _user_format.get(user_id, DEFAULT_FORMAT)
        current_name = SUPPORTED_FORMATS.get(current, current)
        fmt_list = "\n".join(f"  `/setformat {code}` — {name}" for code, name in SUPPORTED_FORMATS.items())
        await update.message.reply_text(
            f"Current output format: *{current_name}*\n\n"
            f"Available formats:\n{fmt_list}",
            parse_mode="Markdown"
        )
        return

    fmt = args[0].lower().strip()
    if fmt in SUPPORTED_FORMATS:
        _user_format[user_id] = fmt
        await update.message.reply_text(
            f"Output format set to *{SUPPORTED_FORMATS[fmt]}*.\n"
            "Your next transcript will use this format.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"Unknown format `{fmt}`.\n"
            "Use `/setformat hi`, `/setformat en`, or `/setformat hinglish`.",
            parse_mode="Markdown"
        )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update, context):
        return

    url = update.message.text.strip()

    if not is_supported_url(url):
        await update.message.reply_text(
            "Hmm, I don't recognise that as a video link.\n\n"
            "Try sending a YouTube, Instagram, or Google Drive link — or just upload the video directly."
        )
        return

    status_msg = await update.message.reply_text("Got it! Downloading the video now...")

    try:
        user_id = update.effective_user.id
        language = _user_lang.get(user_id)
        output_format = _user_format.get(user_id, DEFAULT_FORMAT)

        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = download_audio(url, tmp_dir)

            await status_msg.edit_text("Transcribing and formatting... usually takes 30–90 seconds ⏳")
            transcript = transcribe_file(audio_path, language=language, output_format=output_format)

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
            await status_msg.edit_text(f"Error: `{str(e)[:400]}`", parse_mode="Markdown")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_access(update, context):
        return

    message = update.message
    file_obj = message.video or message.audio or message.voice or message.document

    if not file_obj:
        await message.reply_text("Please send a video, audio, or voice message.")
        return

    status_msg = await message.reply_text("Got it! Give me a moment to process this...")

    try:
        user_id = update.effective_user.id
        language = _user_lang.get(user_id)
        output_format = _user_format.get(user_id, DEFAULT_FORMAT)
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

            await status_msg.edit_text("Transcribing and formatting... usually takes 30–90 seconds ⏳")

            if extension in ("mp3", "ogg", "m4a", "wav"):
                audio_path = file_path
            else:
                audio_path = os.path.join(tmp_dir, "audio.mp3")
                import subprocess
                subprocess.run([
                    "ffmpeg", "-y", "-i", file_path,
                    "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path
                ], capture_output=True)

            transcript = transcribe_file(audio_path, language=language, output_format=output_format)

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
