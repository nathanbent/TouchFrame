# TouchFrame

A home dashboard for the Raspberry Pi Touch Display (7", 800x480): the time, weather and calendar, and text alerts and buttons for the [mcast intercom](https://github.com/nathanbent/mcast-tools). It's [InkFrame](https://github.com/nathanbent/InkFrame)'s weather and calendar on a screen that updates live and can be touched.

![Home](docs/home.png)

| Tab | |
| --- | --- |
| **Home** | The time and date, the weather now, the next 8 hours, the next 4 days, and what's coming up on the calendar |
| **Calendar** | This month, and the next 7 days of events |
| **Alerts** | Text pages from the intercom, as they arrive and afterwards |
| **Lab** | Proxmox, AdGuard, UniFi and Plex at a glance, Prometheus targets that are down and Zabbix problems. Shown when any of them is set |
| **Intercom** | A button for each announcement on the intercom panel. Shown when `[intercom]` is set |
| **Settings** | The theme, brightness, clock, tab order and screensaver, changed with a finger |

## Using it by touch

- **Swipe left or right** to move between tabs, or tap one in the bar at the bottom. The page follows your finger.
- **Home:** swipe the hourly strip for the next 24 hours, and the daily strip for the week. Tap *Coming up* to open the calendar.
- **Calendar:** swipe the month (or use its arrows) to look up to three months ahead. Tap a day to see just that day; tap it again to go back to the next 7 days.
- **Lab:** Overview and Problems are side by side. Swipe between them, or tap a Prometheus or Zabbix tile.
- **Photos:** swipe for the next photo or the one before. A tap brings the dashboard back.
- **An alert:** tap it to close it.

Everything meant to be pressed is at least 48 pixels tall on the 7" display, about 9 mm. The screen goes back to its first tab after two minutes untouched.

## Settings

There are three places to change things, and they do not fight:

1. **`config.toml`**, the file you write by hand. Everything can be set there.
2. **The Settings tab on the screen**, for what a finger can do: theme, brightness and night dimming, 12 or 24-hour clock, units, which tabs show and in what order, and the screensaver.
3. **The settings page**, opened on a phone or computer, for what needs a keyboard: your location, calendar links, the intercom, the Lab services and webhooks.

![The Settings tab](docs/settings.png)

Changes made in 2 and 3 are saved in `settings.json` in the state directory (`~/.local/state/touchframe`) and are laid over `config.toml` when TouchFrame starts, so the file you wrote is never rewritten. A setting put back to what `config.toml` says is dropped from `settings.json`. *Reset to config.toml* on the settings page forgets them all.

Most changes show on the screen within a second or two. Changing the intercom, the Lab services, multicast groups or network access restarts TouchFrame, which takes a few seconds; the page says so before you save.

### From a phone or computer

![The code on the screen](docs/pair.png)

On the screen, open **Settings** and tap **Show code**. The first time, it asks to let other devices reach the Pi, and restarts. Then scan the QR code, or open the address it shows (`http://ADDRESS:8660/admin`), and type in the six-digit code.

![The settings page](docs/admin.png)

- There are no passwords to set up. Being able to read the screen is the proof: a code works once, for ten minutes, and is withdrawn after five wrong guesses.
- The phone or computer is then remembered for 30 days. *Forget* on the Settings tab signs every device out.
- Saved tokens, passwords and calendar links are never sent back to the page. A field shows only that something is saved; leave it empty to keep it.
- File paths and commands (the photo folder, the `mcast` command, a certificate file, the backlight) can only be set in `config.toml`. A web page should not be able to point the server at a file or a program.
- TouchFrame speaks plain HTTP. Use the settings page on a network you trust.
- The dashboard itself stays private to the Pi's own screen, as before, unless `remote_view = true`.

## How it works

A small Python server on the Pi fetches the weather (Open-Meteo, no key needed) and the calendars, and serves a web page. Chromium shows that page full screen. The page scales with the screen, so it also works on a bigger display or in a browser on another computer.

## Install

Use Raspberry Pi OS with the desktop, set to log in automatically (the default). Then:

```bash
git clone <this repo> ~/touchframe
```

```bash
cd ~/touchframe && ./install.sh
```

```bash
nano config.toml
```

```bash
sudo systemctl start touchframe
```

Reboot, and the dashboard opens full screen. `install.sh` makes a Python environment in `.venv`, copies `config.example.toml` to `config.toml`, installs a service that starts on boot, and adds `kiosk.sh` to the desktop's autostart.

Editing `config.toml` is optional: with the service running, the same things can be set from a phone (see [Settings](#settings)).

To update an existing install, pull and run `./install.sh` again (it adds the one new Python package, `segno`, which draws the QR code), then `sudo systemctl restart touchframe`. The screen reloads itself when the page's files have changed.

`[weather]` and `[calendar]` in `config.toml` take the same settings as InkFrame's, so those sections can be copied across.

## Intercom

![Intercom](docs/intercom.png)

On the intercom panel, make an **operator** access token (Settings, then Access tokens) and put it in `config.toml`:

```toml
[intercom]
url = "https://pager.local:8651"
token = "mci_..."
ca_file = "/home/pi/touchframe/intercom-cert.pem"
```

- If the panel runs on this same Pi, use `url = "http://127.0.0.1:8651"` and leave `ca_file` out.
- A panel on another machine serves HTTPS with a self-signed certificate. Copy its `intercom-cert.pem` (beside its `intercom.json`) to this Pi and name it in `ca_file`. The `url` must use a name the certificate has: the panel's host name, `NAME.local` or its address.

What the tab does:

- **Buttons** follow the panel's own groups, order, colours and icons, and change when the panel is edited. Tap one to play it; tap it again, or *Stop* in the bottom bar, to stop it. Announcements set to ask first, and emergency ones, ask before playing.
- **Text pages** show as alerts (below).
- **What's playing** is in the bottom bar on every tab.
- `buttons = false` makes the screen **receive only**: text pages still take it over, but there is no Intercom tab and nothing can be played or stopped from it.
- `categories = ["family"]` shows only those groups' buttons. To make sure this screen can only *play* some announcements, limit the token to those categories on the panel.

The token stays in the server on the Pi. The page never sees it.

**Push to talk is not here.** Live announcements are shown but can't be pressed: the panel only lets its own page open the microphone connection.

## Alerts

![An emergency page](docs/emergency.png)

A text page takes over the whole screen in large type, whichever tab (or photo) is showing, and flashes once as it goes up. Tap it to close it early.

- **Emergency** pages turn the screen red, keep flashing, and stay up until someone taps them, even after the announcement has ended. One stopped by hand on the panel comes down at once. `emergency_stays = false` under `[alerts]` lets them come down like any other.
- **High** priority pages have an orange heading.
- The **Alerts** tab lists the last 50, newest first, with the time and how each arrived. Its badge counts the ones nobody has tapped or looked at since, so a page that came and went with nobody in the room isn't lost. Opening the tab clears the count. The list is kept across restarts.

![The Alerts tab](docs/alerts.png)

Alerts come by three routes, and each works without the others:

### From the panel

With `[intercom]` set, pages come over the panel's API, with the announcement's name and priority, and stay up exactly as long as the panel says. `zones = ["kitchen"]` shows only pages sent to those zones.

### Over multicast

```toml
[multicast]
groups = ["239.192.3.1:11000"]
```

Each group is received by `mcast text recv`, so this needs the `mcast` command from mcast-tools on the Pi:

```bash
curl -fsSL https://github.com/nathanbent/mcast-tools/releases/latest/download/mcast-aarch64-unknown-linux-gnu.tar.gz | sudo tar -xz -C /usr/local/bin mcast
```

On the panel, add the same group to a zone's **Text displays**. Anything else that sends real-time text to the group shows too, for example `mcast text send -g 239.192.3.1:11000 "Bus is here"`.

- The Pi joins the group and reports to the sender over RTCP as `name` (its host name unless set), so it shows up as a receiver on the mcast monitor.
- A channel carries only the words, so a page from one is headed with the sender's name, stays up for `hold_seconds`, and is an emergency only if it has one of `emergency_words`.
- With both routes set, the same page arriving both ways is shown once, and the Alerts tab says it came "via panel" and "via multicast 239.192.3.1:11000". A page that lists only one of them didn't arrive by the other, which is the thing to look for when testing the network.
- The Alerts tab shows each group, whether it's being listened to, and how many pages it has heard.

### From other systems (webhooks)

Anything that can send an HTTP request can put an alert on the screen. Set a token, and listen on the network:

```toml
[app]
listen = "0.0.0.0:8660"

[webhooks]
token = "a-long-random-string"
```

```bash
curl -H "Authorization: Bearer a-long-random-string" -H "Content-Type: application/json" -d '{"title": "Washer", "text": "Laundry is done"}' http://touchframe.local:8660/api/hook/alert
```

| Field | |
| --- | --- |
| `text` | The words: a string, or a list of lines. Needed |
| `title` | The heading ("Alert" unless given) |
| `priority` | `normal`, `high` or `emergency` |
| `seconds` | How long it stays up (`hold_seconds` unless given) |
| `key` | A name for it, so it can be updated, or taken down early with `{"key": "washer", "end": true}` |

The token can also go in the URL (`?token=...`) for systems that can't set a header.

**Grafana:** add a contact point of type *Webhook* with the URL `http://PI:8660/api/hook/grafana?token=...`. Each firing alert goes up once, headed with its name and showing its summary, and comes down when Grafana says it's resolved. Its `severity` label sets the priority (`emergency_severities` and `high_severities` under `[webhooks]`).

Other machines can post webhooks, and can use the settings page once they have entered a code from the screen. They can't open the dashboard (which has no sign-in and shows your calendar) unless `remote_view = true` is set under `[app]`.

## Lab

![The Lab tab](docs/lab.png)

The Lab tab shows up when any of these is set in `config.toml`. **Overview** has a tile for each; **Problems** lists what Prometheus and Zabbix say is wrong (tap their tiles to get there).

```toml
[prometheus]
url = "http://192.0.2.20:9090"

[zabbix]
url = "http://192.0.2.30"
token = "..."

[[proxmox]]
name = "Cluster"
url = "https://192.0.2.10:8006"
token = "touchframe@pve!panel=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
verify_tls = false

[[adguard]]
name = "AdGuard 1"
url = "http://192.0.2.53"
username = "admin"
password = "..."

[[unifi]]
url = "https://192.0.2.1"
key = "..."
verify_tls = false

[[plex]]
url = "http://192.0.2.40:32400"
token = "..."
```

The ones in double brackets can be repeated, for two AdGuards or two Proxmox clusters. `verify_tls = false` is for a self-signed certificate.

- **Proxmox:** a tile for each node with its CPU and memory (the bar turns amber from 75% and red from 90%) and how many of its VMs and containers are running. One address covers a whole cluster. Make an API token under Datacenter, Permissions, API Tokens, and give it (and its user) the `PVEAuditor` role on `/`. The token is written `USER@REALM!TOKENID=SECRET`. `nodes = ["pve1"]` shows only those.
- **AdGuard Home:** DNS queries, the share blocked, and the average answer time, with a warning if protection is switched off.
- **UniFi:** Wi-Fi and wired clients, access points, and whether the internet is up. Sign in with an API key (UniFi OS consoles), or the `username` and `password` of a local view-only user.
- **Plex:** what's playing, for whom, and how far in. The token is your `X-Plex-Token`.
- **Prometheus:** how many scrape targets are up, and a list of the ones that aren't.
- **Zabbix:** current problems by severity, worst first, with the host and how long each has been going. Acknowledged ones are dimmed. It needs Zabbix 6.4 or later and an API token (Users, then API tokens); a read-only user is enough. Problems on disabled hosts and triggers are left out, as on Zabbix's own dashboard.
- The tab's badge counts High and Disaster problems.
- `alert_severity = 4` under `[zabbix]` also puts each *new* High or Disaster problem on the screen as an alert (a Disaster as an emergency). Problems already there when TouchFrame starts don't.

![Problems](docs/lab-problems.png)

Prometheus and Zabbix are checked once a minute, the tiles every 30 seconds (Plex every 15; `interval_seconds` on each). If one stops answering, its tile says "offline" with its last numbers still up, and the corner says "Lab offline".

## Screensaver

After 5 minutes untouched, photos from `~/Pictures/touchframe` (subfolders too) fill the screen, a new one every 30 seconds in a random order, with the time in the corner. Wikimedia Commons' Picture of the Day is added to them, with its caption. A tap brings the dashboard back, and an alert shows over the photos.

```bash
scp *.jpg pi@<pi-address>:~/Pictures/touchframe/
```

JPEG, PNG, WebP and GIF work. Photos are scaled down the first time they're shown, so big ones from a phone are fine. With no photos and `picture_of_the_day = false`, the dashboard just stays up. Settings are under `[screensaver]`.

## Screen brightness

```toml
[screen]
dim_hours = [22, 7]
dim_brightness = 10
```

dims the backlight from 22:00 to 07:00. A touch, or a text page going up, brings it back to full for a minute. `install.sh` adds a udev rule that lets the `video` group set the backlight; log out and in (or reboot) after installing so the group applies.

## Running by hand

```bash
.venv/bin/python -m touchframe --config config.toml -v
```

Then open <http://127.0.0.1:8660/>. To open it from another computer, set `listen = "0.0.0.0:8660"` and `remote_view = true` under `[app]`. There's no sign-in, so anyone on the network can then see the calendar and press the intercom's buttons.

## Logs

```bash
journalctl -u touchframe -f
```

The last weather and calendar data are kept in `~/.local/state/touchframe`, and used when the network is down ("Weather offline" in the corner).

## Layout

```
touchframe/
  server.py     the web server and its JSON API
  weather.py    Open-Meteo forecast
  agenda.py     events from iCal links
  intercom.py   the link to the mcast intercom panel
  multicast.py  text pages from multicast groups, by `mcast text recv`
  alerts.py     the alerts on screen, and their history
  hooks.py      alerts from webhooks (and Grafana)
  lab.py        Prometheus targets and Zabbix problems
  services.py   the Lab tab's tiles: Proxmox, AdGuard, UniFi, Plex
  photos.py     the screensaver's photos
  screen.py     backlight dimming
  web/          the page (index.html, app.css, app.js)
```

## Design

The look follows one set of design tokens: a blue accent, blue-tinted neutrals, Rubik for headings and numbers, Karla for everything else. Status is teal, amber and red, always with an icon or a word beside the colour. [docs/design.md](docs/design.md) has the rules, the touch sizes and the measured contrast.

The fonts are in `touchframe/web/fonts`, under the SIL Open Font License (`OFL.txt` there).

## Tests

```bash
python3 -m unittest discover tests
```

They cover the settings layer: what can be changed and how each value is checked, that secrets are never sent to a page, the pairing code, and which requests another machine may make.
