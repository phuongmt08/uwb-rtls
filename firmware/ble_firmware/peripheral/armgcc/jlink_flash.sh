#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND_FILE="$SCRIPT_DIR/jlink_flash.jlink"

if [[ ! -f "$COMMAND_FILE" ]]; then
  echo "Error: missing $COMMAND_FILE" >&2
  exit 1
fi

JLink.exe -CommandFile "$COMMAND_FILE"
