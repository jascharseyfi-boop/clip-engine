"""HTTP-API der Clip-Engine (nur im Docker-Netz erreichbar; /public/* über Caddy)."""
import html
import re
import time
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, pipeline, schedule, store, telegram, vizard
from .setup import router as setup_router

app = FastAPI(title="Clip-Engine", version="0.1", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(setup_router)


def auth(x_api_key: str = Header(default="")):
    if not config.API_KEY or x_api_key != config.API_KEY:
        raise HTTPException(401, "X-API-Key fehlt oder falsch")


class JobIn(BaseModel):
    url: str
    lang: str = "en"
    n_clips: int = 3
    min_len: float | None = None
    max_len: float | None = None
    layout: str | None = None
    campaign: str = ""
    rules: str = ""
    chat_id: str | None = None
    transcribe: bool = True
    no_claude: bool = False


def _params(d: dict) -> dict:
    lang = (d.get("lang") or "en").lower()
    return {
        "url": d.get("url", ""),
        "lang": lang,
        "platform_hint": "YouTube Shorts (US audience)" if lang == "en" else "TikTok (German audience)",
        "n_clips": max(1, min(10, int(d.get("n_clips") or 3))),
        "min_len": float(d.get("min_len") or config.MIN_LEN),
        "max_len": float(d.get("max_len") or config.MAX_LEN),
        "layout": d.get("layout") if d.get("layout") in ("blur", "crop", "fit") else config.LAYOUT,
        "campaign": d.get("campaign") or "",
        "rules": d.get("rules") or "",
        "chat_id": d.get("chat_id") or config.TELEGRAM_CHAT_ID,
        "transcribe": d.get("transcribe", True),
        "no_claude": d.get("no_claude", False),
    }


def _create(params: dict) -> dict:
    job_id = store.new_job_id()
    job = {"id": job_id, "status": "queued", "created": time.time(), "params": params, "title": params["url"]}
    store.save(job)
    pos = pipeline.enqueue(job_id)
    return {"job_id": job_id, "queue_position": pos}


@app.on_event("startup")
def _startup():
    pipeline.start_worker()


@app.get("/health")
def health():
    return {"ok": True, "claude": bool(config.ANTHROPIC_API_KEY), "telegram": telegram.enabled(),
            "vizard": vizard.enabled(), "public_base": config.PUBLIC_BASE_URL}


@app.post("/jobs", dependencies=[Depends(auth)])
def create_job(j: JobIn):
    return _create(_params(j.model_dump()))


@app.post("/jobs/upload", dependencies=[Depends(auth)])
async def create_job_upload(file: UploadFile = File(...), lang: str = Form("en"), n_clips: int = Form(3),
                            campaign: str = Form(""), rules: str = Form(""), layout: str = Form("")):
    params = _params({"url": f"upload:{file.filename}", "lang": lang, "n_clips": n_clips,
                      "campaign": campaign, "rules": rules, "layout": layout})
    job_id = store.new_job_id()
    d = store.job_dir(job_id)
    d.mkdir(parents=True)
    ext = (file.filename or "x.mp4").rsplit(".", 1)[-1].lower()
    with open(d / f"source.{ext if ext in ('mp4', 'mov', 'mkv', 'webm') else 'mp4'}", "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    store.save({"id": job_id, "status": "queued", "created": time.time(), "params": params, "title": file.filename})
    return {"job_id": job_id, "queue_position": pipeline.enqueue(job_id)}


class TelegramIn(BaseModel):
    text: str
    chat_id: str
    message_id: int | None = None


URL_RE = re.compile(r"https?://\S+")


@app.post("/telegram/message", dependencies=[Depends(auth)])
def telegram_message(m: TelegramIn):
    """Freitext aus Telegram: Link + optionale Angaben, z. B.
    'https://youtu.be/xyz en 3 crop kampagne: SEGA Metaphor regeln: #MetaphorReFantazio @SEGA'"""
    allowed = [c.strip() for c in str(config.TELEGRAM_CHAT_ID).split(",") if c.strip()]
    if allowed and str(m.chat_id) not in allowed:
        return {"ok": False, "error": "chat not allowed"}
    text = m.text.strip()
    urls = URL_RE.findall(text)
    if text.lower() in ("/start", "/hilfe", "hilfe", "/help"):
        telegram.send_message(m.chat_id, HELP)
        return {"ok": True}
    if text.lower() in ("/status", "status"):
        jobs = store.list_jobs(5)
        lines = [f"{j['id']} · {j['status']} · {html.escape(str(j.get('title'))[:50])}" for j in jobs] or ["keine Jobs"]
        telegram.send_message(m.chat_id, "\n".join(lines))
        return {"ok": True}
    if not urls:
        telegram.send_message(m.chat_id, "Schick mir einen Video-Link. /hilfe zeigt die Optionen.", m.message_id)
        return {"ok": False}
    d = {"url": urls[0], "chat_id": m.chat_id}
    rest = URL_RE.sub(" ", text)
    rules = re.search(r"(?:regeln|rules)\s*:\s*(.+)$", rest, re.I | re.S)
    if rules:
        d["rules"] = rules.group(1).strip()
        rest = rest[:rules.start()]
    camp = re.search(r"(?:kampagne|campaign)\s*:\s*([^\n]+?)(?:\s+(?:regeln|rules)\s*:|$)", rest, re.I)
    if camp:
        d["campaign"] = camp.group(1).strip()
        rest = rest.replace(camp.group(0), " ")
    for tok in rest.lower().split():
        if tok in ("de", "en"):
            d["lang"] = tok
        elif tok.isdigit():
            d["n_clips"] = int(tok)
        elif tok in ("blur", "crop", "fit"):
            d["layout"] = tok
        elif tok in ("ohnetext", "notranscript"):
            d["transcribe"] = False
    params = _params(d)
    res = _create(params)
    telegram.send_message(
        m.chat_id,
        f"⏳ Job <code>{res['job_id']}</code> angenommen (Platz {res['queue_position']} in der Warteschlange)\n"
        f"{params['n_clips']} Clips · {params['lang'].upper()} · Layout {params['layout']}"
        + (f"\nKampagne: {html.escape(params['campaign'])}" if params["campaign"] else ""),
        m.message_id)
    return {"ok": True, **res}


HELP = ("<b>Clip-Engine</b>\nSchick einen Link (YouTube, Drive, Dropbox, MP4). Optional dahinter:\n"
        "• <code>en</code> / <code>de</code> – Sprache für Hook, Titel, Beschreibung\n"
        "• Zahl – Anzahl Clips (Standard 3)\n"
        "• <code>blur</code> / <code>crop</code> / <code>fit</code> – 9:16-Layout\n"
        "• <code>kampagne: Name</code>\n• <code>regeln: Pflicht-Hashtags, Erwähnungen …</code> (am Ende)\n"
        "/status – letzte Jobs")


@app.get("/jobs", dependencies=[Depends(auth)])
def jobs():
    return store.list_jobs(30)


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    job = store.load(job_id)
    if not job:
        raise HTTPException(404)
    return job


@app.get("/jobs/{job_id}/clips/{n}/video", dependencies=[Depends(auth)])
def clip_video(job_id: str, n: int):
    job = store.load(job_id)
    if not job or n >= len(job.get("clips", [])):
        raise HTTPException(404)
    return FileResponse(store.job_dir(job_id) / job["clips"][n]["file"], media_type="video/mp4",
                        filename=f"{job_id}_{n}.mp4")


@app.get("/jobs/{job_id}/sheet/{i}", dependencies=[Depends(auth)])
def sheet(job_id: str, i: int):
    p = store.job_dir(job_id) / f"sheet_{i:02d}.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/public/{token}.mp4")
def public_clip(token: str):
    f = store.find_public(token)
    if not f:
        raise HTTPException(404)
    return FileResponse(f, media_type="video/mp4")


class ActionIn(BaseModel):
    action: str               # yt | tt | no
    callback_query_id: str = ""


def _fmt_local(dt) -> str:
    d = dt.astimezone(ZoneInfo(config.TIMEZONE))
    return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][d.weekday()] + d.strftime(" %d.%m. %H:%M")


