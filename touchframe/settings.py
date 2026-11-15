"""Settings that can be changed without editing config.toml: from the screen's
Settings tab, or from the settings page on another device.

config.toml stays the file you write by hand. Changes made here are kept
beside the other state, in settings.json, and are laid over it when the
server starts. Only the settings listed in SECTIONS can be changed this way.
File paths and commands (the photo folder, the mcast command, a certificate
file) are left to config.toml on purpose: a web page should not be able to
point the server at a file or a program.
"""
import copy
import json
import logging
import os
import urllib.parse

log = logging.getLogger(__name__)

FILE = "settings.json"
TABS = ["home", "calendar", "alerts", "streams", "lab", "intercom"]


class SettingsError(ValueError):
    """What was sent can't be saved; the message says which setting and why."""


def F(key, label, kind, hint="", **more):
    return {"key": key, "label": label, "type": kind, "hint": hint, **more}


def _service(kind, title, *fields):
    """A list of the same kind of thing ([[proxmox]] ... in config.toml)."""
    return F(kind, title, "rows", fields=[
        F("name", "Name", "text", "Shown on its tile.", placeholder=title),
        F("url", "Address", "text", url=True, required=True, placeholder="https://192.0.2.10"),
        *fields,
        F("verify_tls", "Check its certificate", "bool",
          "Turn off for a self-signed certificate.", default=True),
    ])


