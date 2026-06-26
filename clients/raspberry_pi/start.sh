#!/usr/bin/env bash
# start.sh - Raspberry Pi Voice Client
#
# First run:  downloads everything, walks through configuration, installs
#             the systemd service, and starts it.
# Later runs: skips setup if already done - just starts / restarts the service.
#
# Usage (first time):
#   mkdir voice-client && cd voice-client
#   bash <(curl -sSL https://raw.githubusercontent.com/wolpa29/homeassistant-local-ai/main/clients/raspberry_pi/start.sh)
#
# NOTE: use  bash <(curl ...)  - NOT  curl ... | bash.  With a pipe, the script's
# stdin is the download stream, so the interactive prompts read garbage instead
# of your answers. Process substitution keeps stdin attached to your terminal.
#
# Usage (after that):
#   bash start.sh

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; DIM='\033[2m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${CYAN}=== $* ===${NC}"; }
# Dimmed one-line explanation printed above a prompt so the user knows what it does.
hint()    { echo -e "    ${DIM}$*${NC}"; }

# Live mic-level meter - helps pick VAD_SILENCE_THRESHOLD without guessing.
# Shows the current mic level (same int16 RMS unit the client uses) continuously
# while you talk/move around the room. Type your chosen threshold WHILE watching,
# press Enter to confirm. One Python process owns the terminal so the live bar
# and your typing don't clobber each other: audio comes from the arecord pipe on
# stdin, keystrokes are read straight from /dev/tty. Sets the global VAD_MEASURED
# to the number you typed, or leaves it empty if you entered nothing / it failed.
measure_mic_level() {
    VAD_MEASURED=""
    local py="$INSTALL_DIR/venv/bin/python3"
    if [[ ! -x "$py" ]]; then
        warn "venv Python not found - skipping measurement."
        return 0
    fi
    local recfile; recfile="$(mktemp)"
    echo "  Talk / move around the room and watch the level."
    echo "  Type the threshold you want while watching, then press Enter."
    echo "  (Rule of thumb: clearly above the quiet level, clearly below speech.)"
    # arecord dies with SIGPIPE when the meter exits; '|| true' keeps pipefail happy.
    arecord -D "$ALSA_INPUT_DEVICE" -f S16_LE -c 1 -r 16000 -t raw 2>/dev/null \
      | RECFILE="$recfile" "$py" - <<'PY' || true
import os, sys, select, termios, tty
import numpy as np

CHUNK = 1280  # 80 ms @ 16 kHz - same chunk size the client's VAD uses
recfile = os.environ["RECFILE"]

tty_fd = os.open("/dev/tty", os.O_RDWR)
old = termios.tcgetattr(tty_fd)
tty.setcbreak(tty_fd)            # read keys one at a time, no line buffering


def w(s: str) -> None:
    os.write(tty_fd, s.encode())


typed = ""
peak = 0
try:
    while True:
        raw = sys.stdin.buffer.read(CHUNK * 2)   # ~80 ms of audio, blocks
        if len(raw) < 2:
            break
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        rms = int((x ** 2).mean() ** 0.5) if x.size else 0
        peak = max(peak, rms)
        bar = "#" * min(30, rms // 50)
        # \r + clear-line, then redraw the whole line incl. what was typed so far
        w(f"\r\033[2K  RMS {rms:5d}  peak {peak:5d} |{bar:<30}|  threshold: {typed}_")
        # drain any keystrokes that arrived during this chunk (non-blocking)
        while select.select([tty_fd], [], [], 0)[0]:
            ch = os.read(tty_fd, 1)
            if ch in (b"\r", b"\n"):
                if typed:
                    with open(recfile, "w") as f:
                        f.write(typed)
                raise SystemExit
            if ch in (b"\x7f", b"\x08"):          # backspace
                typed = typed[:-1]
            elif ch.isdigit():
                typed += ch.decode()
finally:
    termios.tcsetattr(tty_fd, termios.TCSADRAIN, old)
    w("\n")
    os.close(tty_fd)
PY
    [[ -s "$recfile" ]] && VAD_MEASURED="$(cat "$recfile")"
    rm -f "$recfile"
}

ask() {
    local var=$1 prompt=$2 default=${3:-}
    local display_default=""
    [[ -n $default ]] && display_default=" [${default}]"
    read -rp "  ${prompt}${display_default}: " value </dev/tty
    [[ -z $value ]] && value="$default"
    printf -v "$var" '%s' "$value"
}

ask_secret() {
    local var=$1 prompt=$2
    read -rsp "  ${prompt}: " value </dev/tty
    echo
    printf -v "$var" '%s' "$value"
}

confirm() {
    local answer
    read -rp "  $* [y/N]: " answer </dev/tty
    [[ ${answer,,} == "y" || ${answer,,} == "yes" ]]
}

INSTALL_DIR="$(pwd)"
SERVICE_NAME="voice-client"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER="$(whoami)"
GITHUB_RAW="https://raw.githubusercontent.com/wolpa29/homeassistant-local-ai/main/clients/raspberry_pi"

# Subcommand:  bash start.sh measure  -> only run the live mic-level meter, using
# the microphone from an existing .env. Lets you re-tune VAD_SILENCE_THRESHOLD
# anytime without redoing the whole setup.
if [[ "${1:-}" == "measure" ]]; then
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        ALSA_INPUT_DEVICE="$(grep -E '^ALSA_INPUT_DEVICE=' "$INSTALL_DIR/.env" | cut -d= -f2-)"
    fi
    ALSA_INPUT_DEVICE="${ALSA_INPUT_DEVICE:-plughw:1,0}"
    echo -e "${CYAN}Microphone level meter${NC} (device: ${ALSA_INPUT_DEVICE})"
    measure_mic_level
    echo
    if [[ -n "${VAD_MEASURED:-}" && -f "$INSTALL_DIR/.env" ]]; then
        if grep -q '^VAD_SILENCE_THRESHOLD=' "$INSTALL_DIR/.env"; then
            sed -i "s/^VAD_SILENCE_THRESHOLD=.*/VAD_SILENCE_THRESHOLD=${VAD_MEASURED}/" "$INSTALL_DIR/.env"
        else
            echo "VAD_SILENCE_THRESHOLD=${VAD_MEASURED}" >> "$INSTALL_DIR/.env"
        fi
        info "Saved VAD_SILENCE_THRESHOLD=${VAD_MEASURED} to .env."
        if confirm "Restart the service now?"; then
            sudo systemctl restart "$SERVICE_NAME" && info "Restarted."
        fi
    else
        info "No value entered - .env unchanged."
    fi
    exit 0
fi

echo
echo -e "${CYAN}Raspberry Pi Voice Client${NC}"
echo "  Directory : $INSTALL_DIR"
echo "  User      : $CURRENT_USER"
echo

if [[ $CURRENT_USER == "root" ]]; then
    warn "Running as root - the service will run as root too."
    warn "Consider running as your normal user instead."
fi

# ---------------------------------------------------------------------------
# 1 - System packages (only on first run)
# ---------------------------------------------------------------------------
if [[ ! -f "$INSTALL_DIR/voice_client.py" ]]; then
    section "1 - System packages"
    info "Updating package lists..."
    sudo apt-get update -qq
    info "Installing dependencies..."
    sudo apt-get install -y -qq portaudio19-dev python3-venv python3-pip python3-spidev wget
fi

# ---------------------------------------------------------------------------
# 2 - SPI interface (only on first run)
# ---------------------------------------------------------------------------
if [[ ! -f "$INSTALL_DIR/.configured" ]]; then
    section "2 - SPI interface (required for ReSpeaker LED HAT)"
    CONFIG_TXT="/boot/firmware/config.txt"
    [[ ! -f "$CONFIG_TXT" ]] && CONFIG_TXT="/boot/config.txt"

    if grep -q "^dtparam=spi=on" "$CONFIG_TXT" 2>/dev/null; then
        info "SPI already enabled."
    elif grep -q "^#dtparam=spi=on" "$CONFIG_TXT" 2>/dev/null; then
        sudo sed -i 's/^#dtparam=spi=on/dtparam=spi=on/' "$CONFIG_TXT"
        warn "SPI enabled - reboot after setup for LEDs to work."
    else
        echo "dtparam=spi=on" | sudo tee -a "$CONFIG_TXT" > /dev/null
        warn "SPI enabled - reboot after setup for LEDs to work."
    fi

    if ls /dev/spidev* &>/dev/null; then
        info "SPI device found: $(ls /dev/spidev*)"
    else
        warn "No /dev/spidev* found yet - reboot to activate LEDs."
    fi
fi

# ---------------------------------------------------------------------------
# 3 - Download client files (only on first run)
# ---------------------------------------------------------------------------
if [[ ! -f "$INSTALL_DIR/voice_client.py" ]]; then
    section "3 - Download client files"
    mkdir -p "$INSTALL_DIR/models"

    download_file() {
        info "Downloading ${1}..."
        wget -q -O "$INSTALL_DIR/${1}" "${GITHUB_RAW}/${1}"
    }

    download_file "voice_client.py"
    download_file "requirements.txt"
    download_file "start.sh"
    download_file "stop.sh"
    chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh"
fi

# ---------------------------------------------------------------------------
# 4 - Python venv + dependencies (only on first run)
# ---------------------------------------------------------------------------
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    section "4 - Python virtual environment"
    python3 -m venv "$INSTALL_DIR/venv"
    info "Installing openwakeword..."
    "$INSTALL_DIR/venv/bin/pip" install --quiet openwakeword --no-deps
    info "Installing remaining requirements..."
    "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

    section "5 - Download openwakeword models"
    "$INSTALL_DIR/venv/bin/python3" -c "
from openwakeword.utils import download_models
download_models()
print('openwakeword models downloaded.')
"
fi

# ---------------------------------------------------------------------------
# 5 - Configuration (only if .env doesn't exist yet)
# ---------------------------------------------------------------------------
ENV_FILE="$INSTALL_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    section "6 - Configuration"
    echo "  Answer each question. Press Enter to accept the default."
    echo

    hint "Address of the Home Assistant add-on (Voice Gateway, port 8765)."
    hint "Format: http://<home-assistant-ip>:8765"
    ask GATEWAY_URL    "Voice Gateway URL"              "http://10.1.10.78:8765"
    echo
    hint "Only needed if you set an API key in the add-on. Otherwise leave empty."
    ask_secret GATEWAY_API_KEY "Gateway API key (leave empty if none)"
    echo
    hint "Name of this device in the logs and at the gateway, e.g. rpi-kitchen."
    ask DEVICE_ID      "Device ID (e.g. rpi-wohnzimmer)" "rpi-wohnzimmer"

    echo
    hint "The wake word the Pi listens for before it starts recording."
    echo "  Wake word options:"
    echo "    [1] hey_jarvis (default)"
    echo "    [2] alexa"
    echo "    [3] hey_mycroft"
    echo "    [4] hey_rhasspy"
    echo "    [5] custom  (.onnx model file)"
    echo
    read -rp "  Choose [1-5]: " ww_choice </dev/tty
    case "${ww_choice:-1}" in
        2) WAKE_WORD="alexa" ;;
        3) WAKE_WORD="hey_mycroft" ;;
        4) WAKE_WORD="hey_rhasspy" ;;
        5)
            echo
            echo -e "  ${CYAN}Place your .onnx model file in:${NC}"
            echo "    ${INSTALL_DIR}/models/"
            echo
            read -rp "  Press Enter when the file is there..." </dev/tty
            echo
            echo "  Files found in ${INSTALL_DIR}/models/:"
            ls "$INSTALL_DIR/models/"*.onnx 2>/dev/null | xargs -I{} basename {} || echo "    (none)"
            echo
            ask WAKE_WORD_FILE "Filename of your .onnx model" ""
            WAKE_WORD="${INSTALL_DIR}/models/${WAKE_WORD_FILE}"
            if [[ ! -f "$WAKE_WORD" ]]; then
                warn "File not found: $WAKE_WORD - update WAKE_WORD in .env if needed."
            fi
            ;;
        *) WAKE_WORD="hey_jarvis" ;;
    esac
    info "Wake word set to: ${WAKE_WORD}"

    echo
    hint "Wake word sensitivity (0 = everything, 1 = strict)."
    hint "Higher = fewer false triggers, but the wake word must be spoken clearly."
    ask WAKE_THRESHOLD "Wake threshold (0.0-1.0)" "0.5"

    echo
    info "Finding ALSA audio devices..."
    echo "  --- arecord -l ---"
    arecord -l 2>/dev/null | grep "^card" || echo "  (none found)"
    echo "  --- aplay -l ---"
    aplay -l 2>/dev/null | grep "^card" || echo "  (none found)"
    echo

    hint "Microphone. Read from 'arecord -l' above: card 1 -> plughw:1,0."
    ask ALSA_INPUT_DEVICE  "ALSA input device (mic)"      "plughw:1,0"
    echo
    hint "Speaker. ReSpeaker HAT = plughw:1,0, Pi headphone jack = plughw:0,0."
    ask ALSA_OUTPUT_DEVICE "ALSA output device (speaker)" "plughw:1,0"
    echo
    hint "Playback volume, e.g. 90% or 100%."
    ask SPEAKER_VOLUME     "Speaker volume"                "100%"

    echo
    info "VAD = voice activity detection: decides when you have finished speaking."
    echo
    hint "Sound level (RMS) above which audio counts as speech. Raise in noisy"
    hint "rooms so background noise does not keep the recording open forever."
    VAD_SILENCE_THRESHOLD=""
    if confirm "Show the live mic level and type the value while watching?"; then
        measure_mic_level
        VAD_SILENCE_THRESHOLD="${VAD_MEASURED:-}"
    fi
    if [[ -n "$VAD_SILENCE_THRESHOLD" ]]; then
        info "VAD silence threshold: ${VAD_SILENCE_THRESHOLD}"
    else
        ask VAD_SILENCE_THRESHOLD "VAD silence threshold (int16 RMS, raise in noisy rooms)" "500"
    fi
    echo
    hint "How much silence (seconds) marks the end of your sentence."
    ask VAD_SILENCE_DURATION  "VAD silence duration (seconds)"                           "1.0"
    echo
    hint "Maximum recording length (seconds) - after that it is sent regardless."
    ask VAD_MAX_DURATION      "Max recording duration (seconds)"                         "10.0"
    echo
    hint "How long (seconds) to wait for speech to start after the wake word."
    ask VAD_INITIAL_TIMEOUT   "Seconds to wait for speech after wake word"               "5.0"

    echo
    info "Follow-up mode: after the reply the Pi listens again WITHOUT the wake"
    info "word, so you can ask a follow-up question right away."
    echo
    hint "Follow-up on/off. Set to false if you get echo problems (endless loop)."
    ask FOLLOWUP_ENABLED         "Enable follow-up mode (true/false)"               "true"
    echo
    hint "How long (seconds) to wait for a follow-up question after the reply."
    ask FOLLOWUP_INITIAL_TIMEOUT "Follow-up silence timeout (seconds)"              "1.5"
    echo
    hint "How many loud 80 ms blocks count as a real speech onset."
    hint "Higher = echo / TTS tail is filtered out better."
    ask FOLLOWUP_ONSET_CHUNKS    "Follow-up onset chunks (raise to filter TTS echo)" "3"
    echo
    hint "Seconds of mic audio discarded after playback (against your own echo)."
    ask FOLLOWUP_DRAIN_SECONDS   "Drain seconds after playback (absorbs TTS echo)"   "0.5"

    echo
    info "Piper TTS is used as a local fallback if the external TTS server is unavailable."
    TTS_MODEL_PATH=""
    if confirm "Download de_DE-thorsten-low Piper model (~16 MB)?"; then
        MODEL_DIR="$INSTALL_DIR/models"
        MODEL_BASE="de_DE-thorsten-low"
        MODEL_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/low"
        wget -q --show-progress -P "$MODEL_DIR" "${MODEL_URL}/${MODEL_BASE}.onnx"
        wget -q --show-progress -P "$MODEL_DIR" "${MODEL_URL}/${MODEL_BASE}.onnx.json"
        TTS_MODEL_PATH="$MODEL_DIR/${MODEL_BASE}.onnx"
        info "Piper model saved to $TTS_MODEL_PATH"
    fi

    cat > "$ENV_FILE" << EOF
