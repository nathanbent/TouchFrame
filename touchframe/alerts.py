"""Text alerts: what's on the screen now, and a history of what was.

Alerts come from the intercom panel's API and from multicast text channels.
When both carry the same page, it is shown once and marked as heard on both,
which is the point of a test device: the history says which way each page
arrived.
"""
import json
import logging
import threading
import time

log = logging.getLogger(__name__)

KEEP = 50               # alerts kept in the history, and as many again archived
SAME_WITHIN_MS = 30000  # the same words from another source within this long are one alert


def now_ms():
    return int(time.time() * 1000)


class Alerts:
    def __init__(self, cfg, state_dir, on_page=None):
        self.cfg = cfg["alerts"]
        self.file = state_dir / "alerts.json"
        self.on_page = on_page          # called when a new alert goes up
        self.on_change = None           # called after anything changes
        self._lock = threading.Lock()
        self._pages = {}                # id -> page, the ones on screen
        self._closed = set()            # keys closed by hand: a source can't put them back up
        self._history = []
        try:
            self._history = json.loads(self.file.read_text())
        except (OSError, ValueError):
            pass
        self._trim()
        self._next = max([a["id"] for a in self._history], default=0) + 1

    def _priority(self, text, priority):
        words = " ".join(text).lower()
        if any(w.lower() in words for w in self.cfg["emergency_words"]):
            return "emergency"
        return priority

    def _trim(self):
        """Keep the newest KEEP of the history, and the newest KEEP of the archive."""
        kept = {False: 0, True: 0}
        history = []
        for entry in self._history:
            archived = bool(entry.get("archived"))
            if kept[archived] < KEEP:
                kept[archived] += 1
                history.append(entry)
        self._history = history

    def _save(self):
        try:
            tmp = self.file.with_suffix(".part")
            tmp.write_text(json.dumps(self._history))
            tmp.replace(self.file)
        except OSError as exc:
            log.warning("alerts: can't save the history (%s)", exc)

    def _changed(self, new=False):
        if new and self.on_page:
            self.on_page()
        if self.on_change:
            self.on_change()

    def show(self, key, source, name, text, priority="normal", until_ms=None, at_ms=None, image=None,
             same_as=None):
        """Put an alert up (or update the one with this key). `source` says how it came.
        `same_as` is the key the same page has by another route, when its sender says
        which page it is: the two are then one alert whatever their words."""
        text = [t for t in (line.strip() for line in text) if t]
        if not text:
            return
        now = now_ms()
        with self._lock:
            if key in self._closed:
                return
            page = next((p for p in self._pages.values() if key in p["keys"]), None)
            new = page is None
            if new and until_ms is not None and until_ms <= now:
                return                  # already over (a stopped run's last word)
            if page is None and same_as:
                if same_as in self._closed:
                    return              # tapped away already, by its other route
                page = next((p for p in self._pages.values() if same_as in p["keys"]), None)
                if page is not None:
                    new = False
                    page["keys"].append(key)
                    if source not in page["sources"]:
                        page["sources"].append(source)
            if page is None:
                # The same words, just now, by another route: one alert, heard twice.
                page = next((p for p in self._pages.values()
                             if p["text"] == text and now - p["at_ms"] < SAME_WITHIN_MS
                             and source not in p["sources"]), None)
                if page is not None:
                    new = False
                    page["keys"].append(key)
                    page["sources"].append(source)
            if page is None:
                page = {"id": self._next, "keys": [key], "sources": [source], "name": name,
                        "text": text, "priority": "normal", "at_ms": at_ms or now,
                        "until_ms": None, "ended": False, "image": image}
                self._next += 1
                self._pages[page["id"]] = page
                self._history.insert(0, {"id": page["id"], "at_ms": page["at_ms"], "seen": False})
                self._trim()
            # The panel knows the announcement's name, priority and when it ends;
            # a multicast channel only carries the words.
            if source == "panel":
                page["name"] = name
                page["owner"] = key
            priority = self._priority(text, priority)
            if priority == "emergency" or page["priority"] == "normal":
                page["priority"] = priority
            if page.get("owner", key) == key:
                page["until_ms"] = until_ms
            page["sticky"] = (page["priority"] == "emergency" and self.cfg["emergency_stays"]
                              and not page.get("stopped"))
            for entry in self._history:
                if entry["id"] == page["id"]:
                    entry.update(name=page["name"], text=text, priority=page["priority"],
                                 image=page.get("image"),
                                 sources=list(page["sources"]))
            self._save()
        self._changed(new)

    def end(self, key, until_ms, stopped=False):
        """The alert with this key comes down at `until_ms`. An emergency stays up
        until someone taps it, unless its announcement was stopped by hand."""
        with self._lock:
            page = next((p for p in self._pages.values() if key in p["keys"]), None)
            if page is None or page.get("owner", key) != key:
                return
            page["until_ms"] = until_ms
            if stopped:
                page["stopped"], page["sticky"] = True, False
        self._changed()

    def end_source(self, prefix):
        """A source went away: its alerts with no end yet come down now."""
        with self._lock:
            for page in self._pages.values():
                if page["until_ms"] is None and page.get("owner", page["keys"][0]).startswith(prefix):
                    page["until_ms"] = now_ms()
        self._changed()

    def close(self, alert_id):
        """Someone tapped it: take it down and count it as seen."""
        with self._lock:
            page = self._pages.pop(alert_id, None)
            if page:
                self._closed.update(page["keys"])
                if len(self._closed) > 500:
                    self._closed = set(page["keys"])
            for entry in self._history:
                if entry["id"] == alert_id:
                    entry["seen"] = True
            self._save()
        self._changed()

    def seen_all(self):
        with self._lock:
            for entry in self._history:
                entry["seen"] = True
            self._save()
        self._changed()

    def archive(self, alert_id, archived=True):
        """Move one alert out of the history into the archive (or back). An
        archived alert counts as seen."""
        with self._lock:
            for entry in self._history:
                if entry["id"] == alert_id:
                    entry["archived"] = archived
                    entry["seen"] = True
            self._trim()
            self._save()
        self._changed()

    def archive_all(self):
        """Archive everything in the history."""
        with self._lock:
            for entry in self._history:
                entry["archived"] = True
                entry["seen"] = True
            self._trim()
            self._save()
        self._changed()

    def snapshot(self):
        now = now_ms()
        with self._lock:
            for i in [i for i, p in self._pages.items()
                      if p["until_ms"] and p["until_ms"] <= now and not p["sticky"]]:
                del self._pages[i]
            pages = sorted(self._pages.values(),
                           key=lambda p: (p["priority"] != "emergency", -p["at_ms"]))
            return {
                "pages": [{k: p[k] for k in ("id", "name", "text", "priority", "sources",
                                             "at_ms", "until_ms", "sticky", "image")} for p in pages],
                "history": [dict(e) for e in self._history if "text" in e],
                "unseen": sum(1 for e in self._history if not e["seen"] and not e.get("archived")),
            }
