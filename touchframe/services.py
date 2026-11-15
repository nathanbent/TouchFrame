"""Tiles for the Lab tab: Proxmox, AdGuard Home, UniFi and Plex.

Each is a list in config.toml ([[proxmox]], [[adguard]], ...), so there can be
several of a kind. Every source has `name`, and `poll()` returning what its
tiles show.
"""
import base64
import http.cookiejar
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .lab import fetch


def _entry(raw, **defaults):
    """One [[kind]] table from the config, with defaults for what it leaves out."""
    return {"name": "", "url": "", "verify_tls": True, "interval_seconds": 30, **defaults, **raw}


class Proxmox:
    """Each node's CPU and memory, and how many of its VMs and containers are running.
    Needs an API token (Datacenter > Permissions > API Tokens) with the PVEAuditor role."""
    kind = "proxmox"

    def __init__(self, raw):
        self.cfg = _entry(raw, token="", nodes=[])
        self.name = self.cfg["name"] or "Proxmox"

    def poll(self):
        data = fetch(self.cfg["url"].rstrip("/") + "/api2/json/cluster/resources", self.cfg,
                     headers={"Authorization": "PVEAPIToken=" + self.cfg["token"]})["data"]
        nodes = {}
        for r in data:
            if r.get("type") == "node":
                nodes[r["node"]] = {
                    "name": r["node"], "online": r.get("status") == "online",
                    "cpu": round(100 * (r.get("cpu") or 0)),
                    "mem": round(100 * (r.get("mem") or 0) / r["maxmem"]) if r.get("maxmem") else 0,
                    "vms": [0, 0], "lxc": [0, 0]}
        for r in data:
            kind = {"qemu": "vms", "lxc": "lxc"}.get(r.get("type"))
            if kind and not r.get("template") and r.get("node") in nodes:
                count = nodes[r["node"]][kind]
                count[1] += 1
                count[0] += r.get("status") == "running"
        only = self.cfg["nodes"]
        return {"nodes": [n for name, n in sorted(nodes.items()) if not only or name in only]}


class AdGuard:
    """AdGuard Home's query counts for the last day."""
    kind = "adguard"

    def __init__(self, raw):
        self.cfg = _entry(raw, username="", password="")
        self.name = self.cfg["name"] or "AdGuard"

    def poll(self):
        headers = {}
        if self.cfg["username"]:
            login = f"{self.cfg['username']}:{self.cfg['password']}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(login).decode()
        base = self.cfg["url"].rstrip("/")
        stats = fetch(base + "/control/stats", self.cfg, headers=headers)
        status = fetch(base + "/control/status", self.cfg, headers=headers)
        queries = stats.get("num_dns_queries", 0)
        blocked = stats.get("num_blocked_filtering", 0)
        return {"queries": queries, "blocked": blocked,
                "percent": round(100 * blocked / queries, 1) if queries else 0,
                "latency_ms": round(1000 * stats.get("avg_processing_time", 0), 1),
                "protecting": bool(status.get("protection_enabled", True))}