GATEWAY_URL=${GATEWAY_URL}
GATEWAY_API_KEY=${GATEWAY_API_KEY}
DEVICE_ID=${DEVICE_ID}
WAKE_WORD=${WAKE_WORD}
WAKE_THRESHOLD=${WAKE_THRESHOLD}
ALSA_INPUT_DEVICE=${ALSA_INPUT_DEVICE}
ALSA_OUTPUT_DEVICE=${ALSA_OUTPUT_DEVICE}
SPEAKER_VOLUME=${SPEAKER_VOLUME}
TTS_MODEL=${TTS_MODEL_PATH}
VAD_SILENCE_THRESHOLD=${VAD_SILENCE_THRESHOLD}
VAD_SILENCE_DURATION=${VAD_SILENCE_DURATION}
VAD_MAX_DURATION=${VAD_MAX_DURATION}
VAD_INITIAL_TIMEOUT=${VAD_INITIAL_TIMEOUT}
FOLLOWUP_ENABLED=${FOLLOWUP_ENABLED}
FOLLOWUP_INITIAL_TIMEOUT=${FOLLOWUP_INITIAL_TIMEOUT}
FOLLOWUP_ONSET_CHUNKS=${FOLLOWUP_ONSET_CHUNKS}
FOLLOWUP_DRAIN_SECONDS=${FOLLOWUP_DRAIN_SECONDS}
EOF
    chmod 600 "$ENV_FILE"
    info ".env written."

    # ALSA mixer setup - ALSA_INPUT_DEVICE is already set from the prompts above,
    # so we do NOT source the .env (sourcing it would execute any odd value as a
    # shell command).
    section "7 - ALSA mixer setup"
    ALSA_CARD=$(echo "$ALSA_INPUT_DEVICE" | grep -oP '(?<=:)\d+(?=,)' || echo "1")
    info "Configuring ALSA mixer for card $ALSA_CARD..."
    run_amixer() { amixer -c "$ALSA_CARD" sset "$@" 2>/dev/null || true; }
    run_amixer 'Capture' '100%'
    run_amixer 'Left Boost Mixer LINPUT1' on
    run_amixer 'Right Boost Mixer RINPUT1' on
    run_amixer 'Left Input Mixer Boost' on
    run_amixer 'Right Input Mixer Boost' on
    run_amixer 'ADC PCM' '100%'
    run_amixer 'Playback' '100%'
    run_amixer 'Speaker' '100%'
    run_amixer 'Headphone' '100%'
    run_amixer 'Left Output Mixer PCM' on
    run_amixer 'Right Output Mixer PCM' on
    sudo alsactl store "$ALSA_CARD" 2>/dev/null || sudo alsactl store || warn "alsactl store failed"

    touch "$INSTALL_DIR/.configured"