SECTIONS = [
    {
        "id": "display", "title": "Display",
        "description": "How the screen looks, and what it does when nobody is touching it.",
        "help": {
            "use": "the theme, the clock, the brightness and the order of the tabs. The same "
                   "settings are on the screen itself, under Settings.",
            "give": "choices and numbers. Dimming takes the hour it starts and the hour it "
                    "ends, on a 24-hour clock: 22 and 7 dims from 22:00 to 07:00.",
            "get": "a change on the screen within a second or two. Nothing here needs a restart.",
            "limits": "Brightness and dimming only work on a display with a backlight the Pi "
                      "can control, such as the Raspberry Pi Touch Display. A touch or an alert "
                      "always brings a dimmed screen back to full brightness for a while.",
        },
        "fields": [
            F("app.theme", "Theme", "choice", "Auto is light by day and dark at night, by the weather's sunrise and sunset.",
              options=[["dark", "Dark"], ["light", "Light"], ["auto", "Auto"]]),
            F("app.clock", "Clock", "choice", options=[["12h", "12-hour"], ["24h", "24-hour"]]),
            F("app.tabs", "Tabs", "tabs", "The tabs to show, in order. A tab also needs its own "
              "settings filled in before it appears. Alerts still take over the screen when the Alerts tab is hidden."),
            F("app.idle_home_seconds", "Back to the first tab after", "int",
              "Untouched for this long, the screen returns to its first tab. 0 leaves it where it is.",
              min=0, max=3600, unit="seconds"),
            F("screen.brightness", "Brightness", "int", min=5, max=100, unit="%", heading="Brightness"),
            F("screen.dim_hours", "Dim at night", "hours",
              "The hour dimming starts and the hour it ends. Leave both empty for no dimming."),
            F("screen.dim_brightness", "Brightness while dimmed", "int", min=1, max=100, unit="%"),
            F("screen.wake_seconds", "Stay bright after a touch for", "int", min=5, max=3600, unit="seconds"),
        ],
    },
    {
        "id": "weather", "title": "Weather",
        "description": "Where the forecast is for, and the units it is shown in.",
        "help": {
            "use": "setting your location. The forecast comes from Open-Meteo, which needs no account or key.",
            "give": "latitude and longitude as decimal degrees, for example 40.7128 and -74.0060. "
                    "West and south are negative. The name is only a label for the corner of the screen.",
            "get": "a new forecast straight away. In the US, active Weather Service alerts for the "
                   "location show as a banner on Home.",
            "limits": "There is no search by place name: look the coordinates up on a map first.",
        },
        "fields": [
            F("weather.location_name", "Name", "text", "Shown in the corner of the screen.", placeholder="Home"),
            F("weather.latitude", "Latitude", "float", min=-90, max=90, placeholder="40.7128"),
            F("weather.longitude", "Longitude", "float", min=-180, max=180, placeholder="-74.0060"),
            F("weather.units", "Units", "choice", options=[["imperial", "Fahrenheit, mph"], ["metric", "Celsius, km/h"]]),
            F("weather.interval_minutes", "Check every", "int", min=5, max=240, unit="minutes"),
            F("weather.air_quality", "Show the air quality index", "bool",
              "Under the date. The US index with Fahrenheit, the European one with Celsius."),
            F("weather.warnings", "Show Weather Service alerts", "bool",
              "US National Weather Service alerts for this location, as a banner on Home. Nothing outside the US. Changing this restarts TouchFrame.",
              heading="Weather alerts"),
            F("weather.page_severity", "New alerts take over the screen from", "choice",
              options=[["extreme", "Extreme"], ["severe", "Severe"], ["moderate", "Moderate"], ["off", "Never"]]),
            F("weather.page_seconds", "And stay for", "int", min=5, max=3600, unit="seconds"),
        ],
    },
    {
        "id": "calendar", "title": "Calendar",
        "description": "The calendars shown on the Home and Calendar tabs.",
        "help": {
            "use": "adding or removing calendars. Any calendar that offers an iCal (.ics) link works.",
            "give": "each calendar's private iCal link. In Google Calendar it is under Settings, "
                    "the calendar, Integrate calendar, \"Secret address in iCal format\", and "
                    "looks like https://calendar.google.com/calendar/ical/.../basic.ics.",
            "get": "the events on the screen within a few seconds. Recurring events are expanded.",
            "limits": "A link is as good as a password to that calendar, so a saved link is never "
                      "shown again here: only the site it points to. To change one, remove it and add it again. "
                      "The screen can look four months ahead.",
        },
        "fields": [
            F("calendar.sources", "Calendars", "rows", fields=[
                F("name", "Name", "text", "Only to tell them apart here.", placeholder="Family"),
                F("url", "iCal link", "secret", required=True,
                  placeholder="https://calendar.google.com/calendar/ical/.../basic.ics"),
                F("colour", "Colour", "choice", options=[["", "Automatic"], ["blue", "Blue"], ["red", "Red"],
                                                          ["green", "Green"], ["orange", "Orange"], ["purple", "Purple"]]),
            ]),
            F("calendar.days", "Agenda shows the next", "int", min=1, max=31, unit="days"),
            F("calendar.week_start", "Week starts on", "choice", options=[["sunday", "Sunday"], ["monday", "Monday"]]),
            F("calendar.interval_minutes", "Check every", "int", min=1, max=240, unit="minutes"),
        ],
    },
    {
        "id": "screensaver", "title": "Screensaver",
        "description": "Photos that fill the screen when it has been left alone.",
        "help": {
            "use": "turning the photos on or off and setting their timing.",
            "give": "the photos themselves go in ~/Pictures/touchframe on the Pi (subfolders too), "
                    "for example with scp. JPEG, PNG, WebP and GIF work.",
            "get": "photos in a random order, with the time in the corner. A tap brings the "
                   "dashboard back, a swipe changes the photo, and an alert shows over them.",
            "limits": "The folder is set in config.toml, not here. With no photos and the "
                      "picture of the day off, the dashboard just stays up.",
        },
        "fields": [
            F("screensaver.enabled", "Show photos", "bool"),
            F("screensaver.idle_minutes", "Start after", "int", "0 never starts them.", min=0, max=240, unit="minutes"),
            F("screensaver.seconds", "Each photo stays for", "int", min=5, max=3600, unit="seconds"),
            F("screensaver.picture_of_the_day", "Wikimedia Commons' picture of the day", "bool",
              "Adds one new photo a day, with its caption."),
            F("screensaver.clock", "Show the time", "bool"),
        ],
    },
    {
        "id": "alerts", "title": "Alerts",
        "description": "Text that takes over the screen: where it comes from and how long it stays.",
        "help": {
            "use": "choosing how emergencies behave, listening to multicast text channels, and "
                   "letting other systems post alerts over HTTP.",
            "give": "multicast groups as ADDRESS:PORT, such as 239.192.3.1:11000. A webhook token "
                    "is any long random string; the sender puts it in an Authorization header.",
            "get": "alerts on the screen and in the Alerts tab, each marked with how it arrived.",
            "limits": "Multicast needs the mcast command from mcast-tools on the Pi, and a restart "
                      "of TouchFrame to join a new group (saving does that). Webhooks from other "
                      "machines need \"Reachable from other devices\" turned on under Access.",
            "more": "The README has the webhook fields and a Grafana example.",
        },
        "fields": [
            F("alerts.emergency_stays", "Emergencies stay until tapped", "bool",
              "Off lets an emergency come down by itself like any other alert."),
            F("alerts.emergency_words", "Emergency words", "list",
              "An alert with any of these words is treated as an emergency.", placeholder="fire, lockdown"),
            F("multicast.groups", "Multicast groups", "list", placeholder="239.192.3.1:11000", heading="Multicast"),
            F("multicast.name", "Name reported to senders", "text", "Empty uses the Pi's host name.", maxlen=60),
            F("multicast.hold_seconds", "A multicast page stays for", "int", min=3, max=3600, unit="seconds"),
            F("multicast.monitor", "Show the Streams tab", "bool",
              "Measures what arrives on these groups and on streams announced with SAP."),
            F("multicast.watch", "Other groups to measure", "list", "Your audio zones, say.", placeholder="239.192.2.1:10000"),
            F("webhooks.token", "Webhook token", "secret", "Empty turns webhooks off.", heading="Webhooks"),
            F("webhooks.hold_seconds", "A webhook alert stays for", "int",
              "Unless the sender says how long.", min=3, max=86400, unit="seconds"),
            F("webhooks.emergency_severities", "Grafana severities shown as emergencies", "list", placeholder="disaster"),
            F("webhooks.high_severities", "Grafana severities shown as high priority", "list", placeholder="critical, high"),
        ],
    },
    {
        "id": "intercom", "title": "Intercom",
        "description": "The link to an mcast intercom panel: its buttons and its text pages.",
        "help": {
            "use": "connecting this screen to the panel, or limiting what it shows.",
            "give": "the panel's address, such as http://127.0.0.1:8651 on the same Pi or "
                    "https://pager.home.arpa:8651 on another machine, and an operator access "
                    "token made on the panel under Settings, Access tokens.",
            "get": "an Intercom tab with the panel's buttons, and its text pages as alerts.",
            "limits": "A panel on another machine uses a self-signed certificate. Either name a "
                      "copy of its certificate as ca_file in config.toml, or turn off the "
                      "certificate check here (home network only). Saving restarts TouchFrame, "
                      "which takes a few seconds.",
        },
        "fields": [
            F("intercom.url", "Panel address", "text", "Empty turns the intercom off.", url=True,
              placeholder="http://127.0.0.1:8651"),
            F("intercom.token", "Access token", "secret", placeholder="mci_..."),
            F("intercom.verify_tls", "Check the panel's certificate", "bool"),
            F("intercom.buttons", "Show the buttons", "bool",
              "Off makes this screen receive only: text pages still show, but nothing can be played from it."),
            F("intercom.zones", "Only these zones' text pages", "list", "Zone ids. Empty shows every zone.", placeholder="kitchen"),
            F("intercom.categories", "Only these categories' buttons", "list",
              "Empty shows them all. \"other\" is the uncategorized ones.", placeholder="family"),
        ],
    },
    {
        "id": "lab", "title": "Lab",
        "description": "The services on the Lab tab. The tab appears when at least one is filled in.",
        "help": {
            "use": "adding the systems you want at a glance: Proxmox, AdGuard Home, UniFi, Plex, "
                   "Prometheus and Zabbix.",
            "give": "each one's address and a read-only credential. Proxmox tokens are written "
                    "USER@REALM!TOKENID=SECRET and need the PVEAuditor role. Zabbix needs an API "
                    "token and version 6.4 or later.",
            "get": "a tile for each service, and a Problems page for Prometheus and Zabbix.",
            "limits": "Saved tokens and passwords are never shown again; leave a field empty to "
                      "keep what is saved. Saving restarts TouchFrame, which takes a few seconds.",
            "more": "The README says where to find each credential.",
        },
        "fields": [
            _service("proxmox", "Proxmox",
                     F("token", "API token", "secret", placeholder="user@pve!name=secret"),
                     F("nodes", "Only these nodes", "list", "Empty shows every node.", placeholder="pve1")),
            _service("adguard", "AdGuard Home",
                     F("username", "User name", "text"), F("password", "Password", "secret")),
            _service("unifi", "UniFi",
                     F("key", "API key", "secret", "Or a local view-only user, below."),
                     F("username", "User name", "text"), F("password", "Password", "secret"),
                     F("site", "Site", "text", placeholder="default", default="default")),
            _service("plex", "Plex", F("token", "X-Plex-Token", "secret")),
            F("prometheus.url", "Prometheus address", "text", "Empty turns it off.", url=True,
              placeholder="http://192.0.2.20:9090", heading="Prometheus"),
            F("prometheus.token", "Prometheus token", "secret", "Only if yours needs one."),
            F("prometheus.ignore_jobs", "Prometheus jobs to leave out", "list"),
            F("zabbix.url", "Zabbix address", "text", "Empty turns it off.", url=True,
              placeholder="http://192.0.2.30", heading="Zabbix"),
            F("zabbix.token", "Zabbix API token", "secret"),
            F("zabbix.min_severity", "List Zabbix problems from", "choice",
              options=[[1, "Information"], [2, "Warning"], [3, "Average"], [4, "High"], [5, "Disaster"]]),
            F("zabbix.alert_severity", "Put new Zabbix problems on the screen from", "choice",
              options=[[0, "Never"], [3, "Average"], [4, "High"], [5, "Disaster"]]),
        ],
    },
    {
        "id": "access", "title": "Access",
        "description": "Who else on the network can reach this Pi.",
        "help": {
            "use": "letting other devices open this settings page and post webhooks, or letting "
                   "them see the dashboard itself.",
            "give": "nothing to type: two switches.",
            "get": "with the first switch on, other devices can reach the settings page (after "
                   "entering a code shown on the screen) and the webhook addresses (with the token).",
            "limits": "TouchFrame speaks plain HTTP, so use it on a network you trust. The "
                      "dashboard has no sign-in: with the second switch on, anyone on the network "
                      "can see your calendar and press the intercom buttons. Turning the first "
                      "switch off from another device locks that device out.",
        },
        "fields": [
            F("app.listen", "Reachable from other devices", "network",
              "Needed for this page on another device, and for webhooks. Off, only the Pi itself can connect."),
            F("app.remote_view", "Other devices can open the dashboard", "bool",
              "The dashboard has no sign-in and shows your calendar."),
        ],
    },
]

