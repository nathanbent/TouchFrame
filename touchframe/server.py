"""The web server: the page, and a small JSON API behind it."""
import argparse
import hmac
import ipaddress
import json
import logging
import mimetypes
import queue
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from .agenda import Agenda
from .alerts import Alerts
from .hooks import HookError, Hooks
from .intercom import Intercom, IntercomError
from .lab import Lab
from .multicast import Multicast
from .photos import Photos
from .screen import Screen
from .streams import Streams
from .warnings import Warnings
from .weather import Weather

log = logging.getLogger("touchframe")

WEB = Path(__file__).parent / "web"


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        state = cfg["app"]["state_dir"]
        self.weather = Weather(cfg, state)
        self.agenda = Agenda(cfg, state)
        self.screen = Screen(cfg)
        self.photos = Photos(cfg, state)
        self.alerts = Alerts(cfg, state, on_page=self.screen.wake)
        self.alerts.on_change = self.publish
        self.intercom = Intercom(cfg, self.alerts, self.publish)
        self.multicast = Multicast(cfg, self.alerts, self.publish)
        self._subs = set()
        self._lock = threading.Lock()
        self.lab = Lab(cfg, self.alerts, self.publish)
        self.hooks = Hooks(cfg, self.alerts)
        self.warnings = Warnings(cfg, state, self.alerts, self.publish)
        self.streams = Streams(cfg)
        # What was changed on the screen itself (brightness, theme) outlives a restart.
        self.device_file = state / "device.json"
        try:
            self.device = json.loads(self.device_file.read_text())
        except (OSError, ValueError):
            self.device = {}
        if "brightness" in self.device:
            self.screen.set(self.device["brightness"])
        try:
            self.version = subprocess.run(
                ["git", "-C", str(WEB), "log", "-1", "--format=%h %cs"], capture_output=True,
                text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            self.version = ""

    def device_info(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))    # sends nothing; just picks the route
                address = s.getsockname()[0]
        except OSError:
            address = ""
        return {"hostname": socket.gethostname(), "address": address, "version": self.version,
                "backlight": self.screen.dir is not None, "brightness": self.screen.percent,
                "theme": self.device.get("theme", self.cfg["app"]["theme"])}

    def set_device(self, body):
        if "brightness" in body:
            self.screen.set(body["brightness"])
            self.device["brightness"] = self.screen.percent
        if body.get("theme") in ("dark", "light", "auto"):
            self.device["theme"] = body["theme"]
        self.device_file.write_text(json.dumps(self.device))
        return self.device_info()

    def snapshot(self):
        """Everything that changes by itself: the intercom, alerts and multicast groups."""
        return {"intercom": self.intercom.snapshot(), "alerts": self.alerts.snapshot(),
                "multicast": self.multicast.snapshot(), "lab": self.lab.snapshot(),
                "warnings": self.warnings.snapshot(),
                "now_ms": int(time.time() * 1000)}

    def subscribe(self):
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self):
        snap = self.snapshot()
        with self._lock:
            for q in self._subs:
                try:
                    q.put_nowait(snap)
                except queue.Full:
                    pass                    # a stuck screen; it refetches when it reconnects

    def settings(self):
        app, weather = self.cfg["app"], self.cfg["weather"]
        return {"theme": self.device.get("theme", app["theme"]), "clock": app["clock"],
                "streams": self.streams.enabled,
                "idle_home_seconds": app["idle_home_seconds"],
                "units": weather["units"], "intercom": self.intercom.enabled,
                "alerts": True, "lab": self.lab.enabled,
                "intercom_buttons": self.intercom.enabled and self.cfg["intercom"]["buttons"]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app = None

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _json(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, name):
        path = (WEB / name).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self._json({"error": "not found"}, 404)
        self._send(path, "no-cache")

    def _send(self, path, cache):
        if path is None or not path.is_file():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _events(self):
        """Server-sent events: the state, whenever it changes."""
        app = self.app
        q = app.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            snap = app.snapshot()
            while True:
                if snap is None:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    self.wfile.write(b"event: state\ndata: " + json.dumps(snap).encode() + b"\n\n")
                self.wfile.flush()
                try:
                    snap = q.get(timeout=15)
                except queue.Empty:
                    snap = None
        except OSError:
            pass                            # the page went away
        finally:
            app.unsubscribe(q)

    def _allowed(self, path):
        """Other machines may only post webhooks, unless [app] remote_view is on:
        the dashboard has no sign-in, and shows the calendar."""
        if self.app.cfg["app"]["remote_view"] or path.startswith("/api/hook/"):
            return True
        return ipaddress.ip_address(self.client_address[0].split("%")[0]).is_loopback

    def _hook(self, path, query, raw):
        token = self.app.cfg["webhooks"]["token"]
        if not token:
            return self._json({"error": "webhooks are off: set [webhooks] token in config.toml"}, 403)
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
            or urllib.parse.parse_qs(query).get("token", [""])[0]
        if not hmac.compare_digest(given.encode(), token.encode()):
            return self._json({"error": "wrong or missing token"}, 401)
        try:
            body = json.loads(raw or b"{}")
            if path == "/api/hook/alert":
                self._json(self.app.hooks.alert(body), 201)
            elif path == "/api/hook/grafana":
                self._json(self.app.hooks.grafana(body))
            else:
                self._json({"error": "not found"}, 404)
        except (HookError, ValueError) as exc:
            self._json({"error": str(exc)}, 422)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        app = self.app
        if not self._allowed(path):
            return self._json({"error": "only this Pi may open the dashboard ([app] remote_view)"}, 403)
        try:
            if path == "/api/settings":
                self._json(app.settings())
            elif path == "/api/weather":
                self._json(app.weather.get())
            elif path == "/api/calendar":
                month = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("month")
                if month:
                    year, _, mon = month[0].partition("-")
                    self._json(app.agenda.month(int(year), int(mon)))
                else:
                    self._json(app.agenda.get())
            elif path == "/api/streams":
                self._json(app.streams.get())
            elif path == "/api/device":
                self._json(app.device_info())
            elif path == "/api/state":
                self._json(app.snapshot())
            elif path == "/api/photos":
                self._json(app.photos.list())
            elif path.startswith("/photo/"):
                self._send(app.photos.file(urllib.parse.unquote(path[7:])), "max-age=3600")
            elif path == "/photo-of-the-day":
                self._send(app.photos.potd_image, "max-age=86400")
            elif path == "/api/events":
                self._events()
            elif path.startswith("/api/"):
                self._json({"error": "not found"}, 404)
            else:
                self._file("index.html" if path == "/" else path.lstrip("/"))
        except ConnectionError:
            raise                           # the page went away
        except Exception as exc:
            log.warning("%s: %s", path, exc)
            self._json({"error": str(exc)}, 502)

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        path, app = url.path, self.app
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self.close_connection = True
            return self._json({"error": "too big"}, 413)
        raw = self.rfile.read(length)
        if not self._allowed(path):
            return self._json({"error": "only this Pi may use the dashboard ([app] remote_view)"}, 403)
        if path.startswith("/api/hook/"):
            return self._hook(path, url.query, raw)
        # Only this page may press buttons: another site's page open in a
        # browser can't make it play announcements.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self._json({"error": "cross-site request refused"}, 403)
        try:
            if path == "/api/screen/wake":
                app.screen.wake()
                self._json({"ok": True})
            elif path.startswith("/api/alerts/close/"):
                app.alerts.close(int(path.rsplit("/", 1)[1]))
                self._json({"ok": True})
            elif path == "/api/device":
                self._json(app.set_device(json.loads(raw or b"{}")))
            elif path == "/api/alerts/test":
                app.alerts.show(f"test:{time.time_ns()}", "this screen", "Test",
                                ["This is a test alert."], until_ms=int(time.time() * 1000) + 8000)
                self._json({"ok": True})
            elif path == "/api/alerts/seen":
                app.alerts.seen_all()
                self._json({"ok": True})
            elif path.startswith("/api/intercom/") and not app.cfg["intercom"]["buttons"]:
                self._json({"error": "this screen only receives"}, 403)
            elif path.startswith("/api/intercom/play/"):
                self._json(app.intercom.play(urllib.parse.unquote(path.rsplit("/", 1)[1])), 201)
            elif path == "/api/intercom/stop":
                self._json({"stopped": app.intercom.stop()})
            elif path.startswith("/api/intercom/stop/"):
                self._json({"stopped": app.intercom.stop(path.rsplit("/", 1)[1])})
            else:
                self._json({"error": "not found"}, 404)
        except IntercomError as exc:
            self._json({"error": str(exc)}, exc.status)
        except ValueError:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser(prog="touchframe", description="A touch-screen home dashboard")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--listen", help="address:port (default: [app] listen in the config)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = config.load(args.config)
    host, _, port = (args.listen or cfg["app"]["listen"]).rpartition(":")
    app = App(cfg)
    app.screen.start()
    app.intercom.start()
    app.multicast.start()
    app.lab.start()
    app.warnings.start()
    app.streams.start()

    Handler.app = app
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    server.daemon_threads = True
    log.info("serving http://%s:%s/", host or "127.0.0.1", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
