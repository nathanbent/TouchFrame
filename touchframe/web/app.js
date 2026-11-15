"use strict";

const $ = id => document.getElementById(id);

/** Build an element. Text always goes in as text, never as HTML. */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  }
  return el;
}

let settings = { theme: "dark", clock: "12h", idle_home_seconds: 120, intercom: false, alerts: false };
let weather = null;
let calendar = null;
let state = null;          // {intercom, alerts, multicast, now_ms}: what the server pushes

// ---------- weather icons ----------
const SVG = "http://www.w3.org/2000/svg";
const CLOUD = "M19 40h28a10 10 0 0 0 1.6-19.9A15 15 0 0 0 20 17.6 11.5 11.5 0 0 0 19 40z";
const SMALL_CLOUD = "M25 46h24a8.5 8.5 0 0 0 1.4-16.9A12.5 12.5 0 0 0 26 27a9.6 9.6 0 0 0-1 19z";
const SUN_RAYS = "M32 6v6M32 52v6M6 32h6M52 32h6M13.6 13.6l4.3 4.3M46.1 46.1l4.3 4.3M13.6 50.4l4.3-4.3M46.1 17.9l4.3-4.3";
const MOON = "M40 10a22 22 0 1 0 14 34A18 18 0 0 1 40 10z";

function icon(kind, day = true) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("class", "icon");
  const add = (tag, attrs) => {
    const el = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
  };
  const stroke = (d, color, w = 3.5) =>
    add("path", { d, stroke: `var(--${color})`, "stroke-width": w, "stroke-linecap": "round", fill: "none" });
  const cloud = (d = CLOUD) => add("path", { d, fill: "var(--cloud)" });
  const sun = (cx, cy, r) => add("circle", { cx, cy, r, fill: "var(--sun)" });

  if (kind === "clear") {
    if (day) { sun(32, 32, 12); stroke(SUN_RAYS, "sun"); }
    else add("path", { d: MOON, fill: "var(--cloud)" });
  } else if (kind === "partly") {
    if (day) { sun(22, 22, 9); stroke("M22 5v4M5 22h4M10 10l3 3M34 10l-3 3", "sun", 3); }
    else add("path", { d: MOON, fill: "var(--cloud)", transform: "translate(-4 -2) scale(.6)", opacity: ".7" });
    cloud(SMALL_CLOUD);
  } else if (kind === "fog") {
    stroke("M12 24h40M8 34h48M14 44h36", "cloud", 4);
  } else {
    cloud();
    if (kind === "rain") stroke("M24 46l-3 9M34 46l-3 9M44 46l-3 9", "rain");
    if (kind === "snow") for (const x of [23, 33, 43]) add("circle", { cx: x, cy: 51, r: 3, fill: "var(--rain)" });
    if (kind === "storm") add("path", { d: "M35 40l-10 13h7l-3 10 11-14h-7z", fill: "var(--sun)" });
  }
  return svg;
}

// ---------- formatting ----------
const deg = v => `${Math.round(v)}°`;
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];
const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
/** "2026-11-15" as local midnight (new Date() would read it as UTC). */
const day = s => new Date(`${s.slice(0, 10)}T00:00:00`);

function clock(d, short = false) {
  const m = String(d.getMinutes()).padStart(2, "0");
  if (settings.clock === "24h") return { text: `${String(d.getHours()).padStart(2, "0")}:${m}`, ampm: "" };
  const hour = d.getHours() % 12 || 12;
  const ampm = d.getHours() < 12 ? "am" : "pm";
  return { text: short && m === "00" ? `${hour}` : `${hour}:${m}`, ampm };
}
const clockText = (d, short) => { const c = clock(d, short); return c.text + (c.ampm ? (short ? c.ampm : ` ${c.ampm}`) : ""); };

// ---------- home ----------
function drawClock() {
  const now = new Date();
  const c = clock(now);
  $("time").textContent = c.text;
  $("ampm").textContent = c.ampm;
  $("date").textContent = `${WEEKDAYS[now.getDay()]}, ${MONTHS[now.getMonth()]} ${now.getDate()}`;
  $("saver-clock").textContent = c.text + (c.ampm ? ` ${c.ampm}` : "");
}

