#!/usr/bin/env bash
# Set up TouchFrame on a Raspberry Pi: a service that starts on boot, and
# Chromium opening it full screen when the desktop starts.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${VENV:-$DIR/.venv}"

echo "== System packages"
sudo apt-get install -y python3-venv curl
command -v chromium > /dev/null || command -v chromium-browser > /dev/null || sudo apt-get install -y chromium

echo "== Python environment in $VENV"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -r "$DIR/requirements.txt"

echo "== Config"
[ -f "$DIR/config.toml" ] || cp "$DIR/config.example.toml" "$DIR/config.toml"
chmod 600 "$DIR/config.toml"    # it holds calendar links and the intercom token
mkdir -p "$HOME/Pictures/touchframe"

echo "== Backlight (so the screen can dim at night)"
echo 'SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"' \
  | sudo tee /etc/udev/rules.d/90-touchframe-backlight.rules > /dev/null
sudo usermod -aG video "$USER"
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=backlight --action=add

echo "== Service"
sed -e "s|@USER@|$USER|" -e "s|@DIR@|$DIR|" -e "s|@VENV@|$VENV|" "$DIR/touchframe.service" \
  | sudo tee /etc/systemd/system/touchframe.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable touchframe

echo "== Kiosk (Chromium, full screen, when the desktop starts)"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/touchframe-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TouchFrame
Exec=$DIR/kiosk.sh
EOF

echo
echo "Done. Next:"
echo "  1. Set your location (and calendars, and the intercom) in $DIR/config.toml"
echo "  2. Copy photos into $HOME/Pictures/touchframe for the screensaver"
echo "  3. sudo systemctl start touchframe"
echo "  4. Reboot, or run $DIR/kiosk.sh from the desktop"
