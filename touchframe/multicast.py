"""Text pages straight off the network: real-time text (RFC 4103) on multicast groups.

Each group is received by `mcast text recv` from mcast-tools, so this screen
is a real multicast receiver: it joins the group, and reports to the sender
over RTCP under its own name, which is how the intercom panel knows it heard
a page. Nothing here goes through the panel's API.
"""
import json
import logging
import shutil
import socket
import subprocess
import threading
import time

from .alerts import now_ms

log = logging.getLogger(__name__)

GATHER = 0.6        # lines this close together are one page


class Group:
    def __init__(self, group, cfg, alerts, on_change):
        self.group = group
        self.cfg = cfg
        self.alerts = alerts
        self.on_change = on_change
        self.status = {"group": group, "listening": False, "error": None, "last_ms": None, "pages": 0}
        self._lines = []
        self._sender = None
        self._timer = None
        self._lock = threading.Lock()

    def _set(self, **changes):
        self.status.update(changes)
        self.on_change()

    def _flush(self):
        with self._lock:
            lines, sender, self._lines = self._lines, self._sender, []
        if not lines:
            return
        at = now_ms()
        self.status.update(last_ms=at, pages=self.status["pages"] + 1)
        self.alerts.show(f"multicast:{self.group}:{at}", f"multicast {self.group}",
                         sender or "Text page", lines,
                         until_ms=at + self.cfg["hold_seconds"] * 1000)

    def _line(self, line, as_json):
        if as_json:
            # `mcast text recv --json`: an object per line; only "line" is a page's text.
            try:
                entry = json.loads(line)
            except ValueError:
                return
            if entry.get("type") != "line":
                return
            sender, text = entry.get("from"), str(entry.get("text", ""))
        else:
            # Older mcast prints "SENDER: the text", one line at a time.
            sender, sep, text = line.partition(": ")
            if not sep:
                sender, text = None, line
        if not text.strip():
            return
        with self._lock:
            if self._timer:
                self._timer.cancel()
            if self._lines and sender != self._sender:
                lines, old, self._lines = self._lines, self._sender, []
                threading.Thread(target=self._other, args=(lines, old), daemon=True).start()
            self._sender = sender
            self._lines.append(text)
            self._timer = threading.Timer(GATHER, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _other(self, lines, sender):
        at = now_ms()
        self.alerts.show(f"multicast:{self.group}:{at}:{sender}", f"multicast {self.group}",
                         sender or "Text page", lines,
                         until_ms=at + self.cfg["hold_seconds"] * 1000)

    def run(self):
        binary = shutil.which(self.cfg["mcast"])
        if not binary:
            log.error("multicast: no `%s` command; install mcast-tools, or set [multicast] mcast "
                      "to its path", self.cfg["mcast"])
            return self._set(error=f"the {self.cfg['mcast']} command isn't installed")
        cmd = [binary, "text", "recv", "-g", self.group,
               "-n", self.cfg["name"] or socket.gethostname()]
        if self.cfg["interface"]:
            cmd += ["-i", str(self.cfg["interface"])]
        # Structured output, where this mcast has it: a sender's name with ": "
        # in it, or text that looks like a name, can't then be mistaken.
        as_json = _has_json(binary)
        if as_json:
            cmd.append("--json")
        while True:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, errors="replace", bufsize=1)
            except OSError as exc:
                self._set(listening=False, error=str(exc))
                time.sleep(10)
                continue
            log.info("multicast: listening on %s", self.group)
            self._set(listening=True, error=None)
            for line in proc.stdout:
                if line.strip():
                    self._line(line.rstrip("\r\n"), as_json)
            # It only ends when something is wrong: a bad group, no network yet.
            error = (proc.stderr.read() or "").strip().splitlines()
            proc.wait()
            error = error[-1] if error else f"mcast text recv stopped ({proc.returncode})"
            log.warning("multicast %s: %s; trying again in 10 s", self.group, error)
            self._set(listening=False, error=error)
            time.sleep(10)


def _has_json(binary):
    """Whether this `mcast text recv` can print JSON (mcast-tools after 0.5.12)."""
    try:
        out = subprocess.run([binary, "text", "recv", "--help"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "--json" in out


class Multicast:
    def __init__(self, cfg, alerts, on_change):
        self.groups = [Group(g, cfg["multicast"], alerts, on_change) for g in cfg["multicast"]["groups"]]

    def start(self):
        for g in self.groups:
            threading.Thread(target=g.run, name=f"multicast-{g.group}", daemon=True).start()

    def snapshot(self):
        return [dict(g.status) for g in self.groups]
