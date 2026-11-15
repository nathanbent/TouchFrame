"""Photos for the screensaver: a folder of your own, Wikimedia Commons' Picture
of the Day (fetched as InkFrame does, once a day, no API key), and space photos
from NASA's image library (a new set once a day, no API key either)."""
import hashlib
import html
import json
import logging
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "TouchFrame/1.0 (Raspberry Pi wall display)"}
TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
LONGEST = 1600          # photos are scaled down to this before they're sent to the screen
NASA = "https://images-api.nasa.gov/search"
NASA_FILES = "https://images-assets.nasa.gov/"     # the only place its photos are fetched from
NASA_TOPICS = 4         # how many of the topics a day's set is drawn from
NASA_BIGGEST = 12_000_000
# The library also holds NASA's press events, hangars, portraits and diagrams. To keep to
# what telescopes, probes and the space station see, photos are taken only from these
# centers (JPL, Goddard, Johnson), and titles with these words are left out.
NASA_CENTERS = {"JPL", "GSFC", "JSC"}
NASA_SKIP = re.compile(r"\b(portraits?|crew|astronauts?|reception|premiere|briefing|ceremony|press|conference|"
                       r"exhibit|event|visit|tour|award|meeting|students?|team|artist|animation|concept|"
                       r"illustration|diagram|map|chart|graph|logo|patch|poster|infographic|model|"
                       r"cubesats?|deployer|launch|rollout|processing|facility)\b", re.I)


