"""Weather from Open-Meteo (free, no API key), as JSON for the page."""
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

log = logging.getLogger(__name__)

API = "https://api.open-meteo.com/v1/forecast"

# WMO weather code -> (label, icon kind). The same table as InkFrame's.
CODES = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "partly"), 2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloudy"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"), 80: ("Showers", "rain"), 81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"), 85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm", "storm"), 99: ("Thunderstorm", "storm"),
}


def _code(code):
    return CODES.get(code, ("Unknown", "cloudy"))


class Weather:
    def __init__(self, cfg, state_dir):
        self.cfg = cfg["weather"]
        self.cache = state_dir / "weather.json"
        self.interval = max(1, self.cfg["interval_minutes"]) * 60
        self.metric = self.cfg["units"] == "metric"
        self._lock = threading.Lock()
        self._at = 0.0
        self._raw = None
        self._stale = False

    def _fetch(self):
        params = {
            "latitude": self.cfg["latitude"],
            "longitude": self.cfg["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                       "weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,sunrise,sunset",
            "temperature_unit": "celsius" if self.metric else "fahrenheit",
            "wind_speed_unit": "kmh" if self.metric else "mph",
            "timezone": "auto",
            "forecast_days": 6,
        }
        with urllib.request.urlopen(API + "?" + urllib.parse.urlencode(params), timeout=30) as resp:
            return json.load(resp)

    def _load(self):
        """The raw forecast, fetched at most once per interval; the last good one if offline."""
        with self._lock:
            now = time.monotonic()
            if self._raw is not None and now - self._at < self.interval:
                return self._raw, self._stale
            try:
                self._raw, self._stale = self._fetch(), False
                self.cache.write_text(json.dumps(self._raw))
                self._at = now
            except Exception as exc:
                log.warning("weather fetch failed (%s); using the last forecast", exc)
                if self._raw is None:
                    if not self.cache.exists():
                        raise
                    self._raw = json.loads(self.cache.read_text())
                self._stale = True
                self._at = now - self.interval + 60      # try again in a minute
            return self._raw, self._stale

    def get(self):
        data, stale = self._load()
        cur, daily, hourly = data["current"], data["daily"], data["hourly"]
        label, kind = _code(cur["weather_code"])
        # The forecast's own "now" goes stale with the cache, so hours are picked by the clock.
        now = datetime.now().strftime("%Y-%m-%dT%H:00")
        first = next((i for i, t in enumerate(hourly["time"]) if t >= now), 0)
        hours = [{
            "time": hourly["time"][i],
            "temp": hourly["temperature_2m"][i],
            "rain": hourly["precipitation_probability"][i] or 0,
            "kind": _code(hourly["weather_code"][i])[1],
            "day": bool(hourly["is_day"][i]),
        } for i in range(first, min(first + 24, len(hourly["time"])))]
        today = datetime.now().strftime("%Y-%m-%d")
        days = [{
            "date": daily["time"][i],
            "label": _code(daily["weather_code"][i])[0],
            "kind": _code(daily["weather_code"][i])[1],
            "high": daily["temperature_2m_max"][i],
            "low": daily["temperature_2m_min"][i],
            "rain": daily["precipitation_probability_max"][i] or 0,
            "sunrise": daily["sunrise"][i],
            "sunset": daily["sunset"][i],
        } for i in range(len(daily["time"])) if daily["time"][i] >= today]
        return {
            "stale": stale,
            "updated": cur["time"],
            "location": self.cfg["location_name"],
            "wind_unit": "km/h" if self.metric else "mph",
            "current": {
                "temp": cur["temperature_2m"],
                "feels": cur["apparent_temperature"],
                "humidity": cur["relative_humidity_2m"],
                "wind": cur["wind_speed_10m"],
                "label": label,
                "kind": kind,
                "day": bool(cur.get("is_day", 1)),
            },
            "hours": hours,
            "days": days,
        }
