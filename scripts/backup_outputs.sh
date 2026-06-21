#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/azureuser/Blindspot"
DEST="$ROOT/backups"
mkdir -p "$DEST"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUT="$DEST/blindspot-records-$STAMP.tar.gz"
cd "$ROOT"
tar --exclude='./.venv' --exclude='./novaproject-blindspot-design/node_modules' --exclude='./backups' -czf "$OUT" output_protected
printf '%s\n' "$OUT"
