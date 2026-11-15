"""The screensaver's photos from NASA's image library, with the network faked.

Run with:  python3 -m unittest discover tests
"""
import copy
import io
import json
import urllib.error
import tempfile
import time
import unittest
from pathlib import Path

from touchframe import config
from touchframe.photos import Photos, _icloud_size, _nasa_link, icloud_token

FILES = "https://images-assets.nasa.gov/image/"


def link(name, width, height, **more):
    return {"href": FILES + name, "render": "image", "width": width, "height": height, **more}


def item(nasa_id, title, center="JPL", links=None):
    return {"data": [{"nasa_id": nasa_id, "title": title, "center": center}],
            "links": links or [link(f"{nasa_id}/{nasa_id}~medium.jpg", 1280, 720)]}


def jpeg():
    try:
        from PIL import Image
    except ImportError:
        return b"not a photo"
    out = io.BytesIO()
    Image.new("RGB", (64, 36), "navy").save(out, "JPEG")
    return out.getvalue()


class Link(unittest.TestCase):
    def test_the_smallest_that_fills_the_screen(self):
        links = [link("a~thumb.jpg", 640, 360), link("a~medium.jpg", 1280, 720), link("a~orig.jpg", 4000, 2250)]
        self.assertEqual(_nasa_link(links), FILES + "a~medium.jpg")

    def test_tall_small_and_foreign_photos_are_left(self):
        self.assertIsNone(_nasa_link([link("a~orig.jpg", 2000, 3000)]))
        self.assertIsNone(_nasa_link([link("a~small.jpg", 640, 360)]))
        self.assertIsNone(_nasa_link([link("a~orig.tif", 4000, 2250)]))
        self.assertIsNone(_nasa_link([{**link("a.jpg", 4000, 2250), "href": "https://example.com/a.jpg"}]))
        self.assertIsNone(_nasa_link([link("a~orig.jpg", 4000, 2250, size=50_000_000)]))


