import os
import math
import subprocess
import tempfile
from groq import Groq

MAX_FILE_SIZE_BYTES = 24 * 1024 * 1024  # 25 MB Groq limit (1 MB buffer)
WHISPER_MODEL = "whisper-large-v3"
LLM_MODEL = "llama-3.3-70b-versatile"
DEFAULT_LANGUAGE = "hi"

# Hint Whisper it's processing speech, not music — reduces hallucinations
WHISPER_INITIAL_PROMPT = (
    "This is a Hindi and English conversation. "
    "Transcribe only the spoken words. Ignore background music."
)

_FORMAT_PROMPTS: dict[str, str] = {
    "hi": (
        "You are a transcript formatter. The input is a raw Whisper transcription of Hindi/English speech. "
        "Your ONLY jobs are: remove filler sounds (um, uh, hmm, aaa), fix obvious Whisper spelling mistakes. "
        "STRICT RULES — you will be penalised for breaking these:\n"
        "- Do NOT change, replace, or paraphrase ANY word. 'tere' stays 'tere', 'khwaab' stays 'khwaab'.\n"
        "- Do NOT translate or substitute synonyms.\n"
        "- Do NOT rewrite sentences.\n"
        "- Keep every Hindi and English word exactly as spoken.\n"
        "Output only the lightly cleaned transcript in Devanagari script."
    ),
    "en": (
        "You are a literal translator. Translate the following Hindi transcript word-for-word into English. "
        "STRICT RULES:\n"
        "- Translate as literally as possible — do not paraphrase.\n"
        "- Do not change the meaning or substitute words with synonyms.\n"
        "- Keep proper nouns, names, and brand names exactly as-is.\n"
        "Output only the translated text."
    ),
    "hinglish": (
        "You are a transliterator. Convert the Hindi Devanagari transcript into Roman script (Hinglish). "
        "STRICT RULES — you will be penalised for breaking these:\n"
        "- Transliterate each word phonetically, word-for-word. Do NOT change any word.\n"
        "- 'तेरे' → 'tere' (not 'tumhare'). 'ख्वाब' → 'khwaab' (not 'sapne').\n"
        "- Do NOT translate, paraphrase, or substitute synonyms. Ever.\n"
        "- Keep English words that appear in the transcript exactly as-is.\n"
        "- Preserve sentence structure exactly.\n\n"
        "Example:\n"
        "Input: तेरे ख्वाब मेरे दिल में हैं\n"
        "Output: tere khwaab mere dil mein hain\n\n"
        "Output only the transliterated text."
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
    """Transcribe audio and convert to the requested output format."""
    # None means "use default"; "auto" means genuine Whisper auto-detect
    lang = None if language == "auto" else (language or DEFAULT_LANGUAGE)
    client = get_client()
    file_size = os.path.getsize(audio_path)

    if file_size > MAX_FILE_SIZE_BYTES:
        raw = _transcribe_in_chunks(client, audio_path, lang)
    else:
        raw = _transcribe_single(client, audio_path, lang)

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
    # Build prompt: chain initial hint + continuity context from previous chunk
    combined_prompt = WHISPER_INITIAL_PROMPT
    if prompt:
        combined_prompt = combined_prompt + " " + prompt

    kwargs: dict = {
        "model": WHISPER_MODEL,
        "response_format": "text",
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


def _transcribe_in_chunks(
    client: Groq, audio_path: str, language: str | None
) -> str:
    """Split large audio into chunks and transcribe with context continuity."""
    file_size = os.path.getsize(audio_path)
    num_chunks = math.ceil(file_size / MAX_FILE_SIZE_BYTES)

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
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

            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", audio_path,
                    "-ss", str(start), "-t", str(chunk_duration),
                    "-acodec", "libmp3lame", "-q:a", "2",
                    chunk_path,
                ],
                capture_output=True,
            )

            # Last 30 words of previous chunk = continuity context
            context = " ".join(prev_text.split()[-30:]) if prev_text else None
            chunk_text = _transcribe_single(client, chunk_path, language, prompt=context).strip()

            # Skip chunks that are clearly just music/noise (very short or empty)
            if len(chunk_text) > 5:
                transcripts.append(chunk_text)
                prev_text = chunk_text

    return "\n\n".join(transcripts)


def _devanagari_to_roman(text: str) -> str:
    """Mechanical character-level transliteration — no word changes possible."""
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate

    result = transliterate(text, sanscript.DEVANAGARI, sanscript.ITRANS)

    # Make ITRANS output more readable as colloquial Hinglish
    replacements = [
        ("aa", "aa"), ("A", "aa"),
        ("ii", "ee"), ("I", "ee"),
        ("uu", "oo"), ("U", "oo"),
        ("M", "n"),   (".n", "n"),
        (".h", ""),   ("~", ""),
    ]
    for old, new in replacements:
        result = result.replace(old, new)

    return result


def _apply_format(client: Groq, text: str, output_format: str) -> str:
    """Convert transcript to the requested output format."""
    if output_format == "hinglish":
        # Pure mechanical transliteration — no LLM, no word changes
        return _devanagari_to_roman(text)

    # For hi and en, use LLM (cleanup/translation only)
    system_prompt = _FORMAT_PROMPTS.get(output_format, _FORMAT_PROMPTS["hi"])
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()
