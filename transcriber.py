import os
import math
import subprocess
import tempfile
from groq import Groq
import vocab

MAX_FILE_SIZE_BYTES = 24 * 1024 * 1024  # 25 MB Groq limit
WHISPER_MODEL = "whisper-large-v3"
CLEANUP_MODEL = "llama-3.3-70b-versatile"   # pass 1 — fix Devanagari errors
HINGLISH_MODEL = "qwen/qwen3-32b"            # pass 2 — transliterate to Roman
DEFAULT_LANGUAGE = "hi"

CHUNK_SECONDS = 15  # shorter chunks = fewer skipped lines

# Rich Urdu/Hindi shayri vocabulary — guides Whisper towards poetic words
_WHISPER_BASE_PROMPT = (
    "شاعری، غزل، نظم، مشاعرہ۔ "
    "فرہاد، مجنوں، جنازہ، تراشے، تماشے، دلاسے، رخصت، مختصر، خلاصے، یاروں، "
    "لفافے، خط، غم، درد، وقت، خیر، خبر، بیمار، انتظار، جدائی، محفل، عاشق، "
    "محبت، عشق، زندگی، صبح، رات، آنکھیں، آنسو، خواب، یاد، شاعر، ادا، وفا، "
    "جفا، ستم، قرار، بے قرار، تنہا، دیوانہ، پروانہ، شمع، چراغ، آشنا۔"
)


def _build_whisper_prompt(context: str | None = None) -> str:
    parts = [_WHISPER_BASE_PROMPT]
    extra = vocab.whisper_hint_words()
    if extra:
        parts.append(extra)
    if context:
        parts.append(context)
    return " ".join(parts)


# ── Pass 1: Clean raw Whisper Devanagari output ──────────────────────────────

_CLEANUP_PROMPT = """You are a Hindi/Urdu shayri expert and transcript corrector.

INPUT: Raw Whisper transcription of spoken Hindi/Urdu shayri — may have mishearings, wrong words, broken lines.
TASK: Correct only clear errors. Return clean Devanagari text.

RULES:
1. Fix obvious Whisper mishearings using your knowledge of Urdu/Hindi shayri vocabulary.
   Known corrections for this content: {corrections}
2. Preserve the exact meter, rhythm, and meaning — do NOT add, remove, or paraphrase words.
3. Each sher/couplet on its own line. Misra (half-lines) separated by a line break.
4. Fix broken words that Whisper split incorrectly.
5. Remove non-speech sounds (uh, hmm, clapping) if present.
6. Output ONLY the corrected Devanagari text — no commentary, no labels."""

# ── Pass 2: Transliterate clean Devanagari → Hinglish Roman ─────────────────

_HINGLISH_PROMPT = """You are a Hinglish transliteration expert specialising in Urdu/Hindi shayri.

INPUT: Clean Devanagari Hindi/Urdu shayri text.
TASK: Transliterate every single word phonetically to Roman (Hinglish) script.

STRICT RULES:
1. Every Devanagari word → Roman phonetic equivalent. No exceptions.
2. Do NOT translate meaning. Preserve the Urdu/Hindi word:
   दिल=dil  रात=raat  दर्द=dard  इश्क=ishq  ज़िंदगी=zindagi
   वक़्त=waqt  आँखें=aankhein  आँसू=aansu  ख़्वाब=khwaab  यादें=yaadein
3. Phonetic accuracy:
   - aspirated stops: ख=kh  घ=gh  छ=chh  झ=jh  ठ=tth  ढ=ddh  थ=th  भ=bh
   - sibilants: श/ष=sh  स=s  ज़=z  ष=sh
   - nasals endings: मैं=main  हूँ=hoon  हैं=hain  नहीं=nahin
   - anusvara/chandrabindu: अंत=ant  माँ=maa  हाँ=haan
4. Common shayri words standard spellings:
   mohabbat, zindagi, dard, gham, ishq, dil, raat, subah, aankhein, aansu,
   khwaab, yaad, intezaar, judai, mehfil, shayar, wafaa, jafaa, qarar,
   beqarar, tanhaai, deewana, parwana, shamaa, chiragh, aashna, rukhsat,
   mukhtasar, janaze, tamaashe, dilaase, yaaron, lifaafe, farhad, majnoon
5. Each sher stays on its own line. Do NOT merge or split lines.
6. Output ONLY Roman script text — absolutely zero Devanagari characters.
7. No labels, no commentary, no explanations."""

# ── Single-pass formats (hi, en) ─────────────────────────────────────────────

_FORMAT_PROMPTS: dict[str, str] = {
    "hi": (
        "You are a Hindi/Urdu shayri transcript editor.\n"
        "Input: raw Whisper transcription of Hindi/Urdu shayri (may have errors).\n"
        "Task: Clean it up — fix spelling errors, fix clear mishearings, remove filler sounds.\n"
        "Known corrections: {corrections}\n"
        "Rules:\n"
        "- Preserve every word exactly — do NOT replace with synonyms or paraphrase.\n"
        "- Write in proper Devanagari script.\n"
        "- Each sher/couplet on its own line.\n"
        "- Output only the cleaned transcript."
    ),
    "en": (
        "You are a translator of Hindi/Urdu shayri into English.\n"
        "Input: raw Whisper transcription (may have mishearings).\n"
        "Task: First mentally correct obvious Whisper errors, then translate each line poetically.\n"
        "Rules:\n"
        "- Do not paraphrase — keep the meaning close to the original.\n"
        "- Preserve the poetic structure — each sher on its own line.\n"
        "- Output only the translated English text."
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
        "temperature": 0,
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

            context = " ".join(prev_text.split()[-20:]) if prev_text else None
            chunk_text = _transcribe_single(
                client, chunk_path, language, prompt=context
            ).strip()

            if chunk_text:
                transcripts.append(chunk_text)
                prev_text = chunk_text

    return "\n".join(transcripts)


def _llm_call(client: Groq, model: str, system: str, user: str) -> str:
    """Single LLM call, strips any Qwen3 <think>...</think> blocks from output."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=4096,
    )
    text = response.choices[0].message.content.strip()
    # Qwen3 sometimes emits <think>...</think> reasoning blocks — strip them
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text


def _apply_format(client: Groq, text: str, output_format: str) -> str:
    corrections = vocab.llm_correction_examples()

    if output_format == "hinglish":
        # Pass 1 — clean the raw Devanagari
        clean_system = _CLEANUP_PROMPT.replace("{corrections}", corrections or "none")
        clean_hindi = _llm_call(client, CLEANUP_MODEL, clean_system, text)

        # Pass 2 — transliterate clean Devanagari → Hinglish
        return _llm_call(client, HINGLISH_MODEL, _HINGLISH_PROMPT, clean_hindi)

    # Single-pass for hi / en
    template = _FORMAT_PROMPTS.get(output_format, _FORMAT_PROMPTS["hi"])
    system_prompt = template.replace("{corrections}", corrections or "none")
    return _llm_call(client, CLEANUP_MODEL, system_prompt, text)
