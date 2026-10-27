#!/usr/bin/env bash
# Instala localapps: enlaza el script en ~/.local/bin y añade el workflow de Warp.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${LOCALAPPS_BIN_DIR:-$HOME/.local/bin}"
WARP_WORKFLOWS_DIR="$HOME/.warp/workflows"

fail() {
  echo "Error: $1" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "localapps solo está soportado en macOS."
command -v python3 >/dev/null || fail "hace falta python3 (3.7 o superior)."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 7))' || fail "hace falta Python 3.7 o superior."

chmod +x "$REPO_DIR/localapps.py"
mkdir -p "$BIN_DIR"
# Enlace simbólico: un `git pull` en el repo actualiza la versión instalada.
ln -sf "$REPO_DIR/localapps.py" "$BIN_DIR/localapps"
echo "✓ Comando instalado: $BIN_DIR/localapps -> $REPO_DIR/localapps.py"

mkdir -p "$WARP_WORKFLOWS_DIR"
cp "$REPO_DIR/warp/localapps.yaml" "$WARP_WORKFLOWS_DIR/localapps.yaml"
echo "✓ Workflow de Warp instalado: $WARP_WORKFLOWS_DIR/localapps.yaml"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "⚠ $BIN_DIR no está en el PATH. Añádelo a tu ~/.zshrc:"
    echo "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
    ;;
esac

echo
echo "Listo. Ejecuta 'localapps' o, en Warp, Cmd+P → \"Apps corriendo en local\"."
