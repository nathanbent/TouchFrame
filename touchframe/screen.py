"""The display's backlight: dim it overnight, and brighten it for a touch or a page.

The Raspberry Pi Touch Display's backlight is a directory under
/sys/class/backlight. Writing to it needs permission, which install.sh gives
the `video` group with a udev rule. Without a backlight (or permission) this
does nothing.
"""
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path("/sys/class/backlight")


class Screen:
    def __init__(self, cfg):
        self.cfg = cfg["screen"]
        self.dir = self._find()
        self.percent = self.cfg["brightness"]   # may be changed from the screen's settings
        self._wake_until = 0.0
        self._level = None
        self._lock = threading.Lock()

    def _find(self):
        name = self.cfg["backlight"]
        if name == "off" or not ROOT.is_dir():
            return None
        if name != "auto":
            return ROOT / name if (ROOT / name).is_dir() else None
        found = sorted(p for p in ROOT.iterdir() if (p / "brightness").exists())
        return found[0] if found else None

    def _dim_now(self):
        hours = self.cfg["dim_hours"]
        if len(hours) != 2:
            return False
        start, end, hour = hours[0], hours[1], datetime.now().hour
        return start <= hour < end if start < end else hour >= start or hour < end

    def dimmed(self):
        return self.dir is not None and self._dim_now() and time.monotonic() >= self._wake_until

    def apply(self):
        if self.dir is None:
            return
        percent = min(self.cfg["dim_brightness"], self.percent) if self.dimmed() else self.percent
        with self._lock:
            if percent == self._level:
                return
            try:
                top = int((self.dir / "max_brightness").read_text())
                # Never fully off: a black screen can't be seen to be touched.
                value = max(1, round(top * max(0, min(100, percent)) / 100))
                (self.dir / "brightness").write_text(str(value))
                self._level = percent
            except (OSError, ValueError) as exc:
                log.warning("backlight: %s (see README, Screen brightness)", exc)
                self.dir = None

    def set(self, percent):
        self.percent = max(5, min(100, int(percent)))
        self.apply()

    def wake(self):
        """Full brightness for a while: someone touched the screen, or a page went up."""
        self._wake_until = time.monotonic() + self.cfg["wake_seconds"]
        self.apply()

    def start(self):
        if self.dir is None:
            return
        log.info("backlight: %s", self.dir)

        def loop():
            while self.dir is not None:
                self.apply()
                time.sleep(5)
        threading.Thread(target=loop, name="screen", daemon=True).start()
