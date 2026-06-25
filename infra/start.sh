#!/usr/bin/env bash
# Start Whisper (STT) + TTS in one go, wait until both are healthy, then print
# the exact URLs to paste into the Home Assistant add-on configuration.
#
# Usage:  bash infra/start.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Detect the docker compose command (plugin "docker compose" or legacy "docker-compose").
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose not found. Install Docker first: https://docs.docker.com/get-docker/" >&2
    exit 1
fi

# Best-effort LAN IP so the printed URLs work from the HA host, not just localhost.
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-localhost}"

echo "[infra] Starting Whisper + TTS ($COMPOSE up -d, first run builds/pulls images)..."
$COMPOSE up -d

# Wait for both /health endpoints (up to ~120s; first run pulls models).
wait_health() {
    local name="$1" url="$2" tries=60
    printf "[infra] Waiting for %s (%s) " "$name" "$url"
    for ((i = 0; i < tries; i++)); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            echo " OK"
            return 0
        fi
        printf "."
        sleep 2
    done
    echo " TIMEOUT"
    echo "[infra] $name not healthy yet, check logs: $COMPOSE logs $3" >&2
    return 1
}

WHISPER_OK=0
TTS_OK=0
wait_health "Whisper" "http://localhost:10300/health" faster-whisper && WHISPER_OK=1 || true
wait_health "TTS"     "http://localhost:10400/health" tts             && TTS_OK=1 || true

echo
echo "============================================================"
echo " Paste these into the add-on configuration (Step 4):"
echo "------------------------------------------------------------"
echo "   whisper.url :  http://${HOST_IP}:10300/v1/audio/transcriptions"
echo "   tts.url     :  http://${HOST_IP}:10400/tts"
echo "============================================================"

if [[ "$WHISPER_OK" -eq 1 && "$TTS_OK" -eq 1 ]]; then
    echo "[infra] Both services are up. ✅"
else
    echo "[infra] One or more services are not healthy yet. They may still be"
    echo "        downloading models, re-check with: $COMPOSE logs -f"
    exit 1
fi
