"""The settings layer: what can be changed, how it is checked, and who may change it.

Run with:  python3 -m unittest discover tests
"""
import copy
import json
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from touchframe import admin, config, settings
from touchframe.server import Handler
from touchframe.settings import Settings, SettingsError


def make(toml=""):
    """A Settings over a config.toml with this text, in a state directory of its own."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config.toml").write_text(f'[app]\nstate_dir = "{tmp}"\n' + toml)
    cfg = config.load(tmp / "config.toml")
    return Settings(cfg, config.read(tmp / "config.toml")), tmp


class Help(unittest.TestCase):
    """Every page of the settings page has its description and its help panel."""

    def test_every_section_has_help(self):
        for section in settings.SECTIONS:
            with self.subTest(section["id"]):
                self.assertTrue(section["description"].strip())
                for part in ("use", "give", "get", "limits"):
                    self.assertTrue(section["help"].get(part, "").strip(), f"no '{part}' help")
                self.assertLessEqual(set(section["help"]), {"use", "give", "get", "limits", "more"})
                words = sum(len(text.split()) for text in section["help"].values())
                self.assertLessEqual(words, 200, "page help is over 200 words")

    def test_every_field_names_a_real_setting(self):
        for key, field in settings.FIELDS.items():
            section, _, name = key.partition(".")
            target = config.DEFAULTS[section][name] if name else config.DEFAULTS[section]
            self.assertIsNotNone(target, key)
            self.assertTrue(field["label"])

    def test_no_paths_or_commands(self):
        for key in ("app.state_dir", "multicast.mcast", "screensaver.folder", "intercom.ca_file", "screen.backlight"):
            self.assertNotIn(key, settings.FIELDS)

    def test_ascii_only(self):
        self.assertTrue(json.dumps(settings.SECTIONS, ensure_ascii=False).isascii())


class Saving(unittest.TestCase):
    def test_live_change_applies_at_once(self):
        s, tmp = make()
        seen = []
        s.on_live = seen.extend
        self.assertEqual(s.save({"app.theme": "light"}), {"restart": False})
        self.assertEqual(s.cfg["app"]["theme"], "light")
        self.assertEqual(seen, ["app.theme"])
        self.assertEqual(config.load(tmp / "config.toml")["app"]["theme"], "light")

    def test_other_changes_wait_for_a_restart(self):
        s, tmp = make()
        self.assertEqual(s.save({"intercom.url": "http://127.0.0.1:8651"}), {"restart": True})
        self.assertEqual(s.cfg["intercom"]["url"], "")              # not until it starts again
        self.assertEqual(config.load(tmp / "config.toml")["intercom"]["url"], "http://127.0.0.1:8651")

    def test_unchanged_values_save_nothing(self):
        s, tmp = make()
        self.assertEqual(s.save({"app.theme": "dark", "app.clock": "12h"}), {"restart": False})
        self.assertFalse((tmp / settings.FILE).exists())

    def test_back_to_config_toml_drops_the_override(self):
        s, tmp = make('theme = "light"\n')
        s.save({"app.theme": "dark"})
        self.assertEqual(settings.stored(tmp), {"app.theme": "dark"})
        s.save({"app.theme": "light"})
        self.assertEqual(settings.stored(tmp), {})

    def test_file_is_private(self):
        s, tmp = make()
        s.save({"webhooks.token": "a-long-random-string"})
        self.assertEqual(stat.S_IMODE((tmp / settings.FILE).stat().st_mode), 0o600)

    def test_bad_values_are_refused_and_nothing_is_saved(self):
        s, tmp = make()
        bad = [
            {"app.theme": "pink"}, {"app.theme": True}, {"weather.latitude": 123}, {"weather.latitude": "40"},
            {"calendar.days": 2.5}, {"calendar.days": True}, {"screen.dim_hours": [22]}, {"screen.dim_hours": [7, 7]},
            {"screen.dim_hours": [22, 24]}, {"app.tabs": ["home", "home"]}, {"app.tabs": ["settings"]},
            {"intercom.url": "ftp://x"}, {"intercom.url": "javascript:alert(1)"}, {"zabbix.min_severity": True},
            {"app.state_dir": "/tmp/x"}, {"multicast.mcast": "/bin/sh"}, {"nope.nope": 1}, {"proxmox": [{"name": "x"}]},
            {"alerts.emergency_words": "fire"}, {"weather.location_name": "a\nb"}, {},
            {"app.theme": "light", "weather.latitude": 999},        # one bad value spoils the lot
        ]
        for patch in bad:
            with self.subTest(patch=patch):
                with self.assertRaises(SettingsError):
                    s.save(patch)
        self.assertFalse((tmp / settings.FILE).exists())
        self.assertEqual(s.cfg["app"]["theme"], "dark")

    def test_a_damaged_file_is_ignored(self):
        s, tmp = make()
        (tmp / settings.FILE).write_text('{"app.theme": "pink", "app.clock": "24h", "app.state_dir": "/x", "junk": 1}')
        cfg = config.load(tmp / "config.toml")
        self.assertEqual((cfg["app"]["theme"], cfg["app"]["clock"]), ("dark", "24h"))
        self.assertEqual(cfg["app"]["state_dir"], tmp)
        (tmp / settings.FILE).write_text("not json")
        self.assertEqual(config.load(tmp / "config.toml")["app"]["theme"], "dark")

    def test_network_switch_keeps_the_port(self):
        s, _ = make('listen = "127.0.0.1:9000"\n')
        self.assertEqual(s.save({"app.listen": True}), {"restart": True})
        self.assertEqual(settings.stored(s.file.parent)["app.listen"], "0.0.0.0:9000")


class Secrets(unittest.TestCase):
    TOML = ('[intercom]\ntoken = "mci_secret"\n[calendar]\n'
            'sources = ["https://calendar.example.com/private-abc/basic.ics"]\n'
            '[[proxmox]]\nname = "A"\nurl = "https://192.0.2.10:8006"\ntoken = "u@pve!t=secret-a"\ninterval_seconds = 45\n')

    def test_secrets_are_never_described(self):
        s, _ = make(self.TOML)
        text = json.dumps(s.describe())
        for secret in ("mci_secret", "private-abc", "secret-a"):
            self.assertNotIn(secret, text)
        fields = {f["key"]: f for section in s.describe()["sections"] for f in section["fields"]}
        self.assertEqual(fields["intercom.token"]["value"], {"set": True, "hint": ""})
        self.assertEqual(fields["calendar.sources"]["value"][0]["url"], {"set": True, "hint": "calendar.example.com"})

    def test_a_secret_left_alone_is_kept(self):
        s, tmp = make(self.TOML)
        s.save({"intercom.token": None, "intercom.buttons": False})
        self.assertEqual(config.load(tmp / "config.toml")["intercom"]["token"], "mci_secret")
        s.save({"intercom.token": ""})
        self.assertEqual(config.load(tmp / "config.toml")["intercom"]["token"], "")

    def test_rows_keep_their_secrets_and_extras(self):
        s, tmp = make(self.TOML)
        s.save({"proxmox": [
            {"_row": 0, "name": "A renamed", "url": "https://192.0.2.10:8006", "token": None, "nodes": [], "verify_tls": False},
            {"name": "B", "url": "https://192.0.2.11:8006", "token": "u@pve!t=secret-b", "nodes": ["pve3"], "verify_tls": True},
        ]})
        a, b = config.load(tmp / "config.toml")["proxmox"]
        self.assertEqual((a["name"], a["token"], a["interval_seconds"], a["verify_tls"]), ("A renamed", "u@pve!t=secret-a", 45, False))
        self.assertEqual((b["token"], b["nodes"]), ("u@pve!t=secret-b", ["pve3"]))

    def test_a_row_cannot_borrow_another_rows_secret_by_default(self):
        s, tmp = make(self.TOML)
        s.save({"proxmox": [{"name": "new", "url": "https://192.0.2.99", "token": None}]})
        self.assertEqual(config.load(tmp / "config.toml")["proxmox"][0]["token"], "")

    def test_calendars(self):
        s, tmp = make(self.TOML)
        s.save({"calendar.sources": [
            {"_row": 0, "name": "Family", "url": None, "colour": "green"},
            {"name": "", "url": "https://calendar.example.com/other/basic.ics", "colour": ""},
        ]})
        first, second = config.load(tmp / "config.toml")["calendar"]["sources"]
        self.assertEqual((first["url"], first["colour"]), ("https://calendar.example.com/private-abc/basic.ics", "green"))
        self.assertEqual(second["url"], "https://calendar.example.com/other/basic.ics")
        with self.assertRaises(SettingsError):
            s.save({"calendar.sources": [{"name": "no link", "url": None}]})


class Pairing(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.admin = admin.Admin(self.dir)

    def test_a_code_works_once(self):
        code = self.admin.new_code()["code"]
        token = self.admin.pair(f"{code[:3]} {code[3:]}")
        self.assertTrue(self.admin.valid(token))
        with self.assertRaises(admin.AdminError):
            self.admin.pair(code)

    def test_no_code_no_entry(self):
        for guess in ("", "000000", None):
            with self.assertRaises(admin.AdminError):
                self.admin.pair(guess)
        self.assertFalse(self.admin.valid(""))
        self.assertFalse(self.admin.valid("made-up"))

    def test_wrong_guesses_withdraw_the_code(self):
        code = self.admin.new_code()["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(admin.CODE_TRIES):
            with self.assertRaises(admin.AdminError):
                self.admin.pair(wrong)
        with self.assertRaises(admin.AdminError):
            self.admin.pair(code)

    def test_a_code_expires(self):
        code = self.admin.new_code()["code"]
        self.admin._code = (code, time.time() - 1, admin.CODE_TRIES)
        with self.assertRaises(admin.AdminError):
            self.admin.pair(code)

    def test_sessions_outlive_a_restart_and_can_be_forgotten(self):
        token = self.admin.pair(self.admin.new_code()["code"])
        self.assertNotIn(token, (self.dir / "admin.json").read_text())      # only its hash is kept
        again = admin.Admin(self.dir)
        self.assertTrue(again.valid(token))
        self.assertEqual(again.count(), 1)
        again.forget()
        self.assertFalse(admin.Admin(self.dir).valid(token))


class Access(unittest.TestCase):
    """Which requests another machine may make."""

    def allowed(self, path, local=False, paired=False, remote_view=False):
        fake = SimpleNamespace(_local=lambda: local, _paired=lambda: local or paired,
                               app=SimpleNamespace(cfg={"app": {"remote_view": remote_view}}))
        return Handler._allowed(fake, path) is None

    def test_this_pi_may_do_anything(self):
        for path in ("/", "/api/config", "/api/admin/code", "/api/intercom/play/x"):
            self.assertTrue(self.allowed(path, local=True))

    def test_a_stranger_gets_the_settings_page_and_nothing_behind_it(self):
        for path in ("/admin", "/admin.js", "/tokens.css", "/fonts/Karla-Variable.woff2", "/api/admin/pair",
                     "/api/admin/session", "/api/hook/alert"):
            self.assertTrue(self.allowed(path), path)
        for path in ("/", "/app.js", "/api/settings", "/api/calendar", "/api/events", "/api/config", "/api/admin/restart",
                     "/api/admin/reset", "/api/admin/code", "/api/admin/qr.svg", "/api/admin/forget",
                     "/api/intercom/play/x", "/api/alerts/close/1", "/photo/x.jpg"):
            self.assertFalse(self.allowed(path), path)

    def test_a_paired_device_gets_the_settings_only(self):
        for path in ("/api/config", "/api/admin/restart", "/api/admin/reset", "/api/admin/signout"):
            self.assertTrue(self.allowed(path, paired=True), path)
        for path in ("/", "/api/calendar", "/api/admin/code", "/api/admin/qr.svg", "/api/admin/forget", "/api/intercom/play/x"):
            self.assertFalse(self.allowed(path, paired=True), path)

    def test_remote_view_opens_the_dashboard_but_not_the_settings(self):
        for path in ("/", "/api/calendar", "/api/events", "/api/intercom/play/x"):
            self.assertTrue(self.allowed(path, remote_view=True), path)
        for path in ("/api/config", "/api/admin/code", "/api/admin/restart", "/api/admin/forget"):
            self.assertFalse(self.allowed(path, remote_view=True), path)


if __name__ == "__main__":
    unittest.main()
