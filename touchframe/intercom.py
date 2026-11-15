"""A link to an mcast intercom panel (`mcast intercom serve`).

One connection to the panel's API is kept for every screen: its event stream
says what is playing and which text pages are up, and the page's buttons play
and stop announcements through it. The panel's token stays on this side, so
the browser never sees it.
"""
import json
import logging
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
    def __init__(self, cfg, alerts, on_change):
        self.cfg = cfg["intercom"]
        self.url = self.cfg["url"].rstrip("/")
        self.enabled = bool(self.url)
        self.alerts = alerts                # where text pages go
        self.on_change = on_change          # called after anything here changes
        self._priority = {}                 # run id -> its announcement's priority
        self._ssl = None
        if self.url.startswith("https"):
            if not self.cfg["verify_tls"]:
                self._ssl = ssl._create_unverified_context()
            else:
                self._ssl = ssl.create_default_context(cafile=self.cfg["ca_file"] or None)
        self._lock = threading.Lock()
        self._state = {"connected": False, "error": None, "backend_ok": True,
                       "groups": [], "runs": {}}

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
            s = self._state
            return {
                "enabled": self.enabled,
                "connected": s["connected"],
                "error": s["error"],
                "backend_ok": s["backend_ok"],
                "groups": s["groups"],
                "runs": sorted(s["runs"].values(), key=lambda r: r["id"]),
            }

    def _set(self, **changes):
        with self._lock:
            self._state.update(changes)
        self.on_change()

    def _page(self, page):
        """A text page from the panel goes up, or is given its end."""
        zones = set(self.cfg["zones"])
        if zones and not zones & set(page.get("zones", [])):
            return
        text = page["text"]
        # A line per entry, as a multicast channel carries the same page (older
        # panels sent a page of several lines as one entry), so the two match.
        lines = [line for entry in (text if isinstance(text, list) else [text])
                 for line in str(entry).splitlines()]
        # Newer panels say the page's priority; older ones only on its run.
        priority = page.get("priority") or self._priority.get(page["run"], "normal")
        self.alerts.show(f"panel:{page['run']}:{page['at_ms']}", "panel", page["name"], lines,
                         priority=priority,
                         until_ms=page.get("until_ms"), at_ms=page["at_ms"])

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

        all_runs = self._call("GET", "/runs")
        self._priority = {r["id"]: r.get("priority", "normal") for r in all_runs}
        runs = {r["id"]: _run(r) for r in all_runs if r["state"] in ACTIVE}
        backend = self._call("GET", "/backend")
        self._set(connected=True, error=None, groups=groups, runs=runs,
                  backend_ok=bool(backend.get("connected", True)))
        for page in reversed(self._call("GET", "/display")["pages"]):
            self._page(page)

    def _event(self, name, data):
        if name == "run":
            run = data["run"]
            if len(self._priority) > 500:
                self._priority.clear()
            self._priority[run["id"]] = run.get("priority", "normal")
            if run["state"] == "stopped":
                # Stopped by hand: its pages come down now, an emergency's too.
                for shown in run.get("shown", []):
                    self.alerts.end(f"panel:{run['id']}:{shown['at_ms']}", int(time.time() * 1000),
                                    stopped=True)
            with self._lock:
                if run["state"] in ACTIVE:
                    self._state["runs"][run["id"]] = _run(run)
                else:
                    self._state["runs"].pop(run["id"], None)
            self.on_change()
        elif name == "display":
            self._page(data["page"])
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
            self._set(connected=False, error=error, runs={})
            self.alerts.end_source("panel:")
            time.sleep(wait)
            wait = min(wait * 2, 30)

    def start(self):
        if self.enabled:
            threading.Thread(target=self.run, name="intercom", daemon=True).start()


def _run(run):
    return {"id": run["id"], "announcement": run["announcement"], "name": run["name"],
            "state": run["state"], "priority": run.get("priority", "normal")}

