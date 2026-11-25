"""Der eigentliche Ablauf eines Jobs: laden → analysieren → auswählen → schneiden → Telegram."""
import html
import json
import queue
import secrets
import threading
import time
import traceback
from pathlib import Path

from . import analyze, config, render, selector, store, telegram
from .download import fetch

_q: "queue.Queue[str]" = queue.Queue()


def enqueue(job_id: str) -> int:
    _q.put(job_id)
    return _q.qsize()


def _log(job_id: str, msg: str) -> None:
    job = store.load(job_id) or {}
    log = job.get("log", [])
    log.append(f"{time.strftime('%H:%M:%S')} {msg}")
    store.update(job_id, log=log[-50:], stage=msg)


def heuristic_select(an: dict, p: dict) -> dict:
    """Notfall-Auswahl ohne Claude: lauteste Abschnitte, längste Szene als Hero."""
    energy = an["audio"].get("energy") or [50] * int(an["probe"]["duration"])
    L = int((p["min_len"] + p["max_len"]) / 2)
    windows = sorted(((sum(energy[i:i + L]) / L, i) for i in range(0, max(1, len(energy) - L))), reverse=True)
    clips, used = [], []
    for score, i in windows:
        if any(abs(i - u) < L for u in used):
            continue
        used.append(i)
        inside = [s for s in an["scenes"] if s["start"] >= i and s["end"] <= i + L] or an["scenes"][:1]
        hero = max(inside, key=lambda s: s["end"] - s["start"])
        clips.append({"start": i, "end": i + L, "hero_scene": hero["id"], "hook": "", "title": f"Clip {len(clips) + 1}",
                      "description": "", "hashtags": [], "reason": "laute Passage (ohne Claude)", "score": 0})
        if len(clips) >= p["n_clips"]:
            break
    return {"scene_scores": [], "clips": clips}


def _dedupe_tags(c: dict) -> None:
    """Hashtags nur einmal: keine Doppelten in der Liste und keine, die schon in der Beschreibung stehen."""
    import re
    desc = c.get("description", "") or ""
    seen, out = set(), []
    for t in re.findall(r"#\w+", desc):
        if t.lower() in seen:
            desc = re.sub(r"\s*" + re.escape(t) + r"\b", "", desc, count=1) if desc.lower().count(t.lower()) > 1 else desc
        seen.add(t.lower())
    for t in c.get("hashtags") or []:
        t = "#" + t.lstrip("#").strip()
        if len(t) > 1 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    c["description"], c["hashtags"] = desc.strip(), out


