"""
config.py — Loads configuration from the .env file
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"❌ Environment variable '{key}' is not set!\n"
            f"   Copy .env.example → .env and fill in the values."
        )
    return value.strip()


# ── Twitter ───────────────────────────────────────────────────────────────────
# Single fallback (legacy support)
TWITTER_USERNAME: str = os.getenv("TWITTER_USERNAME", "").strip()

# Comma-separated list of usernames to monitor
_usernames_raw = os.getenv("TWITTER_USERNAMES", "")
if _usernames_raw.strip():
    TWITTER_USERNAMES: list[str] = [u.strip() for u in _usernames_raw.split(",") if u.strip()]
else:
    if TWITTER_USERNAME:
        TWITTER_USERNAMES = [TWITTER_USERNAME]
    else:
        raise EnvironmentError(
            "❌ Environment variable 'TWITTER_USERNAMES' or 'TWITTER_USERNAME' must be set!"
        )

# Cookies for X Client authentication
TWITTER_COOKIE_AUTH_TOKEN: str = os.getenv("TWITTER_COOKIE_AUTH_TOKEN", "").strip()
TWITTER_COOKIE_CT0: str = os.getenv("TWITTER_COOKIE_CT0", "").strip()

# Proxy for X Client API requests
TWITTER_PROXY: str = os.getenv("TWITTER_PROXY", "").strip()

# Gemini AI API Key(s) - split by comma to support multiple keys for rotation
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_API_KEYS: list[str] = [k.strip() for k in GEMINI_API_KEY.split(",") if k.strip()]

# ── AI Provider Configuration ──────────────────────────────────────────────────
# Provider: 'gemini' (direct) or 'openai_compatible' (OpenRouter, Groq, Ollama, DeepSeek, etc.)
AI_PROVIDER: str = os.getenv("AI_PROVIDER", "gemini").strip().lower()

# API Key for the selected provider (falls back to GEMINI_API_KEY)
AI_API_KEY: str = os.getenv("AI_API_KEY", "").strip() or GEMINI_API_KEY

# Endpoint URL for OpenAI-compatible providers
AI_API_URL: str = os.getenv("AI_API_URL", "https://openrouter.ai/api/v1/chat/completions").strip()

# Model name to use (e.g. google/gemini-2.5-flash:free or llama-3-8b-instruct:free)
AI_MODEL: str = os.getenv("AI_MODEL", "google/gemini-2.5-flash:free").strip()


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")

# ── Bot behaviour ─────────────────────────────────────────────────────────────
# How often to check for new tweets, in minutes (min: 1, recommended: 5)
CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))

# Number of batches to split the accounts into (default: 2)
NUM_BATCHES: int = int(os.getenv("NUM_BATCHES", "2"))

# Number of tweets to fetch on each check (also used on startup)
INITIAL_FETCH_COUNT: int = int(os.getenv("INITIAL_FETCH_COUNT", "10"))

# Path to the file that stores seen tweet IDs
STATE_FILE = Path(__file__).parent / os.getenv("STATE_FILE", "seen_tweets.json")

# ── Nitter instances (fallback list) ──────────────────────────────────────────
NITTER_INSTANCES: list[str] = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
]