@app.post("/jobs/{job_id}/clips/{n}/action", dependencies=[Depends(auth)])
def clip_action(job_id: str, n: int, a: ActionIn):
    """Wird von n8n bei jedem Button-Klick aufgerufen."""
    job = store.load(job_id)
    if not job or n >= len(job.get("clips", [])):
        telegram.answer_callback(a.callback_query_id, "Clip nicht gefunden (evtl. schon gelöscht)")
        raise HTTPException(404)
    c = job["clips"][n]
    chat, msg = c.get("tg_chat_id"), c.get("tg_message_id")
    ref = f"{job_id}/{n}"

    if a.action == "no":
        store.update_clip(job_id, n, status="rejected")
        telegram.answer_callback(a.callback_query_id, "Verworfen")
        telegram.mark_message(chat, msg, "❌ verworfen")
        return {"next": "none"}

    if a.action == "yt":
        if c.get("youtube", {}).get("status") in ("pending", "scheduled"):
            telegram.answer_callback(a.callback_query_id, "Läuft schon / ist schon geplant")
            return {"next": "none"}
        when = schedule.book("youtube", ref)
        tags = [t.lstrip("#") for t in (c.get("hashtags") or [])]
        desc = (c.get("description", "") + "\n\n" + " ".join(c.get("hashtags") or []) + " #Shorts").strip()
        store.update_clip(job_id, n, youtube={"status": "pending", "publish_at": when.isoformat()})
        telegram.answer_callback(a.callback_query_id, f"YouTube-Upload startet, geplant {_fmt_local(when)}")
        return {
            "next": "youtube",
            "title": (c.get("title") or "Clip")[:95],
            "description": desc[:4800],
            "tags": ",".join(tags + ["Shorts"])[:450],
            "publish_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "publish_at_local": _fmt_local(when),
            "video_url": f"http://clip-engine:8000/jobs/{job_id}/clips/{n}/video",
            "job_id": job_id, "n": n,
        }

    if a.action == "tt":
        if c.get("tiktok", {}).get("status") == "scheduled":
            telegram.answer_callback(a.callback_query_id, "Schon für TikTok geplant")
            return {"next": "none"}
        if not vizard.enabled() or not config.PUBLIC_BASE_URL:
            telegram.answer_callback(a.callback_query_id, "Vizard ist noch nicht eingerichtet (.env)")
            return {"next": "none", "error": "vizard not configured"}
        when = schedule.book("tiktok", ref)
        url = f"{config.PUBLIC_BASE_URL}/public/{c['public_token']}.mp4"
        post = (c.get("description", "") + " " + " ".join(c.get("hashtags") or [])).strip()
        res = vizard.publish(url, int(when.timestamp() * 1000), post[:2000])
        if res["ok"]:
            store.update_clip(job_id, n, tiktok={"status": "scheduled", "publish_at": when.isoformat()})
            telegram.answer_callback(a.callback_query_id, f"TikTok geplant {_fmt_local(when)}")
            telegram.send_message(chat, f"🎵 Clip #{n + 1} für TikTok geplant: {_fmt_local(when)}", msg)
        else:
            schedule.release("tiktok", ref)
            telegram.answer_callback(a.callback_query_id, "Vizard-Fehler: " + res.get("hint", ""))
            telegram.send_message(chat, f"⚠️ TikTok über Vizard fehlgeschlagen: {html.escape(str(res.get('hint')))}"
                                        f" (Code {res.get('code')})", msg)
        return {"next": "none", "vizard": res}

    raise HTTPException(400, "unbekannte Aktion")


