"""The web server: the page, and a small JSON API behind it."""
import argparse
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admin, config
from .admin import Admin, AdminError
from .agenda import Agenda
from .alerts import Alerts
from .hooks import HookError, Hooks
from .intercom import Intercom, IntercomError
from .lab import Lab
from .multicast import Multicast
from .photos import Photos
from .screen import Screen
from .settings import Settings, SettingsError
from .streams import Streams
from .talk import Talk, TalkError
from .warnings import Warnings
from .weather import Weather

log = logging.getLogger("touchframe")

WEB = Path(__file__).parent / "web"

# What a device that isn't this Pi may fetch without [app] remote_view: the
# settings page and what it is built from. Its API asks for a paired device.
ADMIN_FILES = {"/admin", "/admin.html", "/admin.js", "/admin.css", "/tokens.css"}
ADMIN_OPEN = {"/api/admin/pair", "/api/admin/session"}
ADMIN_API = {"/api/config", "/api/admin/restart", "/api/admin/reset", "/api/admin/signout"}
SCREEN_ONLY = {"/api/admin/code", "/api/admin/qr.svg", "/api/admin/forget"}


class App:
    def __init__(self, cfg, base, listen):
        self.cfg = cfg
        self.listen = listen
        state = cfg["app"]["state_dir"]
        self.state_dir = state
        self.settings_store = Settings(cfg, base)
        self.settings_store.on_live = self._live
        self.admin = Admin(state)
        self.weather = Weather(cfg, state)
        self.agenda = Agenda(cfg, state)
        self.screen = Screen(cfg)
        self.photos = Photos(cfg, state)
        self.alerts = Alerts(cfg, state, on_page=self.screen.wake)
        self.alerts.on_change = self.publish
        self.intercom = Intercom(cfg, self.alerts, self.publish)
        self.talk = Talk(self.intercom, self.publish)
        self.multicast = Multicast(cfg, self.alerts, self.publish)
        self._subs = set()
        self._lock = threading.Lock()
        self.lab = Lab(cfg, self.alerts, self.publish)
        self.hooks = Hooks(cfg, self.alerts)
        self.warnings = Warnings(cfg, state, self.alerts, self.publish)
        self.streams = Streams(cfg)
        self.emoji_font = self._emoji_font()
        if self.emoji_font is False:
            log.warning("no emoji font: emoji in text pages will be empty boxes. Install one "
                        "(sudo apt-get install fonts-noto-color-emoji), then restart the browser")
        # Changes each time the server starts. A page that sees a new one reloads
        # itself, so an update or a config change shows without touching the screen.
        self.started = str(time.time_ns())
        try:
            self.version = subprocess.run(
                ["git", "-C", str(WEB), "log", "-1", "--format=%h %cs"], capture_output=True,
                text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            self.version = ""
        self._adopt_device_file()

    def _adopt_device_file(self):
        """An earlier version kept the screen's own brightness and theme in device.json.
        They now live with the other settings; carry them over once."""
        old = self.state_dir / "device.json"
        try:
            device = json.loads(old.read_text())
        except (OSError, ValueError):
            return
        patch = {}
        if device.get("theme") in ("dark", "light", "auto"):
            patch["app.theme"] = device["theme"]
        if isinstance(device.get("brightness"), int):
            patch["screen.brightness"] = max(5, min(100, device["brightness"]))
        try:
            if patch:
                self.settings_store.save(patch)
            old.unlink()
        except (SettingsError, OSError) as exc:
            log.warning("device.json: %s", exc)

    def snapshot(self):
        """Everything that changes by itself: the intercom, alerts and multicast groups."""
        return {"intercom": self.intercom.snapshot() | {"talk": self.talk.snapshot()},
                "alerts": self.alerts.snapshot(),
                "multicast": self.multicast.snapshot(), "lab": self.lab.snapshot(),
                "warnings": self.warnings.snapshot(), "started": self.started,
                "now_ms": int(time.time() * 1000)}

    def subscribe(self):
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, event="state"):
        item = (event, self.snapshot() if event == "state" else {})
        with self._lock:
            for q in self._subs:
                try:
                    q.put_nowait(item)
                except queue.Full:
                    pass                    # a stuck screen; it refetches when it reconnects

    def network(self):
        """Whether other devices can connect at all."""
        return self.listen[0] not in ("127.0.0.1", "localhost", "::1")

    def _emoji_font(self):
        """Whether this machine has a font with emoji in it (None if that can't be
        told). Without one, Chromium draws the emoji in text pages as empty boxes."""
        try:
            out = subprocess.run(["fc-list", ":lang=und-zsye", "family"], capture_output=True,
                                 text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return bool(out.stdout.strip())

    def settings(self, can_edit=True):
        app, weather, screen, saver = (self.cfg[k] for k in ("app", "weather", "screen", "screensaver"))
        return {"theme": app["theme"], "clock": app["clock"],
                "idle_home_seconds": app["idle_home_seconds"], "tabs": app["tabs"],
                "streams": self.streams.enabled,
                "units": weather["units"], "intercom": self.intercom.enabled,
                "alerts": True, "lab": self.lab.enabled, "emoji_font": self.emoji_font,
                "intercom_buttons": self.intercom.enabled and self.cfg["intercom"]["buttons"],
                "intercom_talk": self.intercom.enabled and self.cfg["intercom"]["buttons"]
                and self.cfg["intercom"]["talk"],
                # For the Settings tab.
                "can_edit": can_edit, "network": self.network(), "started": self.started,
                "hostname": socket.gethostname(), "version": self.version,
                "address": admin.local_ip(),
                "backlight": self.screen.dir is not None,
                "brightness": screen["brightness"], "dim_hours": screen["dim_hours"],
                "dim_brightness": screen["dim_brightness"],
                "saver": {k: saver[k] for k in ("enabled", "idle_minutes", "seconds",
                                                "picture_of_the_day", "clock")},
                "emergency_stays": self.cfg["alerts"]["emergency_stays"],
                "week_start": self.cfg["calendar"]["week_start"],
                "paired": self.admin.count()}

    def _live(self, keys):
        """Settings that take effect without a restart were changed."""
        state = self.state_dir
        if any(k.startswith("weather.") for k in keys):
            if "weather.units" in keys or "weather.latitude" in keys or "weather.longitude" in keys:
                (state / "weather.json").unlink(missing_ok=True)    # the saved forecast is for the old ones
            self.weather = Weather(self.cfg, state)
        if any(k.startswith("calendar.") for k in keys):
            self.agenda = Agenda(self.cfg, state)
        if any(k.startswith("screen.") for k in keys):
            self.screen.apply()
        self.publish("settings")

    def restart(self, delay=0.5):
        """Start again with the new settings, once the answer to this request has gone out."""
        def go():
            log.info("restarting to apply settings")
            self.multicast.stop()
            self.streams.stop()
            logging.shutdown()
            os.execv(sys.executable, [sys.executable, "-m", "touchframe", *sys.argv[1:]])
        timer = threading.Timer(delay, go)
        timer.daemon = True
        timer.start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app = None

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _json(self, body, status=200, headers=()):
        self._bytes(json.dumps(body).encode(), "application/json", "no-store", status, headers)

    def _bytes(self, data, kind, cache, status=200, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in headers:
            self.send_header(name, value)
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
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind.endswith("javascript"):
            kind += "; charset=utf-8"
        self._bytes(path.read_bytes(), kind, cache)

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
            # The settings first, so a page that outlived a restart catches up.
            self.wfile.write(b"event: settings\ndata: {}\n\n")
            item = ("state", app.snapshot())
            while True:
                if item is None:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    self.wfile.write(f"event: {item[0]}\ndata: ".encode() + json.dumps(item[1]).encode() + b"\n\n")
                self.wfile.flush()
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    item = None
        except OSError:
            pass                            # the page went away
        finally:
            app.unsubscribe(q)

    def _local(self):
        return ipaddress.ip_address(self.client_address[0].split("%")[0]).is_loopback

    def _paired(self):
        """This Pi's own screen, or a device that has entered a code from it."""
        if self._local():
            return True
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == admin.COOKIE and self.app.admin.valid(value):
                return True
        return False

    def _allowed(self, path):
        """Whether this request may go ahead; if not, the error to send.

        Other machines may post webhooks (with the token) and use the settings
        page (once paired). They can't open the dashboard, which has no sign-in
        and shows the calendar, unless [app] remote_view is on; and even then
        they can't change settings or ask for a pairing code."""
        if self._local() or path.startswith("/api/hook/"):
            return None
        if path in SCREEN_ONLY:
            return "only the screen itself can do that", 403
        if path in ADMIN_API:
            return None if self._paired() else ("enter the code from the screen first", 401)
        if path in ADMIN_FILES or path in ADMIN_OPEN or path.startswith("/fonts/"):
            return None
        if self.app.cfg["app"]["remote_view"]:
            return None
        return "only this Pi may open the dashboard ([app] remote_view)", 403

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
        refused = self._allowed(path)
        if refused:
            return self._json({"error": refused[0]}, refused[1])
        try:
            if path == "/api/settings":
                self._json(app.settings(self._paired()))
            elif path == "/api/config":
                self._json(app.settings_store.describe())
            elif path == "/api/admin/session":
                self._json({"paired": self._paired(), "local": self._local()})
            elif path == "/api/admin/qr.svg":
                urls = admin.addresses(app.listen[1])
                svg = admin.qr_svg(urls[0]) if urls else None
                if svg is None:
                    return self._json({"error": "no QR code (segno isn't installed)"}, 404)
                self._bytes(svg, "image/svg+xml", "no-store")
            elif path == "/admin":
                self._file("admin.html")
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
            elif path == "/api/state":
                self._json(app.snapshot())
            elif path == "/api/photos":
                self._json(app.photos.list())
            elif path.startswith("/photo/"):
                self._send(app.photos.file(urllib.parse.unquote(path[7:])), "max-age=3600")
            elif path.startswith("/received/"):
                self._send(app.multicast.file(urllib.parse.unquote(path[10:])), "max-age=3600")
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
        refused = self._allowed(path)
        if refused:
            return self._json({"error": refused[0]}, refused[1])
        if path.startswith("/api/hook/"):
            return self._hook(path, url.query, raw)
        # Only this page may press buttons: another site's page open in a
        # browser can't make it play announcements.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self._json({"error": "cross-site request refused"}, 403)
        try:
            if path.startswith("/api/admin/") or path == "/api/config":
                return self._admin(path, raw)
            if path == "/api/screen/wake":
                app.screen.wake()
                self._json({"ok": True})
            elif path.startswith("/api/alerts/close/"):
                app.alerts.close(int(path.rsplit("/", 1)[1]))
                self._json({"ok": True})
            elif path == "/api/alerts/test":
                app.alerts.show(f"test:{time.time_ns()}", "this screen", "Test",
                                ["This is a test alert."], until_ms=int(time.time() * 1000) + 8000)
                self._json({"ok": True})
            elif path == "/api/alerts/seen":
                app.alerts.seen_all()
                self._json({"ok": True})
            elif path.startswith("/api/intercom/") and not app.cfg["intercom"]["buttons"]:
                self._json({"error": "this screen only receives"}, 403)
            elif path.startswith("/api/intercom/talk/") and not app.cfg["intercom"]["talk"]:
                self._json({"error": "push to talk is off on this screen ([intercom] talk)"}, 403)
            elif path.startswith("/api/intercom/talk/start/"):
                self._json(app.talk.start(urllib.parse.unquote(path.rsplit("/", 1)[1])), 201)
            elif path == "/api/intercom/talk/audio":
                app.talk.audio(raw)
                self._json({"ok": True})
            elif path == "/api/intercom/talk/end":
                self._json(app.talk.end())
            elif path.startswith("/api/intercom/play/"):
                self._json(app.intercom.play(urllib.parse.unquote(path.rsplit("/", 1)[1])), 201)
            elif path == "/api/intercom/stop":
                self._json({"stopped": app.intercom.stop()})
            elif path.startswith("/api/intercom/stop/"):
                self._json({"stopped": app.intercom.stop(path.rsplit("/", 1)[1])})
            else:
                self._json({"error": "not found"}, 404)
        except (IntercomError, TalkError) as exc:
            self._json({"error": str(exc)}, exc.status)
        except (AdminError, SettingsError) as exc:
            self._json({"error": str(exc)}, 422)
        except ValueError:
            self._json({"error": "not found"}, 404)

    def _admin(self, path, raw):
        """Settings, and the devices allowed to change them. _allowed() has already
        checked that this is the screen or a paired device, except for pairing itself."""
        app = self.app
        body = json.loads(raw or b"{}")
        if path == "/api/config":
            if not self._paired():          # a remote_view visitor: may look, not change
                return self._json({"error": "enter the code from the screen first"}, 401)
            result = app.settings_store.save(body)
            if result["restart"]:
                app.restart()
            self._json(result)
        elif path == "/api/admin/pair":
            token = app.admin.pair(body.get("code", "") if isinstance(body, dict) else "")
            self._json({"paired": True}, headers=[("Set-Cookie",
                       f"{admin.COOKIE}={token}; Max-Age={admin.SESSION_SECONDS}; Path=/; HttpOnly; SameSite=Strict")])
        elif path == "/api/admin/code":
            if not app.network():
                return self._json({"error": "other devices can't connect yet"}, 409)
            urls = admin.addresses(app.listen[1])
            self._json({**app.admin.new_code(), "urls": urls, "qr": bool(urls) and admin.qr_svg(urls[0]) is not None})
        elif path == "/api/admin/forget":
            app.admin.forget()
            app.publish("settings")
            self._json({"ok": True})
        elif path == "/api/admin/signout":
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == admin.COOKIE:
                    app.admin.forget(value)
            self._json({"ok": True}, headers=[("Set-Cookie", f"{admin.COOKIE}=; Max-Age=0; Path=/")])
        elif path == "/api/admin/restart":
            app.restart()
            self._json({"restart": True})
        elif path == "/api/admin/reset":
            app.settings_store.reset()
            app.restart()
            self._json({"restart": True})
        else:
            self._json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A page that went away mid-answer (a reload, a restart) isn't worth a traceback."""
        if isinstance(sys.exception(), ConnectionError):
            return
        super().handle_error(request, client_address)


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
    host, port = host or "127.0.0.1", int(port)
    app = App(cfg, config.read(args.config), (host, port))
    app.screen.start()
    app.intercom.start()
    app.multicast.start()
    app.lab.start()
    app.warnings.start()
    app.streams.start()

    Handler.app = app
    server = Server((host, port), Handler)
    log.info("serving http://%s:%s/", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
