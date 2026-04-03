import os
import math
import subprocess
import tempfile
from groq import Groq

MAX_FILE_SIZE_BYTES = 24 * 1024 * 1024  # 25MB Groq limit (1MB buffer)
WHISPER_MODEL = "whisper-large-v3"
LLM_MODEL = "llama-3.3-70b-versatile"

# Default to Hindi — prevents Whisper from mis-detecting as Urdu
DEFAULT_LANGUAGE = "hi"

_FORMAT_PROMPTS: dict[str, str] = {
    "hi": (
        "You are a transcript cleaner. The input is a raw Hindi transcript "
        "(may contain some English words). Output it cleaned up in proper Hindi "
        "Devanagari script. Fix any obvious errors, remove filler words like 'um', "
        "'uh', 'aaa'. Do NOT translate — keep the language as Hindi. "
        "Return only the cleaned transcript, nothing else."
    ),
    "en": (
        "You are a professional translator. Translate the following Hindi transcript "
        "into fluent, natural English. Preserve the meaning and tone. "
        "Return only the translated text, nothing else."
    ),
    "hinglish": (
        "You are a Hinglish writer. Rewrite the following Hindi transcript in Hinglish — "
        "Roman script (English alphabet) representation of Hindi speech, mixed naturally "
        "with English words as Indians speak in everyday conversation. "
        "Example style: 'Aaj main ek naya project shuru kar raha hoon jo bahut exciting hai.' "
        "Return only the Hinglish text, nothing else."
    ),
}


def get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def transcribe_file(audio_path: str, language: str | None = None, output_format: str = "hi") -> str:
    """Transcribe audio and convert to the requested output format."""
    lang = language if language else DEFAULT_LANGUAGE
    client = get_client()
    file_size = os.path.getsize(audio_path)

    if file_size > MAX_FILE_SIZE_BYTES:
        raw = _transcribe_in_chunks(client, audio_path, lang)
    else:
        raw = _transcribe_single(client, audio_path, lang)

    return _apply_format(client, raw.strip(), output_format)


def _transcribe_single(
    client: Groq, audio_path: str, language: str, prompt: str | None = None
) -> str:
    kwargs: dict = {
        "model": WHISPER_MODEL,
        "response_format": "text",
        "language": language,
    }
    if prompt:
        kwargs["prompt"] = prompt

    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f, "audio/mpeg"),
            **kwargs,
        )

    return result if isinstance(result, str) else result.text


def _transcribe_in_chunks(client: Groq, audio_path: str, language: str) -> str:
    """Split large audio into chunks and transcribe with context continuity."""
    file_size = os.path.getsize(audio_path)
    num_chunks = math.ceil(file_size / MAX_FILE_SIZE_BYTES)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True,
    )
    total_duration = float(probe.stdout.strip())
    chunk_duration = total_duration / num_chunks

    transcripts: list[str] = []
    prev_text = ""

    with tempfile.TemporaryDirectory() as chunk_dir:
        for i in range(num_chunks):
            start = i * chunk_duration
            chunk_path = os.path.join(chunk_dir, f"chunk_{i}.mp3")

            subprocess.run([
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(start), "-t", str(chunk_duration),
                "-acodec", "libmp3lame", "-q:a", "2",
                chunk_path,
            ], capture_output=True)

            prompt = " ".join(prev_text.split()[-30:]) if prev_text else None
            chunk_text = _transcribe_single(client, chunk_path, language, prompt=prompt).strip()
            transcripts.append(chunk_text)
            prev_text = chunk_text

    return "\n\n".join(transcripts)


def _apply_format(client: Groq, text: str, output_format: str) -> str:
    """Use LLM to clean/translate/transliterate the raw transcript."""
    system_prompt = _FORMAT_PROMPTS.get(output_format, _FORMAT_PROMPTS["hi"])

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()
