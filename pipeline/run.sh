#!/usr/bin/env bash
set -euo pipefail

# Usage: ./pipeline/run.sh <clips_folder> [store_id] [camera_id]
#
# Processes every video clip in the folder and writes events.jsonl in that folder.

CLIPS_FOLDER="${1:?Usage: run.sh <clips_folder> [store_id] [camera_id]}"
STORE_ID="${2:-STORE_BLR_002}"
CAMERA_ID="${3:-CAM_ENTRY_01}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_FILE="$CLIPS_FOLDER/events.jsonl"

: > "$OUTPUT_FILE"

shopt -s nullglob
found=0
for clip in "$CLIPS_FOLDER"/*.{mp4,avi,mov,mkv,MP4,AVI,MOV,MKV}; do
  [ -f "$clip" ] || continue
  found=1
  tmp_file="$(mktemp)"
  echo "Processing: $clip"
  python "$PROJECT_ROOT/pipeline/detect.py" \
    --video "$clip" \
    --store-id "$STORE_ID" \
    --camera-id "$CAMERA_ID" \
    --output "$tmp_file"
  cat "$tmp_file" >> "$OUTPUT_FILE"
  rm -f "$tmp_file"
done

if [ "$found" -eq 0 ]; then
  echo "No video clips found in $CLIPS_FOLDER"
  exit 1
fi

echo "Wrote combined events to $OUTPUT_FILE"
