"""Telegram: Clips zur Freigabe schicken, Buttons beantworten."""
import json
from pathlib import Path

import httpx

from . import config


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN)


def _call(method: str, data: dict | None = None, files: dict | None = None, timeout: float = 30) -> dict:
    if not enabled():
        return {"ok": False, "description": "kein TELEGRAM_BOT_TOKEN"}
    with httpx.Client(timeout=timeout) as c:
        if files:
            r = c.post(_url(method), data=data, files=files)
        else:
            r = c.post(_url(method), json=data)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "description": r.text[:300]}


def send_message(chat_id, text: str, reply_to: int | None = None) -> dict:
    data = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    return _call("sendMessage", data)


def keyboard(job_id: str, n: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "▶️ YouTube EN", "callback_data": f"ce|yt|{job_id}|{n}"},
         {"text": "🎵 TikTok DE", "callback_data": f"ce|tt|{job_id}|{n}"}],
        [{"text": "❌ Verwerfen", "callback_data": f"ce|no|{job_id}|{n}"}],
    ]}


def send_clip(chat_id, video: Path, caption: str, job_id: str, n: int, width=1080, height=1920,
              duration: float = 0) -> dict:
    data = {"chat_id": str(chat_id), "caption": caption[:1024], "parse_mode": "HTML",
            "supports_streaming": "true", "width": str(width), "height": str(height),
            "duration": str(int(duration)), "reply_markup": json.dumps(keyboard(job_id, n))}
    with open(video, "rb") as f:
        return _call("sendVideo", data, files={"video": (video.name, f, "video/mp4")}, timeout=300)


def answer_callback(callback_id: str, text: str) -> dict:
    if not callback_id:
        return {}
    return _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})


def mark_message(chat_id, message_id, status_line: str, keep_buttons: dict | None = None) -> dict:
    """Ersetzt die Buttons unter dem Clip durch eine Statuszeile."""
    if not (chat_id and message_id):
        return {}
    markup = keep_buttons or {"inline_keyboard": [[{"text": status_line[:60], "callback_data": "ce|noop"}]]}
    return _call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                            "reply_markup": markup})
