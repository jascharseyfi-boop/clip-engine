# Clip-Engine (Prototyp)

Eigene Clip-Engine neben n8n auf dem Hetzner-Server: Trailer/Gameplay rein, fertige 9:16-Clips zur Freigabe in Telegram raus, nach Klick automatisch geplant auf YouTube (n8n) oder TikTok (Vizard).

```
Telegram-Link ─▶ n8n ─▶ Clip-Engine ─┬─ ffmpeg: Szenenwechsel
                                     ├─ faster-whisper: Sprache + Wortzeiten
                                     ├─ librosa: Beats, Akzente, Lautheit
                                     ├─ Claude: bewertet Standbilder (Kontaktabzüge), wählt Momente, Hook/Titel
                                     ├─ Regeln: stärkstes Bild zuerst, Ende auf Schnitt/Beat, 18–45 s
                                     └─ Render: 9:16, Hook-Text, Wort-Untertitel, Logo
            Telegram ◀── Clip + Buttons [▶️ YouTube EN] [🎵 TikTok DE] [❌]
   Klick ─▶ n8n ─▶ Engine bucht Slot ─▶ YouTube-Upload (n8n, privat + publishAt) / Vizard publish-video (TikTok)
```

## Installation (Hetzner-Web-Konsole, als root)

```bash
curl -fsSL https://raw.githubusercontent.com/jascharseyfi-boop/clip-engine/main/install.sh | bash
```

Das Skript holt den Code nach `/opt/clip-engine`, legt bei Bedarf 4 GB Swap an, hängt die Engine ins Docker-Netz von n8n, ergänzt im Caddyfile die Adresse `clips.<deine-sslip-domain>` (nur `/public/*` und `/setup` sind öffentlich), baut und startet den Container und zeigt am Ende **Setup-Adresse und Setup-Code**.

Danach alles Weitere auf dem iPad im Browser:

1. **`https://clips.188-245-25-76.sslip.io/setup`** öffnen, Setup-Code eingeben und einfügen:
   - Anthropic-API-Key
   - Token eines **neuen** Telegram-Bots (bei @BotFather `/newbot`, z. B. „Clip Engine“). Eigener Bot, weil Telegram pro Bot nur einen Webhook erlaubt und dein Kampagnen-Check-Bot seinen schon belegt.
   - Chat-ID (vorausgefüllt 726174060) – vorher dem neuen Bot einmal `/start` schreiben
   - optional Vizard-API-Key, dann „Vizard-Konten anzeigen“ → TikTok-ID eintragen
2. „Verbindungen prüfen“ – Telegram bekommt eine Testnachricht.
3. Den angezeigten **API-Key** in n8n als Credential *Header Auth* anlegen: Name `X-API-Key`, Value = Key, Credential-Name „Clip-Engine“.

## n8n-Workflow

`n8n/clip-engine-telegram.json` in n8n importieren (Workflows → Import from URL:
`https://raw.githubusercontent.com/jascharseyfi-boop/clip-engine/main/n8n/clip-engine-telegram.json`).

Credentials zuordnen:
- **Clip-Bot** → neues Telegram-Credential mit dem Token des Clip-Bots
- alle HTTP-Knoten → „Clip-Engine“ (Header Auth)
- **YouTube hochladen** → YouTube-OAuth2-Credential für den englischen Kanal (Google-Cloud-Projekt mit „YouTube Data API v3“, Redirect-URL aus n8n)

Workflow aktivieren.

## Benutzung (Telegram, Clip-Bot)

```
https://youtu.be/XXXX en 3 kampagne: SEGA Metaphor regeln: #MetaphorReFantazio @SEGA
```

| Angabe | Bedeutung |
|---|---|
| `en` / `de` | Sprache von Hook, Titel, Beschreibung (Standard `en`) |
| Zahl | Anzahl Clips (Standard 3, max. 10) |
| `blur` / `crop` / `fit` | 9:16-Layout: Video mittig auf unscharfem Hintergrund / Vollbild-Ausschnitt / schwarze Balken |
| `kampagne: …` | Name für Claude |
| `regeln: …` | Pflicht-Hashtags, Erwähnungen, Verbote aus dem Briefing (immer ans Ende) |
| `ohnetext` | Transkription überspringen (reine Musik-Trailer, schneller) |
| `/status` | letzte Jobs |

Quellen: YouTube, Google Drive, Dropbox, direkte MP4-Links. Blockt YouTube die Server-IP („Sign in to confirm you're not a bot“), den Trailer aus dem Kampagnen-Content-Folder (Drive/Dropbox) verlinken oder eine `cookies.txt` nach `/opt/clip-engine/data/assets/` legen.

**Logo:** PNG mit transparentem Hintergrund als `/opt/clip-engine/data/assets/logo.png` ablegen – wird oben rechts eingeblendet.

## Schnittregeln (in `app/render.py`)

1. Start auf einer Szenengrenze (±1,5 s um Claudes Vorschlag).
2. Ende auf dem Szenenwechsel, der am nächsten an Claudes Ende liegt und möglichst auf einen starken Beat fällt; gibt es keinen, auf den nächsten Beat.
3. Länge `CLIP_MIN_LEN`–`CLIP_MAX_LEN` (18–45 s).
4. Das stärkste Bild (Claudes `hero_scene`) läuft 1,2 s als Opener vor dem Clip, falls der Clip nicht ohnehin damit beginnt.
5. Hook-Text 3,5 s oben, Untertitel Wort für Wort (aktuelles Wort gelb), Audio-Fades an Schnitten.
6. Clips, die sich zu mehr als 50 % überschneiden, werden aussortiert.

Alle Werte lassen sich in `/opt/clip-engine/.env` ändern (danach `cd /opt/clip-engine && docker compose up -d`).

## Wartung

```bash
bash /opt/clip-engine/install.sh        # Update (holt neuen Code, baut neu)
docker logs clip-engine --tail 50       # Log
```

Jobs liegen in `/opt/clip-engine/data/jobs/<id>/` (Quelle, Analyse, Kontaktabzüge, Claude-Antwort, Clips) und werden nach 7 Tagen gelöscht.
