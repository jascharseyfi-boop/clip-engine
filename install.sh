#!/usr/bin/env bash
# Clip-Engine installieren bzw. aktualisieren (Hetzner, Ubuntu 24.04, neben n8n + Caddy).
# Aufruf in der Web-Konsole als root:
#   curl -fsSL https://raw.githubusercontent.com/jascharseyfi-boop/clip-engine/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/jascharseyfi-boop/clip-engine.git"
DIR="/opt/clip-engine"
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mFEHLER: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "Bitte als root ausführen."
command -v docker >/dev/null || die "Docker fehlt."

say "Code holen"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone --depth 1 "$REPO" "$DIR"; fi
cd "$DIR"
mkdir -p data/assets

say "Swap prüfen (Whisper + n8n brauchen Luft bei 4 GB RAM)"
if ! swapon --show | grep -q .; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "4 GB Swap angelegt."
else
  echo "Swap vorhanden."
fi

say "n8n und Caddy finden"
N8N_CT=$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($2) ~ /n8n/ {print $1; exit}')
CADDY_CT=$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($2) ~ /caddy/ {print $1; exit}')
[ -n "$N8N_CT" ] || die "Kein laufender n8n-Container gefunden."
[ -n "$CADDY_CT" ] || die "Kein laufender Caddy-Container gefunden."
NET=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$N8N_CT" | awk '{print $1}')
CADDYFILE=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$CADDY_CT")
if [ -z "$CADDYFILE" ]; then
  CDIR=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy"}}{{.Source}}{{end}}{{end}}' "$CADDY_CT")
  [ -n "$CDIR" ] && CADDYFILE="$CDIR/Caddyfile"
fi
[ -f "${CADDYFILE:-}" ] || CADDYFILE=$(find /opt/n8n -maxdepth 2 -name Caddyfile | head -1)
[ -f "${CADDYFILE:-}" ] || die "Caddyfile nicht gefunden."
N8N_HOST=$(grep -oE '^[[:space:]]*[a-zA-Z0-9.-]+\.[a-z]{2,}' "$CADDYFILE" | head -1 | tr -d '[:space:]')
if [ -z "$N8N_HOST" ]; then
  IP=$(hostname -I | awk '{print $1}'); N8N_HOST="n8n.${IP//./-}.sslip.io"
fi
case "$N8N_HOST" in n8n.*) CLIP_HOST="clips.${N8N_HOST#n8n.}";; *) CLIP_HOST="clips.$N8N_HOST";; esac
echo "n8n: $N8N_CT · Netz: $NET · Caddy: $CADDY_CT · Caddyfile: $CADDYFILE"
echo "Clip-Domain: $CLIP_HOST"

say ".env anlegen/ergänzen"
[ -f .env ] || cp .env.example .env
setv() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
getv() { grep "^$1=" .env | head -1 | cut -d= -f2-; }
[ -n "$(getv CLIP_ENGINE_API_KEY)" ] || setv CLIP_ENGINE_API_KEY "$(openssl rand -hex 24)"
[ -n "$(getv SETUP_CODE)" ] || setv SETUP_CODE "$(tr -dc 'A-HJ-NP-Z2-9' </dev/urandom | head -c 8)"
setv PUBLIC_BASE_URL "https://$CLIP_HOST"
setv N8N_NETWORK "$NET"
chmod 600 .env

say "Caddy: öffentliche Clip-Adresse einrichten"
if ! grep -q "$CLIP_HOST" "$CADDYFILE"; then
  cp "$CADDYFILE" "$CADDYFILE.bak.$(date +%s)"
  cat >> "$CADDYFILE" <<EOF

# Clip-Engine: nur fertige Clips (für Vizard/TikTok) und die Setup-Seite sind öffentlich
$CLIP_HOST {
	@allowed path /public/* /setup /setup/*
	handle @allowed {
		reverse_proxy clip-engine:8000
	}
	respond 404
}
EOF
  echo "Block ergänzt."
  if ! docker exec "$CADDY_CT" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    cp "$(ls -t "$CADDYFILE".bak.* | head -1)" "$CADDYFILE"
    die "Caddy-Konfiguration ungültig – alte Version wiederhergestellt, n8n läuft unverändert weiter."
  fi
else
  echo "Block schon vorhanden."
fi

say "Clip-Engine bauen und starten (dauert beim ersten Mal 3–6 Minuten)"
docker compose up -d --build

say "Caddy neu laden"
sleep 3
if ! docker exec "$CADDY_CT" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null; then
  docker restart "$CADDY_CT" >/dev/null
fi

say "Warten, bis die Engine antwortet"
for i in $(seq 1 40); do
  if docker exec clip-engine python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')" 2>/dev/null; then OK=1; break; fi
  sleep 3
done
[ "${OK:-}" = 1 ] || die "Engine startet nicht – Log: docker logs clip-engine --tail 50"

cat <<EOF

=====================================================================
  Clip-Engine läuft.

  1) Auf dem iPad öffnen:   https://$CLIP_HOST/setup
  2) Setup-Code:            $(getv SETUP_CODE)
  3) Dort Schlüssel einfügen, speichern, „Verbindungen prüfen“.

  Update später:   bash /opt/clip-engine/install.sh
  Log ansehen:     docker logs clip-engine --tail 50
=====================================================================
EOF
