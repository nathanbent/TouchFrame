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
  for (const kid of kids.flat(Infinity)) {
    if (kid !== null && kid !== undefined && kid !== false) el.append(kid);
  }
  return el;
}

/** Replace an element's children, leaving out the ones that aren't there. */
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false && k !== ""));

let settings = { theme: "dark", clock: "12h", idle_home_seconds: 120, intercom: false, alerts: false, tabs: [] };
let weather = null;
let calendar = null;
let state = null;          // {intercom, alerts, multicast, now_ms}: what the server pushes

const DEG = "\u00b0", DOT = " \u00b7 ";

// ---------- icons ----------
const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
  home: "M4 11.5 12 4l8 7.5M6 10v9h12v-9",
  calendar: "M5 6h14v13H5zM5 10h14M9 4v3M15 4v3",
  alerts: "M6 17h12l-1.5-2.5V11a4.5 4.5 0 0 0-9 0v3.5zM10.5 20h3",
  lab: "M5 5h14v6H5zM5 13h14v6H5zM8 8h.01M8 16h.01",
  intercom: "M4 10v4h3l6 4V6l-6 4zM16.5 9a4 4 0 0 1 0 6",
  streams: "M4 17v-3M8 17V9M12 17v-6M16 17V6M20 17v-9",
  settings: "M5 7h7M16 7h3M5 12h2M11 12h8M5 17h9M18 17h1M14 5v4M9 10v4M16 15v4",
  check: "M5 12.5l4.5 4.5L19 7",
  warn: "M12 4.5 21 20H3zM12 10v4.5M12 17h.01",
  stop: "M6 6h12v12H6z",
  left: "M15 5l-7 7 7 7", right: "M9 5l7 7-7 7", up: "M5 15l7-7 7 7", down: "M5 9l7 7 7-7",
  minus: "M6 12h12", plus: "M12 6v12M6 12h12",
};

/** A line icon, drawn in the text colour. */
function ico(name, cls = "") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `ico ${cls}`);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

/** "All up" with a tick, "offline" with a warning sign: never the colour alone. */
const mark = (ok, text) => h("span", { class: `state ${ok ? "good" : "bad"}` }, ico(ok ? "check" : "warn", "small"), text);

// ---------- weather icons ----------
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
  const cloud = (d = CLOUD) => add("path", { d, fill: "var(--secondary)" });
  const sun = (cx, cy, r) => add("circle", { cx, cy, r, fill: "var(--sun)" });

  if (kind === "clear") {
    if (day) { sun(32, 32, 12); stroke(SUN_RAYS, "sun"); }
    else add("path", { d: MOON, fill: "var(--secondary)" });
  } else if (kind === "partly") {
    if (day) { sun(22, 22, 9); stroke("M22 5v4M5 22h4M10 10l3 3M34 10l-3 3", "sun", 3); }
    else add("path", { d: MOON, fill: "var(--secondary)", transform: "translate(-4 -2) scale(.6)", opacity: ".7" });
    cloud(SMALL_CLOUD);
  } else if (kind === "fog") {
    stroke("M12 24h40M8 34h48M14 44h36", "secondary", 4);
  } else {
    cloud();
    if (kind === "rain") stroke("M24 46l-3 9M34 46l-3 9M44 46l-3 9", "accent");
    if (kind === "snow") for (const x of [23, 33, 43]) add("circle", { cx: x, cy: 51, r: 3, fill: "var(--accent)" });
    if (kind === "storm") add("path", { d: "M35 40l-10 13h7l-3 10 11-14h-7z", fill: "var(--sun)" });
  }
  return svg;
}

// ---------- formatting ----------
const deg = v => `${Math.round(v)}${DEG}`;
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
    hours.replaceChildren(); days.replaceChildren(); $("sun").replaceChildren();
    return;
  }
  const cur = weather.current, today = weather.days[0];
  now.replaceChildren(
    icon(cur.kind, cur.day),
    h("div", {},
      h("div", { class: "temp num" }, deg(cur.temp)),
      h("div", { class: "label" }, cur.label),
      h("div", { class: "extra" },
        today && h("span", { class: "hi" }, `H ${deg(today.high)}`), " ",
        today && h("span", { class: "lo" }, `L ${deg(today.low)}`),
        `${DOT}Feels ${deg(cur.feels)}`),
      h("div", { class: "extra" }, `Wind ${Math.round(cur.wind)} ${weather.wind_unit}${DOT}Humidity ${cur.humidity}%`)));

  // The next 24 hours: eight on show, the rest a swipe away. Midnight is
  // marked with the day it starts.
  const keep = hours.scrollLeft;
  hours.replaceChildren(...weather.hours.map(hr => {
    const at = new Date(hr.time), midnight = at.getHours() === 0;
    return h("div", { class: `hour${midnight ? " newday" : ""}` },
      h("div", { class: "when" }, midnight ? WEEKDAYS[at.getDay()].slice(0, 3) : clockText(at, true)),
      icon(hr.kind, hr.day),
      h("div", { class: "t num" }, deg(hr.temp)),
      h("div", { class: "rain" }, hr.rain >= 10 && [
        h("span", { class: "rain-track" }, h("span", { class: "rain-fill", style: `width:${hr.rain}%` })),
        `${hr.rain}%`]));
  }));
  hours.scrollLeft = keep;

  // Under the date: sunrise and sunset, and the air.
  const air = weather.air;
  put($("sun"),
    today && today.sunrise && today.sunset &&
      h("span", {}, `Sun ${clockText(new Date(today.sunrise))} to ${clockText(new Date(today.sunset))}`),
    air && h("span", { class: `air level-${air.level}` }, `Air ${air.index}${DOT}${air.label}`));

  const keepDays = days.scrollLeft;
  days.replaceChildren(...weather.days.slice(1).map(d =>
    h("div", { class: "day" },
      h("div", { class: "name" }, WEEKDAYS[day(d.date).getDay()].slice(0, 3)),
      icon(d.kind),
      h("div", { class: "range" }, h("span", { class: "hi num" }, deg(d.high)), " ", h("span", { class: "lo num" }, deg(d.low))),
      h("div", { class: "rain" }, d.rain >= 10 ? `${d.rain}%` : ""))));
  days.scrollLeft = keepDays;

  applyTheme();
  drawStatus();
}

// Weather Service alerts: a banner on Home, and the full words on a tap.
function drawWarnings() {
  const list = state && state.warnings ? state.warnings : [];
  const el = $("warn");
  el.hidden = !list.length;
  $("home").classList.toggle("warned", list.length > 0);    // makes room for the banner
  if (!list.length) return;
  const first = list[0];
  let until = "";
  if (first.ends) {
    const d = new Date(first.ends);
    until = ` until ${clockText(d, true)}` + (ymd(d) === ymd(new Date()) ? "" : ` ${WEEKDAYS[d.getDay()].slice(0, 3)}`);
  }
  el.className = `warn-banner ${first.severity}`;
  put(el, ico("warn"), h("b", {}, first.event), until,
    h("span", { class: "more" }, list.length > 1 && `+${list.length - 1} more`, ico("right", "small")));
  el.onclick = () => detail(list.map(a => [
    h("h1", {}, a.event),
    a.headline && h("p", { class: "muted" }, a.headline),
    a.description && h("p", {}, a.description),
    a.instruction && h("p", {}, h("b", {}, a.instruction))]));
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
  const now = new Date(), on = !e.all_day && e.start <= now && new Date(e.stop) > now;
  return h("div", { class: `event${on ? " on" : ""}` },
    h("span", { class: `bar ${e.colour}` }),
    h("span", { class: "when" }, e.all_day ? "All day" : on ? "Now" : clockText(e.start)),
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
  const problem = calendarProblem();

  // Home: what's next, as many as fit.
  const next = $("next");
  next.replaceChildren(h("h2", {}, "Coming up"));
  next.onclick = () => goTab("calendar");
  if (problem !== null) next.append(h("div", { class: "empty" }, problem));
  else if (!days.length) next.append(h("div", { class: "empty" }, `Nothing in the next ${calendar.days} days`));
  let room = 3.4;
  for (const [d, events] of days) {
    if (room < 2) break;
    next.append(h("h2", {}, dayName(d)));
    room -= .6;
    for (const e of events.slice(0, Math.floor(room))) { next.append(eventRow(e)); room -= 1; }
  }

  drawAgenda(days, problem);
  drawMonth();
  drawStatus();
}

// The Calendar tab: a month that can be swiped (or stepped) to other months,
// and beside it what's coming up, the month being looked at, or a tapped day.
let monthOffset = 0, picked = null;
const otherMonths = {};     // "2026-12" -> events, fetched as they're opened

function shownMonth() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth() + monthOffset, 1);
}
const monthKey = (d = shownMonth()) => `${d.getFullYear()}-${d.getMonth() + 1}`;
const monthEvents = () => !calendar ? [] : monthOffset ? (otherMonths[monthKey()] || []) : calendar.events;

