"""Text pages straight off the network: real-time text (RFC 4103) on multicast groups.

Each group is received by `mcast text recv` from mcast-tools, so this screen
is a real multicast receiver: it joins the group, and reports to the sender
over RTCP under its own name, which is how the intercom panel knows it heard
a page. Nothing here goes through the panel's API.
"""
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from .alerts import now_ms

log = logging.getLogger(__name__)

GATHER = 0.6        # lines this close together are one page
NO_END = 600        # a page whose end never comes is taken down after this many seconds
PANEL_ID = re.compile(r"^(\d+)\.(\d+)$")     # the intercom panel's page ids: RUN.AT_MS


class Group:
    def __init__(self, group, cfg, alerts, on_change):
        self.group = group
        self.cfg = cfg
        self.alerts = alerts
        self.on_change = on_change
        self.status = {"group": group, "listening": False, "error": None, "last_ms": None, "pages": 0}
        self._holds = {}            # page id -> seconds it stays up once cleared
        self._lines = []
        self._sender = None
        self._timer = None
        self._lock = threading.Lock()
        self._proc = None
        self._stopping = False

    def stop(self):
        """Leave the group: before the server restarts itself, so no receiver is left behind."""
        self._stopping = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

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
            if entry.get("type") == "page":
                return self._page(entry)
            if entry.get("type") == "clear":
                return self._clear(entry)
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

    def _key(self, page_id):
        return f"multicast:{self.group}:{page_id}"

    def _page(self, entry):
        """A whole page, with what its sender says it is: its name, its priority,
        and (later) its end. Nothing is guessed from timing or from its words."""
        page_id, at = str(entry.get("id", "")), now_ms()
        lines = [str(line) for line in entry.get("text", [])]
        if not page_id or not lines:
            return
        if len(self._holds) > 200:
            self._holds.clear()
        self._holds[page_id] = max(0, int(entry.get("hold_s") or 0))
        self.status.update(last_ms=at, pages=self.status["pages"] + 1)
        # The panel's own pages have the same id by its API: one alert, heard twice.
        panel = PANEL_ID.match(page_id)
        priority = entry.get("priority")
        self.alerts.show(self._key(page_id), f"multicast {self.group}",
                         entry.get("name") or entry.get("from") or "Text page", lines,
                         priority=priority if priority in ("high", "emergency") else "normal",
                         until_ms=at + NO_END * 1000,
                         same_as=f"panel:{panel[1]}:{panel[2]}" if panel else None)

    def _clear(self, entry):
        """A page's end: after its hold, or at once when it was stopped by hand."""
        page_id, at = str(entry.get("id", "")), now_ms()
        hold = self._holds.pop(page_id, None)
        if hold is None:
            return
        stopped = bool(entry.get("now"))
        self.alerts.end(self._key(page_id), at if stopped else at + hold * 1000, stopped=stopped)

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
        while not self._stopping:
            try:
                proc = self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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


IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
KEEP_FILES = 40


class PictureGroup(Group):
    """Files sent to a group with `mcast file send`. A picture goes up on the
    screen as an alert; anything else is noted in the Alerts tab."""

    def __init__(self, group, cfg, alerts, on_change, folder):
        super().__init__(group, cfg, alerts, on_change)
        self.folder = folder
        self.status["pictures"] = True

    def _got(self, path):
        at = now_ms()
        self.status.update(last_ms=at, pages=self.status["pages"] + 1)
        picture = path.suffix.lower() in IMAGES
        self.alerts.show(f"file:{self.group}:{at}", f"multicast {self.group}",
                         "Picture" if picture else "File received", [path.name],
                         until_ms=at + self.cfg["picture_seconds"] * 1000,
                         image="/received/" + urllib.parse.quote(path.name) if picture else None)
        # Keep the folder from growing for ever.
        files = sorted(self.folder.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[KEEP_FILES:]:
            old.unlink(missing_ok=True)

    def run(self):
        binary = shutil.which(self.cfg["mcast"])
        if not binary:
            return self._set(error=f"the {self.cfg['mcast']} command isn't installed")
        cmd = [binary, "file", "recv", "-g", self.group, "-o", str(self.folder),
               "-n", self.cfg["name"] or socket.gethostname(), "--max-size", "50"]
        if self.cfg["interface"]:
            cmd += ["-i", str(self.cfg["interface"])]
        while not self._stopping:
            error = None
            try:
                proc = self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        text=True, errors="replace", bufsize=1)
                log.info("multicast: listening for pictures on %s", self.group)
                self._set(listening=True, error=None)
                for line in proc.stderr:
                    line = line.strip()
                    # "got NAME (SIZE) in 1.0s, from N packets (M needed), checksum OK; saved PATH"
                    if line.startswith("got ") and "; saved " in line:
                        self._got(Path(line.rsplit("; saved ", 1)[1]))
                    elif line:
                        error = line
                proc.wait()
            except OSError as exc:
                error = str(exc)
            log.warning("multicast %s: %s; trying again in 10 s", self.group, error or "stopped")
            self._set(listening=False, error=error or "mcast file recv stopped")
            time.sleep(10)


class Multicast:
    def __init__(self, cfg, alerts, on_change):
        self.folder = cfg["app"]["state_dir"] / "received"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.groups = [Group(g, cfg["multicast"], alerts, on_change) for g in cfg["multicast"]["groups"]]
        self.groups += [PictureGroup(g, cfg["multicast"], alerts, on_change, self.folder)
                        for g in cfg["multicast"]["pictures"]]

    def file(self, name):
        """A received picture, for the page to show."""
        path = (self.folder / name).resolve()
        if path.parent != self.folder.resolve() or path.suffix.lower() not in IMAGES:
            return None
        return path

    def start(self):
        for g in self.groups:
            threading.Thread(target=g.run, name=f"multicast-{g.group}", daemon=True).start()

    def stop(self):
        for g in self.groups:
            g.stop()

    def snapshot(self):
        return [dict(g.status) for g in self.groups]
