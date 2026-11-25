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
    ("ANTHROPIC_WORKSPACE_ID", "Anthropic Workspace-ID (nur falls der Key keinem Workspace zugeordnet ist)", "wrkspc_… (meist leer lassen)", False),
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


COOKIE = "ce_auth"


def _token() -> str:
    return hmac.new(config.SETUP_CODE.upper().encode(), b"clip-engine-auth", "sha256").hexdigest()


def _authed(request: Request | None) -> bool:
    return bool(request and config.SETUP_CODE and hmac.compare_digest(request.cookies.get(COOKIE, ""), _token()))


def _auth(request: Request | None, code: str) -> str | None:
    """Angemeldet per Cookie oder per Setup-Code."""
    return None if _authed(request) else _check(code)


def _code_field(request: Request | None) -> str:
    if _authed(request):
        return "<p><small class=ok>✓ angemeldet</small></p>"
    return ("<label>Setup-Code</label><input name=code autocomplete=off autocapitalize=characters required>"
            "<p><small>Nur beim ersten Mal – danach merkt sich Safari die Anmeldung 180 Tage.</small></p>")


def _with_cookie(resp: HTMLResponse) -> HTMLResponse:
    resp.set_cookie(COOKIE, _token(), max_age=180 * 86400, httponly=True, secure=True, samesite="lax")
    return resp


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
def setup_form(request: Request = None, msg: str = ""):
    cur = _current()
    rows = []
    for key, label, ph, secret in FIELDS:
        val = cur.get(key) or os.environ.get(key, "")
        hint = "<small class=ok>gesetzt ✓ – leer lassen zum Behalten</small>" if (secret and val) else ""
        value = "" if secret else html.escape(val)
        rows.append(f"<label>{label}</label><input name={key} placeholder='{html.escape(ph)}' value='{value}' "
                    f"autocomplete=off autocapitalize=off autocorrect=off spellcheck=false>{hint}")
    return f"""<!doctype html>{CSS}<h2>Clip-Engine · Setup</h2>{msg}
<form method=post action=/setup>{_code_field(request)}
{''.join(rows)}<button>Speichern &amp; neu starten</button></form>"""


def _restart_soon():
    def _go():
        time.sleep(1.5)
        os._exit(0)  # Docker startet den Container neu (restart: unless-stopped)
    threading.Thread(target=_go, daemon=True).start()


@router.post("/setup", response_class=HTMLResponse)
async def setup_save(request: Request):
    form = await request.form()
    err = _auth(request, str(form.get("code", "")))
    if err:
        return setup_form(request, msg=f"<p class=err>{err}</p>")
    cur = _current()
    for key, *_ in FIELDS:
        v = _clean(key, str(form.get(key, "")))
        if v:
            cur[key] = v
    config.SETTINGS_FILE.write_text(json.dumps(cur, indent=1))
    os.chmod(config.SETTINGS_FILE, 0o600)
    _restart_soon()
    return _with_cookie(HTMLResponse(f"""<!doctype html>{CSS}<h2 class=ok>Gespeichert ✓</h2>
<p>Die Engine startet neu (ca. 5 Sekunden).</p>
<p><b>API-Key für n8n</b> – in n8n als Credential „Header Auth“ mit Name <code>X-API-Key</code> anlegen:</p>
<p><code>{html.escape(config.API_KEY)}</code></p>
<a class=b href="/setup/check">Verbindungen prüfen</a>
<a class=b href="/setup/vizard">Vizard-Konten anzeigen</a>
<a class=b href="/setup/upload">Video hochladen</a>
<a class=b href="/setup">Zurück</a>"""))


@router.get("/setup/check", response_class=HTMLResponse)
def setup_check(request: Request, code: str = ""):
    err = _auth(request, code)
    if err:
        return f"{CSS}<p class=err>{err}</p>"
    out = []
    # Claude
    if config.ANTHROPIC_API_KEY:
        try:
            from .selector import claude_request
            status, data = claude_request({"model": config.CLAUDE_MODEL, "max_tokens": 5,
                                           "messages": [{"role": "user", "content": "Hi"}]}, timeout=30)
            hint = ""
            if "workspace" in str(data).lower():
                hint = (" – Der Key ist keinem Workspace zugeordnet: entweder einen neuen Key mit Workspace „Default“ anlegen "
                        "oder die Workspace-ID (Console → Settings → Workspaces, Spalte ID) im Setup eintragen.")
            msg = f"{config.CLAUDE_MODEL}: OK" if status == 200 else (
                f"HTTP {status} {str(data)[:220]} – gespeichert: {_mask(config.ANTHROPIC_API_KEY)}{hint}")
            out.append(("Claude", status == 200, msg))
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
def setup_vizard(request: Request, code: str = ""):
    err = _auth(request, code)
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


