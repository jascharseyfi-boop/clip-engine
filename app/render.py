"""Schnittregeln + Rendering (9:16, Hook-Text, Untertitel, Logo)."""
import bisect
import subprocess
from pathlib import Path

from . import config

W, H = 1080, 1920


# ---------------------------------------------------------------- Schnittregeln
def _nearest(values: list[float], x: float) -> float | None:
    if not values:
        return None
    i = bisect.bisect_left(values, x)
    cands = values[max(0, i - 1):i + 1]
    return min(cands, key=lambda v: abs(v - x))


def _near(values: list[float], x: float, tol: float) -> bool:
    n = _nearest(values, x)
    return n is not None and abs(n - x) <= tol


def plan_clip(c: dict, an: dict, p: dict) -> dict:
    """Wendet die festen Regeln auf Claudes Vorschlag an.

    1. Start auf Szenengrenze.
    2. Ende auf Szenenwechsel, bevorzugt einer, der auf einen (starken) Beat fällt; sonst auf Beat.
    3. Länge zwischen min_len und max_len.
    4. Stärkstes Bild (hero_scene) als Opener davor, wenn der Clip nicht ohnehin damit beginnt.
    """
    dur = an["probe"]["duration"]
    scenes = an["scenes"]
    bounds = sorted({s["start"] for s in scenes} | {dur})
    beats = an["audio"].get("beats", [])
    strong = an["audio"].get("strong_beats", [])
    min_len, max_len = p["min_len"], p["max_len"]

    start = max(0.0, float(c.get("start", 0)))
    nb = _nearest(bounds, start)
    if nb is not None and abs(nb - start) <= 1.5 and nb < dur - min_len:
        start = nb
    start = min(start, max(0.0, dur - min_len))

    want_end = float(c.get("end", start + (min_len + max_len) / 2))
    want_end = min(max(want_end, start + min_len), start + max_len, dur)
    lo, hi = start + min_len, min(start + max_len, dur)

    def score(b: float) -> float:
        s = -abs(b - want_end)
        if _near(strong, b, 0.15):
            s += 1.5
        elif _near(beats, b, 0.12):
            s += 0.7
        return s

    cand = [b for b in bounds if lo <= b <= hi and abs(b - want_end) <= 4.0]
    end_reason = "scene"
    if cand:
        end = max(cand, key=score)
        if _near(strong, end, 0.15) or _near(beats, end, 0.12):
            end_reason = "scene+beat"
    else:
        bcand = [b for b in (strong or beats) if lo <= b <= hi]
        if bcand:
            end = min(bcand, key=lambda b: abs(b - want_end))
            end_reason = "beat"
        else:
            end = min(max(want_end, lo), hi)
            end_reason = "clamp"

    segments = []
    hero_id = c.get("hero_scene")
    hero = next((s for s in scenes if s["id"] == hero_id), None)
    hero_info = None
    if hero and config.HERO_SECONDS > 0 and abs(hero["start"] - start) > 0.05:
        center = hero.get("frame_t", (hero["start"] + hero["end"]) / 2)
        half = config.HERO_SECONDS / 2
        hs = max(hero["start"], center - half)
        he = min(hero["end"], hs + config.HERO_SECONDS)
        if he - hs >= 0.4:
            segments.append({"start": round(hs, 3), "end": round(he, 3), "kind": "hero"})
            hero_info = hero_id
    segments.append({"start": round(start, 3), "end": round(end, 3), "kind": "body"})
    return {"segments": segments, "hero_scene": hero_info, "end_reason": end_reason,
            "duration": round(sum(s["end"] - s["start"] for s in segments), 2)}


# ---------------------------------------------------------------- Untertitel (ASS)
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(txt: str) -> str:
    return txt.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _map_words(words: list[dict], segments: list[dict]) -> list[dict]:
    out, offset = [], 0.0
    for seg in segments:
        for w in words:
            if w["start"] >= seg["start"] - 0.05 and w["end"] <= seg["end"] + 0.15:
                out.append({"word": w["word"],
                            "start": offset + max(0.0, w["start"] - seg["start"]),
                            "end": offset + min(seg["end"], w["end"]) - seg["start"]})
        offset += seg["end"] - seg["start"]
    return out


