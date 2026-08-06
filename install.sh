#!/usr/bin/env bash
# Install the SOTA WFS server as systemd user services + timers.
#
# Prerequisites: python3, ngrok (with authtoken configured: `ngrok config add-authtoken ...`).
# Usage: ./install.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"

echo "==> Creating venv and installing package"
if [ ! -d "$REPO/.venv" ]; then
    python3 -m venv "$REPO/.venv"
fi
"$REPO/.venv/bin/pip" install --quiet --upgrade pip
"$REPO/.venv/bin/pip" install --quiet -e "$REPO"

DATA="${SOTA_WFS_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/sota-wfs}"
echo "==> Initial data fetch into $DATA (skipped if data already present)"
[ -f "$DATA/summitslist.csv" ]      || "$REPO/.venv/bin/python" "$REPO/fetch/fetch_sota.py"
[ -f "$DATA/superchargers.geojson" ] || "$REPO/.venv/bin/python" "$REPO/fetch/fetch_superchargers.py"

echo "==> Installing systemd user units"
NGROK_BIN="$(command -v ngrok || echo /usr/local/bin/ngrok)"
mkdir -p "$UNIT_DIR"
for unit in "$REPO"/systemd/*; do
    sed -e "s|%h/Dropbox/workspace/sota-wfs|$REPO|g" \
        -e "s|/usr/local/bin/ngrok|$NGROK_BIN|g" \
        "$unit" > "$UNIT_DIR/$(basename "$unit")"
done
systemctl --user daemon-reload

echo "==> Enabling and starting services"
systemctl --user enable --now sota-wfs.service ngrok.service
systemctl --user enable --now fetch-sota.timer fetch-superchargers.timer

echo "==> Enabling lingering (services run without an active login session)"
loginctl enable-linger "$USER"

echo
echo "Done. Check status with:"
echo "  systemctl --user status sota-wfs ngrok"
echo "  systemctl --user list-timers"
echo "  journalctl --user -u sota-wfs -f"