class Nasa(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["screensaver"].update(folder=str(self.tmp / "none"), picture_of_the_day=False,
                                       nasa_topics=["nebula"], nasa_count=5)
        self.photos = Photos(self.cfg, self.tmp)
        self.items = [item("PIA1", "Orion Nebula"), item("PIA2", "Crew Portrait"),
                      item("HQ1", "Helix Nebula", center="HQ"), item("PIA3", "Carina &amp; Friends")]
        self.asked = []
        self.photos._get = self.get

    def get(self, url, timeout=30):
        self.asked.append(url)
        if url.startswith("https://images-api.nasa.gov/search?"):
            return json.dumps({"collection": {"metadata": {"total_hits": len(self.items)},
                                              "items": self.items}}).encode()
        return jpeg()

    def list(self):
        """The list once the fetch behind it has finished."""
        self.photos.list()
        for _ in range(200):
            if not self.photos._nasa_busy:
                break
            time.sleep(.01)
        return self.photos.list()["photos"]

    def test_a_set_is_fetched_and_served(self):
        photos = self.list()
        self.assertEqual(sorted(p["caption"] for p in photos),
                         ["Carina & Friends · NASA", "Orion Nebula · NASA"])
        for photo in photos:
            self.assertTrue(photo["src"].startswith("/photo-nasa/"))
            self.assertTrue(self.photos.nasa_file(photo["src"][12:]).is_file())
        self.assertIsNone(self.photos.nasa_file("../nasa.json"))
        self.assertIsNone(self.photos.nasa_file("0000000000000000.jpg"))

    def test_once_a_day_and_again_when_the_topics_change(self):
        self.list()
        asked = len(self.asked)
        self.list()
        self.assertEqual(len(self.asked), asked)
        self.cfg["screensaver"]["nasa_topics"] = ["galaxy"]
        self.items = [item("PIA1", "Orion Nebula"), item("PIA9", "Whirlpool Galaxy")]
        photos = self.list()
        self.assertEqual(len(photos), 2)
        self.assertEqual(len(self.asked), asked + 2)        # the search and PIA9; PIA1 was kept
        self.assertEqual(len(list(self.photos.nasa_dir.iterdir())), 2)

    def test_the_last_set_stays_when_nasa_cant_be_reached(self):
        before = self.list()
        self.cfg["screensaver"]["nasa_topics"] = ["galaxy"]
        self.photos._get = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
        with self.assertLogs("touchframe.photos", "WARNING"):
            self.assertEqual(self.list(), before)

    def test_off(self):
        self.cfg["screensaver"]["nasa"] = False
        self.assertEqual(self.list(), [])
        self.assertEqual(self.asked, [])


TOKEN = "B0a5qXGF1GZqfJ"                    # B, then "0a": server 0 * 62 + 36


def shared(guid, caption="", video=False, sizes=((342, 256), (2049, 1537))):
    return {"photoGuid": guid, "caption": caption, "mediaAssetType": "video" if video else None,
            "derivatives": {str(h): {"checksum": f"{guid}-{w}", "width": str(w), "height": str(h)}
                            for w, h in sizes}}


class AlbumLink(unittest.TestCase):
    def test_the_token_from_a_link(self):
        for link in (TOKEN, f"https://www.icloud.com/sharedalbum/#{TOKEN}",
                     f" https://www.icloud.com/sharedalbum/en-us/#{TOKEN};1234-ABCD "):
            self.assertEqual(icloud_token(link), TOKEN)
        for link in ("", "https://example.com/", "https://www.icloud.com/sharedalbum/#../x", None):
            self.assertEqual(icloud_token(link), "")

    def test_the_size_to_fetch(self):
        sizes = shared("g", sizes=((342, 256), (1400, 1050), (4032, 3024)))["derivatives"]
        self.assertEqual(_icloud_size(sizes), "g-1400")
        self.assertEqual(_icloud_size(shared("g", sizes=((342, 256),))["derivatives"]), "g-342")
        self.assertIsNone(_icloud_size({}))


class ICloud(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["screensaver"].update(folder=str(self.tmp / "none"), picture_of_the_day=False, nasa=False,
                                       icloud_album=f"https://www.icloud.com/sharedalbum/#{TOKEN}")
        self.photos = Photos(self.cfg, self.tmp)
        self.album = [shared("one", "At the lake"), shared("two"), shared("film", video=True)]
        self.asked = []
        self.photos._get = self.get

    def get(self, url, timeout=30, data=None):
        self.asked.append(url)
        if url.startswith("https://p36-sharedstreams.icloud.com/"):      # the server in the link
            raise urllib.error.HTTPError(url, 330, "moved", {}, io.BytesIO(
                json.dumps({"X-Apple-MMe-Host": "p123-sharedstreams.icloud.com"}).encode()))
        if url == f"https://p123-sharedstreams.icloud.com/{TOKEN}/sharedstreams/webstream":
            return json.dumps({"photos": self.album}).encode()
        if url == f"https://p123-sharedstreams.icloud.com/{TOKEN}/sharedstreams/webasseturls":
            guids = json.loads(data)["photoGuids"]
            return json.dumps({"items": {f"{g}-2049": {"url_location": "cvws.icloud-content.com",
                                                       "url_path": f"/S/{g}?o=1"} for g in guids}}).encode()
        if url.startswith("https://cvws.icloud-content.com/S/"):
            return jpeg()
        raise AssertionError(url)

    def list(self):
        self.photos.list()
        for _ in range(200):
            if not self.photos._icloud_busy:
                break
            time.sleep(.01)
        return self.photos.list()["photos"]

    def test_the_album_is_fetched_and_served(self):
        photos = self.list()
        self.assertEqual(sorted(p["caption"] for p in photos), ["", "At the lake"])      # not the video
        for photo in photos:
            self.assertTrue(self.photos.icloud_file(photo["src"][14:]).is_file())
        self.assertIsNone(self.photos.icloud_file("../icloud.json"))
        self.assertEqual(sum("/S/" in url for url in self.asked), 2)

    def test_only_whats_new_is_fetched_and_whats_gone_is_dropped(self):
        self.list()
        asked = len(self.asked)
        self.list()
        self.assertEqual(len(self.asked), asked)                    # not again within the hour
        meta = json.loads(self.photos.icloud_meta.read_text())
        self.photos.icloud_meta.write_text(json.dumps({**meta, "checked": 0}))
        self.album = [shared("two"), shared("three")]
        self.assertEqual(len(self.list()), 2)
        self.assertEqual([url for url in self.asked[asked:] if "/S/" in url],
                         ["https://cvws.icloud-content.com/S/three?o=1"])
        self.assertEqual(len(list(self.photos.icloud_dir.iterdir())), 2)

    def test_a_big_album_is_drawn_from(self):
        self.album = [shared(f"p{n}") for n in range(40)]
        self.cfg["screensaver"]["icloud_count"] = 7
        self.assertEqual(len(self.list()), 7)

    def test_another_albums_photos_are_never_shown(self):
        self.assertEqual(len(self.list()), 2)
        self.cfg["screensaver"]["icloud_album"] = "B0a5qXGF1GZzzz"
        self.photos._get = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
        with self.assertLogs("touchframe.photos", "WARNING"):
            self.assertEqual(self.list(), [])

    def test_the_last_set_stays_when_apple_cant_be_reached(self):
        before = self.list()
        meta = json.loads(self.photos.icloud_meta.read_text())
        self.photos.icloud_meta.write_text(json.dumps({**meta, "checked": 0}))
        self.photos._get = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
        with self.assertLogs("touchframe.photos", "WARNING"):
            self.assertEqual(self.list(), before)

    def test_no_album(self):
        self.cfg["screensaver"]["icloud_album"] = ""
        self.assertEqual(self.list(), [])
        self.assertEqual(self.asked, [])
