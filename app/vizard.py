"""TikTok-Posting über die Vizard-API (publish-video mit videoUrl)."""
import httpx

from . import config

BASE = "https://elb-api.vizard.ai/hvizard-server-front/open-api/v1/project"


def enabled() -> bool:
    return bool(config.VIZARD_API_KEY and config.VIZARD_TIKTOK_ACCOUNT_ID)


def social_accounts() -> dict:
    r = httpx.get(f"{BASE}/social-accounts", headers={"VIZARDAI_API_KEY": config.VIZARD_API_KEY}, timeout=30)
    return r.json()


def publish(video_url: str, publish_ms: int | None, post: str, title: str = "") -> dict:
    body = {"videoUrl": video_url, "socialAccountId": config.VIZARD_TIKTOK_ACCOUNT_ID, "post": post}
    if publish_ms:
        body["publishTime"] = publish_ms
    if title:
        body["title"] = title
    r = httpx.post(f"{BASE}/publish-video", json=body,
                   headers={"VIZARDAI_API_KEY": config.VIZARD_API_KEY}, timeout=60)
    try:
        data = r.json()
    except ValueError:
        data = {"code": r.status_code, "errMsg": r.text[:300]}
    codes = {4001: "API-Key ungültig", 4004: "Funktion erfordert bezahlten Vizard-Tarif",
             4011: "Social-Account-ID ungültig", 4012: "Clip ist schon eingeplant",
             4013: "Limit von 30 geplanten Videos erreicht", -1000: "ungültige Parameter"}
    data["ok"] = data.get("code") == 2000
    if not data["ok"]:
        data["hint"] = codes.get(data.get("code"), data.get("errMsg", "unbekannter Fehler"))
    return data
