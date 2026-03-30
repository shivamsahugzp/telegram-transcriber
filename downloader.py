import os
import tempfile
import yt_dlp


SUPPORTED_URL_PATTERNS = [
    "youtube.com", "youtu.be",
    "drive.google.com",
    "instagram.com",
    "twitter.com", "x.com",
    "facebook.com",
    "vimeo.com",
]


def is_supported_url(url: str) -> bool:
    return any(pattern in url for pattern in SUPPORTED_URL_PATTERNS) or url.startswith("http")


def download_audio(url: str, output_dir: str) -> str:
    """Download audio from a URL using yt-dlp. Returns path to downloaded audio file."""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "audio.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "96",
        }],
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    output_path = os.path.join(output_dir, "audio.mp3")
    if not os.path.exists(output_path):
        raise FileNotFoundError("Audio download failed — file not found after download.")

    return output_path


def save_telegram_file(file_bytes: bytes, output_dir: str, extension: str = "mp4") -> str:
    """Save bytes from a Telegram file to disk. Returns the file path."""
    file_path = os.path.join(output_dir, f"video.{extension}")
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    return file_path


def extract_audio_from_video(video_path: str, output_dir: str) -> str:
    """Extract audio track from a local video file. Returns path to mp3."""
    audio_path = os.path.join(output_dir, "audio.mp3")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "audio.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "96",
        }],
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"file://{video_path}"])

    if not os.path.exists(audio_path):
        raise FileNotFoundError("Audio extraction failed.")

    return audio_path