function drawWeather() {
  const now = $("now"), hours = $("hours"), days = $("days");
  if (!weather || weather.error) {
    now.replaceChildren(h("div", { class: "muted" }, weather ? "No weather: " + weather.error : ""));
    hours.replaceChildren(); days.replaceChildren();
    return;
  }
  const cur = weather.current, today = weather.days[0];
  now.replaceChildren(
    icon(cur.kind, cur.day),
    h("div", {},
      h("div", { class: "temp" }, deg(cur.temp)),
      h("div", { class: "label" }, cur.label),
      h("div", { class: "extra" },
        today && h("span", { class: "hi" }, `H ${deg(today.high)}`), " ",
        today && h("span", { class: "lo" }, `L ${deg(today.low)}`),
        `  ·  Feels ${deg(cur.feels)}`),
      h("div", { class: "extra" }, `Wind ${Math.round(cur.wind)} ${weather.wind_unit}  ·  Humidity ${cur.humidity}%`)));

  hours.replaceChildren(...weather.hours.slice(0, 8).map(hr =>
    h("div", { class: "hour" },
      h("div", { class: "when" }, clockText(new Date(hr.time), true)),
      icon(hr.kind, hr.day),
      h("div", { class: "t" }, deg(hr.temp)),
      h("div", { class: "rain" }, hr.rain >= 10 ? `${hr.rain}%` : ""))));

  days.replaceChildren(...weather.days.slice(1, 5).map(d =>
    h("div", { class: "day" },
      h("div", { class: "name" }, WEEKDAYS[day(d.date).getDay()].slice(0, 3)),
      icon(d.kind),
      h("div", { class: "range" }, h("span", { class: "hi" }, deg(d.high)), " ", h("span", { class: "lo" }, deg(d.low))),
      h("div", { class: "rain" }, d.rain >= 10 ? `${d.rain}%` : ""))));

  if (settings.theme === "auto") document.documentElement.dataset.theme = cur.day ? "light" : "dark";
  drawStatus();
}

// ---------- calendar ----------
/** Events from now on, grouped by day, as InkFrame does: [[Date, [event...]]...]. */
function agendaDays() {
  if (!calendar) return [];
  const now = new Date(), today = day(ymd(now));
  const end = new Date(today); end.setDate(end.getDate() + calendar.days);
  const days = new Map();
  for (const e of calendar.events) {
    const start = new Date(e.start), stop = new Date(e.stop);
    if (stop <= now || day(e.start) >= end) continue;
    const on = day(e.start) < today ? today : day(e.start);
    const key = ymd(on);
    if (!days.has(key)) days.set(key, []);
    const last = new Date(stop - 1000);
    days.get(key).push({ ...e, start, until: ymd(last) > key ? WEEKDAYS[last.getDay()].slice(0, 3) : null });
  }
  return [...days.entries()].sort().map(([key, events]) => [day(key),
    events.sort((a, b) => (b.all_day - a.all_day) || (a.start - b.start) || a.title.localeCompare(b.title))]);
}

function eventRow(e) {
  return h("div", { class: "event" },
    h("span", { class: `bar ${e.colour}` }),
    h("span", { class: "when" }, e.all_day ? "All day" : clockText(e.start)),
    h("span", { class: "title" }, e.title + (e.until ? ` (until ${e.until})` : "")));
}

function dayName(d) {
  const today = day(ymd(new Date()));
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  return `${WEEKDAYS[d.getDay()]}, ${MONTHS[d.getMonth()].slice(0, 3)} ${d.getDate()}`;
}

