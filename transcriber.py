import os
import math
from groq import Groq

MAX_FILE_SIZE_BYTES = 24 * 1024 * 1024  # 25MB Groq limit (leaving 1MB buffer)
GROQ_MODEL = "whisper-large-v3"


def get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def transcribe_file(audio_path: str, language: str = None) -> str:
    """Transcribe an audio file using Groq Whisper. Auto-detects language if not specified."""
    client = get_client()
    file_size = os.path.getsize(audio_path)

    if file_size > MAX_FILE_SIZE_BYTES:
        return _transcribe_in_chunks(audio_path, language)

    return _transcribe_single(client, audio_path, language)


def _transcribe_single(client: Groq, audio_path: str, language: str = None, prompt: str = None) -> str:
    kwargs = {
        "model": GROQ_MODEL,
        "response_format": "text",
    }
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["prompt"] = prompt

    with open(audio_path, "rb") as audio_file:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), audio_file, "audio/mpeg"),
            **kwargs,
        )

    return result if isinstance(result, str) else result.text


def _transcribe_in_chunks(audio_path: str, language: str = None) -> str:
    """Split large audio into chunks and transcribe each one."""
    import subprocess
    import tempfile

    file_size = os.path.getsize(audio_path)
    num_chunks = math.ceil(file_size / MAX_FILE_SIZE_BYTES)

    # Get audio duration in seconds
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    total_duration = float(result.stdout.strip())
    chunk_duration = total_duration / num_chunks

    client = get_client()
    transcripts = []
    prev_text = ""

    with tempfile.TemporaryDirectory() as chunk_dir:
        for i in range(num_chunks):
            start = i * chunk_duration
            chunk_path = os.path.join(chunk_dir, f"chunk_{i}.mp3")

            subprocess.run([
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(start), "-t", str(chunk_duration),
                "-acodec", "libmp3lame", "-q:a", "4",
                chunk_path
            ], capture_output=True)

            # Pass last ~200 chars of previous chunk as prompt for continuity
            prompt = " ".join(prev_text.split()[-30:]) if prev_text else None
            chunk_text = _transcribe_single(client, chunk_path, language, prompt=prompt)
            chunk_text = chunk_text.strip()
            transcripts.append(chunk_text)
            prev_text = chunk_text

    return "\n\n".join(transcripts)
