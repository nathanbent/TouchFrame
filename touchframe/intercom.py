"""A link to an mcast intercom panel (`mcast intercom serve`).

One connection to the panel's API is kept for every screen: its event stream
says what is playing and which text pages are up, and the page's buttons play
and stop announcements through it. The panel's token stays on this side, so
the browser never sees it.
"""
import json
import logging
import queue
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

PREFIX = "/api/intercom/v1"
ACTIVE = ("playing", "waiting", "stopping")


class IntercomError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class Intercom:
    def __init__(self, cfg, on_page=None):
        self.cfg = cfg["intercom"]
        self.url = self.cfg["url"].rstrip("/")
        self.enabled = bool(self.url)
        self.on_page = on_page              # called when a text page goes up
        self._ssl = None
        if self.url.startswith("https"):
            if not self.cfg["verify_tls"]:
                self._ssl = ssl._create_unverified_context()
            else:
                self._ssl = ssl.create_default_context(cafile=self.cfg["ca_file"] or None)
        self._lock = threading.Lock()
        self._subs = set()
        self._state = {"connected": False, "error": None, "backend_ok": True,
                       "groups": [], "runs": {}, "pages": {}}

    # ---------- the panel's API ----------
    def _open(self, method, path, timeout=15):
        req = urllib.request.Request(self.url + PREFIX + path, method=method)
        if self.cfg["token"]:
            req.add_header("Authorization", "Bearer " + self.cfg["token"])
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=self._ssl)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc)["error"]
            except Exception:
                message = f"the panel answered {exc.code}"
            if exc.code == 401:
                message = "the panel refused the token ([intercom] token in config.toml)"
            raise IntercomError(message, exc.code) from None
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise IntercomError(f"can't reach the intercom panel ({reason})") from None

    def _call(self, method, path):
        with self._open(method, path) as resp:
            return json.load(resp)

    def play(self, announcement):
        return self._call("POST", f"/announcements/{urllib.parse.quote(announcement, safe='')}/play")

    def stop(self, run=None):
        return self._call("DELETE", "/runs" if run is None else f"/runs/{int(run)}")

    # ---------- state ----------
    def snapshot(self):
        with self._lock:
            now = int(time.time() * 1000)
            s = self._state
            for run in [r for r, p in s["pages"].items() if p["until_ms"] and p["until_ms"] <= now]:
                del s["pages"][run]
            return {
                "enabled": self.enabled,
                "connected": s["connected"],
                "error": s["error"],
                "backend_ok": s["backend_ok"],
                "groups": s["groups"],
                "runs": sorted(s["runs"].values(), key=lambda r: r["id"]),
                "pages": sorted(s["pages"].values(), key=lambda p: -p["at_ms"]),
                "now_ms": now,
            }

    def subscribe(self):
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def _publish(self):
        snap = self.snapshot()
        with self._lock:
            for q in self._subs:
                try:
                    q.put_nowait(snap)
                except queue.Full:
                    pass                    # a stuck screen; it refetches when it reconnects

    def _set(self, **changes):
        with self._lock:
            self._state.update(changes)
        self._publish()

    def _refresh(self):
        """Fetch everything the page shows, in the Panel's own order and groups."""
        announcements = {a["id"]: a for a in self._call("GET", "/announcements")}
        categories = {c["id"]: c for c in self._call("GET", "/categories")}
        layout = self._call("GET", "/panel-layout")
        only = set(self.cfg["categories"])

        def button(aid, category):
            a = announcements.get(aid)
            if a is None:
                return None
            return {"id": a["id"], "name": a["name"], "icon": a.get("icon"),
                    "color": a.get("color") or (category or {}).get("color"),
                    "live": a.get("audio") == "live",
                    "priority": a.get("priority", "normal"),
                    "confirm": bool(a.get("confirm")) or a.get("priority") == "emergency"}

        groups = []
        for g in layout["groups"]:
            if only and g["id"] not in only and g["name"] not in only:
                continue
            buttons = [b for b in (button(a, categories.get(g["id"])) for a in g["announcements"]) if b]
            if buttons:
                groups.append({"id": g["id"], "name": g["name"], "buttons": buttons})
        if not only or "other" in only:
            buttons = [b for b in (button(a, None) for a in layout.get("other", [])) if b]
            if buttons:
                groups.append({"id": None, "name": "Other" if groups else "", "buttons": buttons})

        runs = {r["id"]: _run(r) for r in self._call("GET", "/runs") if r["state"] in ACTIVE}
        zones = ",".join(self.cfg["zones"])
        shown = self._call("GET", "/display" + (f"?zones={urllib.parse.quote(zones)}" if zones else ""))
        pages = {p["run"]: _page(p) for p in shown["pages"]}
        backend = self._call("GET", "/backend")
        self._set(connected=True, error=None, groups=groups, runs=runs, pages=pages,
                  backend_ok=bool(backend.get("connected", True)))

    def _event(self, name, data):
        if name == "run":
            run = data["run"]
            with self._lock:
                if run["state"] in ACTIVE:
                    self._state["runs"][run["id"]] = _run(run)
                else:
                    self._state["runs"].pop(run["id"], None)
            self._publish()
        elif name == "display":
            page = data["page"]
            zones = set(self.cfg["zones"])
            if zones and not zones & set(page.get("zones", [])):
                return
            with self._lock:
                new = page["run"] not in self._state["pages"]
                self._state["pages"][page["run"]] = _page(page)
            if new and self.on_page:
                self.on_page()
            self._publish()
        elif name == "backend":
            self._set(backend_ok=bool(data.get("status", {}).get("connected", True)))
        elif name in ("config_changed", "lagged"):
            self._refresh()

    def _listen(self):
        # The panel sends a keep-alive every 15 s, so a minute of silence means the link is dead.
        with self._open("GET", "/events", timeout=60) as resp:
            self._refresh()                 # after the stream is open, so nothing is missed between
            name, lines = None, []
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    lines.append(line[5:].lstrip())
                elif not line:
                    if name and lines:
                        self._event(name, json.loads("\n".join(lines)))
                    name, lines = None, []

    def run(self):
        """Stay connected to the panel for ever, retrying when it goes away."""
        wait = 2
        while True:
            try:
                self._listen()
                error = "the intercom panel closed the connection"
            except IntercomError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"lost the intercom panel ({exc})"
            with self._lock:
                was = self._state["connected"]
            if was:
                wait = 2
            log.warning("intercom: %s; trying again in %d s", error, wait)
            self._set(connected=False, error=error, runs={}, pages={})
            time.sleep(wait)
            wait = min(wait * 2, 30)

    def start(self):
        if self.enabled:
            threading.Thread(target=self.run, name="intercom", daemon=True).start()


def _run(run):
    return {"id": run["id"], "announcement": run["announcement"], "name": run["name"],
            "state": run["state"], "priority": run.get("priority", "normal")}


def _page(page):
    text = page["text"]
    return {"run": page["run"], "name": page["name"],
            "text": text if isinstance(text, list) else [text],
            "at_ms": page["at_ms"], "until_ms": page.get("until_ms")}
