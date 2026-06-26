#!/usr/bin/env bash
# start.sh — Infra: Whisper STT + TTS Server
#
# First run:  downloads all files, Piper TTS model, then starts the containers.
# Later runs: skips downloads if files already exist — just starts the containers.
#
# Usage (first time):
#   mkdir ai-infra && cd ai-infra
#   curl -sSL https://raw.githubusercontent.com/wolpa29/homeassistant-local-ai/main/infra/start.sh | bash
#
# Usage (after that):
#   bash start.sh

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

INSTALL_DIR="$(pwd)"
GITHUB_RAW="https://raw.githubusercontent.com/wolpa29/homeassistant-local-ai/main/infra"

# ---------------------------------------------------------------------------
# Docker Compose detection
# ---------------------------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
    echo "[infra] Docker not accessible — adding $USER to docker group…"
    sudo usermod -aG docker "$USER"
    echo "[infra] Done. Run this to activate without logout:"
    echo
    echo "    newgrp docker"
    echo "    bash start.sh"
    echo
    exit 0
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: Docker Compose not found. Install Docker first: https://docs.docker.com/get-docker/" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# First run: download files
# ---------------------------------------------------------------------------
if [[ ! -f "$INSTALL_DIR/docker-compose.yml" ]]; then
    echo -e "\n${CYAN}=== First run — downloading files ===${NC}"

    if ! command -v wget >/dev/null 2>&1; then
        sudo apt-get install -y -qq wget
    fi

    mkdir -p "$INSTALL_DIR/tts_server/models"
    mkdir -p "$INSTALL_DIR/faster_whisper/model-cache"

    download() {
        info "Downloading $(basename "$1")…"
        wget -q -O "$1" "$2"
    }

    download "$INSTALL_DIR/docker-compose.yml"            "${GITHUB_RAW}/docker-compose.yml"
    download "$INSTALL_DIR/stop.sh"                       "${GITHUB_RAW}/stop.sh"
    download "$INSTALL_DIR/tts_server/Dockerfile"         "${GITHUB_RAW}/tts_server/Dockerfile"
    download "$INSTALL_DIR/tts_server/main.py"            "${GITHUB_RAW}/tts_server/main.py"
    download "$INSTALL_DIR/tts_server/entrypoint.sh"      "${GITHUB_RAW}/tts_server/entrypoint.sh"
    download "$INSTALL_DIR/tts_server/requirements.txt"   "${GITHUB_RAW}/tts_server/requirements.txt"
    chmod +x "$INSTALL_DIR/stop.sh" "$INSTALL_DIR/tts_server/entrypoint.sh"

    MODEL_DIR="$INSTALL_DIR/tts_server/models"
    MODEL_BASE="de_DE-thorsten-low"
    MODEL_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/low"
    info "Downloading Piper TTS model (~16 MB)…"
    wget -q --show-progress -P "$MODEL_DIR" "${MODEL_URL}/${MODEL_BASE}.onnx"
    wget -q --show-progress -P "$MODEL_DIR" "${MODEL_URL}/${MODEL_BASE}.onnx.json"
fi

# ---------------------------------------------------------------------------
# GPU check
# ---------------------------------------------------------------------------
cd "$INSTALL_DIR"

WHISPER_MODEL=$(grep 'WHISPER__MODEL' docker-compose.yml | awk -F= '{print $2}' | tr -d ' ' | head -1)
WHISPER_DEVICE=$(grep 'WHISPER__INFERENCE_DEVICE' docker-compose.yml | awk -F= '{print $2}' | tr -d ' ' | head -1)
TTS_VOICE=$(grep 'DEFAULT_VOICE' docker-compose.yml | awk -F= '{print $2}' | tr -d ' ' | head -1)

echo
echo "[infra] STT : ${WHISPER_MODEL:-unknown}  [device: ${WHISPER_DEVICE:-unknown}]"
echo "[infra] TTS : ${TTS_VOICE:-unknown}"
echo