# ---------------------------------------------------------------- Upload vom iPad (Frame.io, Dateien-App …)
@router.get("/setup/upload", response_class=HTMLResponse)
def upload_form(request: Request = None, msg: str = ""):
    return f"""<!doctype html>{CSS}<h2>Clip-Engine · Video hochladen</h2>{msg}
<p><small>Für Videos aus Frame.io, AirDrop oder der Dateien-App. Die Clips kommen wie gewohnt in Telegram.</small></p>
<form method=post action=/setup/upload enctype=multipart/form-data onsubmit="document.getElementById('b').innerText='Lädt hoch … Seite offen lassen'">
{_code_field(request)}
<label>Video</label><input type=file name=file accept="video/*" required>
<label>Sprache</label><select name=lang style="width:100%;padding:10px;font-size:16px"><option value=en>Englisch (YouTube)</option><option value=de>Deutsch (TikTok)</option></select>
<label>Anzahl Clips</label><input name=n_clips type=number value=3 min=1 max=10>
<label>Layout</label><select name=layout style="width:100%;padding:10px;font-size:16px"><option>blur</option><option>crop</option><option>fit</option></select>
<label>Kampagne</label><input name=campaign placeholder="SEGA Metaphor ReFantazio" autocorrect=off>
<label>Regeln aus dem Briefing</label><input name=rules placeholder="#MetaphorReFantazio @SEGA" autocorrect=off autocapitalize=off>
<button id=b>Hochladen &amp; Clips erstellen</button></form>"""


@router.post("/setup/upload", response_class=HTMLResponse)
async def upload_save(request: Request):
    import time as _t
    from . import pipeline, store
    from .main import _params
    form = await request.form()
    err = _auth(request, str(form.get("code", "")))
    if err:
        return upload_form(request, msg=f"<p class=err>{err}</p>")
    f = form.get("file")
    if not f or not getattr(f, "filename", ""):
        return _with_cookie(HTMLResponse(upload_form(request, msg="<p class=err>Keine Datei gewählt.</p>")))
    params = _params({"url": f"upload:{f.filename}", "lang": form.get("lang"), "n_clips": form.get("n_clips") or 3,
                      "campaign": form.get("campaign") or "", "rules": form.get("rules") or "",
                      "layout": form.get("layout") or ""})
    job_id = store.new_job_id()
    d = store.job_dir(job_id)
    d.mkdir(parents=True)
    ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "mp4").lower()
    ext = ext if ext in ("mp4", "mov", "mkv", "webm", "m4v") else "mp4"
    if ext == "m4v":
        ext = "mp4"
    with open(d / f"source.{ext}", "wb") as out:
        while chunk := await f.read(1 << 20):
            out.write(chunk)
    size = (d / f"source.{ext}").stat().st_size / 1e6
    store.save({"id": job_id, "status": "queued", "created": _t.time(), "params": params, "title": f.filename})
    pos = pipeline.enqueue(job_id)
    from . import telegram
    if telegram.enabled() and params["chat_id"]:
        telegram.send_message(params["chat_id"], f"⏳ Upload <code>{job_id}</code> angenommen: {html.escape(f.filename)} "
                                                 f"({size:.0f} MB), {params['n_clips']} Clips · {params['lang'].upper()}")
    return _with_cookie(HTMLResponse(f"<!doctype html>{CSS}<h2 class=ok>Hochgeladen ✓</h2><p>{html.escape(f.filename)} · {size:.0f} MB · "
            f"Job <code>{job_id}</code> (Platz {pos})</p><p>Die Clips kommen in ein paar Minuten in Telegram.</p>"
            f"<a class=b href=/setup/upload>Noch ein Video</a>"))