class UniFi:
    """A UniFi Network controller: clients, access points and whether the WAN is up.
    Sign in with an API key (UniFi OS consoles), or a local user's name and password."""
    kind = "unifi"

    def __init__(self, raw):
        self.cfg = _entry(raw, key="", username="", password="", site="default")
        self.name = self.cfg["name"] or "UniFi"
        self.base = self.cfg["url"].rstrip("/")
        context = ssl.create_default_context()
        if not self.cfg["verify_tls"]:
            context = ssl._create_unverified_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._prefix = "/proxy/network" if self.cfg["key"] else None    # None: not signed in

    def _request(self, path, body=None):
        req = urllib.request.Request(self.base + path, headers={"Accept": "application/json"})
        if self.cfg["key"]:
            req.add_header("X-API-KEY", self.cfg["key"])
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        with self._opener.open(req, timeout=20) as resp:
            return json.load(resp)

    def _sign_in(self):
        login = {"username": self.cfg["username"], "password": self.cfg["password"]}
        try:
            self._request("/api/auth/login", login)     # UniFi OS (UDM, Cloud Key Gen2+)
            self._prefix = "/proxy/network"
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            self._request("/api/login", login)          # a self-hosted controller
            self._prefix = ""

    def poll(self):
        try:
            if self._prefix is None:
                self._sign_in()
            health = self._request(f"{self._prefix}/api/s/{self.cfg['site']}/stat/health")["data"]
        except urllib.error.HTTPError as exc:
            if not self.cfg["key"]:
                self._prefix = None                     # sign in again next time
            raise RuntimeError("it refused the sign-in" if exc.code in (401, 403)
                               else f"it answered {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise RuntimeError(str(getattr(exc, "reason", exc))) from None
        by = {h.get("subsystem"): h for h in health}
        wlan, lan, wan = by.get("wlan", {}), by.get("lan", {}), by.get("wan", {})
        return {"wifi_clients": wlan.get("num_user", 0), "wired_clients": lan.get("num_user", 0),
                "aps": wlan.get("num_ap", 0), "aps_down": wlan.get("num_disconnected", 0),
                "wan": wan.get("status"), "wlan": wlan.get("status")}


class Plex:
    """What's playing on a Plex server, and for whom."""
    kind = "plex"

    def __init__(self, raw):
        self.cfg = _entry(raw, token="", interval_seconds=15)
        self.name = self.cfg["name"] or "Plex"

    def poll(self):
        data = fetch(self.cfg["url"].rstrip("/") + "/status/sessions", self.cfg,
                     headers={"X-Plex-Token": self.cfg["token"], "Accept": "application/json"})
        streams = []
        for m in data.get("MediaContainer", {}).get("Metadata", []) or []:
            title = m.get("title", "")
            if m.get("type") == "episode":
                title = f"{m.get('grandparentTitle', '')} \u00b7 S{int(m.get('parentIndex') or 0):02d}" \
                        f"E{int(m.get('index') or 0):02d} \u00b7 {title}"
            elif m.get("type") == "track":
                title = f"{m.get('grandparentTitle', '')} \u00b7 {title}"
            duration = float(m.get("duration") or 0)
            streams.append({
                "title": title, "user": (m.get("User") or {}).get("title", ""),
                "state": (m.get("Player") or {}).get("state", "playing"),
                "progress": round(float(m.get("viewOffset") or 0) / duration, 3) if duration else 0,
                "transcoding": (m.get("TranscodeSession") or {}).get("videoDecision") == "transcode"})
        return {"streams": streams}


class Arr:
    """Sonarr or Radarr: what's wanted and what's downloading. `key` is its API key
    (Settings > General)."""

    def __init__(self, raw):
        self.cfg = _entry(raw, key="", interval_seconds=60)
        self.name = self.cfg["name"] or self.kind.title()

    def _get(self, path):
        return fetch(self.cfg["url"].rstrip("/") + "/api/v3/" + path, self.cfg,
                     headers={"X-Api-Key": self.cfg["key"]})

    def poll(self):
        return {"wanted": self._get("wanted/missing?pageSize=1")["totalRecords"],
                "queued": self._get("queue?pageSize=1")["totalRecords"],
                "total": len(self._get(self.library))}


class Sonarr(Arr):
    kind, library, noun = "sonarr", "series", "series"


class Radarr(Arr):
    kind, library, noun = "radarr", "movie", "movies"


class Overseerr:
    """Overseerr or Jellyseerr: requests waiting for a yes or no."""
    kind = "overseerr"

    def __init__(self, raw):
        self.cfg = _entry(raw, key="", interval_seconds=60)
        self.name = self.cfg["name"] or "Requests"

    def poll(self):
        count = fetch(self.cfg["url"].rstrip("/") + "/api/v1/request/count", self.cfg,
                      headers={"X-Api-Key": self.cfg["key"]})
        return {"pending": count.get("pending", 0), "approved": count.get("approved", 0),
                "processing": count.get("processing", 0), "available": count.get("available", 0)}


class Sabnzbd:
    kind = "sabnzbd"

    def __init__(self, raw):
        self.cfg = _entry(raw, key="", interval_seconds=15)
        self.name = self.cfg["name"] or "SABnzbd"

    def poll(self):
        query = urllib.parse.urlencode({"mode": "queue", "output": "json", "apikey": self.cfg["key"]})
        answer = fetch(self.cfg["url"].rstrip("/") + "/api?" + query, self.cfg)
        if "queue" not in answer:
            raise RuntimeError(str(answer.get("error", "no queue in its answer")))
        q = answer["queue"]
        return {"down": float(q.get("kbpersec") or 0) * 1024, "queued": int(q.get("noofslots") or 0),
                "left": q.get("timeleft") or "", "paused": bool(q.get("paused"))}


class QBittorrent:
    kind = "qbittorrent"

    def __init__(self, raw):
        self.cfg = _entry(raw, username="", password="", interval_seconds=15)
        self.name = self.cfg["name"] or "qBittorrent"
        self.base = self.cfg["url"].rstrip("/")
        context = ssl.create_default_context()
        if not self.cfg["verify_tls"]:
            context = ssl._create_unverified_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._in = not self.cfg["username"]     # no login needed when it trusts this address

    def _request(self, path, form=None):
        req = urllib.request.Request(self.base + path, headers={"Referer": self.base})
        if form is not None:
            req.data = urllib.parse.urlencode(form).encode()
        with self._opener.open(req, timeout=20) as resp:
            return resp.read()

    def poll(self):
        try:
            if not self._in:
                answer = self._request("/api/v2/auth/login", {"username": self.cfg["username"],
                                                              "password": self.cfg["password"]})
                if answer.strip() != b"Ok.":
                    raise RuntimeError("it refused the sign-in")
                self._in = True
            info = json.loads(self._request("/api/v2/transfer/info"))
            torrents = json.loads(self._request("/api/v2/torrents/info"))
        except urllib.error.HTTPError as exc:
            self._in = not self.cfg["username"]
            raise RuntimeError("it refused the sign-in" if exc.code in (401, 403)
                               else f"it answered {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise RuntimeError(str(getattr(exc, "reason", exc))) from None
        seeding = ("uploading", "stalledUP", "forcedUP", "queuedUP", "checkingUP")
        seeds = sum(1 for t in torrents if t.get("state") in seeding)
        done = sum(1 for t in torrents if t.get("state") in ("pausedUP", "stoppedUP"))
        return {"down": info.get("dl_info_speed", 0), "up": info.get("up_info_speed", 0),
                "seeding": seeds, "leeching": len(torrents) - seeds - done}


class Portainer:
    """Containers running and stopped on one Portainer environment. `key` is an
    access token (My account > Access tokens); `environment` its number (1 unless set)."""
    kind = "portainer"

    def __init__(self, raw):
        self.cfg = _entry(raw, key="", environment=1, interval_seconds=60)
        self.name = self.cfg["name"] or "Docker"

    def poll(self):
        containers = fetch(f"{self.cfg['url'].rstrip('/')}/api/endpoints/{self.cfg['environment']}"
                           "/docker/containers/json?all=1", self.cfg,
                           headers={"X-API-Key": self.cfg["key"]})
        running = sum(1 for c in containers if c.get("State") == "running")
        unhealthy = [c["Names"][0].lstrip("/") for c in containers
                     if "unhealthy" in str(c.get("Status", "")) and c.get("Names")]
        return {"running": running, "stopped": len(containers) - running, "unhealthy": unhealthy[:3]}


KINDS = [Proxmox, AdGuard, UniFi, Plex, Sonarr, Radarr, Overseerr, Sabnzbd, QBittorrent, Portainer]


def load(cfg):
    """Every configured service, in the order the Lab tab shows them."""
    return [kind(raw) for kind in KINDS for raw in cfg.get(kind.kind, []) if raw.get("url")]
