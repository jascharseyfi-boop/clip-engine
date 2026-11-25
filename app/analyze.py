"""Analyse: Szenenwechsel (ffmpeg), Sprache (faster-whisper), Beats/Energie (librosa), Standbilder."""
import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} fehlgeschlagen: {proc.stderr[-800:]}")
    return proc


def probe(video: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)]).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {
        "duration": float(info["format"]["duration"]),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": round(fps, 3) if fps > 0 else 30.0,
        "has_audio": has_audio,
    }


# ---------------------------------------------------------------- Szenen
def detect_scenes(video: Path, duration: float, threshold: float | None = None) -> list[dict]:
    threshold = threshold or config.SCENE_THRESHOLD
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
         "-vf", f"scale=320:-2,select='gt(scene\\,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800)
    cuts = sorted({round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)})
    cuts = [c for c in cuts if 0.25 < c < duration - 0.25]
    # sehr kurze Szenen (< 0.25 s, z. B. Blitzschnitte) mit der vorherigen zusammenfassen
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= 0.25:
            bounds.append(c)
    bounds.append(round(duration, 3))
    return [{"id": i, "start": bounds[i], "end": bounds[i + 1]} for i in range(len(bounds) - 1)]


# ---------------------------------------------------------------- Audio
def extract_audio(video: Path, out: Path) -> Path:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", str(out)])
    return out


def beats_and_energy(wav: Path) -> dict:
    import librosa
    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    if len(y) < sr:
        return {"tempo": 0, "beats": [], "downbeats": [], "energy": []}
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sr).round(3).tolist()
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    strengths = onset_env[np.clip(beat_frames, 0, len(onset_env) - 1)] if len(beat_frames) else np.array([])
    # "starke" Beats: obere 30 % der Onset-Stärke (Akzente, Drops)
    strong = []
    if len(strengths):
        thr = np.percentile(strengths, 70)
        strong = [b for b, s in zip(beats, strengths) if s >= thr]
    # Lautheit pro Sekunde (0-100), damit Claude Höhepunkte der Musik sieht
    hop = sr
    rms = np.array([np.sqrt(np.mean(y[i:i + hop] ** 2)) for i in range(0, len(y), hop)])
    energy = (100 * rms / (rms.max() or 1)).round().astype(int).tolist()
    return {"tempo": float(np.atleast_1d(tempo)[0]), "beats": beats, "strong_beats": strong, "energy": energy}


_whisper = None


def transcribe(wav: Path) -> dict:
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                                cpu_threads=config.WHISPER_THREADS,
                                download_root=str(config.DATA_DIR / "models"))
    # WAV selbst einlesen (16 kHz mono float32) statt über PyAV – neuere PyAV-Versionen brechen faster-whisper
    import soundfile as sf
    audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    segments, info = _whisper.transcribe(audio, word_timestamps=True, vad_filter=True, beam_size=1)
    segs, words = [], []
    for s in segments:
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
        for w in s.words or []:
            if w.word.strip():
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()})
    return {"language": info.language, "segments": segs, "words": words}


# ---------------------------------------------------------------- Standbilder
def grab_frame(video: Path, t: float, out: Path, width: int = 480) -> Path:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)], timeout=120)
    return out


def _font(size: int):
    try:
        return ImageFont.truetype(str(config.FONTS_DIR / "ArchivoBlack-Regular.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def contact_sheets(video: Path, scenes: list[dict], work: Path, per_sheet: int = 12,
                   max_sheets: int | None = None) -> list[dict]:
    """Ein Standbild pro Szene (Mitte), beschriftet mit Szenen-ID, als 4x3-Kontaktabzüge.

    Bei sehr vielen Szenen werden kurze Szenen ausgedünnt, damit max_sheets eingehalten wird.
    """
    max_sheets = max_sheets or config.MAX_SHEETS
    limit = per_sheet * max_sheets
    chosen = scenes
    if len(scenes) > limit:
        # längste Szenen behalten, aber zeitlich sortiert
        chosen = sorted(sorted(scenes, key=lambda s: s["end"] - s["start"], reverse=True)[:limit],
                        key=lambda s: s["start"])
    frames_dir = work / "frames"
    frames_dir.mkdir(exist_ok=True)
    thumbs = []
    for s in chosen:
        t = s["start"] + (s["end"] - s["start"]) * 0.5
        p = frames_dir / f"s{s['id']:03d}.jpg"
        try:
            grab_frame(video, t, p)
            s["frame_t"] = round(t, 3)
            thumbs.append((s, p))
        except RuntimeError:
            continue
    sheets = []
    cols, rows, tw, th = 4, 3, 480, 270
    font = _font(30)
    for i in range(0, len(thumbs), per_sheet):
        batch = thumbs[i:i + per_sheet]
        sheet = Image.new("RGB", (cols * tw, rows * th), (0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        for k, (s, p) in enumerate(batch):
            im = Image.open(p).convert("RGB")
            im.thumbnail((tw, th))
            x, y = (k % cols) * tw, (k // cols) * th
            sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
            label = f"S{s['id']}"
            draw.rectangle([x, y, x + 20 + 22 * len(label), y + 44], fill=(255, 220, 0))
            draw.text((x + 8, y + 4), label, fill=(0, 0, 0), font=font)
        out = work / f"sheet_{i // per_sheet:02d}.jpg"
        sheet.save(out, quality=82)
        sheets.append({"file": out.name, "scene_ids": [s["id"] for s, _ in batch]})
    return sheets
