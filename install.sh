#!/usr/bin/env bash
# Instala localapps: enlaza el script en ~/.local/bin (o LOCALAPPS_BIN_DIR) y añade el workflow de Warp.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${LOCALAPPS_BIN_DIR:-$HOME/.local/bin}"
WARP_WORKFLOWS_DIR="$HOME/.warp/workflows"
TARGET="$REPO_DIR/localapps.py"
LINK="$BIN_DIR/localapps"
WORKFLOW="$WARP_WORKFLOWS_DIR/localapps.yaml"

fail() {
  echo "Error: $1" >&2
  exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "localapps solo está soportado en macOS."
command -v python3 >/dev/null || fail "hace falta python3 (3.7 o superior)."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 7))' || fail "hace falta Python 3.7 o superior."

# No se pisa nada ajeno: solo se continúa si no existe o ya es el enlace a este repo.
if [[ -e "$LINK" || -L "$LINK" ]]; then
  if [[ ! -L "$LINK" || "$(readlink "$LINK")" != "$TARGET" ]]; then
    fail "ya existe $LINK y no es un enlace a este repo; bórralo tú o instala en otra carpeta con LOCALAPPS_BIN_DIR."
  fi
fi

chmod +x "$TARGET"
mkdir -p "$BIN_DIR"
# Enlace simbólico: un `git pull` en el repo actualiza la versión instalada.
ln -sf "$TARGET" "$LINK"
echo "✓ Comando instalado: $LINK -> $TARGET"

mkdir -p "$WARP_WORKFLOWS_DIR"
if [[ -L "$WORKFLOW" ]]; then
  echo "⚠ $WORKFLOW es un enlace simbólico; no se toca."
elif [[ -e "$WORKFLOW" ]] && ! cmp -s "$REPO_DIR/warp/localapps.yaml" "$WORKFLOW"; then
  echo "⚠ $WORKFLOW ya existe y es distinto; se conserva tu versión. Para actualizarla, bórrala y vuelve a ejecutar install.sh."
else
  cp "$REPO_DIR/warp/localapps.yaml" "$WORKFLOW"
  echo "✓ Workflow de Warp instalado: $WORKFLOW"
fi

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
