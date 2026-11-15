"""What this Pi is receiving over multicast, stream by stream: rate, loss,
jitter, TTL, DSCP and audio level.

The measuring is done by mcast-tools' own monitor. TouchFrame starts
`mcast serve` on a loopback port for it (API only), watching the text groups,
any extra groups, and every stream announced with SAP; or it reads the monitor
of an mcast server that is already running (`monitor_url`).
"""
import json
import logging
import shutil
import subprocess
import threading
import time
import urllib.request

log = logging.getLogger(__name__)


class Streams:
    def __init__(self, cfg):
        self.cfg = cfg["multicast"]
        self.groups = list(dict.fromkeys(self.cfg["groups"] + self.cfg["pictures"] + self.cfg["watch"]))
        self.enabled = bool(self.cfg["monitor"] and (self.groups or self.cfg["monitor_url"]))
        self.url = (self.cfg["monitor_url"] or f"http://127.0.0.1:{self.cfg['monitor_port']}").rstrip("/")
        self.error = None
        self._proc = None
        self._stopping = False

    def stop(self):
        """Stop the monitor: before the server restarts itself, so the port is free again."""
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _serve(self):
        binary = shutil.which(self.cfg["mcast"])
        if not binary:
            self.error = f"the {self.cfg['mcast']} command isn't installed"
            return
        cmd = [binary, "serve", "--listen", f"127.0.0.1:{self.cfg['monitor_port']}", "--no-web"]
        for g in self.groups:
            cmd += ["--watch", g]
        if self.cfg["interface"]:
            cmd += ["--iface", str(self.cfg["interface"])]
        while not self._stopping:
            try:
                proc = self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        text=True, errors="replace")
                self.error = None
                tail = (proc.stderr.read() or "").strip().splitlines()
                proc.wait()
                self.error = tail[-1] if tail else f"mcast serve stopped ({proc.returncode})"
            except OSError as exc:
                self.error = str(exc)
            log.warning("stream monitor: %s; trying again in 10 s", self.error)
            time.sleep(10)

    def start(self):
        if self.enabled and not self.cfg["monitor_url"]:
            threading.Thread(target=self._serve, name="streams", daemon=True).start()

    def get(self):
        if not self.enabled:
            return {"enabled": False, "error": None, "streams": [], "listeners": []}
        try:
            with urllib.request.urlopen(self.url + "/api/v1/monitor", timeout=4) as resp:
                snap = json.load(resp)
        except (OSError, ValueError) as exc:
            return {"enabled": True, "streams": [], "listeners": [],
                    "error": self.error or f"the monitor isn't answering ({getattr(exc, 'reason', exc)})"}
        names = {g["group"]: g for g in snap.get("groups", [])}
        heard = set()
        streams = []
        for s in snap.get("streams", []):
            heard.add(s["group"])
            g = names.get(s["group"], {})
            streams.append({k: s.get(k) for k in (
                "group", "source", "ssrc", "codec", "payload_type", "packets", "kbit_per_sec",
                "loss_percent", "lost", "late", "jitter_ms", "max_gap_ms", "ttl", "tos",
                "peak_dbfs", "idle_ms")} | {"name": s.get("name") or g.get("name"),
                                            "announced": bool(g.get("from_sap"))})
        # Groups being watched that nothing has been heard on: the thing to see when testing.
        for group, g in names.items():
            if group not in heard:
                streams.append({"group": group, "name": g.get("name"), "silent": True,
                                "announced": bool(g.get("from_sap")), "error": g.get("error")})
        listeners = [{k: l.get(k) for k in ("group", "name", "from", "about", "loss_percent",
                                            "jitter_ms", "last_heard_ms", "left")}
                     for l in snap.get("listeners", [])]
        return {"enabled": True, "error": None, "streams": streams, "listeners": listeners}
