#!/usr/bin/env bash
# Start (or restart) the voice-client systemd service.
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

SERVICE_NAME="voice-client"

if ! systemctl list-unit-files "${SERVICE_NAME}.service" &>/dev/null; then
    echo "Service '${SERVICE_NAME}' is not installed yet. Run install.sh first."
    exit 1
fi

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "[voice-client] Restarting ${SERVICE_NAME}…"
    sudo systemctl restart "$SERVICE_NAME"
else
    echo "[voice-client] Starting ${SERVICE_NAME}…"
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl start "$SERVICE_NAME"
fi

STATUS=$(sudo systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo "unknown")
if [[ $STATUS == "active" ]]; then
    echo -e "[voice-client] ${GREEN}Service is running.${NC}"
else
    echo -e "[voice-client] ${YELLOW}Service status: ${STATUS}${NC}"
fi

echo
echo "  Live logs:  journalctl -u ${SERVICE_NAME} -f"
echo "  Stop:       bash stop.sh"
