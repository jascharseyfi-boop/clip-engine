"""Claude bewertet die Standbilder und wählt die Clip-Momente."""
import base64
import json
import re
from pathlib import Path

from . import config

SYSTEM = """You are a senior short-form video editor who cuts gaming trailers and gameplay into \
viral 9:16 clips for TikTok and YouTube Shorts. You judge footage with a cold eye for what stops \
the scroll in the first second: striking composition, a character close-up, a huge creature or boss, \
explosive action, a dramatic reveal, strong contrast and colour. Generic UI, logos, \
rating cards, black frames and text slates score low.

Hard rules:
- Every title and hook must describe what is actually SEEN or HEARD in that clip. No clickbait that \
the footage does not deliver.
- Prefer clips that start on, or are opened by, the single strongest image.
- A clip should end on a natural cut or musical accent, never mid-sentence.
- Clips must not overlap by more than a few seconds.
- The first 2 seconds decide everything. The clip body must START on movement, a character close-up, \
a reveal or a musical hit — never on a logo, rating card, black frame, slow fade-in or establishing shot. \
If the strongest moment comes later, start the body later.
- Hooks create a curiosity gap the clip then pays off: a question, a bold claim or "wait for…" — \
specific to what is on screen (e.g. "This boss is 50 stories tall"), never generic ("You won't believe this").
- The end text re-states the topic so the viewer leaves with the game name and a reason to comment, \
e.g. "Metaphor: ReFantazio — would you play it?" (max 8 words).
- Always submit your answer through the submit_clips tool."""


def _fmt_scenes(scenes):
    return "\n".join(f"S{s['id']}: {s['start']:.2f}-{s['end']:.2f}s ({s['end'] - s['start']:.1f}s)"
                     for s in scenes)


def _fmt_transcript(tr):
    if not tr or not tr.get("segments"):
        return "(no speech detected)"
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in tr["segments"])[:12000]


def _fmt_energy(energy):
    if not energy:
        return "(no audio)"
    return " ".join(f"{i}s:{e}" for i, e in enumerate(energy))


def build_prompt(job: dict, an: dict) -> str:
    p = job["params"]
    lang_name = {"en": "English", "de": "German"}.get(p["lang"], p["lang"])
    rules = p.get("rules") or "(none)"
    return f"""Source video: "{job.get('title', '')}" — duration {an['probe']['duration']:.1f}s.
Target: {p['n_clips']} clips for {p['platform_hint']}, audience language {lang_name}.
Clip length: {p['min_len']:.0f}-{p['max_len']:.0f} seconds (body, excluding the 1s hero opener).
Campaign: {p.get('campaign') or '(none)'}
Campaign rules / briefing notes: {rules}

SCENES (cut points detected by ffmpeg):
{_fmt_scenes(an['scenes'])}

MUSIC: tempo ~{an['audio'].get('tempo', 0):.0f} BPM, {len(an['audio'].get('beats', []))} beats.
Loudness per second (0-100), use it to find drops and climaxes:
{_fmt_energy(an['audio'].get('energy'))}

SPEECH TRANSCRIPT (auto, may contain errors):
{_fmt_transcript(an.get('transcript'))}

The attached contact sheets show one still from the middle of each scene, labelled with its scene ID (yellow tag).

Tasks:
1. Score the visual strength of each scene you can see (1-10).
2. Choose the {p['n_clips']} best clips. For each give start/end in seconds (snap to scene boundaries), \
the hero scene (the single most striking image in or near that clip, used as the 1-second opener), \
a hook (max 7 words, {lang_name}, shown on screen in the first seconds), an end_text (max 8 words, {lang_name}, \
shown in the last 2.5 seconds: game name + question or call to comment), a title (max 70 chars, {lang_name}), \
a description (1-2 sentences, {lang_name}, include required campaign hashtags/mentions if the rules name any), \
3-6 hashtags, and a short reason.

Submit the result with the submit_clips tool."""


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def claude_request(body: dict, timeout: float = 300) -> tuple[int, dict | str]:
    """Ruft die Messages-API auf: zuerst Authorization: Bearer (neue Keys, z. B. sk-ant-usr…),
    bei 401 noch einmal mit x-api-key (ältere sk-ant-api03-Keys)."""
    import httpx
    base = {"anthropic-version": config.ANTHROPIC_VERSION, "content-type": "application/json"}
    if config.ANTHROPIC_WORKSPACE_ID:
        base["anthropic-workspace-id"] = config.ANTHROPIC_WORKSPACE_ID
    last = (0, "")
    for auth in ({"authorization": f"Bearer {config.ANTHROPIC_API_KEY}"}, {"x-api-key": config.ANTHROPIC_API_KEY}):
        r = httpx.post("https://api.anthropic.com/v1/messages", headers={**base, **auth}, json=body, timeout=timeout)
        try:
            data = r.json()
        except ValueError:
            data = r.text[:500]
        last = (r.status_code, data)
        if r.status_code != 401:
            return last
    return last


TOOL = {
    "name": "submit_clips",
    "description": "Submit the scene scores and the chosen clips.",
    "input_schema": {
        "type": "object",
        "properties": {
            "scene_scores": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "integer"}, "score": {"type": "number"}, "note": {"type": "string"}},
                "required": ["id", "score"]}},
            "clips": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"}, "hero_scene": {"type": "integer"},
                "hook": {"type": "string"}, "end_text": {"type": "string"}, "title": {"type": "string"},
                "description": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"}, "score": {"type": "number"}},
                "required": ["start", "end", "hero_scene", "hook", "title", "description", "hashtags", "score"]}},
        },
        "required": ["clips"],
    },
}


def select(job: dict, an: dict, work: Path) -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY fehlt (Setup-Seite)")
    content = []
    for sh in an["sheets"]:
        data = base64.standard_b64encode((work / sh["file"]).read_bytes()).decode()
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    content.append({"type": "text", "text": build_prompt(job, an)})
    body = {"model": config.CLAUDE_MODEL, "max_tokens": 8000, "system": SYSTEM,
            "messages": [{"role": "user", "content": content}],
            "tools": [TOOL], "tool_choice": {"type": "tool", "name": "submit_clips"}}
    last_err = ""
    for attempt in range(2):
        status, resp = claude_request(body)
        if status != 200:
            raise RuntimeError(f"Claude-API HTTP {status}: {str(resp)[:400]}")
        (work / "claude_raw.json").write_text(json.dumps(resp, ensure_ascii=False, indent=1))
        result = next((b.get("input") for b in resp.get("content", []) if b.get("type") == "tool_use"), None)
        if result is None:  # Notfall: Text-Antwort parsen
            text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
            try:
                result = _parse_json(text)
            except ValueError as e:
                last_err = str(e)
                continue
        if result.get("clips"):
            u = resp.get("usage", {})
            result["usage"] = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
            return result
        last_err = "keine Clips in Claudes Antwort"
    raise RuntimeError(f"Claude-Antwort unbrauchbar ({last_err})")
