#!/usr/bin/env bash
# NineDeo — installation du moteur (hôte natif) sous Linux et macOS.
# Usage : bash installer/unix/install.sh [--uninstall]
set -euo pipefail

HOST_NAME="com.ninedeo.host"
EXT_ID="${NINEDEO_EXTENSION_ID:-hcijkjkgdbihledmefkjiafcejmcgjom}"
FIREFOX_ID="ninedeo@ninedeo.app"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ "$(uname)" == "Darwin" ]]; then
  INSTALL_DIR="${NINEDEO_INSTALL_DIR:-$HOME/Library/Application Support/NineDeo}"
  BROWSER_DIRS=(
    "$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
    "$HOME/Library/Application Support/Chromium/NativeMessagingHosts"
    "$HOME/Library/Application Support/Microsoft Edge/NativeMessagingHosts"
    "$HOME/Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts"
    "$HOME/Library/Application Support/Vivaldi/NativeMessagingHosts"
  )
  FIREFOX_DIR="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts"
else
  INSTALL_DIR="${NINEDEO_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/ninedeo}"
  BROWSER_DIRS=(
    "$HOME/.config/google-chrome/NativeMessagingHosts"
    "$HOME/.config/chromium/NativeMessagingHosts"
    "$HOME/.config/microsoft-edge/NativeMessagingHosts"
    "$HOME/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts"
    "$HOME/.config/vivaldi/NativeMessagingHosts"
  )
  FIREFOX_DIR="$HOME/.mozilla/native-messaging-hosts"
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  for d in "${BROWSER_DIRS[@]}" "$FIREFOX_DIR"; do rm -f "$d/$HOST_NAME.json"; done
  rm -rf "$INSTALL_DIR"
  echo "NineDeo (moteur) désinstallé."
  exit 0
fi

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m[OK]\033[0m %s\n' "$1"; }
warn() { printf '    \033[33m[!]\033[0m  %s\n' "$1"; }

step "Python 3.9+"
PY="${PYTHON:-}"
for c in python3 python; do
  [[ -n "$PY" ]] && break
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then PY="$(command -v "$c")"; fi
done
[[ -z "$PY" ]] && { echo "Python 3.9+ introuvable."; exit 1; }
ok "$("$PY" --version) ($PY)"

step "Copie de l'hôte et environnement virtuel"
mkdir -p "$INSTALL_DIR/host"
rm -rf "$INSTALL_DIR/host/ninedeo"
cp -R "$ROOT/host/ninedeo" "$ROOT/host/ninedeo_host.py" "$ROOT/host/requirements.txt" "$INSTALL_DIR/host/"
find "$INSTALL_DIR/host" -name __pycache__ -prune -exec rm -rf {} +
[[ -x "$INSTALL_DIR/venv/bin/python" ]] || "$PY" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$INSTALL_DIR/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade -r "$INSTALL_DIR/host/requirements.txt" \
  || "$INSTALL_DIR/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade "yt-dlp[default]"
ok "yt-dlp $("$INSTALL_DIR/venv/bin/python" -c 'from yt_dlp.version import __version__; print(__version__)')"

LAUNCHER="$INSTALL_DIR/ninedeo-host"
cat > "$LAUNCHER" <<SH
#!/bin/sh
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/host/ninedeo_host.py" "\$@"
SH
chmod +x "$LAUNCHER"

step "ffmpeg"
if command -v ffmpeg >/dev/null; then ok "$(command -v ffmpeg)"
else warn "ffmpeg absent : installez-le (apt install ffmpeg / brew install ffmpeg)."; fi

step "Déclaration auprès des navigateurs"
manifest() { # $1 = clé d'autorisation, $2 = valeur JSON
  printf '{\n  "name": "%s",\n  "description": "NineDeo - moteur de telechargement (yt-dlp + ffmpeg)",\n  "path": "%s",\n  "type": "stdio",\n  "%s": [%s]\n}\n' \
    "$HOST_NAME" "$LAUNCHER" "$1" "$2"
}
for d in "${BROWSER_DIRS[@]}"; do
  mkdir -p "$d" && manifest allowed_origins "\"chrome-extension://$EXT_ID/\"" > "$d/$HOST_NAME.json" && ok "$d"
done
mkdir -p "$FIREFOX_DIR" && manifest allowed_extensions "\"$FIREFOX_ID\"" > "$FIREFOX_DIR/$HOST_NAME.json" && ok "$FIREFOX_DIR"

step "Auto-test"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/host/ninedeo_host.py" --selftest || true
printf '\n\033[32mInstallation terminée.\033[0m Rechargez l'"'"'extension puis « Tester la connexion » dans ses paramètres.\n'
