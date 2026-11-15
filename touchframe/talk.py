"""Push to talk: this screen's microphone, relayed to the intercom panel.

The panel takes a live announcement's audio over a WebSocket, and only lets
a browser open one from its own page. This server isn't a browser: it opens
the connection itself, with its token, and passes on the audio the page
posts to it. So the token still never reaches the page.

The audio is what the panel's own page sends: 16-bit little-endian mono PCM
at 48 kHz, in binary messages.
"""
import base64
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse

log = logging.getLogger(__name__)

PREFIX = "/api/intercom/v1"
START_WAIT = 20         # seconds for the panel to say the page has started
QUIET_AFTER = 10        # a page whose screen stops sending audio is ended after this long
MAX_AUDIO = 400_000     # bytes per post from the page: about four seconds


class TalkError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class _Socket:
    """Just enough of a WebSocket client (RFC 6455) for the panel's live route."""

    def __init__(self, url, token, context, timeout=10):
        u = urllib.parse.urlsplit(url)
        secure = u.scheme in ("https", "wss")
        port = u.port or (443 if secure else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        if secure:
            raw = (context or ssl.create_default_context()).wrap_socket(raw, server_hostname=u.hostname)
        self.sock = raw
        self._buf = b""
        self._send_lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [f"GET {u.path} HTTP/1.1", f"Host: {u.netloc}", "Upgrade: websocket",
                 "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        if token:
            lines.append(f"Authorization: Bearer {token}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = self._until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
        code = int(status[1]) if len(status) > 1 and status[1].isdigit() else 0
        if code != 101:
            length = 0
            for line in head.split(b"\r\n")[1:]:
                name, _, value = line.partition(b":")
                if name.strip().lower() == b"content-length" and value.strip().isdigit():
                    length = int(value)
            try:
                message = json.loads(self._exactly(min(length, 10_000)))["error"]
            except Exception:
                message = f"the panel answered {code}"
            self.sock.close()
            raise TalkError(message, code if 400 <= code < 500 else 502)
        self.sock.settimeout(None)

    def _fill(self):
        more = self.sock.recv(65536)
        if not more:
            raise ConnectionError("the panel closed the connection")
        self._buf += more

    def _until(self, mark):
        while mark not in self._buf:
            if len(self._buf) > 65536:
                raise ConnectionError("the panel's answer made no sense")
            self._fill()
        head, _, self._buf = self._buf.partition(mark)
        return head

    def _exactly(self, n):
        while len(self._buf) < n:
            self._fill()
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def send(self, opcode, payload=b""):
        """One frame, masked as a client's must be. 1 is text, 2 binary, 8 close."""
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        mask = os.urandom(4)
        # XOR with the mask repeated, as one big number: quick for 20 ms of audio.
        repeated = (mask * (n // 4 + 1))[:n]
        body = (int.from_bytes(payload, "big") ^ int.from_bytes(repeated, "big")).to_bytes(n, "big")
        with self._send_lock:
            self.sock.sendall(head + mask + body)

    def recv(self):
        """The next message as (opcode, bytes); pings are answered here."""
        message, kind = b"", None
        while True:
            b0, b1 = self._exactly(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._exactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._exactly(8))[0]
            if n > 1_000_000:
                raise ConnectionError("the panel sent too much")
            mask = self._exactly(4) if b1 & 0x80 else None
            data = self._exactly(n)
            if mask:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            if opcode == 9:
                self.send(10, data)
                continue
            if opcode == 10:
                continue
            if opcode == 8:
                return 8, data
            kind = opcode or kind
            message += data
            if b0 & 0x80:
                return kind, message

    def close(self):
        try:
            self.send(8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Talk:
    """One live page at a time from this screen."""

    def __init__(self, intercom, on_change):
        self.intercom = intercom
        self.on_change = on_change
        self._lock = threading.Lock()
        self._ws = None
        self._state = None          # {"announcement", "phase": "starting" | "live", "run"}
        self._last = None           # how the last one ended: {"announcement", "reason", "at_ms"}
        self._heard = 0.0

    def snapshot(self):
        with self._lock:
            return {"now": dict(self._state) if self._state else None,
                    "last": dict(self._last) if self._last else None}

    def start(self, announcement):
        with self._lock:
            if self._state:
                raise TalkError("this screen is already talking", 409)
            self._state = {"announcement": announcement, "phase": "starting", "run": None}
            self._last = None
        self.on_change()
        url = (self.intercom.url + PREFIX + "/live/" + urllib.parse.quote(announcement, safe=""))
        try:
            ws = _Socket(url, self.intercom.cfg["token"], self.intercom._ssl)
        except TalkError as exc:
            self._over(str(exc))
            raise
        except (OSError, ValueError) as exc:
            self._over(f"can't reach the intercom panel ({exc})")
            raise TalkError(f"can't reach the intercom panel ({exc})") from None
        with self._lock:
            self._ws = ws
            self._heard = time.monotonic()
        threading.Thread(target=self._listen, args=(ws,), name="talk", daemon=True).start()
        threading.Thread(target=self._watch, args=(ws,), name="talk-watch", daemon=True).start()
        return self.snapshot()

    def audio(self, pcm):
        """Audio from the page. It may come before the panel says "started":
        the panel keeps it, and plays it after the chime."""
        if len(pcm) > MAX_AUDIO or len(pcm) % 2:
            raise TalkError("that isn't the audio a live page sends", 422)
        with self._lock:
            ws = self._ws
            self._heard = time.monotonic()
        if ws is None:
            raise TalkError("this screen isn't talking", 409)
        try:
            # In the panel's own 20 ms chunks, so its senders never wait on a big one.
            for i in range(0, len(pcm), 1920):
                ws.send(2, pcm[i:i + 1920])
        except OSError:
            raise TalkError("the live page has ended", 409) from None

    def end(self):
        """Stop talking: what's been said plays out, then the end chime."""
        with self._lock:
            ws = self._ws
        if ws is not None:
            try:
                ws.send(1, b"end")
            except OSError:
                pass
        return self.snapshot()

    def _over(self, reason=None):
        with self._lock:
            ws, self._ws = self._ws, None
            if self._state:
                self._last = {"announcement": self._state["announcement"], "reason": reason,
                              "at_ms": int(time.time() * 1000)}
            self._state = None
        if ws is not None:
            ws.close()
        self.on_change()

    def _listen(self, ws):
        reason = None
        try:
            while True:
                kind, data = ws.recv()
                if kind == 8:
                    break
                if kind != 1:
                    continue
                message = json.loads(data)
                if message.get("type") == "started":
                    with self._lock:
                        if self._ws is ws and self._state:
                            self._state.update(phase="live", run=message.get("run"))
                    self.on_change()
                elif message.get("type") == "ended":
                    reason = message.get("reason")
                    if reason == "stopped":
                        reason = None
        except (OSError, ValueError) as exc:
            with self._lock:
                mine = self._ws is ws
            if mine:
                reason = f"lost the intercom panel ({exc})"
        with self._lock:
            mine = self._ws is ws
        if mine:
            if reason:
                log.warning("talk: %s", reason)
            self._over(reason)

    def _watch(self, ws):
        """End a page nobody is feeding: the page was closed, or the browser crashed."""
        began = time.monotonic()
        while True:
            time.sleep(1)
            with self._lock:
                if self._ws is not ws:
                    return
                quiet = time.monotonic() - self._heard
                starting = self._state and self._state["phase"] == "starting"
            if starting and time.monotonic() - began > START_WAIT:
                return self._over("the intercom panel didn't start the page")
            if quiet > QUIET_AFTER:
                log.warning("talk: no audio from the screen for %d s; ending the page", QUIET_AFTER)
                try:
                    ws.send(1, b"end")
                except OSError:
                    pass
                return
