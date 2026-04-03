import os
import glob
import subprocess
import yt_dlp


SUPPORTED_URL_PATTERNS = [
    "youtube.com", "youtu.be",
    "drive.google.com",
    "instagram.com",
    "twitter.com", "x.com",
    "facebook.com",
    "vimeo.com",
]

LOGIN_REQUIRED_PATTERNS = ["instagram.com"]


def is_supported_url(url: str) -> bool:
    return any(pattern in url for pattern in SUPPORTED_URL_PATTERNS) or url.startswith("http")


def _get_cookies_file(url: str, tmp_dir: str) -> str | None:
    """Write cookies from env var to a temp file if available for this URL."""
    if "instagram.com" in url:
        cookies = os.environ.get("INSTAGRAM_COOKIES")
        if cookies:
            cookies_path = os.path.join(tmp_dir, "cookies.txt")
            with open(cookies_path, "w") as f:
                f.write(cookies)
            return cookies_path
    return None


def _extract_audio_ffmpeg(input_path: str, output_dir: str) -> str:
    """Extract and speech-enhance audio from video using ffmpeg."""
    output_path = os.path.join(output_dir, "audio.mp3")
    # highpass=f=80  — removes bass/music rumble below 80Hz
    # dynaudnorm     — normalises volume so quiet speech isn't drowned out
    # These two filters together greatly reduce background music interference
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vn",
            "-af", "highpass=f=80,dynaudnorm",
            "-acodec", "libmp3lame", "-q:a", "2",
            "-ar", "16000",
            output_path,
        ],
        capture_output=True, text=True,
    )
    if not os.path.exists(output_path):
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr[-500:]}")
    return output_path


def download_audio(url: str, output_dir: str) -> str:
    """Download video from URL and extract audio. Returns path to mp3."""
    if "instagram.com" in url and not os.environ.get("INSTAGRAM_COOKIES"):
        raise ValueError(
            "Instagram requires login to download.\n\n"
            "Please download the video and send it as a file directly to the bot instead."
        )

    cookies_file = _get_cookies_file(url, output_dir)

    # Step 1: Download raw video (no postprocessing — avoids ffprobe codec issues)
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "outtmpl": os.path.join(output_dir, "video.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    # Step 2: Find the downloaded file
    downloaded = (
        glob.glob(os.path.join(output_dir, "video.*"))
    )
    downloaded = [f for f in downloaded if not f.endswith(".txt")]  # exclude cookies file

    if not downloaded:
        raise FileNotFoundError("Download failed — no video file found after download.")

    video_path = downloaded[0]

    # Step 3: Extract audio with ffmpeg directly (no ffprobe codec detection needed)
    return _extract_audio_ffmpeg(video_path, output_dir)
