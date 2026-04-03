import os
import math
import subprocess
import tempfile
from groq import Groq
import vocab

MAX_FILE_SIZE_BYTES = 24 * 1024 * 1024  # 25 MB Groq limit
WHISPER_MODEL = "whisper-large-v3"
LLM_MODEL = "llama-3.3-70b-versatile"
DEFAULT_LANGUAGE = "hi"

# 30-second chunks — Whisper processes audio in 30s windows internally.
# Forcing this prevents it from stopping early on long videos with background music.
CHUNK_SECONDS = 15  # shorter chunks = fewer skipped lines

# Urdu/Hindi shayri vocabulary — helps Whisper recognise poetic words correctly
_WHISPER_BASE_PROMPT = (
    "شاعری، غزل، نظم۔ "
    "فرہاد، مجنوں، جنازہ، تراشے، تماشے، دلاسے، رخصت، مختصر، خلاصے، یاروں، "
    "لفافے، خط، غم، درد، وقت، خیر، خبر، بیمار، انتظار، جدائی، محفل، عاشق۔"
)


def _build_whisper_prompt(context: str | None = None) -> str:
    parts = [_WHISPER_BASE_PROMPT]
    extra = vocab.whisper_hint_words()
    if extra:
        parts.append(extra)
    if context:
        parts.append(context)
    return " ".join(parts)

_FORMAT_PROMPTS: dict[str, str] = {
    "hi": (
        "You are a Hindi shayri transcript editor. "
        "The input is a raw Whisper transcription of Hindi shayri or poetry. "
        "Clean it up: fix spelling errors, remove filler sounds. "
        "Preserve every word exactly — do NOT replace any word with a synonym. "
        "Write in proper Devanagari script. Keep each sher/line on its own line. "
        "Output only the cleaned transcript."
    ),
    "en": (
        "You are a translator of Hindi shayri into English. "
        "Translate each line poetically but accurately. "
        "Do not paraphrase — keep the meaning close to the original. "
        "Output only the translated text."
    ),
    "hinglish": (
        "You are converting Devanagari Hindi/Urdu shayri into Hinglish Roman script.\n\n"
        "Input: Devanagari text from Whisper (may have errors).\n"
        "Output: Roman script Hinglish — phonetic transliteration of every word.\n\n"
        "RULES:\n"
        "1. Transliterate every Devanagari word into Roman letters phonetically.\n"
        "2. Fix clear Whisper mishearings using Urdu shayri knowledge. Examples:\n"
        "   तराचे→tarashe, तमाचे→tamashe, दिलाते→dilaase, जनादे→janaze, "
        "   परवाद→farhad, रुखतीती→rukhsat, मुखतसर→mukhtasar, जारो→yaaron\n"
        "   {corrections}\n"
        "3. Do NOT translate — 'dil' not 'heart', 'raat' not 'night'\n"
        "4. Do NOT skip or add any lines\n"
        "5. Put each sher on its own line\n"
        "6. Output ONLY Roman script — no Devanagari characters at all\n\n"
        "Example:\n"
        "Input: तेरे ख्वाबों में सो जाता हूँ मैं\n"
        "Output: tere khwabon mein so jaata hoon main"
    ),
}


def get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


def transcribe_file(
    audio_path: str,
    language: str | None = None,
    output_format: str = "hi",
) -> str:
    lang = None if language == "auto" else (language or DEFAULT_LANGUAGE)
    client = get_client()

    # Always chunk by time — ensures Whisper covers the full audio
    # even when background music causes early stopping
    raw = _transcribe_in_chunks(client, audio_path, lang)

    raw = raw.strip()
    if not raw:
        return ""

    return _apply_format(client, raw, output_format)


def _transcribe_single(
    client: Groq,
    audio_path: str,
    language: str | None,
    prompt: str | None = None,
) -> str:
    combined_prompt = _build_whisper_prompt(context=prompt)

    kwargs: dict = {
        "model": WHISPER_MODEL,
        "response_format": "text",
        "temperature": 0,           # deterministic — no random skipping
        "prompt": combined_prompt,
    }
    if language:
        kwargs["language"] = language

    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f, "audio/mpeg"),
            **kwargs,
        )

    return result if isinstance(result, str) else result.text


def _get_duration(audio_path: str) -> float:
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True, text=True,
    )
    return float(probe.stdout.strip())


def _transcribe_in_chunks(
    client: Groq, audio_path: str, language: str | None
) -> str:
    """Split audio into fixed 30-second chunks so Whisper covers everything."""
    total_duration = _get_duration(audio_path)
    num_chunks = max(1, math.ceil(total_duration / CHUNK_SECONDS))

    transcripts: list[str] = []
    prev_text = ""

    with tempfile.TemporaryDirectory() as chunk_dir:
        for i in range(num_chunks):
            start = i * CHUNK_SECONDS
            chunk_path = os.path.join(chunk_dir, f"chunk_{i}.mp3")

            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", audio_path,
                    "-ss", str(start), "-t", str(CHUNK_SECONDS),
                    "-acodec", "libmp3lame", "-q:a", "2",
                    chunk_path,
                ],
                capture_output=True,
            )

            if not os.path.exists(chunk_path):
                continue

            # Pass last 20 words of previous chunk so Whisper knows the context
            context = " ".join(prev_text.split()[-20:]) if prev_text else None
            chunk_text = _transcribe_single(
                client, chunk_path, language, prompt=context
            ).strip()

            if chunk_text:
                transcripts.append(chunk_text)
                prev_text = chunk_text

    return "\n".join(transcripts)


def _apply_format(client: Groq, text: str, output_format: str) -> str:
    template = _FORMAT_PROMPTS.get(output_format, _FORMAT_PROMPTS["hi"])
    system_prompt = template.replace("{corrections}", vocab.llm_correction_examples())

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0,
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()
