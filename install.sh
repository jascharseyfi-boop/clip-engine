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
CADDY_MODE=file
CADDYFILE=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$CADDY_CT")
if [ -z "$CADDYFILE" ]; then
  CDIR=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy"}}{{.Source}}{{end}}{{end}}' "$CADDY_CT")
  [ -n "$CDIR" ] && CADDYFILE="$CDIR/Caddyfile"
fi
if [ -f "${CADDYFILE:-}" ]; then
  N8N_HOST=$(grep -oE '^[[:space:]]*[a-zA-Z0-9.-]+\.[a-z]{2,}' "$CADDYFILE" | head -1 | tr -d '[:space:]')
else
  # Caddy läuft ohne Caddyfile als "caddy reverse-proxy --from … --to …"
  CMDLINE=$(docker inspect -f '{{join .Config.Entrypoint " "}} {{join .Config.Cmd " "}}' "$CADDY_CT")
  echo "$CMDLINE" | grep -q "reverse-proxy" || die "Caddy-Konfiguration nicht erkannt: $CMDLINE"
  CADDY_MODE=cmd
  RP_FROM=$(echo "$CMDLINE" | grep -oE -- '--from[ =][^ ]+' | head -1 | sed -E 's/--from[ =]//; s#^https?://##; s#/.*$##')
  RP_TO=$(echo "$CMDLINE" | grep -oE -- '--to[ =][^ ]+' | head -1 | sed -E 's/--to[ =]//')
  [ -n "$RP_FROM" ] && [ -n "$RP_TO" ] || die "Konnte --from/--to aus dem Caddy-Befehl nicht lesen: $CMDLINE"
  N8N_HOST="$RP_FROM"
  C_WD=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$CADDY_CT")
  C_SVC=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CADDY_CT")
  C_CFG=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$CADDY_CT" | cut -d, -f1)
  C_IMG=$(docker inspect -f '{{.Config.Image}}' "$CADDY_CT")
  [ -n "$C_WD" ] && [ -n "$C_SVC" ] && [ -f "$C_CFG" ] || die "Caddy wurde nicht mit Docker Compose gestartet."
  CADDYFILE="$C_WD/Caddyfile"
  case "$(basename "$C_CFG")" in
    compose.yaml) C_OVR="$C_WD/compose.override.yaml";; compose.yml) C_OVR="$C_WD/compose.override.yml";;
    docker-compose.yaml) C_OVR="$C_WD/docker-compose.override.yaml";; *) C_OVR="$C_WD/docker-compose.override.yml";;
  esac
  [ -f "$C_OVR" ] && die "$C_OVR existiert schon – bitte Claude fragen, bevor es überschrieben wird."
fi
if [ -z "${N8N_HOST:-}" ]; then
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

CLIP_BLOCK="
# Clip-Engine: nur fertige Clips (für Vizard/TikTok) und die Setup-Seite sind öffentlich
$CLIP_HOST {
	@allowed path /public/* /setup /setup/*
	handle @allowed {
		reverse_proxy clip-engine:8000
	}
	respond 404
}"

say "Clip-Engine bauen und starten (dauert beim ersten Mal 3–6 Minuten)"
docker compose up -d --build

say "Caddy: öffentliche Clip-Adresse einrichten"
if [ "$CADDY_MODE" = cmd ]; then
  # Caddy von "reverse-proxy"-Befehl auf ein Caddyfile umstellen (gleiche n8n-Weiterleitung + Clip-Adresse)
  cat > "$CADDYFILE" <<EOF
$N8N_HOST {
	reverse_proxy $RP_TO
}
$CLIP_BLOCK
EOF
  docker run --rm -v "$CADDYFILE:/etc/caddy/Caddyfile:ro" "$C_IMG" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
    || { rm -f "$CADDYFILE"; die "Erzeugtes Caddyfile ungültig – nichts geändert, n8n läuft weiter."; }
  cat > "$C_OVR" <<EOF
# angelegt von clip-engine/install.sh: Caddy liest jetzt ./Caddyfile
services:
  $C_SVC:
    command: ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
EOF
  (cd "$C_WD" && docker compose -f "$C_CFG" -f "$C_OVR" up -d "$C_SVC")
  echo "Prüfe, ob n8n weiter erreichbar ist …"
  N8N_OK=""
  for i in $(seq 1 20); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' "https://$N8N_HOST/" || true)
    case "$code" in 2*|3*|401) N8N_OK=1; break;; esac
    sleep 3
  done
  if [ -z "$N8N_OK" ]; then
    rm -f "$C_OVR"
    (cd "$C_WD" && docker compose -f "$C_CFG" up -d "$C_SVC")
    die "n8n war nach der Umstellung nicht erreichbar – alte Caddy-Einstellung wiederhergestellt."
  fi
  echo "Caddy umgestellt, n8n erreichbar."
elif ! grep -q "$CLIP_HOST" "$CADDYFILE"; then
  cp "$CADDYFILE" "$CADDYFILE.bak.$(date +%s)"
  printf '%s\n' "$CLIP_BLOCK" >> "$CADDYFILE"
  if ! docker exec "$CADDY_CT" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    cp "$(ls -t "$CADDYFILE".bak.* | head -1)" "$CADDYFILE"
    die "Caddy-Konfiguration ungültig – alte Version wiederhergestellt, n8n läuft unverändert weiter."
  fi
  docker exec "$CADDY_CT" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
    || docker restart "$CADDY_CT" >/dev/null
  echo "Block ergänzt."
else
  echo "Block schon vorhanden."
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
