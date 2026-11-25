"""Setup-Seite im Browser (iPad): Schlüssel einfügen statt in der Web-Konsole abtippen."""
import hmac
import html
import re
import json
import os
import threading
import time

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from . import config

router = APIRouter()
_fails: list[float] = []

FIELDS = [
    ("ANTHROPIC_API_KEY", "Anthropic API-Key", "sk-ant-…", True),
    ("TELEGRAM_BOT_TOKEN", "Telegram-Bot-Token (neuer Clip-Bot von @BotFather)", "123456:ABC…", True),
    ("TELEGRAM_CHAT_ID", "Telegram-Chat-ID", "726174060", False),
    ("VIZARD_API_KEY", "Vizard API-Key (für TikTok)", "optional", True),
    ("VIZARD_TIKTOK_ACCOUNT_ID", "Vizard TikTok-Account-ID", "nach dem Speichern über „Vizard-Konten“", False),
    ("CLAUDE_MODEL", "Claude-Modell", "claude-sonnet-5-5", False),
    ("SLOTS_YOUTUBE", "YouTube-Uhrzeiten (Berlin)", "01:00", False),
    ("SLOTS_TIKTOK", "TikTok-Uhrzeiten (Berlin)", "18:00", False),
]

CSS = """<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font:16px -apple-system,system-ui,sans-serif;max-width:640px;margin:24px auto;padding:0 16px;color:#111}
label{display:block;margin:14px 0 4px;font-weight:600}input{width:100%;padding:10px;font-size:16px;border:1px solid #bbb;border-radius:8px;box-sizing:border-box}
small{color:#666}button,a.b{display:inline-block;margin-top:18px;padding:12px 18px;font-size:16px;border:0;border-radius:8px;background:#111;color:#fff;text-decoration:none}
code{background:#f2f2f2;padding:2px 6px;border-radius:4px;word-break:break-all}.ok{color:#0a7d2c}.err{color:#b00020}</style>"""


def _check(code: str) -> str | None:
    now = time.time()
    _fails[:] = [t for t in _fails if now - t < 600]
    if len(_fails) >= 5:
        return "Zu viele Fehlversuche – 10 Minuten warten."
    if not config.SETUP_CODE or not hmac.compare_digest(code.strip().upper(), config.SETUP_CODE.upper()):
        _fails.append(now)
        return "Setup-Code falsch (steht am Ende der Installation in der Konsole und in /opt/clip-engine/.env)."
    return None


SECRET_KEYS = {k for k, _, _, secret in FIELDS if secret}


def _clean(key: str, v: str) -> str:
    """Entfernt Leerzeichen, Zeilenumbrüche und unsichtbare Zeichen (iPad-Einfügen) aus Schlüsseln."""
    v = v.strip()
    if key in SECRET_KEYS or key.endswith("_ID"):
        v = re.sub(r"[\s\u200b-\u200f\u2060\ufeff\u00a0]", "", v)
        v = v.replace("\u2013", "-").replace("\u2014", "--")  # Gedankenstriche aus Autokorrektur
    return v


def _mask(v: str) -> str:
    if not v:
        return "leer"
    if len(v) <= 14:
        return f"{v[:3]}… ({len(v)} Zeichen)"
    return f"{v[:10]}…{v[-4:]} ({len(v)} Zeichen)"