GPU_WARN=0
if [[ "${WHISPER_DEVICE:-cuda}" == "cuda" ]]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        echo "[infra] GPU detected: ${GPU_NAME} ✅"
    else
        echo "┌─────────────────────────────────────────────────────────────────┐"
        echo "│  WARNING: Whisper is configured for CUDA but no NVIDIA GPU was  │"
        echo "│  detected!                                                       │"
        echo "│                                                                  │"
        echo "│  → Transcription will fail with HTTP 500 on first request.      │"
        echo "│  → Switch to CPU in docker-compose.yml:                         │"
        echo "│      image:                    ...:latest-cpu                   │"
        echo "│      WHISPER__INFERENCE_DEVICE=cpu                              │"
        echo "│      WHISPER__COMPUTE_TYPE    =int8                             │"
        echo "└─────────────────────────────────────────────────────────────────┘"
        echo
        GPU_WARN=1
    fi
fi

# ---------------------------------------------------------------------------
# Start containers
# ---------------------------------------------------------------------------
echo "[infra] Starting Whisper + TTS (first run builds/pulls images)…"
$COMPOSE up -d

wait_health() {
    local name="$1" url="$2" tries=60
    printf "[infra] Waiting for %s " "$name"
    for ((i = 0; i < tries; i++)); do
        if curl -fsS "$url" >/dev/null 2>&1; then echo " OK"; return 0; fi
        printf "."; sleep 2
    done
    echo " TIMEOUT"
    echo "[infra] $name not healthy — check logs: $COMPOSE logs $3" >&2
    return 1
}

WHISPER_OK=0; TTS_OK=0
wait_health "Whisper" "http://localhost:10300/health" faster-whisper && WHISPER_OK=1 || true
wait_health "TTS"     "http://localhost:10400/health" tts             && TTS_OK=1 || true

# Whisper functional test — /health passes before the model is loaded
WHISPER_FUNCTIONAL=0
if [[ "$WHISPER_OK" -eq 1 ]]; then
    printf "[infra] Testing Whisper transcription (may take 30-60s on first run) …"
    TMPWAV=$(mktemp /tmp/whisper-test-XXXXXX.wav)
    python3 - "$TMPWAV" <<'PYEOF'
import wave, struct, sys
with wave.open(sys.argv[1], 'w') as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
    wf.writeframes(struct.pack('<8000h', *([0] * 8000)))
PYEOF
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 120 \
        -X POST "http://localhost:10300/v1/audio/transcriptions" \
        -F "file=@${TMPWAV};type=audio/wav" \
        -F "model=${WHISPER_MODEL}" \
        -F "language=de" \
        -F "response_format=json" 2>/dev/null || echo "000")
    rm -f "$TMPWAV"
    if [[ "$HTTP_CODE" == "200" ]]; then
        echo " OK ✅"; WHISPER_FUNCTIONAL=1
    else
        echo " FAILED (HTTP ${HTTP_CODE}) ❌"
        $COMPOSE logs --tail=30 faster-whisper 2>/dev/null \
            | grep -E "RuntimeError|ERROR|Error" | tail -5 || true
        [[ "$GPU_WARN" -eq 1 ]] && echo "[infra] → Most likely cause: no CUDA GPU available"
    fi
fi

# ---------------------------------------------------------------------------
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; HOST_IP="${HOST_IP:-localhost}"
echo
echo "============================================================"
echo " Paste these into the add-on configuration (Step 4):"
echo "------------------------------------------------------------"
echo "   whisper.url   :  http://${HOST_IP}:10300/v1/audio/transcriptions"
echo "   tts.url       :  http://${HOST_IP}:10400/tts"
echo "   whisper model :  ${WHISPER_MODEL:-–}"
echo "   tts voice     :  ${TTS_VOICE:-–}"
echo "============================================================"

if [[ "$WHISPER_OK" -eq 1 && "$TTS_OK" -eq 1 && "$WHISPER_FUNCTIONAL" -eq 1 ]]; then
    echo "[infra] Both services are up and functional. ✅"
elif [[ "$WHISPER_OK" -eq 1 && "$TTS_OK" -eq 1 ]]; then
    echo "[infra] ⚠️  Services are up but Whisper transcription is not working!"
    echo "[infra]    Check logs: $COMPOSE logs faster-whisper"
    exit 1
else
    echo "[infra] One or more services not healthy yet — check: $COMPOSE logs -f"
    exit 1
fi

echo
echo "  Stop: bash ${INSTALL_DIR}/stop.sh"
echo
