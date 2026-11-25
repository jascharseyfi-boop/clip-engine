"""Einstellungen: .env auf dem Server, überschrieben von data/settings.json (Setup-Seite im Browser)."""
import json
import os
from pathlib import Path

SETTINGS_FILE = Path(os.environ.get("DATA_DIR", "/data")) / "settings.json"
if SETTINGS_FILE.exists():
    try:
        for _k, _v in json.loads(SETTINGS_FILE.read_text()).items():
            if _v not in (None, ""):
                os.environ[_k] = str(_v)
    except (ValueError, OSError):
        pass

SETUP_KEYS = ["ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "VIZARD_API_KEY",
              "VIZARD_TIKTOK_ACCOUNT_ID", "CLAUDE_MODEL", "SLOTS_YOUTUBE", "SLOTS_TIKTOK"]


def _env(name, default=None, cast=str):
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    if cast is bool:
        return val.strip().lower() in ("1", "true", "yes", "ja", "on")
    return cast(val)


DATA_DIR = Path(_env("DATA_DIR", "/data"))
JOBS_DIR = DATA_DIR / "jobs"
ASSETS_DIR = DATA_DIR / "assets"          # logo.png hier ablegen
FONTS_DIR = Path(_env("FONTS_DIR", str(Path(__file__).resolve().parent.parent / "fonts")))

API_KEY = _env("CLIP_ENGINE_API_KEY", "")          # Pflicht für alle Endpunkte außer /public und /health
SETUP_CODE = _env("SETUP_CODE", "")                 # schützt die Setup-Seite
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "").rstrip("/")  # z. B. https://clips.188-245-25-76.sslip.io

# Claude
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-sonnet-5-5")
ANTHROPIC_WORKSPACE_ID = _env("ANTHROPIC_WORKSPACE_ID", "")   # nur für Keys ohne festen Workspace
ANTHROPIC_VERSION = _env("ANTHROPIC_VERSION", "2023-06-01")
MAX_SHEETS = _env("MAX_SHEETS", 10, int)           # Kontaktabzüge pro Video an Claude (je 12 Bilder)

# Transkription
WHISPER_MODEL = _env("WHISPER_MODEL", "small")     # tiny / base / small / medium
WHISPER_THREADS = _env("WHISPER_THREADS", 2, int)

# Szenen / Schnitt
SCENE_THRESHOLD = _env("SCENE_THRESHOLD", 0.30, float)
MIN_LEN = _env("CLIP_MIN_LEN", 18, float)
MAX_LEN = _env("CLIP_MAX_LEN", 45, float)
HERO_SECONDS = _env("HERO_SECONDS", 1.2, float)    # Länge des vorangestellten stärksten Bildes; 0 = aus
HOOK_SECONDS = _env("HOOK_SECONDS", 3.5, float)    # wie lange der Hook-Text steht; 0 = ganzer Clip
LAYOUT = _env("LAYOUT", "blur")                    # blur | crop | fit
FG_ZOOM = _env("FG_ZOOM", 1.15, float)             # Vergrößerung des Videos im blur-Layout
CRF = _env("CRF", 21, int)
X264_PRESET = _env("X264_PRESET", "veryfast")
LOGO_FILE = _env("LOGO_FILE", "logo.png")          # in ASSETS_DIR
LOGO_WIDTH = _env("LOGO_WIDTH", 170, int)

# Telegram (eigener Bot für die Clip-Engine)
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")

# Veröffentlichungs-Slots (Uhrzeit Europe/Berlin, kommagetrennt). 01:00 Berlin = 19:00 New York
TIMEZONE = _env("TIMEZONE", "Europe/Berlin")
SLOTS_YOUTUBE = _env("SLOTS_YOUTUBE", "01:00")
SLOTS_TIKTOK = _env("SLOTS_TIKTOK", "18:00")

# Vizard (TikTok-Posting)
VIZARD_API_KEY = _env("VIZARD_API_KEY", "")
VIZARD_TIKTOK_ACCOUNT_ID = _env("VIZARD_TIKTOK_ACCOUNT_ID", "")

# Aufräumen
KEEP_DAYS = _env("KEEP_DAYS", 7, int)

for d in (JOBS_DIR, ASSETS_DIR):
    d.mkdir(parents=True, exist_ok=True)