FIELDS = {f["key"]: f for s in SECTIONS for f in s["fields"]}

# Changes to these take effect at once. The rest need the server to start again.
LIVE = ("app.", "alerts.", "webhooks.", "screen.", "screensaver.", "weather.", "calendar.")


def is_live(key):
    return key.startswith(LIVE) and key not in ("app.listen", "weather.warnings")


# ---------- reading and writing the config by key ----------
def _get(cfg, key):
    section, _, name = key.partition(".")
    return cfg[section][name] if name else cfg[section]


def _set(cfg, key, value):
    section, _, name = key.partition(".")
    if name:
        cfg[section][name] = value
    else:
        cfg[section] = value


def stored(state_dir):
    """What settings.json holds: {key: value}. Empty if there is none or it can't be read."""
    try:
        data = json.loads((state_dir / FILE).read_text())
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("settings: can't read %s (%s); using config.toml alone", FILE, exc)
        return {}


def overlay(cfg, changes):
    """Lay saved changes over the config. Anything that no longer validates is skipped."""
    for key, value in changes.items():
        field = FIELDS.get(key)
        if field is None:
            continue
        try:
            _set(cfg, key, _clean(field, value, _get(cfg, key), own=True))
        except (SettingsError, KeyError, TypeError) as exc:
            log.warning("settings: ignoring saved %s (%s)", key, exc)
    return cfg


