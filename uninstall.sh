#!/usr/bin/env bash
# Desinstala localapps: quita el enlace de ~/.local/bin y el workflow de Warp.
set -euo pipefail

BIN_DIR="${LOCALAPPS_BIN_DIR:-$HOME/.local/bin}"
WARP_WORKFLOW="$HOME/.warp/workflows/localapps.yaml"

# Solo se borra si es un enlace, para no tocar un fichero que no instaló este script.
if [[ -L "$BIN_DIR/localapps" ]]; then
  rm "$BIN_DIR/localapps"
  echo "✓ Eliminado $BIN_DIR/localapps"
fi

if [[ -f "$WARP_WORKFLOW" ]]; then
  rm "$WARP_WORKFLOW"
  echo "✓ Eliminado $WARP_WORKFLOW"
fi

echo "localapps desinstalado. Los logs de ~/Library/Logs/localapps no se han tocado."