function drawAgenda(days = agendaDays(), problem = calendarProblem()) {
  const span = calendar ? `Next ${calendar.days} days` : "", first = shownMonth();
  put($("agenda-head"),
    h("h1", {}, picked ? dayName(day(picked)) : monthOffset ? `${MONTHS[first.getMonth()]} ${first.getFullYear()}` : span),
    (picked || monthOffset !== 0) && h("button", { class: "btn round", onclick: backToNow }, span));
  const agenda = $("agenda");
  agenda.replaceChildren();
  if (problem !== null) return agenda.append(h("div", { class: "empty" }, problem));
  if (picked || monthOffset) {
    // One day, or the whole of the month being looked at.
    const byDay = new Map();
    for (const e of monthEvents()) {
      const last = new Date(new Date(e.stop) - 1000);
      for (let d = day(e.start); d <= last; d.setDate(d.getDate() + 1)) {
        const key = ymd(d);
        if (picked ? key !== picked : d.getMonth() !== first.getMonth()) continue;
        if (!byDay.has(key)) byDay.set(key, []);
        byDay.get(key).push({ ...e, start: new Date(e.start), all_day: e.all_day || ymd(new Date(e.start)) !== key, until: null });
      }
    }
    for (const [key, list] of [...byDay.entries()].sort()) {
      if (!picked) agenda.append(h("h2", {}, dayName(day(key))));
      agenda.append(...list.sort((a, b) => (b.all_day - a.all_day) || (a.start - b.start) || a.title.localeCompare(b.title)).map(eventRow));
    }
    if (!byDay.size) agenda.append(h("div", { class: "empty" }, picked ? "Nothing on this day" : "Nothing this month"));
    return;
  }
  if (!days.length) agenda.append(h("div", { class: "empty" }, `Nothing in the next ${calendar.days} days`));
  for (const [d, events] of days) {
    agenda.append(h("h2", {}, dayName(d)), ...events.map(eventRow));
  }
}

function pick(date) {
  picked = date === picked ? null : date;
  drawAgenda();
  drawMonth();
}

function backToNow() {
  const slide = monthOffset > 0 ? "from-left" : monthOffset < 0 ? "from-right" : "";
  monthOffset = 0;
  picked = null;
  drawAgenda();
  drawMonth(slide);
}

function calendarProblem() {
  return !calendar ? "" : calendar.error ? calendar.error
    : !calendar.configured ? "Add a calendar: Settings, then Set up from another device" : null;
}

async function shiftMonth(by) {
  const to = Math.max(-12, Math.min(24, monthOffset + by));
  if (to === monthOffset) return;
  monthOffset = to;
  picked = null;
  drawAgenda();
  drawMonth(by > 0 ? "from-right" : "from-left");
  if (!monthOffset || !calendar || !calendar.configured) return;
  const key = monthKey();
  try {
    otherMonths[key] = (await get(`/api/calendar?month=${key}`)).events;
    if (monthOffset && monthKey() === key) { drawAgenda(); drawMonth(); }
  } catch (err) { toast(err.message); }
}

function drawMonth(slide = "") {
  const now = new Date(), first = shownMonth();
  const monday = calendar && calendar.week_start === "monday";
  const busy = new Set();
  for (const e of monthEvents()) {
    const last = new Date(new Date(e.stop) - 1000);
    for (let d = day(e.start); d <= last; d.setDate(d.getDate() + 1)) busy.add(ymd(d));
  }
  const grid = h("div", { class: `grid ${slide}` });
  const names = ["S", "M", "T", "W", "T", "F", "S"];
  for (let i = 0; i < 7; i++) grid.append(h("div", { class: "dow" }, names[(i + (monday ? 1 : 0)) % 7]));
  const d = new Date(first);
  d.setDate(1 - ((first.getDay() - (monday ? 1 : 0) + 7) % 7));
  do {
    for (let i = 0; i < 7; i++, d.setDate(d.getDate() + 1)) {
      const key = ymd(d);
      if (d.getMonth() !== first.getMonth()) { grid.append(h("div", { class: "d out" }, String(d.getDate()))); continue; }
      const cls = ["d"];
      if (key === ymd(now)) cls.push("today");
      if (busy.has(key)) cls.push("has");
      if (key === picked) cls.push("picked");
      grid.append(h("button", { class: cls.join(" "), onclick: () => pick(key) }, String(d.getDate())));
    }
  } while (d.getMonth() === first.getMonth());
  $("month").replaceChildren(
    h("div", { class: "month-head" },
      h("button", { "aria-label": "Earlier", disabled: monthOffset <= -12, onclick: () => shiftMonth(-1) }, ico("left")),
      h("h1", {}, `${MONTHS[first.getMonth()]} ${first.getFullYear()}`),
      h("button", { "aria-label": "Later", disabled: monthOffset >= 24, onclick: () => shiftMonth(1) }, ico("right"))),
    grid);
}

// ---------- streams ----------
// What this Pi is receiving over multicast. Asked for only while the tab is open.
let streamsTimer = null;

const bits = k => k >= 1000 ? `${(k / 1000).toFixed(1)} Mbit/s` : `${k < 10 ? k.toFixed(1) : Math.round(k)} kbit/s`;
const DSCP = { 0: "BE", 46: "EF", 34: "AF41", 26: "AF31", 48: "CS6", 40: "CS5", 32: "CS4", 24: "CS3" };
const none = v => v === null || v === undefined;

function streamRow(s) {
  if (s.silent) {
    return h("div", { class: "strow card silent" },
      h("div", { class: "st-main" }, h("b", {}, s.name || s.group), h("div", { class: "lab-sub" }, s.name ? s.group : "")),
      h("div", { class: "st-state" }, s.error ? mark(false, s.error) : "Nothing heard yet"));
  }
  const idle = s.idle_ms > 3000;
  const lossy = s.loss_percent >= 1;
  // Level: -60 dBFS to 0 across the bar.
  const level = none(s.peak_dbfs) ? null : Math.max(0, Math.min(100, (s.peak_dbfs + 60) / 60 * 100));
  const dscp = none(s.tos) ? "" : (DSCP[s.tos >> 2] || `DSCP ${s.tos >> 2}`);
  return h("div", { class: `strow card${idle ? " idle" : ""}` },
    h("div", { class: "st-main" },
      h("b", {}, s.name || s.group),
      h("div", { class: "lab-sub" }, [s.name ? s.group : null, s.source, s.codec || (!none(s.payload_type) ? `pt ${s.payload_type}` : null)].filter(Boolean).join(DOT)),
      level !== null && !idle && h("div", { class: "meter-track" }, h("div", { class: "meter-fill", style: `width:${level}%` }))),
    h("div", { class: "st-stats" },
      stat(idle ? "idle" : bits(s.kbit_per_sec), idle ? `${Math.round(s.idle_ms / 1000)} s` : "rate"),
      h("div", { class: `stat${lossy ? " bad" : ""}` }, h("b", { class: "num" }, `${s.loss_percent.toFixed(s.loss_percent < 10 ? 1 : 0)}%`), h("span", {}, lossy ? mark(false, `${s.lost} lost`) : `${s.lost} lost`)),
      stat(none(s.jitter_ms) ? "-" : `${s.jitter_ms < 10 ? s.jitter_ms.toFixed(1) : Math.round(s.jitter_ms)} ms`, "jitter"),
      stat(none(s.ttl) ? "-" : String(s.ttl), `TTL${dscp ? `${DOT}${dscp}` : ""}`)));
}

