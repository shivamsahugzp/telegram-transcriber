"""Persistent vocabulary — Whisper mishearing corrections for Urdu/Hindi shayri."""
import json
import os
import logging

logger = logging.getLogger(__name__)

VOCAB_FILE = os.environ.get("VOCAB_FILE", "/data/shayri_vocab.json")

# Base corrections hardcoded — never lost on restart
BASE_CORRECTIONS: dict[str, str] = {
    "taraache": "tarashe",
    "tamache": "tamashe",
    "dilaate": "dilaase",
    "janaade": "janaze",
    "janaadon": "janaze",
    "janadon": "janaze",
    "parwaad": "farhad",
    "parwaadon": "farhad",
    "rukhteeti": "rukhsat",
    "rukhteeton": "rukhsat",
    "mukhsatar": "mukhtasar",
    "makhtsaar": "mukhtasar",
    "jaro": "yaaron",
    "jaaro": "yaaron",
    "khulashe": "khulase",
    "lifaphe": "lifaafe",
    "dsh": "dash",
}

# In-memory learned corrections (loaded from file on startup)
_learned: dict[str, str] = {}


def init() -> None:
    """Load persisted corrections from file."""
    global _learned
    if os.path.exists(VOCAB_FILE):
        try:
            with open(VOCAB_FILE) as f:
                _learned = json.load(f)
            logger.info("Vocab loaded — %d learned corrections", len(_learned))
        except Exception as e:
            logger.warning("Could not load vocab file: %s", e)
            _learned = {}
    else:
        _learned = {}


def _save() -> None:
    try:
        with open(VOCAB_FILE, "w") as f:
            json.dump(_learned, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Could not save vocab: %s", e)


def add(wrong: str, correct: str) -> None:
    """Learn a new correction."""
    _learned[wrong.lower().strip()] = correct.lower().strip()
    _save()


def remove(wrong: str) -> bool:
    """Remove a learned correction. Returns True if it existed."""
    key = wrong.lower().strip()
    if key in _learned:
        del _learned[key]
        _save()
        return True
    return False


def all_corrections() -> dict[str, str]:
    """Merged base + learned corrections (learned takes priority)."""
    return {**BASE_CORRECTIONS, **_learned}


def learned_corrections() -> dict[str, str]:
    return dict(_learned)


def whisper_hint_words() -> str:
    """Extra correct words to inject into the Whisper prompt."""
    correct_words = set(all_corrections().values())
    return ", ".join(sorted(correct_words))


def llm_correction_examples() -> str:
    """Format corrections as examples for the LLM prompt."""
    corrections = all_corrections()
    if not corrections:
        return ""
    examples = ", ".join(f"'{w}'→'{c}'" for w, c in list(corrections.items())[:20])
    return f"Known Whisper corrections for this content: {examples}"