fi

# ---------------------------------------------------------------------------
# 6 - systemd service (install if not present)
# ---------------------------------------------------------------------------
if [[ ! -f "$SERVICE_FILE" ]]; then
    section "8 - Install systemd service"
    sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Smart Home Voice Client
After=network.target sound.target
Wants=network.target

[Service]
User=${CURRENT_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/voice_client.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
fi

# ---------------------------------------------------------------------------
# 7 - Start / restart service
# ---------------------------------------------------------------------------
info "Enabling autostart on boot..."
sudo systemctl enable "$SERVICE_NAME"

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Restarting ${SERVICE_NAME}..."
    sudo systemctl restart "$SERVICE_NAME"
else
    info "Starting ${SERVICE_NAME}..."
    sudo systemctl start "$SERVICE_NAME"
fi

# ---------------------------------------------------------------------------
echo
STATUS=$(sudo systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo "unknown")
if [[ $STATUS == "active" ]]; then
    echo -e "  ${GREEN}Service is running.${NC}"
else
    echo -e "  ${YELLOW}Service status: ${STATUS}${NC}"
fi

echo
if ! ls /dev/spidev* &>/dev/null; then
    echo -e "  ${YELLOW}! SPI not yet active - reboot to enable LEDs:  sudo reboot${NC}"
    echo
fi

echo "  Live logs : journalctl -u ${SERVICE_NAME} -f"
echo "  Stop      : bash ${INSTALL_DIR}/stop.sh"
echo "  Config    : nano ${INSTALL_DIR}/.env  then: bash ${INSTALL_DIR}/start.sh"
echo
