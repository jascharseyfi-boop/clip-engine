"""Einfache Datei-Ablage: jeder Job ist ein Ordner mit job.json."""
import json
import secrets
import shutil
import threading
import time
from pathlib import Path

from . import config

_lock = threading.RLock()


def new_job_id() -> str:
    return time.strftime("%m%d") + secrets.token_hex(3)


def job_dir(job_id: str) -> Path:
    if not job_id.isalnum():
        raise ValueError("ungültige Job-ID")
    return config.JOBS_DIR / job_id


def save(job: dict) -> None:
    with _lock:
        d = job_dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        job["updated"] = time.time()
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2))
        tmp.replace(d / "job.json")


def load(job_id: str) -> dict | None:
    with _lock:
        p = job_dir(job_id) / "job.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())


def update(job_id: str, **fields) -> dict:
    with _lock:
        job = load(job_id) or {"id": job_id}
        job.update(fields)
        save(job)
        return job


def update_clip(job_id: str, n: int, **fields) -> dict:
    with _lock:
        job = load(job_id)
        clip = job["clips"][n]
        clip.update(fields)
        save(job)
        return clip


def find_public(token: str) -> Path | None:
    """Sucht die Clip-Datei zu einem öffentlichen Token."""
    if not token.isalnum():
        return None
    for jd in config.JOBS_DIR.iterdir():
        p = jd / "job.json"
        if not p.exists():
            continue
        try:
            job = json.loads(p.read_text())
        except Exception:
            continue
        for c in job.get("clips", []):
            if c.get("public_token") == token and c.get("file"):
                f = jd / c["file"]
                return f if f.exists() else None
    return None


def list_jobs(limit: int = 20) -> list[dict]:
    jobs = []
    for jd in sorted(config.JOBS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        j = load(jd.name) if jd.name.isalnum() else None
        if j:
            jobs.append({k: j.get(k) for k in ("id", "status", "title", "created", "error")})
    return jobs


def cleanup() -> int:
    """Löscht Jobs, die älter als KEEP_DAYS sind."""
    cutoff = time.time() - config.KEEP_DAYS * 86400
    n = 0
    for jd in config.JOBS_DIR.iterdir():
        if jd.is_dir() and jd.stat().st_mtime < cutoff:
            shutil.rmtree(jd, ignore_errors=True)
            n += 1
    return n