def _overlap(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(0.1, min(a["end"] - a["start"], b["end"] - b["start"]))


def _caption(i: int, c: dict) -> str:
    e = html.escape
    tags = " ".join(c.get("hashtags") or [])
    body = c["plan"]["segments"][-1]
    lines = [
        f"<b>#{i + 1} · {e(c.get('title', ''))}</b>",
        f"Hook: <i>{e(c.get('hook', ''))}</i>" + (f" · Ende: <i>{e(c.get('end_text', ''))}</i>" if c.get("end_text") else ""),
        f"⏱ {c['plan']['duration']:.0f}s · Quelle {body['start']:.1f}–{body['end']:.1f}s · Ende: {c['plan']['end_reason']}"
        + (f" · Opener S{c['plan']['hero_scene']}" if c['plan'].get('hero_scene') is not None else ""),
        f"Score {c.get('score', '?')}/10 – {e(c.get('reason', ''))}",
        "",
        e(c.get("description", "")),
        e(tags),
    ]
    return "\n".join(lines)


def process(job_id: str) -> None:
    job = store.load(job_id)
    p = job["params"]
    work = store.job_dir(job_id)
    chat = p.get("chat_id") or config.TELEGRAM_CHAT_ID
    store.update(job_id, status="running", started=time.time())
    try:
        # 1. Quelle
        src = None
        for f in work.glob("source.*"):
            if f.suffix in (".mp4", ".mkv", ".webm", ".mov"):
                src = f
        if src is None:
            _log(job_id, "lade Video")
            src, title = fetch(p["url"], work)
            store.update(job_id, title=title)
            job["title"] = title
        info = analyze.probe(src)
        _log(job_id, f"Video {info['duration']:.0f}s, {info['width']}x{info['height']}")

        # 2. Analyse
        _log(job_id, "Szenen erkennen")
        scenes = analyze.detect_scenes(src, info["duration"])
        audio = {"tempo": 0, "beats": [], "strong_beats": [], "energy": []}
        transcript = {"segments": [], "words": []}
        if info["has_audio"]:
            wav = analyze.extract_audio(src, work / "audio.wav")
            _log(job_id, "Beats finden")
            audio = analyze.beats_and_energy(wav)
            if p.get("transcribe", True):
                _log(job_id, "Sprache transkribieren")
                transcript = analyze.transcribe(wav)
        _log(job_id, f"{len(scenes)} Szenen, {len(audio['beats'])} Beats, {len(transcript['words'])} Wörter")
        _log(job_id, "Standbilder erstellen")
        sheets = analyze.contact_sheets(src, scenes, work)
        an = {"probe": info, "scenes": scenes, "audio": audio, "transcript": transcript, "sheets": sheets}
        (work / "analysis.json").write_text(json.dumps(an, ensure_ascii=False))

        # 3. Auswahl
        job = store.load(job_id)
        if config.ANTHROPIC_API_KEY and not p.get("no_claude"):
            _log(job_id, f"Claude bewertet {len(sheets)} Kontaktabzüge")
            sel = selector.select(job, an, work)
        else:
            _log(job_id, "kein Claude-Key → einfache Auswahl")
            sel = heuristic_select(an, p)
        (work / "selection.json").write_text(json.dumps(sel, ensure_ascii=False, indent=1))

        # 4. Regeln + Schnitt
        for c in sel.get("clips", []):
            _dedupe_tags(c)
        cands = sorted(sel.get("clips", []), key=lambda c: c.get("score", 0), reverse=True)
        clips = []
        for c in cands:
            plan = render.plan_clip(c, an, p)
            body = plan["segments"][-1]
            if any(_overlap(body, k["plan"]["segments"][-1]) > 0.5 for k in clips):
                continue
            c["plan"] = plan
            clips.append(c)
            if len(clips) >= p["n_clips"]:
                break
        out_clips = []
        for i, c in enumerate(clips):
            _log(job_id, f"rendere Clip {i + 1}/{len(clips)}")
            words = render._map_words(transcript.get("words", []), c["plan"]["segments"])
            hero_dur = sum(sg["end"] - sg["start"] for sg in c["plan"]["segments"] if sg.get("kind") == "hero")
            end_text = c.get("end_text", "") if config.END_TEXT_SECONDS > 0 else ""
            ass = render.build_ass(work / f"clip_{i:02d}.ass", c.get("hook", ""), words, c["plan"]["duration"],
                                   end_text=end_text, hero_dur=hero_dur)
            out = work / f"clip_{i:02d}.mp4"
            render.render(src, c["plan"], ass, out, p["layout"], info["has_audio"],
                          src_w=info["width"], src_h=info["height"])
            c.update({"n": i, "file": out.name, "public_token": secrets.token_urlsafe(18).replace("-", "").replace("_", ""),
                      "size_mb": round(out.stat().st_size / 1e6, 1), "status": "review"})
            out_clips.append(c)
            store.update(job_id, clips=out_clips)

        # 5. Telegram
        if telegram.enabled() and chat:
            usage = sel.get("usage", {})
            telegram.send_message(chat, f"🎬 <b>{html.escape(job.get('title', ''))}</b>\n"
                                        f"{len(scenes)} Szenen · {audio.get('tempo', 0):.0f} BPM · "
                                        f"{len(out_clips)} Clips"
                                        + (f" · Claude {usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)} Tokens" if usage else ""))
            for c in out_clips:
                r = telegram.send_clip(chat, work / c["file"], _caption(c["n"], c), job_id, c["n"],
                                       duration=c["plan"]["duration"])
                if r.get("ok"):
                    c["tg_message_id"] = r["result"]["message_id"]
                    c["tg_chat_id"] = chat
                else:
                    _log(job_id, f"Telegram-Fehler: {r.get('description')}")
            store.update(job_id, clips=out_clips)
        store.update(job_id, status="done", finished=time.time())
        _log(job_id, "fertig")
    except Exception as e:  # noqa: BLE001
        store.update(job_id, status="failed", error=str(e)[:1500], trace=traceback.format_exc()[-3000:])
        if telegram.enabled() and chat:
            telegram.send_message(chat, f"⚠️ Clip-Job {job_id} fehlgeschlagen:\n{html.escape(str(e)[:900])}")


def _worker():
    last_cleanup = 0.0
    while True:
        try:
            job_id = _q.get(timeout=600)
        except queue.Empty:
            job_id = None
        if time.time() - last_cleanup > 86400:
            store.cleanup()
            last_cleanup = time.time()
        if job_id:
            process(job_id)


def start_worker():
    # unterbrochene Jobs nach Neustart wieder einreihen
    for jd in sorted(config.JOBS_DIR.iterdir()) if config.JOBS_DIR.exists() else []:
        j = store.load(jd.name) if jd.name.isalnum() else None
        if j and j.get("status") in ("queued", "running"):
            store.update(j["id"], status="queued")
            _q.put(j["id"])
    threading.Thread(target=_worker, daemon=True).start()