# ---------- checking a value ----------
def _text(field, value, limit=None):
    if not isinstance(value, str):
        raise SettingsError(f"{field['label']}: expected text")
    value = value.strip()
    limit = limit or field.get("maxlen", 2000)
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise SettingsError(f"{field['label']}: too long, or has characters that can't be used")
    if field.get("url") and value:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SettingsError(f"{field['label']}: needs an address starting with http:// or https://")
    return value


def _number(field, value, kind):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{field['label']}: expected a number")
    if kind is int and value != int(value):
        raise SettingsError(f"{field['label']}: expected a whole number")
    value = kind(value)
    if not field.get("min", value) <= value <= field.get("max", value):
        raise SettingsError(f"{field['label']}: must be from {field['min']} to {field['max']}")
    return value


def _clean(field, value, current=None, own=False):
    """`value` as it should be stored, or SettingsError. `current` is what is
    in use now, for a secret that was left alone (sent as None). `own` is for
    values read back from settings.json rather than sent by a page."""
    kind, label = field["type"], field["label"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{label}: expected on or off")
        return value
    if kind == "choice":
        if isinstance(value, bool) or value not in [o[0] for o in field["options"]]:
            raise SettingsError(f"{label}: not one of the choices")
        return value
    if kind == "int":
        return _number(field, value, int)
    if kind == "float":
        return _number(field, value, float)
    if kind == "text":
        return _text(field, value)
    if kind == "secret":
        if value is None:
            return current if isinstance(current, str) else ""
        return _text(field, value)
    if kind == "list":
        if not isinstance(value, list) or len(value) > 50:
            raise SettingsError(f"{label}: expected a list")
        return [v for v in (_text(field, item, 200) for item in value) if v]
    if kind == "hours":
        if value == []:
            return []
        if (not isinstance(value, list) or len(value) != 2
                or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 23 for v in value)):
            raise SettingsError(f"{label}: needs two hours from 0 to 23, or neither")
        if value[0] == value[1]:
            raise SettingsError(f"{label}: the two hours can't be the same")
        return value
    if kind == "tabs":
        if not isinstance(value, list) or any(v not in TABS for v in value) or len(set(value)) != len(value):
            raise SettingsError(f"{label}: not a list of tabs")
        return value
    if kind == "network":
        if isinstance(value, bool):         # from a switch: keep the port, change who can connect
            port = str(current or "").rpartition(":")[2] or "8660"
            return f"{'0.0.0.0' if value else '127.0.0.1'}:{port}"
        host, _, port = _text(field, value, 80).rpartition(":")
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise SettingsError(f"{label}: needs ADDRESS:PORT")
        return f"{host or '127.0.0.1'}:{port}"
    if kind == "rows":
        if not isinstance(value, list) or len(value) > 30:
            raise SettingsError(f"{label}: expected a list")
        current = [_row(r) for r in current] if isinstance(current, list) else []
        return [_clean_row(field, row, current, own) for row in value]
    raise SettingsError(f"{label}: can't be changed here")


