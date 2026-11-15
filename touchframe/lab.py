"""The home lab at a glance: Prometheus targets that are down, and Zabbix problems."""
import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

SEVERITIES = ["Not classified", "Information", "Warning", "Average", "High", "Disaster"]
SHOWN = 40              # problems and targets listed; the counts cover them all


def fetch(url, cfg, body=None, headers=None):
    """GET (or POST `body` as JSON) and return the JSON answer."""
    context = None
    if url.startswith("https") and not cfg["verify_tls"]:
        context = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers=headers or {})
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"it answered {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise RuntimeError(str(getattr(exc, "reason", exc))) from None


class Prometheus:
    name = "prometheus"

    def __init__(self, cfg):
        self.cfg = cfg["prometheus"]
        self.configured = bool(self.cfg["url"])

    def poll(self):
        headers = {"Authorization": "Bearer " + self.cfg["token"]} if self.cfg["token"] else {}
        data = fetch(self.cfg["url"].rstrip("/") + "/api/v1/targets?state=active", self.cfg,
                     headers=headers)
        targets = data["data"]["activeTargets"]
        ignore = set(self.cfg["ignore_jobs"])
        targets = [t for t in targets if t["labels"].get("job") not in ignore]
        down = [{"job": t["labels"].get("job", ""), "instance": t["labels"].get("instance", ""),
                 "error": t.get("lastError", "")}
                for t in targets if t.get("health") != "up"]
        down.sort(key=lambda t: (t["job"], t["instance"]))
        return {"total": len(targets), "up": len(targets) - len(down), "down": down[:SHOWN]}


class Zabbix:
    name = "zabbix"

    def __init__(self, cfg, alerts):
        self.cfg = cfg["zabbix"]
        self.configured = bool(self.cfg["url"])
        self.alerts = alerts
        self._known = None          # event ids seen so far; None until the first look

    def _call(self, method, params):
        url = self.cfg["url"].rstrip("/")
        if not url.endswith(".php"):
            url += "/api_jsonrpc.php"
        answer = fetch(url, self.cfg, {"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                       {"Authorization": "Bearer " + self.cfg["token"]})
        if "error" in answer:
            err = answer["error"]
            raise RuntimeError(f"{err.get('message', '')} {err.get('data', '')}".strip())
        return answer["result"]

    def poll(self):
        problems = self._call("problem.get", {
            "output": ["eventid", "objectid", "name", "severity", "clock", "acknowledged"],
            "source": 0, "object": 0, "suppressed": False,
            "severities": list(range(self.cfg["min_severity"], 6)),
            "sortfield": ["eventid"], "sortorder": "DESC", "limit": 2000})
        # Only triggers that are really being watched, as the Zabbix dashboard shows:
        # not disabled ones, nor those waiting on another trigger. This also names the host.
        triggers = self._call("trigger.get", {
            "triggerids": sorted({p["objectid"] for p in problems}),
            "output": ["triggerid"], "selectHosts": ["name"],
            "monitored": True, "skipDependent": True}) if problems else []
        hosts = {t["triggerid"]: ", ".join(h["name"] for h in t.get("hosts", [])) for t in triggers}
        out = [{"id": p["eventid"], "severity": int(p["severity"]), "name": p["name"],
                "host": hosts[p["objectid"]], "at_ms": int(p["clock"]) * 1000,
                "acknowledged": p.get("acknowledged") == "1"}
               for p in problems if p["objectid"] in hosts]
        out.sort(key=lambda p: (-p["severity"], -p["at_ms"]))
        counts = [0] * 6
        for p in out:
            counts[p["severity"]] += 1
        self._page(out)
        return {"counts": counts, "total": len(out), "problems": out[:SHOWN]}

    def _page(self, problems):
        """A new problem at or above alert_severity goes up as an alert. Ones already
        there when TouchFrame starts don't."""
        ids = {p["id"] for p in problems}
        if self._known is not None and self.cfg["alert_severity"] > 0:
            for p in problems:
                if p["id"] not in self._known and p["severity"] >= self.cfg["alert_severity"]:
                    self.alerts.show(
                        f"zabbix:{p['id']}", "Zabbix", f"Zabbix \u00b7 {SEVERITIES[p['severity']]}",
                        [p["host"], p["name"]],
                        priority="emergency" if p["severity"] == 5 else "high",
                        until_ms=int(time.time() * 1000) + self.cfg["alert_seconds"] * 1000)
        self._known = ids


class Lab:
    def __init__(self, cfg, alerts, on_change):
        from . import services
        self.problems = [s for s in (Prometheus(cfg), Zabbix(cfg, alerts)) if s.configured]
        self.tiles = services.load(cfg)
        self.on_change = on_change
        self.enabled = bool(self.problems or self.tiles)
        self._state = {id(s): {"ok": None, "error": None} for s in self.problems + self.tiles}
        self._lock = threading.Lock()
        self._cfg = cfg

    def _interval(self, source):
        cfg = self._cfg[source.name] if source in self.problems else source.cfg
        return max(10, cfg["interval_seconds"])

    def _run(self, source):
        while True:
            try:
                new = {"ok": True, "error": None, **source.poll()}
            except Exception as exc:
                log.warning("%s: %s", source.name, exc)
                # Keep the last numbers on screen, marked as old.
                new = {**self._state[id(source)], "ok": False, "error": str(exc)[:200]}
            with self._lock:
                changed = new != self._state[id(source)]
                self._state[id(source)] = new
            if changed:
                self.on_change()
            time.sleep(self._interval(source))

    def start(self):
        for s in self.problems + self.tiles:
            threading.Thread(target=self._run, args=(s,), name=s.name, daemon=True).start()

    def snapshot(self):
        with self._lock:
            out = {s.name: self._state[id(s)] for s in self.problems}
            out["tiles"] = [{"kind": s.kind, "name": s.name, "noun": getattr(s, "noun", None),
                             **self._state[id(s)]} for s in self.tiles]
            return out
