"""Severe weather alerts from the US National Weather Service (api.weather.gov, no key).

Active alerts for the configured location are shown as a banner on Home. A new
warning that is severe enough also goes up as an alert page. Outside the US
the service has nothing to say, and this stays quiet.
"""
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

log = logging.getLogger(__name__)

API = "https://api.weather.gov/alerts/active"
HEADERS = {"User-Agent": "TouchFrame/1.0 (Raspberry Pi wall display)",
           "Accept": "application/geo+json"}
RANK = {"extreme": 4, "severe": 3, "moderate": 2, "minor": 1}
EVERY = 300             # seconds between looks


class Warnings:
    def __init__(self, cfg, state_dir, alerts, on_change):
        self.cfg = cfg["weather"]
        self.enabled = bool(self.cfg["warnings"])
        self.alerts = alerts
        self.on_change = on_change
        self.file = state_dir / "warnings.json"
        self._active = []
        self._paged = set()             # events already put up as pages, while they last
        try:
            self._paged = set(json.loads(self.file.read_text()))
        except (OSError, ValueError):
            pass
        self._lock = threading.Lock()

    def _fetch(self):
        point = f"{self.cfg['latitude']:.4f},{self.cfg['longitude']:.4f}"
        req = urllib.request.Request(API + "?" + urllib.parse.urlencode({"point": point}),
                                     headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            features = json.load(resp).get("features", [])
        out = []
        for f in features:
            p = f.get("properties", {})
            if p.get("status") != "Actual" or p.get("messageType") == "Cancel":
                continue
            out.append({
                "id": p.get("id", ""), "event": p.get("event", "Weather alert"),
                "severity": str(p.get("severity", "")).lower(),
                "headline": p.get("headline") or "",
                "description": p.get("description") or "",
                "instruction": p.get("instruction") or "",
                "ends": p.get("ends") or p.get("expires")})
        out.sort(key=lambda a: -RANK.get(a["severity"], 0))
        return out

    def _page(self, active):
        """New events at or above page_severity go up as pages: once each, however
        often the Weather Service updates them, and not again after a restart."""
        floor = RANK.get(str(self.cfg["page_severity"]).lower())
        events = {a["event"] for a in active}
        for a in active:
            rank = RANK.get(a["severity"], 0)
            if floor and rank >= floor and a["event"] not in self._paged:
                self._paged.add(a["event"])
                text = [a["event"]]
                if a["ends"]:
                    try:
                        ends = datetime.fromisoformat(a["ends"]).astimezone()
                        text.append("until " + ends.strftime("%-I:%M %p %A").replace(":00", ""))
                    except ValueError:
                        pass
                self.alerts.show(f"nws:{a['id']}", "Weather Service", "Weather alert", text,
                                 priority="emergency" if rank == 4 else "high",
                                 until_ms=int(time.time() * 1000) + self.cfg["page_seconds"] * 1000)
        if self._paged - events:
            self._paged &= events       # over: the next one of its kind pages again
        try:
            self.file.write_text(json.dumps(sorted(self._paged)))
        except OSError:
            pass

    def _run(self):
        failing = False
        while True:
            try:
                active = self._fetch()
                failing = False
                self._page(active)
                with self._lock:
                    changed = active != self._active
                    self._active = active
                if changed:
                    self.on_change()
            except Exception as exc:
                if not failing:
                    log.warning("weather alerts: %s", exc)
                failing = True
            time.sleep(EVERY)

    def start(self):
        if self.enabled:
            threading.Thread(target=self._run, name="warnings", daemon=True).start()

    def snapshot(self):
        with self._lock:
            return list(self._active)
