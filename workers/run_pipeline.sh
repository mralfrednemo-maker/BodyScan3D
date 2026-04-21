#!/usr/bin/env bash
# run_pipeline.sh — Run a BodyScan3D pipeline worker from WSL
# Usage: ./run_pipeline.sh <scan_id> [--mock]
#
# Auto-detects the Windows host IP for WSL→Windows connectivity.

set -e

SCAN_ID="$1"
MOCK="${2:-}"

if [ -z "$SCAN_ID" ]; then
    echo "Usage: $0 <scan_id> [--mock]"
    exit 1
fi

# Detect Windows host IP from WSL default route
WINDOWS_HOST=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
if [ -z "$WINDOWS_HOST" ]; then
    WINDOWS_HOST="172.20.224.1"  # fallback
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPLOADS_DIR="$(dirname "$SCRIPT_DIR")/uploads"

export BS3D_API_BASE="http://${WINDOWS_HOST}:5000"
export BS3D_UPLOADS_DIR="$UPLOADS_DIR"

if [ "$MOCK" = "--mock" ]; then
    export SAM2_MOCK=1
    echo "[run_pipeline] SAM2_MOCK=1 (no GPU)"
fi

echo "[run_pipeline] Scan $SCAN_ID | API=$BS3D_API_BASE | Uploads=$BS3D_UPLOADS_DIR"

python3 "$SCRIPT_DIR/pipeline.py" "$SCAN_ID"