def _group(words: list[dict], max_words=3, max_span=1.4, max_gap=0.6) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["end"] - cur[0]["start"] > max_span
                    or w["start"] - cur[-1]["end"] > max_gap or cur[-1]["word"][-1:] in ".,!?;:"):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(path: Path, hook: str, words: list[dict], total: float) -> Path:
    hook_end = total if config.HOOK_SECONDS <= 0 else min(total, config.HOOK_SECONDS)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Hook,Anton,124,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,7,2,8,70,70,330,1",
        "Style: Sub,Archivo Black,66,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,6,2,2,80,80,520,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(hook_end)},Hook,,0,0,0,,"
                     f"{{\\fad(120,200)}}{_esc(hook.upper())}")
    for g in _group(words):
        g_end = g[-1]["end"] + 0.08
        for i, w in enumerate(g):
            s = w["start"]
            e = g[i + 1]["start"] if i + 1 < len(g) else g_end
            if e - s < 0.04:
                continue
            parts = []
            for k, ww in enumerate(g):
                t = _esc(ww["word"].upper())
                parts.append("{\\c&H00E6FF&}" + t + "{\\c&HFFFFFF&}" if k == i else t)
            lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Sub,,0,0,0,,{' '.join(parts)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- ffmpeg
def _layout_filter(layout: str) -> str:
    if layout == "crop":
        return f"[vc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v1]"
    if layout == "fit":
        return (f"[vc]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1]")
    fw = int(W * config.FG_ZOOM) // 2 * 2
    return (f"[vc]split=2[b][f];"
            f"[b]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
            f"scale={W}:{H},eq=brightness=-0.12:saturation=1.15[bg];"
            f"[f]scale={fw}:-2,crop='min(iw,{W})':'min(ih,{H})'[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v1]")


def render(src: Path, plan: dict, ass: Path, out: Path, layout: str, has_audio: bool, crf: int | None = None) -> Path:
    segs = plan["segments"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s in segs:
        cmd += ["-ss", f"{s['start']:.3f}", "-t", f"{s['end'] - s['start']:.3f}", "-i", str(src)]
    n = len(segs)
    if not has_audio:
        for s in segs:
            cmd += ["-f", "lavfi", "-t", f"{s['end'] - s['start']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    logo = config.ASSETS_DIR / config.LOGO_FILE
    logo_idx = None
    if logo.exists():
        logo_idx = len(segs) * (1 if has_audio else 2)
        cmd += ["-i", str(logo)]

    parts = []
    for i, s in enumerate(segs):
        d = s["end"] - s["start"]
        a_in = f"{i}:a" if has_audio else f"{n + i}:a"
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS,fps=30,format=yuv420p,scale=iw:ih,setsar=1[v{i}]")
        parts.append(f"[{a_in}]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                     f"afade=t=in:d=0.03,afade=t=out:st={max(0, d - 0.06):.3f}:d=0.06[a{i}]")
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=1[vc][ac]")
    parts.append(_layout_filter(layout))
    ass_path = str(ass).replace("\\", "/").replace(":", "\\:")
    fonts = str(config.FONTS_DIR).replace(":", "\\:")
    parts.append(f"[v1]ass='{ass_path}':fontsdir='{fonts}'[v2]")
    vout = "[v2]"
    if logo_idx is not None:
        parts.append(f"[{logo_idx}:v]scale={config.LOGO_WIDTH}:-1,format=rgba,colorchannelmixer=aa=0.92[lg]")
        parts.append("[v2][lg]overlay=W-w-48:120[v3]")
        vout = "[v3]"
    total = plan["duration"]
    parts.append(f"[ac]afade=t=out:st={max(0, total - 0.35):.3f}:d=0.35[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", vout, "-map", "[aout]",
            "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", str(crf or config.CRF),
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", "-threads", "2", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError("Rendern fehlgeschlagen: " + proc.stderr[-1200:])
    # Telegram-Bots dürfen max. 50 MB senden
    if out.stat().st_size > 48 * 1024 * 1024 and (crf or config.CRF) < 30:
        return render(src, plan, ass, out, layout, has_audio, crf=(crf or config.CRF) + 4)
    return out
