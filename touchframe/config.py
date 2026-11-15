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
        "idle_home_seconds": 120,   # go back to the Home tab after this long untouched (0 = never)
    },
    # [weather] and [calendar] take the same settings as InkFrame's.
    "weather": {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "location_name": "",
        "units": "imperial",        # or "metric"
        "interval_minutes": 15,
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
        "zones": [],                # show text pages for these zones only ([] = all)
        "categories": [],           # show buttons in these categories only ([] = all)
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


def load(path):
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path).expanduser()
    if path.exists():
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    cfg["app"]["state_dir"] = Path(cfg["app"]["state_dir"]).expanduser()
    cfg["app"]["state_dir"].mkdir(parents=True, exist_ok=True)
    return cfg
