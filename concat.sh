#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <folder_path> [output_file_or_path]"
  exit 1
fi

SOURCE_FOLDER="$1"

if [[ ! -d "$SOURCE_FOLDER" ]]; then
  echo "Error: '$SOURCE_FOLDER' is not a valid folder."
  exit 1
fi

SOURCE_FOLDER="$(cd "$SOURCE_FOLDER" && pwd)"

if [[ $# -eq 2 ]]; then
  ARG_OUTPUT="$2"
  if [[ "$ARG_OUTPUT" == */* ]]; then
    OUTPUT="$ARG_OUTPUT"
  else
    OUTPUT="$SOURCE_FOLDER/$ARG_OUTPUT"
  fi
else
  OUTPUT="$SOURCE_FOLDER/output.mp4"
fi

mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(python3 - <<'PY' "$OUTPUT"
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"

TMPFILE="$(mktemp)"
cleanup() {
  rm -f "$TMPFILE"
}
trap cleanup EXIT

found=0

while IFS= read -r -d '' file; do
  # Exclude the output file if it lives in the source folder and matches the extension.
  if [[ "$(python3 - <<'PY' "$file"
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)" == "$OUTPUT" ]]; then
    continue
  fi

  found=1
  escaped=${file//\'/\'\\\'\'}
  printf "file '%s'\n" "$escaped" >> "$TMPFILE"
done < <(find "$SOURCE_FOLDER" -maxdepth 1 -type f \( -iname "*.mp4" \) -print0 | sort -z)

if [[ $found -eq 0 ]]; then
  echo "Error: no .mp4 files found in '$SOURCE_FOLDER' (excluding output file if applicable)."
  exit 1
fi

echo "Concatenating files from '$SOURCE_FOLDER' into '$OUTPUT'..."
ffmpeg -f concat -safe 0 -i "$TMPFILE" -c copy "$OUTPUT"
echo "Done: $OUTPUT"