def _row(raw):
    """A calendar may be given as a bare link in config.toml."""
    return {"url": raw} if isinstance(raw, str) else dict(raw)


def _clean_row(field, row, current, own=False):
    if isinstance(row, str):
        row = {"url": row}
    if not isinstance(row, dict):
        raise SettingsError(f"{field['label']}: expected a list of entries")
    was = row.get("_row")
    old = current[was] if isinstance(was, int) and not isinstance(was, bool) and 0 <= was < len(current) else {}
    # Settings the page doesn't show (interval_seconds, say) stay as config.toml has
    # them. A page can't add any; they only come back from this server's own file.
    known = {f["key"] for f in field["fields"]} | {"_row"}
    out = {k: v for k, v in (row if own else old).items() if k not in known}
    for sub in field["fields"]:
        key = sub["key"]
        if key in row:
            value = _clean(sub, row[key], old.get(key))
        elif key in old:
            value = old[key]
        else:
            value = sub.get("default", {"bool": False, "list": []}.get(sub["type"], ""))
        if sub.get("required") and not value:
            raise SettingsError(f"{field['label']}: {sub['label']} is needed")
        out[key] = value
    return out


# ---------- what the pages are shown ----------
def _host(value):
    try:
        return urllib.parse.urlsplit(value).hostname or ""
    except ValueError:
        return ""


