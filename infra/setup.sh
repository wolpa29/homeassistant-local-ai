#!/usr/bin/env bash
# setup.sh — Infra setup (Whisper STT + TTS server)
#
# Run this on the host machine (from whatever directory you want):
#   mkdir ai-infra && cd ai-infra
#   curl -sSL https://raw.githubusercontent.com/wolpay29/hass-ai-gateway/main/infra/setup.sh | bash

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${CYAN}=== $* ===${NC}"; }

INSTALL_DIR="$(pwd)"
GITHUB_RAW="https://raw.githubusercontent.com/wolpay29/hass-ai-gateway/main/infra"

# ---------------------------------------------------------------------------
section "Infra Setup — Whisper STT + TTS Server"
# ---------------------------------------------------------------------------
echo "  Install directory : $INSTALL_DIR"
echo

# ---------------------------------------------------------------------------
section "1 — Check dependencies"
# ---------------------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
    info "Docker Compose (plugin) found ✅"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
    info "docker-compose (standalone) found ✅"
else
    error "Docker Compose not found. Install Docker first: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! command -v wget >/dev/null 2>&1; then
    info "Installing wget…"
    sudo apt-get install -y -qq wget
fi

# ---------------------------------------------------------------------------
section "2 — Download files"
# ---------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR/tts_server/models"
mkdir -p "$INSTALL_DIR/faster_whisper/model-cache"

download() {
    local dest="$1" url="$2"
    info "Downloading $(basename "$dest")…"
    wget -q -O "$dest" "$url"
}

download "$INSTALL_DIR/docker-compose.yml"     "${GITHUB_RAW}/docker-compose.yml"
download "$INSTALL_DIR/start.sh"               "${GITHUB_RAW}/start.sh"
download "$INSTALL_DIR/stop.sh"                "${GITHUB_RAW}/stop.sh"
download "$INSTALL_DIR/tts_server/Dockerfile"  "${GITHUB_RAW}/tts_server/Dockerfile"
download "$INSTALL_DIR/tts_server/main.py"     "${GITHUB_RAW}/tts_server/main.py"
download "$INSTALL_DIR/tts_server/entrypoint.sh" "${GITHUB_RAW}/tts_server/entrypoint.sh"
download "$INSTALL_DIR/tts_server/requirements.txt" "${GITHUB_RAW}/tts_server/requirements.txt"

chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh" "$INSTALL_DIR/tts_server/entrypoint.sh"

# ---------------------------------------------------------------------------
section "3 — Download Piper TTS model"
# ---------------------------------------------------------------------------
MODEL_DIR="$INSTALL_DIR/tts_server/models"
MODEL_BASE="de_DE-thorsten-low"
MODEL_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/low"

if [[ -f "$MODEL_DIR/${MODEL_BASE}.onnx" ]]; then
    info "Piper model already exists — skipping download."
else
    info "Downloading ${MODEL_BASE}.onnx (~16 MB)…"
    wget -q --show-progress -P "$MODEL_DIR" "${MODEL_URL}/${MODEL_BASE}.onnx"
    wget -q --show-progress -P "$MODEL_DIR" "${MODEL_URL}/${MODEL_BASE}.onnx.json"
    info "Piper model downloaded ✅"
fi

# ---------------------------------------------------------------------------
section "4 — Start services"
# ---------------------------------------------------------------------------
bash "$INSTALL_DIR/start.sh"

# ---------------------------------------------------------------------------
section "Done"
# ---------------------------------------------------------------------------
echo
echo "  Useful commands:"
echo "    bash ${INSTALL_DIR}/start.sh   — start Whisper + TTS"
echo "    bash ${INSTALL_DIR}/stop.sh    — stop containers"
echo
