#!/usr/bin/env bash
# run_pipeline_venv.sh — Run the Slice 1.5 backend pipeline from the bs3d-venv
# which has torch-cu121 + SAM-2 installed. Real GPU segmentation + real
# reconstruction. No SAM2_MOCK.
#
# Usage: ./run_pipeline_venv.sh <scan_id>
set -u
SCAN_ID="${1:-}"
if [ -z "$SCAN_ID" ]; then
    echo "Usage: $0 <scan_id>"
    exit 1
fi

# Detect Windows host IP from WSL default route
WINDOWS_HOST=$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')
if [ -z "$WINDOWS_HOST" ]; then
    WINDOWS_HOST="172.20.224.1"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UPLOADS_DIR="$(dirname "$SCRIPT_DIR")/uploads"

export BS3D_API_BASE="http://${WINDOWS_HOST}:5000"
export BS3D_UPLOADS_DIR="$UPLOADS_DIR"
export SAM2_CHECKPOINT="/home/christos/sam2_checkpoints/sam2_hiera_small.pt"
export SAM2_CONFIG="configs/sam2/sam2_hiera_s.yaml"
# SAM2_MOCK left unset -> real SAM 2 runs

# Activate venv with torch-cu121 + sam2
source /home/christos/bs3d-venv/bin/activate

echo "[run] Scan $SCAN_ID | API=$BS3D_API_BASE | SAM2_CHECKPOINT=$SAM2_CHECKPOINT"
python3 "$SCRIPT_DIR/pipeline.py" "$SCAN_ID"
