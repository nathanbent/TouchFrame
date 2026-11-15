"""Load config.toml and fill in defaults for anything left out."""
import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "app": {
        "listen": "127.0.0.1:8660",
        "state_dir": "~/.local/state/touchframe",
        "theme": "dark",            # "dark", "light" or "auto" (light by day, dark at night)
        "clock": "12h",             # or "24h"
        "remote_view": False,       # True lets other machines open the dashboard (no sign-in)
        "idle_home_seconds": 120,   # go back to the first tab after this long untouched (0 = never)
        "tabs": [],                 # the tabs to show, in order, e.g. ["home", "lab"] ([] = all)
    },
    # [weather] and [calendar] take the same settings as InkFrame's.
    "weather": {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "location_name": "",
        "units": "imperial",        # or "metric"
        "interval_minutes": 15,
        "air_quality": True,
        "warnings": True,           # US National Weather Service alerts for this location
        "page_severity": "severe",  # new ones this severe go up as pages: extreme, severe, moderate, off
        "page_seconds": 120,
    },
    "calendar": {
        "sources": [],              # iCal links, or tables with url and colour
        "days": 7,
        "week_start": "sunday",     # or "monday"
        "interval_minutes": 5,
    },
    "intercom": {
        "url": "",                  # the mcast intercom panel; "" turns the Intercom tab off
        "token": "",                # an operator access token
        "ca_file": "",              # the panel's certificate, when it's self-signed
        "verify_tls": True,
        "buttons": True,            # False: receive only (text pages, no buttons)
        "talk": True,               # push to talk on live announcements, with a microphone on this Pi
        "zones": [],                # show text pages for these zones only ([] = all)
        "categories": [],           # show buttons in these categories only ([] = all)
    },
    "multicast": {
        "groups": [],               # text channels to show, e.g. ["239.192.3.1:11000"]
        "mcast": "mcast",           # the mcast-tools command (or its full path)
        "name": "",                 # reported to senders over RTCP ("" = the host name)
        "interface": "",            # IPv4 address to join on ("" = any)
        "hold_seconds": 20,         # how long a page from a channel stays up
        "pictures": [],             # groups that pictures are sent to (mcast file send)
        "picture_seconds": 30,      # how long a picture stays up
        "monitor": True,            # the Streams tab: measure what arrives (with groups or watch set)
        "watch": [],                # other groups to measure, e.g. audio zones
        "monitor_port": 8659,       # loopback port for the monitor TouchFrame starts
        "monitor_url": "",          # or an mcast server that's already running, e.g. "http://127.0.0.1:8650"
    },
    "alerts": {
        "emergency_stays": True,    # an emergency page stays up until it's tapped
        "emergency_words": [],      # pages with any of these words are emergencies
    },
    "prometheus": {
        "url": "",                  # e.g. "http://192.0.2.20:9090"; "" = off
        "token": "",
        "verify_tls": True,
        "ignore_jobs": [],          # jobs left out of the counts
        "interval_seconds": 60,
    },
    "zabbix": {
        "url": "",                  # e.g. "http://192.0.2.30"; "" = off
        "token": "",                # an API token (Users > API tokens)
        "verify_tls": True,
        "min_severity": 2,          # 2 Warning, 3 Average, 4 High, 5 Disaster
        "alert_severity": 0,        # new problems this severe go up as alerts (0 = never)
        "alert_seconds": 60,
        "interval_seconds": 60,
    },
    # Lists of tables ([[proxmox]] ...), so there can be several of each; see services.py.
    "proxmox": [],
    "adguard": [],
    "unifi": [],
    "plex": [],
    "sonarr": [],
    "radarr": [],
    "overseerr": [],
    "sabnzbd": [],
    "qbittorrent": [],
    "portainer": [],
    "webhooks": {
        "token": "",                # "" = off
        "hold_seconds": 60,         # how long an alert stays up unless it says
        "emergency_severities": ["disaster"],
        "high_severities": ["critical", "high", "error"],
    },
    "screensaver": {
        "enabled": True,
        "idle_minutes": 5,
        "folder": "~/Pictures/touchframe",
        "seconds": 30,              # per photo
        "picture_of_the_day": True, # add Wikimedia Commons' picture of the day
        "clock": True,
    },
    "screen": {
        "backlight": "auto",        # a directory under /sys/class/backlight, "auto" or "off"
        "brightness": 100,          # percent
        "dim_hours": [],            # e.g. [22, 7] dims from 22:00 to 07:00
        "dim_brightness": 10,       # percent while dimmed
        "wake_seconds": 60,         # full brightness this long after a touch or a page
    },
}


def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def read(path):
    """config.toml over the defaults: the settings as the file gives them."""
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path).expanduser()
    if path.exists():
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    cfg["app"]["state_dir"] = Path(cfg["app"]["state_dir"]).expanduser()
    cfg["app"]["state_dir"].mkdir(parents=True, exist_ok=True)
    return cfg


def load(path):
    """The settings in use: config.toml, then whatever was changed from the
    screen or the settings page (settings.json in the state directory)."""
    from . import settings
    cfg = read(path)
    settings.overlay(cfg, settings.stored(cfg["app"]["state_dir"]))
    return cfg
