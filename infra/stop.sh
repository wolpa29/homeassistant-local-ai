#!/usr/bin/env bash
# Stop Whisper (STT) + TTS containers.
# After this, containers will NOT auto-restart on reboot (unlike a system crash).
# To bring them back up: bash infra/start.sh
#
# Usage:  bash infra/stop.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose not found." >&2
    exit 1
fi

echo "[infra] Stopping Whisper + TTS..."
$COMPOSE down

echo "[infra] Containers stopped. ✅"
echo "[infra] Note: containers will NOT auto-start on next reboot."
echo "[infra] To restart: bash infra/start.sh"