async function loadStreams() {
  clearTimeout(streamsTimer);
  if (current !== "streams") return;
  let data;
  try { data = await get("/api/streams"); } catch (err) { data = { error: err.message, streams: [], listeners: [] }; }
  const live = data.streams.filter(s => !s.silent), quiet = data.streams.filter(s => s.silent);
  live.sort((a, b) => (a.idle_ms > 3000) - (b.idle_ms > 3000) || (a.name || a.group).localeCompare(b.name || b.group));
  const list = $("streams-list"), keep = list.scrollTop;
  put(list,
    data.error && h("div", { class: "lab-error" }, mark(false, data.error)),
    live.map(streamRow), quiet.map(streamRow),
    data.listeners.length && h("h2", {}, "Receivers reporting"),
    data.listeners.filter(l => !l.left).map(l => h("div", { class: "strow card" },
      h("div", { class: "st-main" }, h("b", {}, l.name || l.from), h("div", { class: "lab-sub" }, `${l.group}${DOT}${l.about === null ? "hearing nothing" : "listening"}`)),
      h("div", { class: "st-stats" }, stat(none(l.loss_percent) ? "-" : `${l.loss_percent.toFixed(1)}%`, "loss")))),
    !data.error && !data.streams.length && h("div", { class: "empty" }, "No streams yet"));
  list.scrollTop = keep;
  streamsTimer = setTimeout(loadStreams, 2000);
}

// ---------- intercom ----------
async function post(path) {
  const resp = await fetch(path, { method: "POST" });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.error || `Failed (${resp.status})`);
  return body;
}

/** A question over the page. Resolves true for the first button, false for
 *  Cancel or if the screen is left alone until it goes back to its first tab. */
let dialogDone = null, dialogHold = false;
function ask(text, { sub = "", yes = "OK", no = "Cancel", danger = false } = {}) {
  closeDialog();
  return new Promise(resolve => {
    dialogDone = resolve;
    put($("dialog"), h("div", { class: "dialog-box card" },
      h("div", { class: "dialog-text" }, text),
      sub && h("div", { class: "dialog-sub" }, sub),
      h("div", { class: "dialog-buttons" },
        no && h("button", { class: "btn", onclick: () => closeDialog(false) }, no),
        h("button", { class: `btn ${danger ? "stop" : "primary"}`, onclick: () => closeDialog(true) }, yes))));
    $("dialog").hidden = false;
  });
}
/** Something long to read, over the page, with a Close button. */
function detail(...kids) {
  closeDialog();
  put($("dialog"), h("div", { class: "dialog-box wide card" },
    h("div", { class: "detail-text scroll" }, kids),
    h("div", { class: "dialog-buttons" }, h("button", { class: "btn", onclick: () => closeDialog() }, "Close"))));
  $("dialog").hidden = false;
}

function closeDialog(answer = false) {
  $("dialog").hidden = true;
  dialogHold = false;
  if (dialogDone) { const done = dialogDone; dialogDone = null; done(answer); }
}

async function press(button, run) {
  try {
    if (run) return await post(`/api/intercom/stop/${run.id}`);
    if (button.confirm && !await ask(`Play "${button.name}"?`, { yes: "Play", danger: true })) return;
    await post(`/api/intercom/play/${encodeURIComponent(button.id)}`);
  } catch (err) {
    toast(err.message);
  }
}

// ---------- push to talk ----------
// A live announcement's button: tap to talk, tap again to end. The
// microphone goes to this Pi's server in short posts, and from there to the
// intercom panel, so the panel's token never comes to the page.
let mic = null;             // {id, ctx, stream, chunks, sending, started, ended}
let talkSeen = 0;           // the last ended page whose reason was shown

function micProblem() {
  if (!window.isSecureContext) return "The microphone only works on this Pi's own screen (or over HTTPS)";
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.AudioWorkletNode) {
    return "This browser can't use a microphone";
  }
  return null;
}

async function sendAudio(m) {
  if (m.sending) return;
  m.sending = true;
  try {
    while (m.chunks.length && !m.ended) {
      const body = new Blob(m.chunks.splice(0));
      const resp = await fetch("/api/intercom/talk/audio", { method: "POST", body });
      if (!resp.ok) { stopTalking(false); break; }       // the page ended on the panel's side
    }
  } catch {
    stopTalking(false);
  } finally {
    m.sending = false;
  }
}

async function startTalking(button) {
  if (mic) return;
  const problem = micProblem();
  if (problem) return toast(problem);
  if (button.confirm && !await confirmPlay(`Talk on "${button.name}"?`)) return;
  const m = mic = { id: button.id, ctx: new AudioContext(), stream: null, chunks: [], sending: false, ended: false };
  drawIntercom();
  try {
    m.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    await m.ctx.audioWorklet.addModule("mic-worklet.js");
    if (m.ended) return;
    await post(`/api/intercom/talk/start/${encodeURIComponent(button.id)}`);
    if (m.ended) return post("/api/intercom/talk/end").catch(() => {});
    m.started = true;
    const node = new AudioWorkletNode(m.ctx, "mic-capture");
    node.port.onmessage = e => {
      m.chunks.push(e.data.pcm);
      if (m.chunks.length >= 5) sendAudio(m);             // 100 ms a post
    };
    m.ctx.createMediaStreamSource(m.stream).connect(node);
    await m.ctx.resume();
  } catch (err) {
    stopTalking(false);
    toast(err.name === "NotAllowedError" || err.name === "NotFoundError"
      ? "No microphone: plug one in, and allow this page to use it" : err.message);
  }
}

// Let go of the microphone; with `tell`, also end the page on the panel.
function stopTalking(tell) {
  const m = mic;
  if (!m) return;
  mic = null;
  m.ended = true;
  if (m.stream) for (const t of m.stream.getTracks()) t.stop();
  m.ctx.close().catch(() => {});
  if (tell) post("/api/intercom/talk/end").catch(() => {});
  drawIntercom();
}