function drawCalendar() {
  const days = agendaDays();
  const problem = !calendar ? "" : calendar.error ? calendar.error
    : !calendar.configured ? "Add calendar links under [calendar] in config.toml" : null;

  // Home: what's next, as many as fit.
  const next = $("next");
  next.replaceChildren(h("h2", {}, "Coming up"));
  if (problem !== null) next.append(h("div", { class: "empty" }, problem));
  else if (!days.length) next.append(h("div", { class: "empty" }, `Nothing in the next ${calendar.days} days`));
  let room = 4;
  for (const [d, events] of days) {
    if (room < 2) break;
    next.append(h("h2", {}, dayName(d)));
    room -= .6;
    for (const e of events.slice(0, Math.floor(room))) { next.append(eventRow(e)); room -= 1; }
  }

  // Calendar tab: the agenda...
  const agenda = $("agenda");
  agenda.replaceChildren();
  if (problem !== null) agenda.append(h("div", { class: "empty" }, problem));
  else if (!days.length) agenda.append(h("div", { class: "empty" }, `Nothing in the next ${calendar.days} days`));
  for (const [d, events] of days) {
    agenda.append(h("h2", { class: dayName(d) === "Today" ? "today" : "" }, dayName(d)), ...events.map(eventRow));
  }

  // ...and the month.
  const now = new Date(), first = new Date(now.getFullYear(), now.getMonth(), 1);
  const monday = calendar && calendar.week_start === "monday";
  const busy = new Set();
  for (const e of (calendar ? calendar.events : [])) {
    const last = new Date(new Date(e.stop) - 1000);
    for (let d = day(e.start); d <= last; d.setDate(d.getDate() + 1)) busy.add(ymd(d));
  }
  const grid = h("div", { class: "grid" });
  const names = ["S", "M", "T", "W", "T", "F", "S"];
  for (let i = 0; i < 7; i++) grid.append(h("div", { class: "dow" }, names[(i + (monday ? 1 : 0)) % 7]));
  const d = new Date(first);
  d.setDate(1 - ((first.getDay() - (monday ? 1 : 0) + 7) % 7));
  do {
    for (let i = 0; i < 7; i++, d.setDate(d.getDate() + 1)) {
      const cls = ["d"];
      if (d.getMonth() !== now.getMonth()) cls.push("out");
      else {
        if (ymd(d) === ymd(now)) cls.push("today");
        if (busy.has(ymd(d))) cls.push("has");
      }
      grid.append(h("div", { class: cls.join(" ") }, String(d.getDate())));
    }
  } while (d.getMonth() === now.getMonth());
  $("month").replaceChildren(h("h1", {}, `${MONTHS[now.getMonth()]} ${now.getFullYear()}`), grid);
  drawStatus();
}

// ---------- intercom ----------
async function post(path) {
  const resp = await fetch(path, { method: "POST" });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.error || `Failed (${resp.status})`);
  return body;
}

function confirmPlay(text) {
  return new Promise(resolve => {
    $("confirm-text").textContent = text;
    $("confirm").hidden = false;
    const done = yes => { $("confirm").hidden = true; resolve(yes); };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
  });
}

async function press(button, run) {
  try {
    if (run) return await post(`/api/intercom/stop/${run.id}`);
    if (button.confirm && !await confirmPlay(`Play "${button.name}"?`)) return;
    await post(`/api/intercom/play/${encodeURIComponent(button.id)}`);
  } catch (err) {
    toast(err.message);
  }
}

function drawIntercom() {
  const ic = state && state.intercom;
  if (!ic || !ic.enabled || !settings.intercom_buttons) return;    // receive only: no buttons
  $("tab-intercom").hidden = false;
  const runs = ic.runs;

  const status = $("ic-status");
  const bad = !ic.connected ? (ic.error || "Connecting to the intercom…")
    : !ic.backend_ok ? "The intercom can't reach its mcast server" : null;
  status.textContent = bad || (runs.length ? `Playing: ${runs.map(r => r.name).join(", ")}` : "Tap to play");
  status.className = "ic-status" + (bad && ic.error ? " bad" : "");
  $("stop-all").hidden = !runs.length;

  $("ic-groups").replaceChildren(...ic.groups.flatMap(g => [
    g.name && h("h2", {}, g.name),
    h("div", { class: "tiles" }, g.buttons.map(b => {
      const run = runs.find(r => r.announcement === b.id);
      return h("button", {
        class: `tile ${b.color || ""}${run ? " busy" : ""}`,
        disabled: b.live || !ic.connected,
        onclick: () => press(b, run),
      },
        b.icon && h("span", { class: "ico" }, b.icon),
        h("span", { class: "name" }, b.name),
        h("span", { class: "sub" }, b.live ? "Talk from the intercom's own page" : run ? "Playing · tap to stop" : ""));
    })),
  ]));
  if (ic.connected && !ic.groups.length) {
    $("ic-groups").append(h("div", { class: "empty" }, "No announcements on the intercom panel yet"));
  }

  // The nav bar says what's playing, on every tab.
  const playing = $("playing");
  playing.hidden = !runs.length;
  if (runs.length) {
    playing.replaceChildren(
      h("span", {}, runs.length === 1 ? runs[0].name : `${runs.length} playing`),
      h("button", { onclick: () => post("/api/intercom/stop").catch(e => toast(e.message)) }, "■ Stop"));
  }
}

