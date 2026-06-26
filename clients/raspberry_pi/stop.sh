#!/usr/bin/env bash
# Stop the voice-client systemd service.
# To bring it back up: bash start.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

SERVICE_NAME="voice-client"

if ! systemctl list-unit-files "${SERVICE_NAME}.service" &>/dev/null; then
    echo -e "${YELLOW}[WARN]${NC}  Service '${SERVICE_NAME}' is not installed."
    exit 0
fi

echo "[voice-client] Stopping ${SERVICE_NAME}…"
sudo systemctl stop "$SERVICE_NAME"

echo "[voice-client] Disabling autostart…"
sudo systemctl disable "$SERVICE_NAME"

STATUS=$(sudo systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo "inactive")
if [[ $STATUS == "inactive" ]]; then
    echo -e "[voice-client] ${GREEN}Service stopped.${NC}"
else
    echo -e "[voice-client] ${YELLOW}Service status: ${STATUS}${NC}"
fi

echo
echo "  To start again:  bash start.sh"
