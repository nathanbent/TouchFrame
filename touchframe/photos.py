"""Photos for the screensaver: a folder of your own, and Wikimedia Commons'
Picture of the Day (fetched as InkFrame does, once a day, no API key)."""
import hashlib
import html
import json
import logging
import re
import threading
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "TouchFrame/1.0 (Raspberry Pi wall display)"}
TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
LONGEST = 1600          # photos are scaled down to this before they're sent to the screen


class Photos:
    def __init__(self, cfg, state_dir):
        self.cfg = cfg["screensaver"]
        self.folder = Path(self.cfg["folder"]).expanduser()
        self.cache = state_dir / "photos"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.potd_meta = self.cache / "potd.json"
        self.potd_image = self.cache / "potd.jpg"
        self._lock = threading.Lock()

    # ---------- the folder ----------
    def _files(self):
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.rglob("*")
                      if p.suffix.lower() in TYPES and p.is_file() and not p.name.startswith("."))

    def list(self):
        photos = [{"src": "/photo/" + urllib.parse.quote(p.relative_to(self.folder).as_posix()),
                   "caption": ""} for p in self._files()]
        if self.cfg["enabled"] and self.cfg["picture_of_the_day"]:
            meta = self._potd()
            if meta:
                caption = meta["caption"] + (f" · {meta['artist']}" if meta.get("artist") else "")
                photos.append({"src": f"/photo-of-the-day?{meta['date']}", "caption": caption})
        return {"enabled": self.cfg["enabled"], "idle_minutes": self.cfg["idle_minutes"],
                "seconds": self.cfg["seconds"], "clock": self.cfg["clock"], "photos": photos}

    def file(self, name):
        """The file to send for /photo/NAME: a smaller copy when Pillow is installed
        (a phone's 12-megapixel photo is slow to show on a Pi), else the photo itself."""
        path = (self.folder / name).resolve()
        if self.folder.resolve() not in path.parents or path.suffix.lower() not in TYPES \
                or not path.is_file():
            return None
        if path.suffix.lower() == ".gif":
            return path
        stat = path.stat()
        key = hashlib.sha1(f"{path}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:16]
        small = self.cache / f"{key}.jpg"
        if small.exists():
            return small
        try:
            from PIL import Image, ImageOps
        except ImportError:
            return path
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((LONGEST, LONGEST))
                tmp = small.with_suffix(".part")
                img.convert("RGB").save(tmp, "JPEG", quality=85)
            tmp.replace(small)
            return small
        except Exception as exc:
            log.warning("photo %s: %s", path.name, exc)
            return path

    # ---------- picture of the day ----------
    def _api(self, **params):
        params.update(format="json", formatversion=2)
        req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def _expand(self, template):
        return self._api(action="expandtemplates", prop="wikitext",
                         text="{{" + template + "}}")["expandtemplates"]["wikitext"].strip()

    def _fetch(self, day):
        filename = self._expand(f"Potd/{day.isoformat()}")
        if not filename or "Potd/" in filename:
            raise LookupError(f"no picture of the day for {day}")
        info = self._api(action="query", titles=f"File:{filename}", prop="imageinfo",
                         iiprop="url|extmetadata", iiurlwidth=LONGEST)
        page = info["query"]["pages"][0]["imageinfo"][0]
        extra = page.get("extmetadata", {})
        try:
            caption = _plain(self._expand(f"Potd/{day.isoformat()} (en)"))
        except Exception:
            caption = ""
        if not caption or "Potd/" in caption:
            caption = _plain(extra.get("ObjectName", {}).get("value", "")) or filename
        artist = _plain(extra.get("Artist", {}).get("value", ""))
        req = urllib.request.Request(page.get("thumburl") or page["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            tmp = self.potd_image.with_suffix(".part")
            tmp.write_bytes(resp.read())
        tmp.replace(self.potd_image)
        meta = {"date": day.isoformat(), "caption": caption, "artist": artist}
        self.potd_meta.write_text(json.dumps(meta))
        log.info("picture of the day for %s: %s", day, filename)
        return meta

    def _potd(self):
        """Today's picture; the last one fetched if today's can't be had; None if there is none."""
        with self._lock:
            meta = None
            if self.potd_meta.exists() and self.potd_image.exists():
                meta = json.loads(self.potd_meta.read_text())
            today = date.today()
            if meta and (meta["date"] == today.isoformat() or meta.get("tried") == today.isoformat()):
                return meta
            try:
                try:
                    return self._fetch(today)
                except LookupError:
                    return self._fetch(today - timedelta(days=1))
            except Exception as exc:
                log.warning("picture of the day: %s", exc)
                if meta:                # ask again tomorrow, not every time the list is read
                    meta["tried"] = today.isoformat()
                    self.potd_meta.write_text(json.dumps(meta))
                return meta


def _plain(text):
    """Strip wikitext / HTML down to readable text."""
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)   # [[link|label]]
    text = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", text)         # [url label]
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)                       # leftover templates
    text = re.sub(r"<[^>]+>", "", text)                              # html tags
    text = html.unescape(text.replace("'''", "").replace("''", ""))
    return re.sub(r"\s+", " ", text).strip()
