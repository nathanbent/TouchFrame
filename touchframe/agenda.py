"""Calendar events from iCal (ICS) links, as JSON for the page.

The fetching and event handling follow InkFrame's calendar mode: Google
Calendar's "Secret address in iCal format" works, recurring events are
expanded, and the last good copy of each calendar is used when offline.
"""
import hashlib
import logging
import threading
import time as _time
import urllib.request
from datetime import datetime, time, timedelta

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "TouchFrame/1.0 (Raspberry Pi wall display)"}
COLOURS = ["blue", "red", "green", "orange", "purple"]
MONTHS = 4              # this month and the next three can be looked through


class Agenda:
    def __init__(self, cfg, state_dir):
        self.cfg = cfg["calendar"]
        self.interval = max(1, self.cfg["interval_minutes"]) * 60
        self.dir = state_dir / "calendar"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache = None

    def _sources(self):
        out = []
        for i, src in enumerate(self.cfg["sources"]):
            if isinstance(src, str):
                src = {"url": src}
            colour = str(src.get("colour", "")).lower()
            out.append((i, src["url"], colour if colour in COLOURS else COLOURS[i % len(COLOURS)]))
        return out

    def _download(self):
        texts, stale = [], False
        for i, url, colour in self._sources():
            cached = self.dir / f"{i}-{hashlib.sha1(url.encode()).hexdigest()[:10]}.ics"
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                _check_ics(data)
                cached.write_bytes(data)
            except Exception as exc:
                if not cached.exists() or not _is_ics(cached.read_bytes()):
                    raise RuntimeError(f"Calendar {i + 1}: {_short(exc)}") from None
                log.warning("calendar %d fetch failed (%s); using the last copy", i + 1, _short(exc))
                data, stale = cached.read_bytes(), True
            texts.append((data, colour))
        return texts, stale

    def _events(self):
        import icalendar
        import recurring_ical_events

        texts, stale = self._download()
        today = datetime.now().date()
        first = today.replace(day=1)
        end = first.month - 1 + MONTHS
        end = first.replace(year=first.year + end // 12, month=end % 12 + 1)
        last = max(today + timedelta(days=self.cfg["days"]), end) + timedelta(days=1)
        events = []
        for data, colour in texts:
            cal = icalendar.Calendar.from_ical(data)
            for ev in recurring_ical_events.of(cal).between(first, last):
                if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                start, stop, all_day = _span(ev)
                events.append({"start": start, "stop": stop, "all_day": all_day, "colour": colour,
                               "title": str(ev.get("SUMMARY", "")).strip() or "(no title)"})
        events.sort(key=lambda e: (e["start"].date(), not e["all_day"], e["start"], e["title"]))
        return events, stale

    def get(self):
        """{"configured", "stale", "error", "week_start", "months", "events": [...]}: the
        events of this month and the next few, each with ISO start and stop in the Pi's
        local time. The agenda lists the next `days` days of them."""
        out = {"configured": bool(self.cfg["sources"]), "stale": False, "error": None,
               "week_start": self.cfg["week_start"], "days": self.cfg["days"],
               "months": MONTHS, "events": []}
        if not out["configured"]:
            return out
        with self._lock:
            if not self._cache or _time.monotonic() - self._cache[0] >= self.interval:
                try:
                    self._cache = (_time.monotonic(), *self._events(), None)
                except Exception as exc:
                    log.warning("calendar: %s", _short(exc))
                    # Show the error, and look again in a minute.
                    self._cache = (_time.monotonic() - self.interval + 60, [], False, _short(exc))
            _, events, out["stale"], out["error"] = self._cache
        out["events"] = [{"title": e["title"], "colour": e["colour"], "all_day": e["all_day"],
                          "start": e["start"].isoformat(), "stop": e["stop"].isoformat()}
                         for e in events]
        return out


def _is_ics(data):
    return data.lstrip()[:15].upper().startswith(b"BEGIN:VCALENDAR")


def _check_ics(data):
    if _is_ics(data):
        return
    if b"<html" in data[:2000].lower() or b"<!doctype" in data[:2000].lower():
        raise ValueError("that link returned a web page (probably a sign-in page), not calendar "
                         "data. Use the calendar's 'Secret address in iCal format', which ends "
                         "in /basic.ics")
    raise ValueError("that link did not return iCal (.ics) data")


def _short(exc, limit=200):
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _local(dt):
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def _span(ev):
    start = ev["DTSTART"].dt
    if ev.get("DTEND") is not None:
        stop = ev["DTEND"].dt
    elif ev.get("DURATION") is not None:
        stop = start + ev["DURATION"].dt
    else:
        stop = start + (timedelta(days=1) if not isinstance(start, datetime) else timedelta(0))
    if not isinstance(start, datetime):
        stop = stop if not isinstance(stop, datetime) else stop.date()
        return datetime.combine(start, time.min), datetime.combine(stop, time.min), True
    start, stop = _local(start), _local(stop if isinstance(stop, datetime) else
                                       datetime.combine(stop, time.min))
    return start, max(stop, start + timedelta(minutes=1)), False
