#!/usr/bin/env bash
# level.sh - show the live microphone level (view only) until Ctrl-C.
#
# Thin wrapper around start.sh so the service stop/restart and device handling
# live in one place. Run it from the client directory:
#   bash level.sh
#
# It stops the voice-client service (which holds the mic), shows the live RMS
# level with a marker at the configured VAD_SILENCE_THRESHOLD, and restarts the
# service when you press Ctrl-C.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$DIR/start.sh" level
