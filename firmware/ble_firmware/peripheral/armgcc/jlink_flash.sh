#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND_FILE="$SCRIPT_DIR/jlink_flash.jlink"

if [[ ! -f "$COMMAND_FILE" ]]; then
  echo "Error: missing $COMMAND_FILE" >&2
  exit 1
fi

if command -v JLinkExe >/dev/null 2>&1; then
  exec JLinkExe -CommanderScript "$COMMAND_FILE"
fi

if command -v JLink.exe >/dev/null 2>&1; then
  exec JLink.exe -CommandFile "$COMMAND_FILE"
fi

for exe in \
  "/c/Program Files/SEGGER/JLink/JLink.exe" \
  "/c/Program Files (x86)/SEGGER/JLink/JLink.exe"
do
  if [[ -x "$exe" ]]; then
    exec "$exe" -CommandFile "$COMMAND_FILE"
  fi
done

for base in \
  "/c/Program Files/SEGGER" \
  "/c/Program Files (x86)/SEGGER"
do
  if [[ -d "$base" ]]; then
    for exe in "$base"/JLink_V*/JLink.exe; do
      if [[ -x "$exe" ]]; then
        exec "$exe" -CommandFile "$COMMAND_FILE"
      fi
    done
  fi
done

echo "Error: J-Link Commander not found." >&2
echo "Install SEGGER J-Link or add it to PATH." >&2
exit 1
