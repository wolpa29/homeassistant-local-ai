#!/usr/bin/env bash
# Stage core/ + services/ sources into _build/ and build the Docker image locally.
#
# Usage:
#   ./build-local.sh           # builds amd64
#   ./build-local.sh aarch64
#   ./build-local.sh armv7
set -euo pipefail

ARCH="${1:-amd64}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/_build"

case "$ARCH" in
    amd64|aarch64|armv7) ;;
    *) echo "[build-local] unknown arch: $ARCH (amd64|aarch64|armv7)" >&2; exit 1 ;;
esac

echo "[build-local] staging core/ + services/ -> $BUILD_DIR"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/services"
cp -r "$REPO_ROOT/core"                    "$BUILD_DIR/core"
cp -r "$REPO_ROOT/services/voice_gateway"  "$BUILD_DIR/services/voice_gateway"
cp -r "$REPO_ROOT/services/notify_gateway" "$BUILD_DIR/services/notify_gateway"
cp -r "$REPO_ROOT/services/telegram_bot"   "$BUILD_DIR/services/telegram_bot"

# Strip pycache + per-service venvs that may exist from the systemd-style install.
find "$BUILD_DIR" -type d \( -name __pycache__ -o -name '*_env' \) -prune -exec rm -rf {} + 2>/dev/null || true
# Strip any stray .env (secrets must come from HA options.json, not baked into the image).
find "$BUILD_DIR" -type f -name '.env*' ! -name '*.example' -delete

BASE="ghcr.io/home-assistant/${ARCH}-base-python:3.12-alpine3.19"
IMG="local/homeassistant-ai-gateway-${ARCH}:dev"

echo "[build-local] docker build  arch=${ARCH}  base=${BASE}  tag=${IMG}"
docker build \
    --build-arg "BUILD_FROM=${BASE}" \
    -t "${IMG}" \
    "${SCRIPT_DIR}"

echo
echo "[build-local] done: ${IMG}"
echo
echo "Run with a stub options.json:"
echo "  mkdir -p ${SCRIPT_DIR}/_data"
echo "  # write ${SCRIPT_DIR}/_data/options.json (see DOCS.md for an example)"
echo "  docker run --rm -p 8765:8765 -p 8766:8766 \\"
echo "      -v ${SCRIPT_DIR}/_data:/data \\"
echo "      ${IMG}"
