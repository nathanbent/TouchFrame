#!/usr/bin/env bash
# Open the dashboard full screen in Chromium. Run from the desktop session
# (install.sh adds it to the desktop's autostart).
URL="${1:-http://127.0.0.1:8660/}"
until curl -fs "${URL}api/settings" > /dev/null; do sleep 1; done
# --use-fake-ui-for-media-stream: let the page use the microphone (push to
# talk) without a prompt nobody at a kiosk can answer.
BROWSER="$(command -v chromium || command -v chromium-browser)"
exec "$BROWSER" --kiosk --no-first-run --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble --disable-pinch --overscroll-history-navigation=0 \
  --use-fake-ui-for-media-stream --password-store=basic "$URL"
