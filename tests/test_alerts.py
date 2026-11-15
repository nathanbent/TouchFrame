"""The alerts history and its archive.

Run with:  python3 -m unittest discover tests
"""
import tempfile
import unittest
from pathlib import Path

from touchframe import alerts
from touchframe.alerts import Alerts

CFG = {"alerts": {"emergency_words": [], "emergency_stays": True}}


def make():
    tmp = Path(tempfile.mkdtemp())
    return Alerts(CFG, tmp), tmp


def history(al):
    return al.snapshot()["history"]


class Archive(unittest.TestCase):
    def test_archive_moves_one_and_counts_it_seen(self):
        al, _ = make()
        al.show("a", "test", "A", ["first"])
        al.show("b", "test", "B", ["second"])
        self.assertEqual(al.snapshot()["unseen"], 2)
        first = next(e for e in history(al) if e["name"] == "A")
        al.archive(first["id"])
        snap = al.snapshot()
        self.assertEqual(snap["unseen"], 1)
        self.assertEqual([e["name"] for e in snap["history"] if e.get("archived")], ["A"])
        self.assertEqual([e["name"] for e in snap["history"] if not e.get("archived")], ["B"])

    def test_restore_brings_it_back(self):
        al, _ = make()
        al.show("a", "test", "A", ["first"])
        alert_id = history(al)[0]["id"]
        al.archive(alert_id)
        al.archive(alert_id, archived=False)
        self.assertFalse(history(al)[0]["archived"])
        self.assertTrue(history(al)[0]["seen"])

    def test_archive_all(self):
        al, _ = make()
        for n in range(3):
            al.show(f"k{n}", "test", f"N{n}", [f"text {n}"])
        al.archive_all()
        snap = al.snapshot()
        self.assertTrue(all(e["archived"] for e in snap["history"]))
        self.assertEqual(snap["unseen"], 0)

    def test_kept_across_restarts(self):
        al, tmp = make()
        al.show("a", "test", "A", ["first"])
        al.archive(history(al)[0]["id"])
        again = Alerts(CFG, tmp)
        self.assertTrue(history(again)[0]["archived"])

    def test_archive_doesnt_push_out_the_recent_ones(self):
        al, _ = make()
        for n in range(alerts.KEEP):
            al.show(f"old{n}", "test", "Old", [f"old {n}"])
        al.archive_all()
        for n in range(alerts.KEEP + 5):
            al.show(f"new{n}", "test", "New", [f"new {n}"])
        snap = history(al)
        self.assertEqual(sum(1 for e in snap if not e.get("archived")), alerts.KEEP)
        self.assertEqual(sum(1 for e in snap if e.get("archived")), alerts.KEEP)


if __name__ == "__main__":
    unittest.main()
