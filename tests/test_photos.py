"""The screensaver's photos from NASA's image library, with the network faked.

Run with:  python3 -m unittest discover tests
"""
import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from touchframe import config
from touchframe.photos import Photos, _nasa_link

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