// ---------- alerts ----------
// A text page takes over the screen while it's up. An emergency flashes red
// and stays until someone taps it.
let pageTimer = null, pageShown = null;

function drawPage() {
  clearTimeout(pageTimer);
  const el = $("page");
  const now = Date.now() + (state ? state.now_ms - state.at : 0);     // by the server's clock
  const pages = state ? state.alerts.pages.filter(p => p.sticky || !p.until_ms || p.until_ms > now) : [];
  if (!pages.length) { el.hidden = true; pageShown = null; return; }
  const page = pages[0];
  $("page-name").textContent = page.name + (pages.length > 1 ? `  (+${pages.length - 1} more)` : "");
  $("page-hint").textContent = page.sticky ? "Tap to acknowledge" : "Tap to close";
  const text = $("page-text");
  text.replaceChildren(...page.text.map(line => h("div", {}, line)));
  el.className = `page ${page.priority}`;
  el.hidden = false;
  if (pageShown !== page.id) {
    // A new one: flash, so it's noticed from across the room.
    pageShown = page.id;
    el.classList.remove("arrive"); void el.offsetWidth; el.classList.add("arrive");
    endSaver();
  } else el.classList.add("arrive");
  // The biggest type that fits.
  let size = 6;
  do { text.style.fontSize = `${size}rem`; size -= .25; } while (size > 1 && text.scrollHeight > text.clientHeight);
  el.onclick = () => post(`/api/alerts/close/${page.id}`).catch(e => toast(e.message));
  const next = Math.min(...pages.filter(p => p.until_ms && !p.sticky).map(p => p.until_ms));
  if (Number.isFinite(next)) pageTimer = setTimeout(drawPage, Math.max(250, next - now));
}

function ago(ms) {
  const d = new Date(ms), today = ymd(new Date());
  if (ymd(d) === today) return clockText(d);
  return `${WEEKDAYS[d.getDay()].slice(0, 3)} ${d.getDate()} ${clockText(d)}`;
}

function drawAlerts() {
  if (!state) return;
  const al = state.alerts, ic = state.intercom;
  $("tab-alerts").hidden = !settings.alerts;
  const badge = $("badge");
  badge.hidden = !al.unseen;
  badge.textContent = al.unseen;

  // Where alerts come from, and whether each is working.
  const sources = [];
  if (ic.enabled) sources.push(h("span", { class: `src ${ic.connected ? "ok" : "bad"}` }, ic.connected ? "Intercom panel" : "Intercom panel offline"));
  for (const g of state.multicast) {
    sources.push(h("span", { class: `src ${g.listening ? "ok" : "bad"}`, title: g.error || "" },
      g.listening ? `Multicast ${g.group}${g.pages ? ` · ${g.pages} heard` : ""}` : `Multicast ${g.group}: ${g.error || "starting"}`));
  }
  $("al-sources").replaceChildren(...sources);

  const list = $("al-list");
  list.replaceChildren(...al.history.map(a =>
    h("div", { class: `alert ${a.priority}${a.seen ? "" : " unseen"}` },
      h("div", { class: "al-when" }, ago(a.at_ms)),
      h("div", { class: "al-body" },
        h("div", { class: "al-text" }, a.text.join(" ")),
        h("div", { class: "al-from" }, [a.name, ...a.sources.map(s => s === "panel" ? "via panel" : `via ${s}`)].join(" · "))))));
  if (!al.history.length) list.append(h("div", { class: "empty" }, "No alerts yet"));
  if (document.querySelector("#alerts.active") && al.unseen && !al.pages.length) markSeen();
}

let marking = false;
function markSeen() {
  if (marking) return;
  marking = true;
  // Left bold for a moment, so the new ones can be told from the old.
  setTimeout(() => { marking = false; post("/api/alerts/seen").catch(() => {}); }, 4000);
}

// ---------- screensaver ----------
let saver = null, saverTimer = null, saverIdle = null, saverOrder = [], saverFront = false;

async function loadPhotos() {
  try { saver = await get("/api/photos"); } catch { /* keep the last list */ }
}

