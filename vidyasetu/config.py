"""All settings come from environment variables (or a .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    if not os.getenv("VIDYASETU_NO_DOTENV"):  # tests set this so a real .env can never leak in
        load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("VIDYASETU_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- IBM watsonx.ai -------------------------------------------------------
WATSONX_URL = (os.getenv("WATSONX_URL") or "https://us-south.ml.cloud.ibm.com").rstrip("/")
WATSONX_API_KEY = os.getenv("WATSONX_API_KEY", "").strip()
WATSONX_PROJECT_ID = os.getenv("WATSONX_PROJECT_ID", "").strip()
IAM_URL = os.getenv("IBM_IAM_URL", "https://iam.cloud.ibm.com/identity/token")
HAVE_KEYS = bool(WATSONX_API_KEY and WATSONX_PROJECT_ID)  # watsonx credentials present

# Model ids differ by region/account. Run `python scripts/list_models.py` to see
# the ids your project can actually use, then override these in .env.
GEN_MODEL = os.getenv("GEN_MODEL") or "ibm/granite-4-h-small"
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL") or GEN_MODEL
EMBED_MODEL = os.getenv("EMBED_MODEL") or "ibm/granite-embedding-278m-multilingual"

CHAT_API_VERSION = os.getenv("WATSONX_CHAT_VERSION", "2024-10-08")
EMBED_API_VERSION = os.getenv("WATSONX_EMBED_VERSION", "2024-05-31")

# --- Sarvam AI ------------------------------------------------------------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
SARVAM_BASE_URL = (os.getenv("SARVAM_BASE_URL") or "https://api.sarvam.ai").rstrip("/")
SARVAM_MODEL = os.getenv("SARVAM_MODEL") or "sarvam-105b"
SARVAM_RPM = int(os.getenv("SARVAM_RPM") or "36")  # Starter plan allows 40/min for sarvam-105b
SARVAM_MAX_TOKENS = int(os.getenv("SARVAM_MAX_TOKENS") or "4096")  # Starter plan cap
SARVAM_TTS_MODEL = os.getenv("SARVAM_TTS_MODEL") or "bulbul:v3"
SARVAM_TTS_SPEAKER = os.getenv("SARVAM_TTS_SPEAKER") or "ishita"
SARVAM_TTS_RPM = int(os.getenv("SARVAM_TTS_RPM") or "27")  # bulbul:v3 allows 30/min on Starter
HAVE_SARVAM = bool(SARVAM_API_KEY)

# --- Which provider does which job -----------------------------------------
# Each of these is: auto | watsonx | sarvam | mock   (EMBED: auto | watsonx | local)
#   GEN_PROVIDER        simplify, quizzes, textbook answers, topic lessons
#   TRANSLATE_PROVIDER  translation, back-translation (quality check), question -> English
#   EMBED_PROVIDER      textbook search + translation check
#   SPEECH_PROVIDER     auto | sarvam | browser   (audio for "Listen")
# "auto" picks whatever you have keys for: Granite first for generation, Sarvam first
# for translation, otherwise demo (mock) mode.
# VIDYASETU_MODE=mock forces demo mode regardless of keys.
_FORCE_MOCK = os.getenv("VIDYASETU_MODE", "auto").lower() == "mock"
_LIVE_ONLY = os.getenv("VIDYASETU_MODE", "auto").lower() == "live"


def _pick(env: str, order: list[str], fallback: str) -> str:
    if _FORCE_MOCK:
        return fallback
    value = (os.getenv(env) or "auto").lower()
    if value != "auto":
        return value
    have = {"watsonx": HAVE_KEYS, "sarvam": HAVE_SARVAM}
    for name in order:
        if have[name]:
            return name
    return "watsonx" if _LIVE_ONLY else fallback  # live mode with no keys should fail loudly


GEN_PROVIDER = _pick("GEN_PROVIDER", ["watsonx", "sarvam"], "mock")
TRANSLATE_PROVIDER = _pick("TRANSLATE_PROVIDER", ["sarvam", "watsonx"], "mock")
EMBED_PROVIDER = _pick("EMBED_PROVIDER", ["watsonx"], "local")
if EMBED_PROVIDER not in ("watsonx", "local"):
    EMBED_PROVIDER = "local"
_speech = (os.getenv("SPEECH_PROVIDER") or "auto").lower()
SPEECH_PROVIDER = "sarvam" if (not _FORCE_MOCK and HAVE_SARVAM and _speech in ("auto", "sarvam")) else "browser"

# True only when nothing real is configured (the whole app is placeholder AI)
MOCK = GEN_PROVIDER == "mock" and TRANSLATE_PROVIDER == "mock"

# --- Pipeline tuning ------------------------------------------------------
# Neural embeddings (watsonx) compare meaning; the built-in local embedder compares words,
# so back-translations (which re-word things) score lower and need a lower bar.
QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD") or ("0.80" if EMBED_PROVIDER == "watsonx" else "0.50"))
MAX_PARAGRAPHS = int(os.getenv("MAX_PARAGRAPHS", "40"))
WORKERS = int(os.getenv("VIDYASETU_WORKERS", "3"))
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "auto").lower()  # auto | chroma | sqlite
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