class PublishedIn(BaseModel):
    platform: str = "youtube"
    video_id: str = ""
    error: str = ""


@app.post("/jobs/{job_id}/clips/{n}/published", dependencies=[Depends(auth)])
def clip_published(job_id: str, n: int, p: PublishedIn):
    job = store.load(job_id)
    c = job["clips"][n]
    chat, msg = c.get("tg_chat_id"), c.get("tg_message_id")
    yt = c.get("youtube", {})
    if p.error or not p.video_id:
        schedule.release("youtube", f"{job_id}/{n}")
        store.update_clip(job_id, n, youtube={"status": "failed", "error": p.error[:500]})
        telegram.send_message(chat, f"⚠️ YouTube-Upload von Clip #{n + 1} fehlgeschlagen: {html.escape(p.error[:500])}", msg)
        return {"ok": False}
    from datetime import datetime
    when = datetime.fromisoformat(yt.get("publish_at")) if yt.get("publish_at") else None
    store.update_clip(job_id, n, youtube={"status": "scheduled", "video_id": p.video_id,
                                          "publish_at": yt.get("publish_at")})
    telegram.send_message(chat, f"▶️ Clip #{n + 1} auf YouTube hochgeladen, geht online "
                                f"{_fmt_local(when) if when else ''}\nhttps://youtube.com/shorts/{p.video_id}", msg)
    return {"ok": True}


@app.get("/vizard/accounts", dependencies=[Depends(auth)])
def vizard_accounts():
    """Hilfsendpunkt: zeigt die Vizard-Social-Accounts, um VIZARD_TIKTOK_ACCOUNT_ID zu finden."""
    return vizard.social_accounts()