class Photos:
    def __init__(self, cfg, state_dir):
        self.cfg = cfg["screensaver"]
        self.folder = Path(self.cfg["folder"]).expanduser()
        self.cache = state_dir / "photos"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.potd_meta = self.cache / "potd.json"
        self.potd_image = self.cache / "potd.jpg"
        self.nasa_dir = self.cache / "nasa"
        self.nasa_meta = self.cache / "nasa.json"
        self._lock = threading.Lock()
        self._nasa_lock = threading.Lock()
        self._nasa_busy = False
        self._nasa_wait = 0             # after a failure: not before this (time.monotonic)

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
                caption = meta["caption"] + (f" \u00b7 {meta['artist']}" if meta.get("artist") else "")
                photos.append({"src": f"/photo-of-the-day?{meta['date']}", "caption": caption})
        if self.cfg["enabled"] and self.cfg["nasa"]:
            photos += [{"src": "/photo-nasa/" + p["file"], "caption": p["caption"]} for p in self._nasa()]
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

    # ---------- NASA's image library ----------
    def _nasa_read(self):
        try:
            return json.loads(self.nasa_meta.read_text())
        except (OSError, ValueError):
            return {"photos": []}

    def _nasa(self):
        """The space photos on hand. A new set is fetched behind the scenes once a day,
        or when the topics or the count change; until it is ready the last set is shown."""
        meta = self._nasa_read()
        want = {"date": date.today().isoformat(), "topics": self._nasa_topics(),
                "count": self.cfg["nasa_count"]}
        with self._nasa_lock:
            stale = any(meta.get(k) != v for k, v in want.items())
            if stale and want["topics"] and not self._nasa_busy and time.monotonic() >= self._nasa_wait:
                self._nasa_busy = True
                threading.Thread(target=self._nasa_refresh, args=(want, meta), daemon=True).start()
        return meta["photos"]

    def _nasa_topics(self):
        return [t.strip() for t in self.cfg["nasa_topics"] if isinstance(t, str) and t.strip()]

    def nasa_file(self, name):
        """The file to send for /photo-nasa/NAME: only one that is in the current set."""
        if any(p["file"] == name for p in self._nasa_read()["photos"]):
            path = self.nasa_dir / name
            if path.is_file():
                return path
        return None

    def _nasa_refresh(self, want, old):
        try:
            photos = self._nasa_fetch(want["topics"], max(1, min(100, int(want["count"]))),
                                      {p["file"]: p for p in old["photos"]})
            tmp = self.nasa_meta.with_suffix(".part")
            tmp.write_text(json.dumps({**want, "photos": photos}))
            tmp.replace(self.nasa_meta)
            keep = {p["file"] for p in photos}
            for path in self.nasa_dir.iterdir():
                if path.name not in keep:
                    path.unlink(missing_ok=True)
            log.info("NASA photos: %d for %s", len(photos), want["date"])
        except Exception as exc:
            log.warning("NASA photos: %s", exc)
            self._nasa_wait = time.monotonic() + 3600      # the last set stays; ask again in an hour
        finally:
            self._nasa_busy = False

    def _get(self, url, timeout=30):
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _nasa_search(self, topic):
        """Photos for one topic: a page from among its first five hundred, wide enough to show."""
        def page(n):
            query = urllib.parse.urlencode({"q": topic, "media_type": "image", "page_size": 100, "page": n})
            return json.loads(self._get(f"{NASA}?{query}"))["collection"]
        found = page(1)
        pages = min(-(-found.get("metadata", {}).get("total_hits", 0) // 100), 5)
        if pages > 1:
            n = random.randint(1, pages)
            found = found if n == 1 else page(n)
        out = []
        for item in found.get("items", []):
            data = (item.get("data") or [{}])[0]
            url = _nasa_link(item.get("links") or [])
            title = _plain(data.get("title") or "")[:120]
            if not url or not data.get("nasa_id") or data.get("center") not in NASA_CENTERS \
                    or NASA_SKIP.search(title):
                continue
            credit = _plain(data.get("secondary_creator") or data.get("photographer") or "")[:80] or "NASA"
            out.append({"id": data["nasa_id"], "url": url, "caption": f"{title} \u00b7 {credit}" if title else credit})
        return out

    def _nasa_fetch(self, topics, count, have):
        self.nasa_dir.mkdir(parents=True, exist_ok=True)
        found = []
        for topic in random.sample(topics, min(len(topics), NASA_TOPICS)):
            try:
                photos = self._nasa_search(topic)
            except Exception as exc:
                log.warning("NASA photos for %r: %s", topic, exc)
                continue
            random.shuffle(photos)
            found.append(photos)
        if not any(found):
            raise LookupError("nothing found for " + ", ".join(topics))
        # A turn for each topic, so one with many photos does not crowd out the others.
        picked, seen = [], set()
        while any(found) and len(picked) < count:
            for photos in found:
                if photos and len(picked) < count:
                    photo = photos.pop()
                    if photo["id"] in seen:
                        continue
                    seen.add(photo["id"])
                    name = hashlib.sha1(photo["id"].encode()).hexdigest()[:16] + ".jpg"
                    if name not in have or not (self.nasa_dir / name).is_file():
                        try:
                            self._nasa_save(photo["url"], self.nasa_dir / name)
                        except Exception as exc:
                            log.warning("NASA photo %s: %s", photo["id"], exc)
                            continue
                    picked.append({"file": name, "caption": photo["caption"]})
        if not picked:
            raise LookupError("no photo could be fetched")
        return picked

    def _nasa_save(self, url, path):
        tmp = path.with_suffix(".part")
        tmp.write_bytes(self._get(url, 60))
        try:
            from PIL import Image
            with Image.open(tmp) as img:
                if max(img.size) > LONGEST or img.format != "JPEG":
                    img.thumbnail((LONGEST, LONGEST))
                    img.convert("RGB").save(tmp, "JPEG", quality=85)
        except ImportError:
            pass
        tmp.replace(path)


def _nasa_link(links):
    """The copy of a photo to fetch: the smallest that fills the screen well, and only a
    wide one, since a tall photo loses most of itself on a wide screen."""
    wide = []
    for link in links:
        href, width, height = link.get("href", ""), link.get("width"), link.get("height")
        if link.get("render") != "image" or not href.startswith(NASA_FILES) \
                or not href.lower().endswith((".jpg", ".jpeg")) \
                or not isinstance(width, int) or not isinstance(height, int) \
                or width < 1000 or width < height * 1.2 or (link.get("size") or 0) > NASA_BIGGEST:
            continue
        wide.append((width, urllib.parse.quote(href, safe=":/~%")))
    good = [w for w in wide if w[0] >= 1280]
    return min(good)[1] if good else max(wide)[1] if wide else None


def _plain(text):
    """Strip wikitext / HTML down to readable text."""
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)   # [[link|label]]
    text = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", text)         # [url label]
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)                       # leftover templates
    text = re.sub(r"<[^>]+>", "", text)                              # html tags
    text = html.unescape(text.replace("'''", "").replace("''", ""))
    return re.sub(r"\s+", " ", text).strip()
