"""Nächsten freien Veröffentlichungs-Slot pro Plattform finden (ein Clip pro Slot)."""
import json
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config

_lock = threading.Lock()
_FILE = config.DATA_DIR / "slots.json"


def _load() -> dict:
    if _FILE.exists():
        return json.loads(_FILE.read_text())
    return {}


def _slots(platform: str) -> list[tuple[int, int]]:
    raw = config.SLOTS_YOUTUBE if platform == "youtube" else config.SLOTS_TIKTOK
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            h, m = part.split(":")
            out.append((int(h), int(m)))
    return sorted(out) or [(1, 0)]


def book(platform: str, ref: str, now: datetime | None = None) -> datetime:
    """Reserviert den nächsten freien Slot ab jetzt + 30 min und gibt ihn (UTC) zurück."""
    tz = ZoneInfo(config.TIMEZONE)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    earliest = now + timedelta(minutes=30)
    with _lock:
        data = _load()
        taken = set(data.get(platform, {}).keys())
        day = earliest.date()
        for _ in range(400):
            for h, m in _slots(platform):
                cand = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
                key = cand.astimezone(timezone.utc).isoformat()
                if cand >= earliest and key not in taken:
                    data.setdefault(platform, {})[key] = ref
                    _FILE.write_text(json.dumps(data, indent=1))
                    return cand.astimezone(timezone.utc)
            day += timedelta(days=1)
    raise RuntimeError("kein freier Slot gefunden")


def release(platform: str, ref: str) -> None:
    with _lock:
        data = _load()
        slots = data.get(platform, {})
        for k in [k for k, v in slots.items() if v == ref]:
            del slots[k]
        _FILE.write_text(json.dumps(data, indent=1))