function drawIntercom() {
  const ic = state && state.intercom;
  if (!ic || !ic.enabled || !settings.intercom_buttons) return;    // receive only: no buttons
  const runs = ic.runs;
  const talk = ic.talk || { now: null, last: null };
  // The panel ended the page (stopped there, or its groups were taken over).
  if (mic && mic.started && !talk.now) stopTalking(false);
  if (talk.last && talk.last.at_ms > talkSeen) {
    if (talkSeen && talk.last.reason) toast(`Talking ended: ${talk.last.reason}`);
    talkSeen = talk.last.at_ms;
  } else if (!talkSeen) {
    talkSeen = 1;
  }

  const status = $("ic-status");
  const bad = !ic.connected ? (ic.error || "Connecting to the intercom...")
    : !ic.backend_ok ? "The intercom can't reach its mcast server" : null;
  status.textContent = bad || (runs.length ? `Playing: ${runs.map(r => r.name).join(", ")}` : "Tap to play");
  status.className = "ic-status" + (bad && ic.error ? " bad" : "");
  $("stop-all").hidden = !runs.length;

  put($("ic-groups"), ic.groups.flatMap(g => [
    g.name && h("h2", {}, g.name),
    h("div", { class: "tiles" }, g.buttons.map(b => {
      const run = runs.find(r => r.announcement === b.id);
      if (b.live) {
        // Talking here, on another screen (a run with no microphone of ours), or idle.
        const mine = (mic && mic.id === b.id) || (talk.now && talk.now.announcement === b.id);
        const phase = !mine ? null : talk.now && talk.now.phase === "live" ? "live" : "starting";
        return h("button", {
          class: `tile ${b.color || ""}${mine || run ? " busy" : ""}${phase === "live" ? " talking" : ""}`,
          disabled: !ic.connected || !settings.intercom_talk || (mic && !mine),
          onclick: () => (mine ? stopTalking(true) : run ? press(b, run) : startTalking(b)),
        },
          h("span", { class: "emoji" }, b.icon || "🎙️"),
          h("span", { class: "name" }, b.name),
          h("span", { class: "sub" }, !settings.intercom_talk ? "Talk from the intercom's own page"
            : phase === "live" ? `Talking${DOT}tap to end` : phase ? "Starting..."
            : run ? `Someone is talking${DOT}tap to stop` : "Tap to talk"));
      }
      return h("button", {
        class: `tile ${b.color || ""}${run ? " busy" : ""}`,
        disabled: !ic.connected,
        onclick: () => press(b, run),
      },
        b.icon && h("span", { class: "emoji" }, b.icon),
        h("span", { class: "name" }, b.name),
        h("span", { class: "sub" }, run ? `Playing${DOT}tap to stop` : ""));
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
      h("button", { class: "btn stop round", onclick: () => post("/api/intercom/stop").catch(e => toast(e.message)) },
        ico("stop", "solid small"), "Stop"));
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
  el.className = `page ${page.priority}${page.image ? " pictured" : ""}`;
  $("page-image").hidden = !page.image;
  $("page-image").style.backgroundImage = page.image ? `url("${page.image}")` : "";
  el.hidden = false;
  if (pageShown !== page.id) {
    // A new one: flash, so it's noticed from across the room.
    pageShown = page.id;
    el.classList.remove("arrive"); void el.offsetWidth; el.classList.add("arrive");
    endSaver();
  } else el.classList.add("arrive");
  // The biggest type that fits.
  let size = page.image ? 1.2 : 6;
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

const showPicture = src => detail(h("img", { class: "detail-image", src, alt: "" }));

function drawAlerts() {
  if (!state) return;
  const al = state.alerts, ic = state.intercom;
  setBadge("alerts", al.unseen);

  // Where alerts come from, and whether each is working.
  const sources = [];
  if (ic.enabled) sources.push(h("span", { class: `src ${ic.connected ? "ok" : "bad"}` }, ic.connected ? "Intercom panel" : "Intercom panel offline"));
  for (const g of state.multicast) {
    sources.push(h("span", { class: `src ${g.listening ? "ok" : "bad"}` },
      g.listening ? `Multicast ${g.group}${g.pages ? `${DOT}${g.pages} heard` : ""}` : `Multicast ${g.group}: ${g.error || "starting"}`));
  }
  $("al-sources").replaceChildren(...sources);

  const list = $("al-list");
  list.replaceChildren(...al.history.map(a =>
    h("div", { class: `alert card ${a.priority}${a.seen ? "" : " unseen"}` },
      a.image && h("img", { class: "al-thumb", src: a.image, alt: "", onclick: () => showPicture(a.image) }),
      h("div", { class: "al-when" }, ago(a.at_ms)),
      h("div", { class: "al-body" },
        h("div", { class: "al-text" }, a.text.join(" ")),
        h("div", { class: "al-from" }, [a.name, ...a.sources.map(s => s === "panel" ? "via panel" : `via ${s}`)].join(DOT))))));
  if (!al.history.length) list.append(h("div", { class: "empty" }, "No alerts yet"));
  if (current === "alerts" && al.unseen && !al.pages.length) markSeen();
}

let marking = false;
function markSeen() {
  if (marking) return;
  marking = true;
  // Left bold for a moment, so the new ones can be told from the old.
  setTimeout(() => { marking = false; post("/api/alerts/seen").catch(() => {}); }, 4000);
}

// ---------- lab ----------
const SEVERITIES = ["Not classified", "Information", "Warning", "Average", "High", "Disaster"];

function age(ms) {
  const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (mins < 60) return `${mins}m`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

const num = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n.toLocaleString();

/** A labelled bar from 0 to 100%. The number carries the value; the bar turns
 *  amber, then red, only as a warning that it's nearly full. */
function meter(label, percent) {
  const level = percent >= 90 ? " serious" : percent >= 75 ? " warn" : "";
  return h("div", { class: "meter" },
    h("div", { class: "meter-text" }, h("span", {}, label), h("b", { class: "num" }, `${percent}%`)),
    h("div", { class: "meter-track" }, h("div", { class: `meter-fill${level}`, style: `width:${Math.max(1, Math.min(100, percent))}%` })));
}

/** A number with its label under it. */
const stat = (value, label) => h("div", { class: "stat" }, h("b", { class: "num" }, value), h("span", {}, label));

function tile(kind, name, t, ...body) {
  const waiting = t && t.ok === null;
  return h(kind === "link" ? "button" : "div", { class: `ltile card ${kind}` },
    h("div", { class: "ltile-name" }, h("h2", {}, name),
      t && t.ok === false && mark(false, "offline")),
    waiting ? h("div", { class: "muted" }, "Checking...") : body,
    t && t.ok === false && !body.flat(Infinity).some(Boolean) && h("div", { class: "lab-error" }, t.error));
}

function serviceTiles(t) {
  if (t.kind === "proxmox") {
    if (!t.nodes) return [tile("proxmox", t.name, t)];
    return t.nodes.map(n => tile("proxmox", n.name, t,
      n.online ? [meter("CPU", n.cpu), meter("Memory", n.mem)] : mark(false, "Node offline"),
      h("div", { class: "ltile-foot" }, `${n.vms[0]}/${n.vms[1]} VMs` + (n.lxc[1] ? `${DOT}${n.lxc[0]}/${n.lxc[1]} LXC` : ""))));
  }
  if (t.kind === "adguard") {
    return [tile("adguard", t.name, t, t.queries !== undefined && [
      h("div", { class: "stats" }, stat(num(t.queries), "queries"), stat(`${t.percent}%`, "blocked")),
      h("div", { class: "ltile-foot" }, t.protecting ? `${t.latency_ms} ms average` : mark(false, "Protection is off"))])];
  }
  if (t.kind === "unifi") {
    const up = t.wan === "ok";
    return [tile("unifi", t.name, t, t.aps !== undefined && [
      h("div", { class: "stats" }, stat(String(t.wifi_clients), "Wi-Fi"), stat(String(t.wired_clients), "wired"), stat(String(t.aps), t.aps === 1 ? "AP" : "APs")),
      h("div", { class: "ltile-foot" },
        t.wan ? mark(up, up ? "Internet up" : `Internet: ${t.wan}`) : "",
        t.aps_down ? mark(false, `${t.aps_down} AP down`) : "")])];
  }
  if (t.kind === "plex") {
    return [tile("plex", t.name, t, t.streams && (t.streams.length ? t.streams.slice(0, 3).map(s =>
      h("div", { class: "stream" },
        h("div", { class: "stream-title" }, s.title),
        h("div", { class: "stream-sub" }, [s.user, s.state === "paused" ? "paused" : null, s.transcoding ? "transcoding" : null].filter(Boolean).join(DOT)),
        h("div", { class: "meter-track" }, h("div", { class: "meter-fill", style: `width:${Math.round(s.progress * 100)}%` })))).concat(
      t.streams.length > 3 ? [h("div", { class: "ltile-foot" }, `and ${t.streams.length - 3} more`)] : [])
      : h("div", { class: "muted" }, "Nothing playing")))];
  }
  if (t.kind === "sonarr" || t.kind === "radarr") {
    return [tile(t.kind, t.name, t, t.total !== undefined && [
      h("div", { class: "stats" }, stat(String(t.wanted), "wanted"), stat(String(t.queued), "queued")),
      h("div", { class: "ltile-foot" }, `${num(t.total)} ${t.noun}`)])];
  }
  if (t.kind === "overseerr") {
    return [tile(t.kind, t.name, t, t.pending !== undefined && [
      h("div", { class: "stats" }, stat(String(t.pending), "pending"), stat(String(t.processing), "processing")),
      h("div", { class: "ltile-foot" }, t.pending ? mark(false, `${t.pending} waiting for an answer`) : `${num(t.available)} available`)])];
  }
  if (t.kind === "sabnzbd" || t.kind === "qbittorrent") {
    const sab = t.kind === "sabnzbd";
    return [tile(t.kind, t.name, t, t.down !== undefined && [
      h("div", { class: "stats" }, stat(speed(t.down), "down"), sab ? stat(String(t.queued), "queued") : stat(speed(t.up), "up")),
      h("div", { class: "ltile-foot" }, sab ? (t.paused ? "Paused" : t.queued ? `${t.left} left` : "Idle") : `${t.leeching} downloading${DOT}${t.seeding} seeding`)])];
  }
  if (t.kind === "portainer") {
    return [tile(t.kind, t.name, t, t.running !== undefined && [
      h("div", { class: "stats" }, stat(String(t.running), "running"), stat(String(t.stopped), "stopped")),
      h("div", { class: "ltile-foot" }, t.unhealthy.length ? mark(false, `unhealthy: ${t.unhealthy.join(", ")}`) : t.stopped ? "" : mark(true, "All running"))])];
  }
  return [];
}

/** Bytes a second, in words. */
const speed = b => b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB/s` : b >= 1024 ? `${Math.round(b / 1024)} kB/s` : "0";

const openProblems = () => go("problems");

function promTile(p) {
  const down = p.total === undefined ? 0 : p.total - p.up;
  const el = tile("link", "Prometheus", p, p.total !== undefined && [
    h("div", { class: "stats" }, stat(`${p.up}/${p.total}`, "targets up")),
    h("div", { class: "ltile-foot" }, down ? [mark(false, `${down} down`), ico("right", "small")] : mark(true, "All up"))]);
  el.addEventListener("click", openProblems);
  return el;
}

function zbxTile(z) {
  const el = tile("link", "Zabbix", z, z.counts && [
    h("div", { class: "chips" }, [5, 4, 3, 2].filter(s => z.counts[s]).map(s => h("span", { class: `chip sev-${s}` }, `${z.counts[s]} ${SEVERITIES[s]}`))),
    h("div", { class: "ltile-foot" }, z.total ? [`${z.total} problems`, ico("right", "small")] : mark(true, "No problems"))]);
  el.addEventListener("click", openProblems);
  return el;
}

function drawLab() {
  if (!state || !settings.lab) return;
  const prom = state.lab.prometheus, zbx = state.lab.zabbix, tiles = state.lab.tiles;

  // Overview (tiles) and Problems (the lists) are two pages, side by side.
  // The switch at the top of each shows when there are both.
  const both = tiles.length && (prom || zbx);
  for (const el of document.querySelectorAll(".switch")) {
    el.hidden = !both;
    put(el, [["lab", "Overview"], ["problems", "Problems"]].map(([view, label]) =>
      h("button", { class: view === el.dataset.lab ? "active" : "", onclick: () => go(view) }, label)));
  }
  put($("lab-tiles"), tiles.flatMap(serviceTiles), prom && promTile(prom), zbx && zbxTile(zbx));

  $("lab-prom").hidden = !prom;
  if (prom) {
    const known = prom.total !== undefined;
    put($("lab-prom-head"),
      h("h2", {}, "Prometheus"),
      known && h("div", { class: "big num" }, h("span", { class: prom.down.length ? "" : "good" }, String(prom.up)), h("span", { class: "muted" }, ` / ${prom.total} up`)),
      prom.error && h("div", { class: "lab-error" }, prom.ok === false ? `Not answering: ${prom.error}` : ""));
    $("lab-prom-list").replaceChildren(...(known ? prom.down : []).map(t =>
      h("div", { class: "lab-row card sev-4" },
        h("div", { class: "lab-main" }, h("b", {}, t.instance), h("div", { class: "lab-sub" }, t.job)))));
    if (known && !prom.down.length) $("lab-prom-list").append(h("div", { class: "empty" }, "Every target is up"));
  }

  $("lab-zbx").hidden = !zbx;
  if (zbx) {
    const known = zbx.counts !== undefined;
    put($("lab-zbx-head"),
      h("h2", {}, "Zabbix problems"),
      known && h("div", { class: "chips" }, [5, 4, 3, 2, 1, 0].filter(s => zbx.counts[s]).map(s =>
        h("span", { class: `chip sev-${s}` }, `${zbx.counts[s]} ${SEVERITIES[s]}`))),
      zbx.error && h("div", { class: "lab-error" }, `Not answering: ${zbx.error}`));
    $("lab-zbx-list").replaceChildren(...(known ? zbx.problems : []).map(p =>
      h("div", { class: `lab-row card sev-${p.severity}${p.acknowledged ? " acked" : ""}` },
        h("div", { class: "lab-main" }, h("b", {}, p.host), h("div", { class: "lab-sub" }, p.name)),
        h("div", { class: "lab-age" }, age(p.at_ms)))));
    if (known && !zbx.total) $("lab-zbx-list").append(h("div", { class: "empty" }, "No problems"));
    if (known && zbx.total > zbx.problems.length) {
      $("lab-zbx-list").append(h("div", { class: "empty" }, `and ${zbx.total - zbx.problems.length} more`));
    }
  }

  // The tab is marked while something is High or Disaster.
  setBadge("lab", zbx && zbx.counts ? zbx.counts[5] + zbx.counts[4] : 0);
}

// ---------- screensaver ----------
// A tap ends it; a swipe goes to the next photo, or back to the last.
let saver = null, saverTimer = null, saverIdle = null, saverOrder = [], saverFront = false;
let saverSeen = [], saverAt = -1;

async function loadPhotos() {
  try { saver = await get("/api/photos"); } catch { /* keep the last list */ }
}

function showPhoto(step = 1, quick = false) {
  if (!saver || !saver.photos.length) return endSaver();
  saverAt += step;
  if (saverAt < 0) saverAt = 0;
  if (saverAt >= saverSeen.length) {
    if (!saverOrder.length) saverOrder = saver.photos.map((_, i) => i).sort(() => Math.random() - .5);
    saverSeen.push(saverOrder.pop());
    if (saverSeen.length > 50) { saverSeen.shift(); }
    saverAt = saverSeen.length - 1;
  }
  const photo = saver.photos[saverSeen[saverAt] % saver.photos.length];
  const img = new Image();
  img.onload = () => {
    // Two layers, cross-faded.
    saverFront = !saverFront;
    const show = $(saverFront ? "saver-a" : "saver-b"), hide = $(saverFront ? "saver-b" : "saver-a");
    $("saver").classList.toggle("quick", quick);
    show.style.backgroundImage = `url("${photo.src}")`;
    show.classList.add("on"); hide.classList.remove("on");
    $("saver-caption").textContent = photo.caption;
  };
  img.onerror = () => { clearTimeout(saverTimer); saverTimer = setTimeout(showPhoto, 1000); };
  img.src = photo.src;
  clearTimeout(saverTimer);
  saverTimer = setTimeout(showPhoto, Math.max(5, saver.seconds) * 1000);
}

async function startSaver(asked) {
  await loadPhotos();
  if (asked === true && saver && !saver.photos.length) return toast("No photos yet: copy some into the photo folder");
  if (!saver || (!saver.enabled && asked !== true) || !saver.photos.length || !$("page").hidden || !$("dialog").hidden) return armSaver();
  $("saver").hidden = false;
  $("saver-clock").hidden = !saver.clock;
  showPhoto();
}

function endSaver() {
  clearTimeout(saverTimer);
  if (!$("saver").hidden) { $("saver").hidden = true; armSaver(); }
}

// The touch that ends it is used up here, so it can't press what's underneath.
{
  let from = null;
  $("saver").addEventListener("pointerdown", e => { from = e.clientX; });
  $("saver").addEventListener("pointerup", e => {
    const dx = from === null ? 0 : e.clientX - from;
    from = null;
    if (Math.abs(dx) < 40) endSaver();
    else showPhoto(dx < 0 ? 1 : -1, true);
  });
}

function armSaver() {
  clearTimeout(saverIdle);
  if (saver && saver.enabled && saver.idle_minutes > 0) saverIdle = setTimeout(startSaver, saver.idle_minutes * 60000);
}

// ---------- status, toast ----------
function drawStatus() {
  const notes = [];
  // Only on this machine's own screen: another browser draws emoji with its own fonts.
  if (settings.emoji_font === false && ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) notes.push("No emoji font");
  if (weather && weather.stale) notes.push("Weather offline");
  if (calendar && calendar.stale) notes.push("Calendar offline");
  if (state && state.intercom.enabled && !state.intercom.connected) notes.push("Intercom offline");
  if (state && state.multicast.some(g => !g.listening)) notes.push("Multicast off");
  if (state && [state.lab.prometheus, state.lab.zabbix, ...state.lab.tiles].some(s => s && s.ok === false)) notes.push("Lab offline");
  const where = weather && weather.location;
  $("status").textContent = notes.join(DOT) || where || "";
  $("status").hidden = !$("playing").hidden;
}

let toastTimer = null;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5000);
}

function setBadge(tab, count) {
  const badge = document.querySelector(`.tab[data-tab="${tab}"] .badge`);
  if (!badge) return;
  badge.hidden = !count;
  badge.textContent = count;
}

// ---------- tabs and pages ----------
// Each tab is one page, except Lab, which is two (Overview and Problems).
// The pages sit side by side: swipe left and right to move between them, or
// tap a tab.
const TAB_NAMES = { home: "Home", calendar: "Calendar", alerts: "Alerts", streams: "Streams", lab: "Lab", intercom: "Intercom", settings: "Settings" };
const TAB_ORDER = ["home", "calendar", "alerts", "streams", "lab", "intercom"];
const main = $("pager");
let pages = ["home"], current = "home", settling = null;

const tabReady = tab => tab === "alerts" ? !!settings.alerts : tab === "lab" ? !!settings.lab
  : tab === "streams" ? !!settings.streams
  : tab === "intercom" ? !!settings.intercom_buttons : true;

function tabViews(tab) {
  if (tab !== "lab") return [tab];
  if (!state) return ["lab"];
  const lab = state.lab, views = [];
  if (lab.tiles.length) views.push("lab");
  if (lab.prometheus || lab.zabbix) views.push("problems");
  return views.length ? views : ["lab"];
}

/** Work out which tabs and pages there are, and redraw the tab bar if they changed. */
function layout() {
  const wanted = (settings.tabs && settings.tabs.length ? settings.tabs : TAB_ORDER).filter(tabReady);
  const tabs = wanted.length ? wanted : ["home"];
  const next = tabs.flatMap(tabViews);
  if (settings.can_edit) next.push("settings");
  $("tab-settings").hidden = !settings.can_edit;
  if (!$("tab-settings").firstChild) $("tab-settings").append(ico("settings"));
  if (next.join() === pages.join() && $("tabs").childElementCount === tabs.length) return;
  pages = next;
  put($("tabs"), tabs.map(tab => h("button", { class: "tab", "data-tab": tab },
    ico(tab), TAB_NAMES[tab], h("span", { class: "badge", hidden: true }))));
  if (!pages.includes(current)) go(pages[0], false);
  markTab();
  if (state) { drawAlerts(); drawLab(); }       // the badges were just made anew
}

function markTab() {
  const tab = $(current).dataset.tab;
  for (const el of document.querySelectorAll(".tab")) el.classList.toggle("active", el.dataset.tab === tab);
}

/** Put the pages where they belong once a slide has finished (or straight away). */
function settle(view = settling) {
  clearTimeout(settle.timer);
  settling = null;
  main.classList.remove("settle");
  const was = current;
  current = view;
  for (const el of document.querySelectorAll(".view")) {
    el.classList.toggle("active", el.id === view);
    el.classList.remove("peek");
    el.style.transform = "";
  }
  markTab();
  if (was !== view) shown(view, was);
}

/** Slide `from` out and `to` in. `dir` is 1 when `to` comes from the right. */
function slide(from, to, dir) {
  main.classList.add("settle");
  $(from).style.transform = `translateX(${-dir * 100}%)`;
  $(to).style.transform = "translateX(0)";
  settling = to;
  settle.timer = setTimeout(settle, 300);
}

function go(view, animate = true) {
  if (settling) settle();
  if (!pages.includes(view) || view === current) return;
  if (!animate) return settle(view);
  const dir = pages.indexOf(view) > pages.indexOf(current) ? 1 : -1;
  const to = $(view);
  to.classList.add("peek");
  to.style.transform = `translateX(${dir * 100}%)`;
  void to.offsetWidth;                // so the slide starts from there
  slide(current, view, dir);
}

const goTab = tab => go(pages.find(p => $(p).dataset.tab === tab));
function step(by) {
  if (settling) settle();             // a second step lands a page further on, not on the same one
  const to = pages[pages.indexOf(current) + by];
  if (to) go(to);
}

function shown(view, was) {
  if (view === "alerts" && state && state.alerts.unseen) markSeen();
  if (was === "calendar" && (picked || monthOffset)) { picked = null; monthOffset = 0; drawCalendar(); }
  if (view === "streams") loadStreams();
  if (view === "settings") drawSettings();
}

document.querySelector("nav").addEventListener("click", e => {
  const tab = e.target.closest(".tab");
  if (tab) goTab(tab.dataset.tab);
});
document.addEventListener("keydown", e => {
  if (e.key === "ArrowLeft") step(-1);
  if (e.key === "ArrowRight") step(1);
});

// Swiping. A finger that moves sideways drags the page with it; up and down
// is left to the lists. Strips that scroll sideways themselves (the hours,
// the days) keep their own swipes, and on the month a swipe changes the month.
{
  let drag = null, swallow = 0;

  main.addEventListener("pointerdown", e => {
    if (!e.isPrimary || e.button > 0 || e.target.closest(".hscroll, input")) return;
    if (settling) settle();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, lastX: e.clientX, lastT: e.timeStamp, v: 0,
             locked: false, month: !!e.target.closest('[data-swipe="month"]'), other: null, dx: 0 };
  });

  main.addEventListener("pointermove", e => {
    if (!drag || e.pointerId !== drag.id) return;
    let dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.locked) {
      if (Math.abs(dy) > 10 && Math.abs(dy) >= Math.abs(dx)) { drag = null; return; }
      if (Math.abs(dx) < 10) return;
      drag.locked = true;
      drag.x += Math.sign(dx) * 10;         // start from where it was caught, without a jump
      dx = e.clientX - drag.x;
      try { main.setPointerCapture(e.pointerId); } catch { /* the pointer has gone */ }
    }
    if (e.timeStamp > drag.lastT) {
      drag.v = .7 * (e.clientX - drag.lastX) / (e.timeStamp - drag.lastT) + .3 * drag.v;
      drag.lastX = e.clientX; drag.lastT = e.timeStamp;
    }
    drag.dx = dx;
    if (drag.month || !dx) return;
    const other = pages[pages.indexOf(current) + (dx < 0 ? 1 : -1)] || null;
    if (other !== drag.other) {
      if (drag.other) { $(drag.other).classList.remove("peek"); $(drag.other).style.transform = ""; }
      if (other) $(other).classList.add("peek");
      drag.other = other;
    }
    const move = other ? dx : dx * .25;     // nothing that way: it gives a little, and springs back
    $(current).style.transform = `translateX(${move}px)`;
    if (other) $(other).style.transform = `translateX(${move + (dx < 0 ? 1 : -1) * main.clientWidth}px)`;
  });

  const end = (e, cancelled) => {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    if (!d.locked) return;
    swallow = performance.now() + 350;      // this was a swipe, not a press
    if (d.month) {
      if (!cancelled && Math.abs(d.dx) > 40) shiftMonth(d.dx < 0 ? 1 : -1);
      return;
    }
    const flick = Math.abs(d.v) > .4 && Math.sign(d.v) === Math.sign(d.dx);
    const dir = d.dx < 0 ? 1 : -1;
    if (d.other && !cancelled && (flick || Math.abs(d.dx) > main.clientWidth * .3)) return slide(current, d.other, dir);
    // Not far enough: back where it was.
    main.classList.add("settle");
    $(current).style.transform = "translateX(0)";
    if (d.other) $(d.other).style.transform = `translateX(${dir * 100}%)`;
    settling = current;
    settle.timer = setTimeout(settle, 300);
  };
  main.addEventListener("pointerup", e => end(e, false));
  main.addEventListener("pointercancel", e => end(e, true));
  window.addEventListener("click", e => {
    if (performance.now() < swallow) { e.stopPropagation(); e.preventDefault(); }
  }, true);
}

$("stop-all").append(ico("stop", "solid small"), "All stop");
$("stop-all").addEventListener("click", () => post("/api/intercom/stop").catch(e => toast(e.message)));

// Back to the first tab when nobody has touched the screen for a while; and
// a touch brightens a dimmed screen.
let idleTimer = null, lastWake = 0;
function touched() {
  clearTimeout(idleTimer);
  if ($("saver").hidden) armSaver();
  if (settings.idle_home_seconds > 0) {
    idleTimer = setTimeout(() => {
      if (dialogHold) return touched();     // someone is reading a code off the screen
      closeDialog();
      go(pages[0]);
    }, settings.idle_home_seconds * 1000);
  }
  if (Date.now() - lastWake > 20000) {
    lastWake = Date.now();
    fetch("/api/screen/wake", { method: "POST" }).catch(() => {});
  }
}
document.addEventListener("pointerdown", touched);

// ---------- settings ----------
// The things worth changing with a finger. Everything that needs typing
// (addresses, tokens, calendar links) is on the settings page, opened from
// another device: "Set up from another device" shows how.
async function save(patch) {
  try {
    const resp = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.error || `Failed (${resp.status})`);
    if (body.restart) $("wait").hidden = false;
    else await loadSettings();
    return true;
  } catch (err) {
    toast(err.message);
    drawSettings();
    return false;
  }
}

const row = (label, hint, control, off = false) => h("div", { class: `row${off ? " off" : ""}` },
  h("div", { class: "row-label" }, label, hint && h("small", {}, hint)),
  h("div", { class: "row-control" }, control));

const seg = (options, value, change) => h("div", { class: "seg" }, options.map(([v, label]) =>
  h("button", { class: v === value ? "active" : "", onclick: () => v !== value && change(v) }, label)));

const toggle = (label, value, change) => h("button", { class: "toggle", role: "switch", "aria-label": label,
  "aria-checked": String(!!value), onclick: () => change(!value) }, h("i"));

/** Minus and plus through a list of values (the current one is added if it isn't there). */
function stepper(values, value, text, change) {
  const list = [...new Set([...values, value])].sort((a, b) => a - b), at = list.indexOf(value);
  return h("div", { class: "stepper" },
    h("button", { class: "btn square", "aria-label": "Less", disabled: at <= 0, onclick: () => change(list[at - 1]) }, ico("minus")),
    h("div", { class: "value num" }, text(value)),
    h("button", { class: "btn square", "aria-label": "More", disabled: at >= list.length - 1, onclick: () => change(list[at + 1]) }, ico("plus")));
}

const span = s => s === 0 ? "Never" : s < 60 ? `${s} s` : s % 3600 === 0 ? `${s / 3600} h` : `${Math.round(s / 60)} min`;
const hour = hr => settings.clock === "24h" ? `${String(hr).padStart(2, "0")}:00` : `${hr % 12 || 12} ${hr < 12 ? "am" : "pm"}`;

let sliding = false, slideTimer = null, railPick = null;

function drawSettings() {
  if (current !== "settings" || sliding || !settings.can_edit) return;
  const s = settings, body = $("set-body"), keep = body.scrollTop;
  const group = (...rows) => h("div", { class: "set-group card" }, rows);
  const dim = s.dim_hours.length === 2;

  // The tabs, in order: the ones shown first, then the hidden ones.
  const shownTabs = s.tabs.length ? s.tabs : TAB_ORDER;
  const order = [...shownTabs, ...TAB_ORDER.filter(t => !shownTabs.includes(t))];
  const setTabs = list => save({ "app.tabs": list.join() === TAB_ORDER.join() ? [] : list });
  const move = (tab, by) => {
    const list = [...shownTabs], at = list.indexOf(tab);
    [list[at], list[at + by]] = [list[at + by], list[at]];
    setTabs(list);
  };

  const sections = [
    ["Display", group(
      row("Theme", "Auto is light by day, dark at night", seg([["dark", "Dark"], ["light", "Light"], ["auto", "Auto"]], s.theme, v => save({ "app.theme": v }))),
      s.backlight
        ? row("Brightness", "", [
          h("input", { class: "slider", type: "range", min: 10, max: 100, step: 5, value: s.brightness, style: `--fill:${(s.brightness - 10) / .9}%`,
            "aria-label": "Brightness",
            onpointerdown: () => { sliding = true; },
            oninput: e => {
              const v = Number(e.target.value);
              e.target.style.setProperty("--fill", `${(v - 10) / .9}%`);
              e.target.nextSibling.textContent = `${v}%`;
              clearTimeout(slideTimer);
              slideTimer = setTimeout(() => save({ "screen.brightness": v }), 120);
            },
            onpointerup: () => { sliding = false; }, onpointercancel: () => { sliding = false; },
            onchange: () => { sliding = false; } }),
          h("div", { class: "slider-value num" }, `${s.brightness}%`)])
        : row("Brightness", "This display has no backlight the Pi can control", h("span", { class: "muted" }, "Not available")),
      s.backlight && row("Dim at night", "", toggle("Dim at night", dim, on => save({ "screen.dim_hours": on ? [22, 7] : [] }))),
      s.backlight && dim && row("Dim from", "", stepper([...Array(24).keys()].filter(v => v !== s.dim_hours[1]), s.dim_hours[0], hour, v => save({ "screen.dim_hours": [v, s.dim_hours[1]] }))),
      s.backlight && dim && row("Dim until", "", stepper([...Array(24).keys()].filter(v => v !== s.dim_hours[0]), s.dim_hours[1], hour, v => save({ "screen.dim_hours": [s.dim_hours[0], v] }))),
      s.backlight && dim && row("Brightness while dimmed", "", stepper([5, 10, 15, 20, 30, 40, 50], s.dim_brightness, v => `${v}%`, v => save({ "screen.dim_brightness": v }))))],
    ["Clock and units", group(
      row("Clock", "", seg([["12h", "12-hour"], ["24h", "24-hour"]], s.clock, v => save({ "app.clock": v }))),
      row("Temperature", "", seg([["imperial", `${DEG}F`], ["metric", `${DEG}C`]], s.units, v => save({ "weather.units": v }))),
      row("Week starts on", "", seg([["sunday", "Sunday"], ["monday", "Monday"]], s.week_start, v => save({ "calendar.week_start": v }))))],
    ["Tabs", group(
      order.map(tab => {
        const on = shownTabs.includes(tab), at = shownTabs.indexOf(tab);
        return row(TAB_NAMES[tab], !tabReady(tab) ? "Not set up yet" : tab === "alerts" && !on ? "Alerts still take over the screen" : "", [
          h("button", { class: "btn square", "aria-label": "Earlier", disabled: !on || at === 0, onclick: () => move(tab, -1) }, ico("up")),
          h("button", { class: "btn square", "aria-label": "Later", disabled: !on || at === shownTabs.length - 1, onclick: () => move(tab, 1) }, ico("down")),
          toggle(TAB_NAMES[tab], on, v => {
            if (!v && shownTabs.filter(tabReady).length <= 1 && tabReady(tab)) return toast("One tab has to stay");
            setTabs(v ? [...shownTabs, tab] : shownTabs.filter(t => t !== tab));
          })], !on);
      }),
      row("Back to the first tab", "After the screen is left alone", stepper([0, 30, 60, 120, 300, 600], s.idle_home_seconds, span, v => save({ "app.idle_home_seconds": v }))))],
    ["Screensaver", group(
      row("Show photos", "When the screen is left alone", toggle("Show photos", s.saver.enabled, v => save({ "screensaver.enabled": v }))),
      s.saver.enabled && row("Start after", "", stepper([1, 2, 5, 10, 15, 30, 60], s.saver.idle_minutes, v => span(v * 60), v => save({ "screensaver.idle_minutes": v }))),
      s.saver.enabled && row("Each photo stays", "", stepper([10, 15, 30, 60, 120, 300], s.saver.seconds, span, v => save({ "screensaver.seconds": v }))),
      s.saver.enabled && row("Picture of the day", "From Wikimedia Commons", toggle("Picture of the day", s.saver.picture_of_the_day, v => save({ "screensaver.picture_of_the_day": v }))),
      s.saver.enabled && row("Space photos", "From NASA's image library", toggle("Space photos", s.saver.nasa, v => save({ "screensaver.nasa": v }))),
      s.saver.enabled && row("Show the time", "", toggle("Show the time", s.saver.clock, v => save({ "screensaver.clock": v }))))],
    ["Alerts", group(
      row("Emergencies stay up", "Until someone taps them", toggle("Emergencies stay up", s.emergency_stays, v => save({ "alerts.emergency_stays": v }))))],
    ["Other devices", group(
      row("Set up from another device", "Calendars, weather, the intercom and the Lab are set from a phone or computer",
        h("button", { class: "btn primary", onclick: pair }, "Show code")),
      s.paired > 0 && row(`${s.paired} ${s.paired === 1 ? "device" : "devices"} remembered`, "They can open the settings page without a code",
        h("button", { class: "btn", onclick: forget }, "Forget")),
      s.network && row("Reachable from other devices", "For the settings page and webhooks",
        toggle("Reachable from other devices", true, () => network(false))))],
    ["This screen", [group(
      row("Show photos now", "", h("button", { class: "btn", onclick: () => startSaver(true) }, "Show")),
      s.alerts && row("Send a test alert", "Takes over the screen for a few seconds", h("button", { class: "btn", onclick: () => post("/api/alerts/test").catch(e => toast(e.message)) }, "Send")),
      row("Restart TouchFrame", "Takes a few seconds", h("button", { class: "btn", onclick: restart }, "Restart")),
      row("Reload this page", "", h("button", { class: "btn", onclick: () => location.reload() }, "Reload"))),
      h("div", { class: "set-info" }, [s.hostname, s.address, s.version && `version ${s.version}`].filter(Boolean).join(DOT))]],
  ];

  put(body, sections.map(([name, el]) => [h("h1", {}, name), el]));
  body.scrollTop = keep;
  const heads = [...body.querySelectorAll("h1")];
  // The rail jumps to a section, and shows which one is at the top. The last
  // ones can't reach the top, so a tapped one stays marked until the next touch.
  put($("set-rail"), sections.map(([name], i) => h("button", { onclick: () => {
    railPick = i;
    body.scrollTop = heads[i].offsetTop - body.offsetTop;
    spot();
  } }, name)));
  const spot = () => {
    const at = heads.filter(el => el.offsetTop - body.offsetTop <= body.scrollTop + 8).length - 1;
    [...$("set-rail").children].forEach((el, i) => el.classList.toggle("active", i === (railPick ?? Math.max(0, at))));
  };
  body.onscroll = spot;
  body.onpointerdown = () => { railPick = null; };
  spot();
}

async function restart() {
  if (!await ask("Restart TouchFrame?", { sub: "The screen comes back in a few seconds.", yes: "Restart" })) return;
  $("wait").hidden = false;
  post("/api/admin/restart").catch(e => { $("wait").hidden = true; toast(e.message); });
}

async function forget() {
  if (!await ask("Forget every remembered device?", { sub: "Each will need a new code from this screen to open the settings page.", yes: "Forget", danger: true })) return;
  post("/api/admin/forget").catch(e => toast(e.message));
}

async function network(on) {
  const sure = on
    ? await ask("Let other devices reach this Pi?", { sub: "Needed for the settings page. The dashboard itself stays private to this screen. TouchFrame restarts, which takes a few seconds.", yes: "Turn on" })
    : await ask("Stop other devices reaching this Pi?", { sub: "The settings page and webhooks stop working from other devices. TouchFrame restarts, which takes a few seconds.", yes: "Turn off", danger: true });
  if (!sure) return false;
  return save({ "app.listen": on });
}

/** Show the code, and where to type it. */
async function pair() {
  if (!settings.network) {
    // Carry on with the code once the restart is over.
    try { sessionStorage.setItem("pair", "1"); } catch { /* it will take one more tap */ }
    if (!await network(true)) { try { sessionStorage.removeItem("pair"); } catch { /* nothing to undo */ } }
    return;
  }
  let info;
  try { info = await post("/api/admin/code"); } catch (err) { return toast(err.message); }
  closeDialog();
  dialogHold = true;
  put($("dialog"), h("div", { class: "dialog-box wide card" },
    h("div", { class: "pair" },
      info.qr && h("img", { src: `/api/admin/qr.svg?${info.code}`, alt: "" }),
      h("div", { class: "pair-steps" },
        h("div", { class: "dialog-sub" }, info.qr ? "On a phone or computer on this network, scan the code or open" : "On a phone or computer on this network, open"),
        info.urls.map((url, i) => [i > 0 && h("div", { class: "muted" }, "or"), h("div", { class: "pair-url" }, url)]),
        !info.urls.length && h("div", { class: "pair-url" }, "this Pi's address, then /admin"),
        h("div", { class: "dialog-sub" }, "and enter"),
        h("div", { class: "pair-code num" }, `${info.code.slice(0, 3)} ${info.code.slice(3)}`),
        h("div", { class: "muted" }, `The code works once, for ${Math.round(info.seconds / 60)} minutes.`))),
    h("div", { class: "dialog-buttons" }, h("button", { class: "btn primary", onclick: () => closeDialog() }, "Done"))));
  $("dialog").hidden = false;
  setTimeout(() => { if (dialogHold) closeDialog(); }, info.seconds * 1000);
}

// ---------- loading ----------
async function get(path) {
  const resp = await fetch(path);
  const body = await resp.json();
  if (!resp.ok) throw new Error(body.error || `Failed (${resp.status})`);
  return body;
}

function applyTheme() {
  const auto = weather && weather.current ? (weather.current.day ? "light" : "dark") : "dark";
  document.documentElement.dataset.theme = settings.theme === "auto" ? auto : settings.theme;
}

let serverStarted = null;

async function loadSettings() {
  const was = settings;
  let fresh;
  try { fresh = await get("/api/settings"); } catch { return; /* the last ones do */ }
  // The server was restarted (an update, or a setting that needs it): load the new page.
  if (serverStarted && fresh.started !== serverStarted) return location.reload();
  serverStarted = fresh.started;
  settings = fresh;
  applyTheme();
  layout();
  drawClock();
  if (was.clock !== settings.clock) { drawWeather(); drawCalendar(); }
  if (was.units !== settings.units) loadWeather();
  if (was.week_start !== settings.week_start) loadCalendar();
  if (state) { drawIntercom(); drawAlerts(); drawLab(); }
  drawSettings();
  loadPhotos().then(() => { if ($("saver").hidden) armSaver(); });
  let wanted = null;
  try { wanted = sessionStorage.getItem("pair"); sessionStorage.removeItem("pair"); } catch { /* no storage */ }
  if (wanted && settings.network && settings.can_edit) { go("settings", false); pair(); }
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
    if (serverStarted && state.started !== serverStarted) return location.reload();     // as in loadSettings
    layout();
    drawIntercom(); drawAlerts(); drawLab(); drawWarnings(); drawPage(); drawStatus();
  });
  // Sent when a setting changes (and on connecting).
  es.addEventListener("settings", loadSettings);
}

async function start() {
  drawClock();
  await loadSettings();
  setInterval(drawClock, 1000);
  loadWeather(); loadCalendar();
  setInterval(loadWeather, 5 * 60 * 1000);
  // Redrawn every minute too, so events that end drop off and midnight rolls over.
  setInterval(loadCalendar, 60 * 1000);
  listen();
  touched();
}
start();
