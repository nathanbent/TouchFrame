"""The web server: the page, and a small JSON API behind it."""
import argparse
import json
import logging
import mimetypes
import queue
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from .agenda import Agenda
from .intercom import Intercom, IntercomError
from .screen import Screen
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
        self.intercom = Intercom(cfg, on_page=self.screen.wake)

    def settings(self):
        app, weather = self.cfg["app"], self.cfg["weather"]
        return {"theme": app["theme"], "clock": app["clock"],
                "idle_home_seconds": app["idle_home_seconds"],
                "units": weather["units"], "intercom": self.intercom.enabled}


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
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _events(self):
        """Server-sent events: the intercom's state, whenever it changes."""
        intercom = self.app.intercom
        q = intercom.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            snap = intercom.snapshot()
            while True:
                if snap is None:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    self.wfile.write(b"event: intercom\ndata: " + json.dumps(snap).encode() + b"\n\n")
                self.wfile.flush()
                try:
                    snap = q.get(timeout=15)
                except queue.Empty:
                    snap = None
        except OSError:
            pass                            # the page went away
        finally:
            intercom.unsubscribe(q)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        app = self.app
        try:
            if path == "/api/settings":
                self._json(app.settings())
            elif path == "/api/weather":
                self._json(app.weather.get())
            elif path == "/api/calendar":
                self._json(app.agenda.get())
            elif path == "/api/intercom":
                self._json(app.intercom.snapshot())
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
        path = urllib.parse.urlsplit(self.path).path
        app = self.app
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        # Only this page may press buttons: another site's page open in a
        # browser can't make it play announcements.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self._json({"error": "cross-site request refused"}, 403)
        try:
            if path == "/api/screen/wake":
                app.screen.wake()
                self._json({"ok": True})
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

    Handler.app = app
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    server.daemon_threads = True
    log.info("serving http://%s:%s/", host or "127.0.0.1", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
