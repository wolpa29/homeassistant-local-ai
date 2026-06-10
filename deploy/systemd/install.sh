#!/usr/bin/env bash
# Install script for the homeassistant-ai-gateway systemd services.
#
# For each service listed in SERVICES below it will:
#   1. create a venv inside the service directory: <service>_env/
#   2. install requirements.txt into that venv
#   3. copy the matching .service file into /etc/systemd/system/
#
# Then it runs `systemctl daemon-reload` and (optionally) enables + starts
# every service. Re-running the script is safe (idempotent).
#
# Usage:
#   sudo ./install.sh              # install + enable + start
#   sudo ./install.sh --no-start   # install only, don't enable/start
#   sudo ./install.sh voice_gateway notify_gateway   # subset
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICES_DIR="$REPO_ROOT/services"
SYSTEMD_DEST="/etc/systemd/system"

# service_dir_name : systemd_unit_filename
declare -A SERVICES=(
  [telegram_bot]="telegram-bot.service"
  [voice_gateway]="voice-gateway.service"
  [notify_gateway]="notify-gateway.service"
)

START_SERVICES=1
SELECTED=()

for arg in "$@"; do
  case "$arg" in
    --no-start) START_SERVICES=0 ;;
    -h|--help)
      sed -n '2,15p' "$0"; exit 0 ;;
    *) SELECTED+=("$arg") ;;
  esac
done

if [[ "$EUID" -ne 0 ]]; then
  echo "[install] WARNING: not running as root — copying unit files will fail." >&2
fi

# Determine which services to process
if [[ ${#SELECTED[@]} -eq 0 ]]; then
  TARGETS=("${!SERVICES[@]}")
else
  TARGETS=("${SELECTED[@]}")
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

for svc in "${TARGETS[@]}"; do
  unit="${SERVICES[$svc]:-}"
  if [[ -z "$unit" ]]; then
    echo "[install] unknown service: $svc (known: ${!SERVICES[*]})" >&2
    exit 1
  fi

  svc_dir="$SERVICES_DIR/$svc"
  venv_dir="$svc_dir/${svc}_env"
  req_file="$svc_dir/requirements.txt"
  unit_src="$SCRIPT_DIR/$unit"

  echo
  echo "=== $svc ==="

  if [[ ! -d "$svc_dir" ]]; then
    echo "[install] missing service dir: $svc_dir" >&2; exit 1
  fi
  if [[ ! -f "$req_file" ]]; then
    echo "[install] missing requirements.txt: $req_file" >&2; exit 1
  fi
  if [[ ! -f "$unit_src" ]]; then
    echo "[install] missing unit file: $unit_src" >&2; exit 1
  fi

  if [[ ! -d "$venv_dir" ]]; then
    echo "[install] creating venv: $venv_dir"
    "$PYTHON_BIN" -m venv "$venv_dir"
  else
    echo "[install] venv exists: $venv_dir"
  fi

  echo "[install] upgrading pip"
  "$venv_dir/bin/pip" install --upgrade pip wheel >/dev/null

  echo "[install] installing requirements"
  "$venv_dir/bin/pip" install -r "$req_file"

  echo "[install] copying $unit -> $SYSTEMD_DEST/"
  install -m 0644 "$unit_src" "$SYSTEMD_DEST/$unit"
done

echo
echo "[install] systemctl daemon-reload"
systemctl daemon-reload

if [[ "$START_SERVICES" -eq 1 ]]; then
  for svc in "${TARGETS[@]}"; do
    unit="${SERVICES[$svc]}"
    echo "[install] enabling + (re)starting $unit"
    systemctl enable "$unit"
    systemctl restart "$unit"
  done
  echo
  echo "[install] done. Status:"
  for svc in "${TARGETS[@]}"; do
    systemctl --no-pager --lines=0 status "${SERVICES[$svc]}" || true
  done
else
  echo "[install] --no-start given; not enabling services."
  echo "Enable manually with: systemctl enable --now <unit>"
fi