function nextPhoto() {
  if (!saver || !saver.photos.length) return endSaver();
  if (!saverOrder.length) {
    saverOrder = saver.photos.map((_, i) => i).sort(() => Math.random() - .5);
  }
  const photo = saver.photos[saverOrder.pop() % saver.photos.length];
  const img = new Image();
  img.onload = () => {
    // Two layers, cross-faded.
    saverFront = !saverFront;
    const show = $(saverFront ? "saver-a" : "saver-b"), hide = $(saverFront ? "saver-b" : "saver-a");
    show.style.backgroundImage = `url("${photo.src}")`;
    show.classList.add("on"); hide.classList.remove("on");
    $("saver-caption").textContent = photo.caption;
  };
  img.onerror = () => { clearTimeout(saverTimer); saverTimer = setTimeout(nextPhoto, 1000); };
  img.src = photo.src;
  clearTimeout(saverTimer);
  saverTimer = setTimeout(nextPhoto, Math.max(5, saver.seconds) * 1000);
}

async function startSaver() {
  await loadPhotos();
  if (!saver || !saver.enabled || !saver.photos.length || !$("page").hidden) return armSaver();
  $("saver").hidden = false;
  $("saver-clock").hidden = !saver.clock;
  nextPhoto();
}

function endSaver() {
  clearTimeout(saverTimer);
  if (!$("saver").hidden) { $("saver").hidden = true; armSaver(); }
}

// The tap that ends it is used up here, so it can't press what's underneath.
$("saver").addEventListener("click", endSaver);

function armSaver() {
  clearTimeout(saverIdle);
  if (saver && saver.enabled && saver.idle_minutes > 0) saverIdle = setTimeout(startSaver, saver.idle_minutes * 60000);
}

// ---------- status, toast, tabs ----------
function drawStatus() {
  const notes = [];
  if (weather && weather.stale) notes.push("Weather offline");
  if (calendar && calendar.stale) notes.push("Calendar offline");
  if (state && state.intercom.enabled && !state.intercom.connected) notes.push("Intercom offline");
  if (state && state.multicast.some(g => !g.listening)) notes.push("Multicast off");
  const where = weather && weather.location;
  $("status").textContent = notes.join(" · ") || where || "";
}

let toastTimer = null;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5000);
}

function show(view) {
  for (const el of document.querySelectorAll(".view")) el.classList.toggle("active", el.id === view);
  for (const el of document.querySelectorAll("nav > button")) el.classList.toggle("active", el.dataset.view === view);
  if (view === "alerts" && state && state.alerts.unseen) markSeen();
}
for (const el of document.querySelectorAll("nav > button")) el.addEventListener("click", () => show(el.dataset.view));
$("stop-all").addEventListener("click", () => post("/api/intercom/stop").catch(e => toast(e.message)));

// Back to Home when nobody has touched the screen for a while; and a touch
// brightens a dimmed screen.
let idleTimer = null, lastWake = 0;
function touched() {
  clearTimeout(idleTimer);
  if ($("saver").hidden) armSaver();
  if (settings.idle_home_seconds > 0) {
    idleTimer = setTimeout(() => { $("confirm").hidden = true; show("home"); }, settings.idle_home_seconds * 1000);
  }
  if (Date.now() - lastWake > 20000) {
    lastWake = Date.now();
    fetch("/api/screen/wake", { method: "POST" }).catch(() => {});
  }
}
document.addEventListener("pointerdown", touched);

// ---------- loading ----------
async function get(path) {
  const resp = await fetch(path);
  const body = await resp.json();
  if (!resp.ok) throw new Error(body.error || `Failed (${resp.status})`);
  return body;
}

async function loadWeather() {
  try { weather = await get("/api/weather"); }
  catch (err) { if (!weather || weather.error) weather = { error: err.message }; else weather.stale = true; }
  drawWeather();
}

async function loadCalendar() {
  try { calendar = await get("/api/calendar"); }
  catch (err) { if (calendar) calendar.stale = true; }
  drawCalendar();
}

function listen() {
  const es = new EventSource("/api/events");
  es.addEventListener("state", e => {
    state = JSON.parse(e.data);
    state.at = Date.now();
    drawIntercom(); drawAlerts(); drawPage(); drawStatus();
  });
}

async function start() {
  drawClock();
  try { settings = await get("/api/settings"); } catch { /* the defaults do */ }
  if (settings.theme !== "auto") document.documentElement.dataset.theme = settings.theme;
  drawClock();
  setInterval(drawClock, 1000);
  loadWeather(); loadCalendar();
  setInterval(loadWeather, 5 * 60 * 1000);
  // Redrawn every minute too, so events that end drop off and midnight rolls over.
  setInterval(loadCalendar, 60 * 1000);
  listen();
  await loadPhotos();
  touched();
}
start();
