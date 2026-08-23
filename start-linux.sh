#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
printf '%s\n' 'Starting Blender Render Queue...'
exec python3 app.py
