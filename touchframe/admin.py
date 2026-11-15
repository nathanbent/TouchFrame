"""Who may use the settings page from another device.

There are no passwords. Someone at the screen asks for a code (Settings, then
"Set up from another device"), and types it into the settings page on their
phone or computer. Being able to read the screen is the proof. The device is
then remembered for 30 days by a cookie.
"""
import hashlib
import json
import logging
import secrets
import socket
import threading
import time

log = logging.getLogger(__name__)

CODE_SECONDS = 600          # a code works for this long
CODE_TRIES = 5              # and for this many wrong guesses
SESSION_SECONDS = 30 * 86400
COOKIE = "touchframe_admin"


class AdminError(Exception):
    pass


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Admin:
    def __init__(self, state_dir):
        self.file = state_dir / "admin.json"
        self._lock = threading.Lock()
        self._code = None           # (code, expires, tries left)
        self._sessions = {}         # sha256 of the cookie -> when it expires
        try:
            now = time.time()
            self._sessions = {k: v for k, v in json.loads(self.file.read_text()).items() if v > now}
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _save(self):
        try:
            tmp = self.file.with_suffix(".part")
            tmp.write_text(json.dumps(self._sessions))
            tmp.chmod(0o600)
            tmp.replace(self.file)
        except OSError as exc:
            log.warning("admin: can't save sessions (%s)", exc)

    def new_code(self):
        """A fresh six-digit code, replacing any earlier one."""
        with self._lock:
            code = f"{secrets.randbelow(10 ** 6):06d}"
            self._code = (code, time.time() + CODE_SECONDS, CODE_TRIES)
            return {"code": code, "seconds": CODE_SECONDS}

    def pair(self, code):
        """Trade a code for a session cookie's value. A code works once."""
        given = "".join(c for c in str(code) if c.isdigit())
        with self._lock:
            if not self._code or self._code[1] < time.time():
                self._code = None
                raise AdminError("There is no code to enter yet. On the screen, open Settings "
                                 "and choose \"Set up from another device\".")
            real, expires, tries = self._code
            if not secrets.compare_digest(given.encode(), real.encode()):
                self._code = (real, expires, tries - 1) if tries > 1 else None
                raise AdminError("That code isn't right." if tries > 1 else
                                 "That code isn't right, and it has now been withdrawn. Ask for a new one on the screen.")
            self._code = None
            token = secrets.token_urlsafe(32)
            now = time.time()
            self._sessions = {k: v for k, v in self._sessions.items() if v > now}
            self._sessions[_hash(token)] = now + SESSION_SECONDS
            self._save()
            log.info("admin: a device was paired (%d remembered)", len(self._sessions))
            return token

    def valid(self, token):
        if not token:
            return False
        with self._lock:
            return self._sessions.get(_hash(token), 0) > time.time()

    def forget(self, token=None):
        """Sign one device out, or all of them."""
        with self._lock:
            if token is None:
                self._sessions = {}
            else:
                self._sessions.pop(_hash(token), None)
            self._save()

    def count(self):
        now = time.time()
        with self._lock:
            return sum(1 for v in self._sessions.values() if v > now)


def local_ip():
    """This machine's address on the network, or "" when it has none."""
    try:
        # No packet is sent: connecting a UDP socket only picks the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
        return "" if ip.startswith("127.") else ip
    except OSError:
        return ""


def addresses(port):
    """Where another device can find the settings page: by address, and by name."""
    out = []
    ip = local_ip()
    if ip:
        out.append(f"http://{ip}:{port}/admin")
    name = socket.gethostname().split(".")[0]
    if name and name != "localhost":
        out.append(f"http://{name}.local:{port}/admin")
    return out


def qr_svg(text):
    """A QR code for `text` as SVG, or None when segno isn't installed."""
    try:
        import segno
    except ImportError:
        return None
    import io
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=8, border=3, dark="#12161B", light="#FFFFFF",
                                     xmldecl=False, nl=False)
    return out.getvalue()