def _shown(field, value):
    """A value as the page may see it. A secret is never sent: only whether one
    is saved, and for a link, the site it points to."""
    kind = field["type"]
    if kind == "secret":
        return {"set": bool(value), "hint": _host(value) if isinstance(value, str) else ""}
    if kind == "network":
        return _host("http://" + str(value)) not in ("127.0.0.1", "localhost", "::1")
    if kind == "rows":
        return [{"_row": i, **{sub["key"]: _shown(sub, row.get(sub["key"], sub.get("default", "")))
                               for sub in field["fields"]}}
                for i, row in enumerate(_row(r) for r in value)]
    return value


class Settings:
    def __init__(self, cfg, base):
        self.cfg = cfg                  # the settings in use; changed in place
        self.base = base                # config.toml alone, to tell what was changed here
        self.file = cfg["app"]["state_dir"] / FILE
        self.on_live = None             # called with the keys of live settings that changed

    def describe(self):
        """The sections, their fields and the current values, for the settings page."""
        sections = copy.deepcopy(SECTIONS)
        for section in sections:
            for field in section["fields"]:
                field["value"] = _shown(field, _get(self.cfg, field["key"]))
                field["live"] = is_live(field["key"])
        return {"sections": sections, "tabs": TABS}

    def changed(self):
        """Keys whose values differ from config.toml's."""
        return sorted(stored(self.file.parent))

    def save(self, patch):
        """Check and apply {key: value}. Returns {"restart": bool}: whether the
        server has to start again for some of it. Nothing is saved if any value is bad."""
        if not isinstance(patch, dict) or not patch:
            raise SettingsError("nothing to save")
        new = {}
        for key, value in patch.items():
            field = FIELDS.get(key)
            if field is None:
                raise SettingsError(f"{key}: can't be changed here")
            new[key] = _clean(field, value, _get(self.cfg, key))
        new = {k: v for k, v in new.items() if v != _get(self.cfg, k)}
        if not new:
            return {"restart": False}

        saved = stored(self.file.parent)
        for key, value in new.items():
            if value == _get(self.base, key):
                saved.pop(key, None)        # back to what config.toml says
            else:
                saved[key] = value
        tmp = self.file.with_suffix(".part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # it can hold tokens
        with os.fdopen(fd, "w") as f:
            json.dump(saved, f, indent=1, sort_keys=True)
        tmp.replace(self.file)

        live = [k for k in new if is_live(k)]
        for key in live:
            _set(self.cfg, key, new[key])
        if live and self.on_live:
            self.on_live(live)
        log.info("settings: changed %s", ", ".join(sorted(new)))
        return {"restart": len(live) < len(new)}

    def reset(self):
        """Forget everything changed here: back to config.toml. Needs a restart."""
        self.file.unlink(missing_ok=True)
