#!/usr/bin/env bash
# Desinstala localapps: quita el enlace de ~/.local/bin (o LOCALAPPS_BIN_DIR) y el workflow de Warp, solo si son los que instaló este repo.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${LOCALAPPS_BIN_DIR:-$HOME/.local/bin}"
TARGET="$REPO_DIR/localapps.py"
LINK="$BIN_DIR/localapps"
WARP_WORKFLOW="$HOME/.warp/workflows/localapps.yaml"

# Solo se borra lo que instaló este repo: el enlace a su script y su workflow idéntico.
if [[ -L "$LINK" && "$(readlink "$LINK")" == "$TARGET" ]]; then
  rm "$LINK"
  echo "✓ Eliminado $LINK"
elif [[ -e "$LINK" || -L "$LINK" ]]; then
  echo "⚠ $LINK no apunta a este repo; no se toca."
else
  echo "No hay enlace en $BIN_DIR. Si instalaste con LOCALAPPS_BIN_DIR, desinstala con la misma variable."
fi

if [[ -L "$WARP_WORKFLOW" ]]; then
  echo "⚠ $WARP_WORKFLOW es un enlace simbólico; no se toca."
elif [[ -f "$WARP_WORKFLOW" ]]; then
  if cmp -s "$REPO_DIR/warp/localapps.yaml" "$WARP_WORKFLOW"; then
    rm "$WARP_WORKFLOW"
    echo "✓ Eliminado $WARP_WORKFLOW"
  else
    echo "⚠ $WARP_WORKFLOW difiere del de este repo; se conserva."
  fi
fi

echo "localapps desinstalado. Los logs de ~/Library/Logs/localapps no se han tocado."
