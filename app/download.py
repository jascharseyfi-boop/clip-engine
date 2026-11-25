"""Quellvideo holen: YouTube, Google Drive, Dropbox, direkte MP4-Links (alles über yt-dlp)."""
import json
import re
import subprocess
from pathlib import Path

from . import config


class DownloadError(RuntimeError):
    pass


def _ytdlp_cmd(url: str, out_tmpl: str) -> list[str]:
    cmd = [
        "yt-dlp", "--no-playlist", "--no-progress", "--restrict-filenames",
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b",
        "--merge-output-format", "mp4",
        "--print-json", "-o", out_tmpl,
    ]
    cookies = config.ASSETS_DIR / "cookies.txt"
    if cookies.exists():
        cmd += ["--cookies", str(cookies)]
    if "dropbox.com" in url and "dl=0" in url:
        url = url.replace("dl=0", "dl=1")
    return cmd + [url]


def _drive_id(url: str) -> str | None:
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:.*&)?id=)([A-Za-z0-9_-]{20,})", url)
    return m.group(1) if m else None


def _fetch_drive(url: str, dest_dir: Path) -> tuple[Path, str]:
    """Google Drive über gdown (umgeht die Virenscan-Warnseite bei großen Dateien)."""
    if "/folders/" in url:
        raise DownloadError("Das ist ein Drive-ORDNER. Bitte den Link zur einzelnen Videodatei schicken "
                            "(im Ordner beim Video ⋮ → Teilen → Link kopieren).")
    fid = _drive_id(url)
    if not fid:
        raise DownloadError("Drive-Link nicht erkannt")
    out = dest_dir / "source.mp4"
    proc = subprocess.run(["gdown", "--fuzzy", "--quiet", f"https://drive.google.com/uc?id={fid}", "-O", str(out)],
                          capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 10000:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-2:])
        raise DownloadError("Drive-Download fehlgeschlagen. Ist die Datei für „Jeder mit dem Link“ freigegeben? " + tail)
    return out, f"Drive {fid[:8]}"


def fetch(url: str, dest_dir: Path) -> tuple[Path, str]:
    """Lädt das Video und gibt (Pfad, Titel) zurück."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if "drive.google.com" in url:
        return _fetch_drive(url, dest_dir)
    out_tmpl = str(dest_dir / "source.%(ext)s")
    proc = subprocess.run(_ytdlp_cmd(url, out_tmpl), capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()[-3:]
        msg = " ".join(err)
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            msg += " | YouTube blockt die Server-IP. Lösung: cookies.txt nach /opt/clip-engine/data/assets/ legen oder das Video als direkten Link (Drive/Dropbox) schicken."
        raise DownloadError(msg or "Download fehlgeschlagen")
    title = "video"
    for line in proc.stdout.splitlines():
        try:
            info = json.loads(line)
            title = info.get("title") or title
        except json.JSONDecodeError:
            pass
    files = sorted(dest_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if f.suffix in (".mp4", ".mkv", ".webm", ".mov")]
    if not files:
        raise DownloadError("Keine Videodatei nach dem Download gefunden")
    return files[0], title