def _current() -> dict:
    try:
        return json.loads(config.SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


@router.get("/setup", response_class=HTMLResponse)
def setup_form(msg: str = ""):
    cur = _current()
    rows = []
    for key, label, ph, secret in FIELDS:
        val = cur.get(key) or os.environ.get(key, "")
        hint = "<small class=ok>gesetzt ✓ – leer lassen zum Behalten</small>" if (secret and val) else ""
        value = "" if secret else html.escape(val)
        rows.append(f"<label>{label}</label><input name={key} placeholder='{html.escape(ph)}' value='{value}' "
                    f"autocomplete=off autocapitalize=off autocorrect=off spellcheck=false>{hint}")
    return f"""<!doctype html>{CSS}<h2>Clip-Engine · Setup</h2>{msg}
<form method=post action=/setup><label>Setup-Code</label><input name=code autocomplete=off autocapitalize=characters required>
{''.join(rows)}<button>Speichern &amp; neu starten</button></form>"""


def _restart_soon():
    def _go():
        time.sleep(1.5)
        os._exit(0)  # Docker startet den Container neu (restart: unless-stopped)
    threading.Thread(target=_go, daemon=True).start()


@router.post("/setup", response_class=HTMLResponse)
async def setup_save(request: Request):
    form = await request.form()
    err = _check(str(form.get("code", "")))
    if err:
        return setup_form(msg=f"<p class=err>{err}</p>")
    cur = _current()
    for key, *_ in FIELDS:
        v = _clean(key, str(form.get(key, "")))
        if v:
            cur[key] = v
    config.SETTINGS_FILE.write_text(json.dumps(cur, indent=1))
    os.chmod(config.SETTINGS_FILE, 0o600)
    code = html.escape(str(form.get("code")).strip())
    _restart_soon()
    return f"""<!doctype html>{CSS}<h2 class=ok>Gespeichert ✓</h2>
<p>Die Engine startet neu (ca. 5 Sekunden).</p>
<p><b>API-Key für n8n</b> – in n8n als Credential „Header Auth“ mit Name <code>X-API-Key</code> anlegen:</p>
<p><code>{html.escape(config.API_KEY)}</code></p>
<a class=b href="/setup/check?code={code}">Verbindungen prüfen</a>
<a class=b href="/setup/vizard?code={code}">Vizard-Konten anzeigen</a>
<a class=b href="/setup">Zurück</a>"""


@router.get("/setup/check", response_class=HTMLResponse)
def setup_check(code: str):
    err = _check(code)
    if err:
        return f"{CSS}<p class=err>{err}</p>"
    out = []
    # Claude
    if config.ANTHROPIC_API_KEY:
        try:
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
                "x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
                json={"model": config.CLAUDE_MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": "Hi"}]})
            msg = f"{config.CLAUDE_MODEL}: OK" if r.status_code == 200 else (
                f"HTTP {r.status_code} {r.text[:200]} – gespeichert: {_mask(config.ANTHROPIC_API_KEY)}. "
                "Ein Key beginnt mit sk-ant-api03- und hat etwa 108 Zeichen.")
            out.append(("Claude", r.status_code == 200, msg))
        except httpx.HTTPError as e:
            out.append(("Claude", False, str(e)))
    else:
        out.append(("Claude", False, "kein Key"))
    # Telegram
    if config.TELEGRAM_BOT_TOKEN:
        from . import telegram
        me = telegram._call("getMe", {})
        if not me.get("ok"):
            out.append(("Telegram", False, f"Token ungültig ({me.get('description')}) – gespeichert: {_mask(config.TELEGRAM_BOT_TOKEN)}. "
                                            "Ein Token sieht so aus: 1234567890:AAH… (Zahlen, Doppelpunkt, ca. 35 Zeichen)"))
        else:
            r = telegram.send_message(config.TELEGRAM_CHAT_ID, "✅ Clip-Engine ist verbunden.")
            bot = "@" + me["result"].get("username", "")
            out.append(("Telegram", r.get("ok"), f"{bot}: " + ("Testnachricht gesendet" if r.get("ok") else
                        f"{r.get('description')} – schreib {bot} zuerst /start und prüf die Chat-ID {config.TELEGRAM_CHAT_ID}")))
    else:
        out.append(("Telegram", False, "kein Token"))
    # Vizard
    from . import vizard
    out.append(("Vizard/TikTok", vizard.enabled(), "bereit" if vizard.enabled() else "Key oder Account-ID fehlt (optional)"))
    out.append(("Öffentliche URL", bool(config.PUBLIC_BASE_URL), config.PUBLIC_BASE_URL or "fehlt"))
    rows = "".join(f"<p>{'✅' if ok else '❌'} <b>{n}</b> – {html.escape(str(t))}</p>" for n, ok, t in out)
    return f"<!doctype html>{CSS}<h2>Verbindungen</h2>{rows}<a class=b href=/setup>Zurück</a>"


@router.get("/setup/vizard", response_class=HTMLResponse)
def setup_vizard(code: str):
    err = _check(code)
    if err:
        return f"{CSS}<p class=err>{err}</p>"
    if not config.VIZARD_API_KEY:
        return f"{CSS}<p class=err>Erst den Vizard-API-Key speichern.</p><a class=b href=/setup>Zurück</a>"
    from . import vizard
    try:
        data = vizard.social_accounts()
    except Exception as e:  # noqa: BLE001
        return f"{CSS}<p class=err>{html.escape(str(e))}</p>"
    accs = data.get("publishAccounts") or []
    rows = "".join(f"<p><b>{html.escape(str(a.get('platform')))}</b> @{html.escape(str(a.get('username')))} – "
                   f"ID <code>{html.escape(str(a.get('id')))}</code> ({html.escape(str(a.get('status')))})</p>" for a in accs)
    return (f"<!doctype html>{CSS}<h2>Vizard-Konten</h2>{rows or '<p>Keine verbundenen Konten. In Vizard unter Social Accounts TikTok verbinden.</p>'}"
            f"<p><small>Die TikTok-ID oben ins Feld „Vizard TikTok-Account-ID“ eintragen.</small></p><a class=b href=/setup>Zurück</a>"
            f"<details><summary>Rohdaten</summary><pre>{html.escape(json.dumps(data, indent=1)[:3000])}</pre></details>")
