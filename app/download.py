"""Quellvideo holen: YouTube, Google Drive, Dropbox, direkte MP4-Links (alles über yt-dlp)."""
import json
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


def fetch(url: str, dest_dir: Path) -> tuple[Path, str]:
    """Lädt das Video und gibt (Pfad, Titel) zurück."""
    dest_dir.mkdir(parents=True, exist_ok=True)
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
