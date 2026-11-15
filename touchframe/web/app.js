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
      h("div", { class: "rain" }, hr.rain >= 10 && [
        h("span", { class: "rain-track" }, h("span", { class: "rain-fill", style: `width:${hr.rain}%` })),
        `${hr.rain}%`]))));

  // Under the date: sunrise and sunset, and the air.
  const air = weather.air;
  put($("sun"),
    today && today.sunrise && today.sunset &&
      h("span", {}, `Sun ${clockText(new Date(today.sunrise))} – ${clockText(new Date(today.sunset))}`),
    air && h("span", { class: `air level-${air.level}` }, `Air ${air.index} · ${air.label}`));

  days.replaceChildren(...weather.days.slice(1, 5).map(d =>
    h("div", { class: "day" },
      h("div", { class: "name" }, WEEKDAYS[day(d.date).getDay()].slice(0, 3)),
      icon(d.kind),
      h("div", { class: "range" }, h("span", { class: "hi" }, deg(d.high)), " ", h("span", { class: "lo" }, deg(d.low))),
      h("div", { class: "rain" }, d.rain >= 10 ? `${d.rain}%` : ""))));

  if (settings.theme === "auto") document.documentElement.dataset.theme = cur.day ? "light" : "dark";
  drawStatus();
}

// Weather Service alerts: a banner on Home, and the full words on a tap.
function drawWarnings() {
  const list = state ? state.warnings : [];
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
  put(el, h("b", {}, `⚠ ${first.event}`), until, list.length > 1 && h("span", { class: "more" }, `+${list.length - 1} more`), h("span", { class: "more" }, "›"));
  el.onclick = () => {
    put($("detail-text"), list.map(a => [
      h("h1", {}, a.event),
      a.headline && h("p", { class: "muted" }, a.headline),
      a.description && h("p", {}, a.description),
      a.instruction && h("p", {}, h("b", {}, a.instruction))]));
    $("detail").hidden = false;
  };
}
$("detail-close").addEventListener("click", () => { $("detail").hidden = true; });

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

  drawMonth(problem, days);
  drawStatus();
}

// The Calendar tab: a month you can page through, and beside it what's coming
// up, or the day that was tapped.
let calMonth = null;        // [year, month 0-11] when not this month
let calDay = null;          // "2026-11-18" when a day was tapped
const otherMonths = {};     // "2026-12" -> events, fetched as they're opened

function monthEvents() {
  if (!calMonth) return calendar ? calendar.events : [];
  return otherMonths[`${calMonth[0]}-${calMonth[1] + 1}`] || [];
}

async function turnMonth(by) {
  const now = new Date();
  const d = calMonth ? new Date(calMonth[0], calMonth[1] + by, 1) : new Date(now.getFullYear(), now.getMonth() + by, 1);
  calDay = null;
  calMonth = by === 0 || (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) ? null : [d.getFullYear(), d.getMonth()];
  drawCalendar();
  if (!calMonth) return;
  const key = `${calMonth[0]}-${calMonth[1] + 1}`;
  try {
    otherMonths[key] = (await get(`/api/calendar?month=${key}`)).events;
    drawCalendar();
  } catch (err) { toast(err.message); }
}

function drawMonth(problem, days) {
  const now = new Date();
  const [year, month] = calMonth || [now.getFullYear(), now.getMonth()];
  const first = new Date(year, month, 1);
  const events = monthEvents();

  // Beside the month: the tapped day, another month's events, or what's coming up.
  const agenda = $("agenda");
  agenda.replaceChildren();
  if (problem !== null) agenda.append(h("div", { class: "empty" }, problem));
  else if (calDay || calMonth) {
    const byDay = new Map();
    for (const e of events) {
      const last = new Date(new Date(e.stop) - 1000);
      for (let d = day(e.start); d <= last; d.setDate(d.getDate() + 1)) {
        const key = ymd(d);
        if (calDay ? key !== calDay : d.getMonth() !== month) continue;
        if (!byDay.has(key)) byDay.set(key, []);
        byDay.get(key).push({ ...e, start: new Date(e.start), all_day: e.all_day || ymd(new Date(e.start)) !== key, until: null });
      }
    }
    if (calDay) agenda.append(h("h2", { class: "today" }, dayName(day(calDay))));
    for (const [key, list] of [...byDay.entries()].sort()) {
      if (!calDay) agenda.append(h("h2", {}, dayName(day(key))));
      agenda.append(...list.sort((a, b) => (b.all_day - a.all_day) || (a.start - b.start)).map(eventRow));
    }
    if (!byDay.size) agenda.append(h("div", { class: "empty" }, calDay ? "Nothing that day" : "Nothing this month"));
  } else {
    if (!days.length) agenda.append(h("div", { class: "empty" }, `Nothing in the next ${calendar.days} days`));
    for (const [d, list] of days) {
      agenda.append(h("h2", { class: dayName(d) === "Today" ? "today" : "" }, dayName(d)), ...list.map(eventRow));
    }
  }

  const monday = calendar && calendar.week_start === "monday";
  const busy = new Set();
  for (const e of events) {
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
      const cls = ["d"], key = ymd(d);
      if (d.getMonth() !== month) cls.push("out");
      else {
        if (key === ymd(now)) cls.push("today");
        if (busy.has(key)) cls.push("has");
        if (key === calDay) cls.push("picked");
      }
      grid.append(h("button", {
        class: cls.join(" "), disabled: d.getMonth() !== month,
        onclick: () => { calDay = calDay === key ? null : key; drawCalendar(); },
      }, String(d.getDate())));
    }
  } while (d.getMonth() === month);
  put($("month"),
    h("div", { class: "month-head" },
      h("h1", {}, `${MONTHS[month]} ${year}`),
      (calMonth || calDay) && h("button", { class: "pill", onclick: () => turnMonth(0) }, "Today"),
      h("button", { class: "arrow", onclick: () => turnMonth(-1) }, "‹"),
      h("button", { class: "arrow", onclick: () => turnMonth(1) }, "›")),
    grid);
}

// ---------- streams ----------
// What this Pi is receiving over multicast. Asked for only while the tab is open.
let streamsTimer = null;

const bits = k => k >= 1000 ? `${(k / 1000).toFixed(1)} Mbit/s` : `${k < 10 ? k.toFixed(1) : Math.round(k)} kbit/s`;
const DSCP = { 0: "BE", 46: "EF", 34: "AF41", 26: "AF31", 48: "CS6", 40: "CS5", 32: "CS4", 24: "CS3" };

function streamRow(s) {
  if (s.silent) {
    return h("div", { class: "strow silent" },
      h("div", { class: "st-main" }, h("b", {}, s.name || s.group), h("div", { class: "lab-sub" }, s.name ? s.group : "")),
      h("div", { class: "st-state" }, s.error ? `⚠ ${s.error}` : "Nothing heard yet"));
  }
  const idle = s.idle_ms > 3000;
  const lossy = s.loss_percent >= 1;
  // Level: -60 dBFS to 0 across the bar.
  const level = s.peak_dbfs === null || s.peak_dbfs === undefined ? null : Math.max(0, Math.min(100, (s.peak_dbfs + 60) / 60 * 100));
  const dscp = s.tos === null || s.tos === undefined ? "" : (DSCP[s.tos >> 2] || `DSCP ${s.tos >> 2}`);
  return h("div", { class: `strow${idle ? " idle" : ""}` },
    h("div", { class: "st-main" },
      h("b", {}, s.name || s.group),
      h("div", { class: "lab-sub" }, [s.name ? s.group : null, s.source, s.codec || (s.payload_type !== null ? `pt ${s.payload_type}` : null)].filter(Boolean).join(" · ")),
      level !== null && !idle && h("div", { class: "meter-track" }, h("div", { class: "meter-fill", style: `width:${level}%` }))),
    h("div", { class: "st-stats" },
      stat(idle ? "idle" : bits(s.kbit_per_sec), idle ? `${Math.round(s.idle_ms / 1000)} s` : "rate"),
      h("div", { class: `stat${lossy ? " bad" : ""}` }, h("b", {}, `${lossy ? "⚠ " : ""}${s.loss_percent.toFixed(s.loss_percent < 10 ? 1 : 0)}%`), h("span", {}, `${s.lost} lost`)),
      stat(s.jitter_ms === null ? "–" : `${s.jitter_ms < 10 ? s.jitter_ms.toFixed(1) : Math.round(s.jitter_ms)} ms`, "jitter"),
      stat(s.ttl === null ? "–" : String(s.ttl), `TTL${dscp ? ` · ${dscp}` : ""}`)));
}

async function loadStreams() {
  clearTimeout(streamsTimer);
  if (!document.querySelector("#streams.active")) return;
  let data;
  try { data = await get("/api/streams"); } catch (err) { data = { error: err.message, streams: [], listeners: [] }; }
  const live = data.streams.filter(s => !s.silent), quiet = data.streams.filter(s => s.silent);
  live.sort((a, b) => (a.idle_ms > 3000) - (b.idle_ms > 3000) || (a.name || a.group).localeCompare(b.name || b.group));
  put($("streams-list"),
    data.error && h("div", { class: "lab-error" }, `⚠ ${data.error}`),
    live.map(streamRow), quiet.map(streamRow),
    data.listeners.length && h("h2", {}, "Receivers reporting"),
    data.listeners.filter(l => !l.left).map(l => h("div", { class: "strow" },
      h("div", { class: "st-main" }, h("b", {}, l.name || l.from), h("div", { class: "lab-sub" }, `${l.group} · ${l.about === null ? "hearing nothing" : "listening"}`)),
      h("div", { class: "st-stats" }, stat(l.loss_percent === null || l.loss_percent === undefined ? "–" : `${l.loss_percent.toFixed(1)}%`, "loss")))),
    !data.error && !data.streams.length && h("div", { class: "empty" }, "No streams yet"));
  streamsTimer = setTimeout(loadStreams, 2000);
}

// ---------- settings (the gear) ----------
async function openSettings() {
  let dev;
  try { dev = await get("/api/device"); } catch (err) { return toast(err.message); }
  const save = body => fetch("/api/device", { method: "POST", body: JSON.stringify(body) }).then(r => r.json());
  const themes = h("div", { class: "seg" }, ["dark", "light", "auto"].map(t =>
    h("button", { class: dev.theme === t ? "active" : "", onclick: async e => {
      await save({ theme: t });
      settings.theme = t;
      for (const b of e.target.parentNode.children) b.classList.toggle("active", b === e.target);
      document.documentElement.dataset.theme = t === "auto" ? (weather && weather.current && !weather.current.day ? "dark" : "light") : t;
    } }, t[0].toUpperCase() + t.slice(1))));
  const value = h("b", {}, `${dev.brightness}%`);
  const slider = h("input", { type: "range", min: 5, max: 100, step: 5, value: dev.brightness,
    oninput: e => { value.textContent = `${e.target.value}%`; },
    onchange: e => save({ brightness: Number(e.target.value) }) });
  put($("settings-body"),
    dev.backlight && h("div", { class: "set-row" }, h("span", {}, "Brightness"), slider, value),
    h("div", { class: "set-row" }, h("span", {}, "Theme"), themes),
    h("div", { class: "set-row" },
      h("button", { class: "pill", onclick: () => { $("settings").hidden = true; startSaver(true); } }, "Show photos now"),
      settings.alerts && h("button", { class: "pill", onclick: () => { $("settings").hidden = true; post("/api/alerts/test").catch(e => toast(e.message)); } }, "Send a test alert"),
      h("button", { class: "pill", onclick: () => location.reload() }, "Reload")),
    h("div", { class: "set-info" }, [dev.hostname, dev.address, dev.version && `version ${dev.version}`].filter(Boolean).join("  ·  ")));
  $("settings").hidden = false;
}
$("gear").addEventListener("click", openSettings);
$("settings-close").addEventListener("click", () => { $("settings").hidden = true; });

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

  put($("ic-groups"), ic.groups.flatMap(g => [
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

function showPicture(src) {
  put($("detail-text"), h("img", { class: "detail-image", src, alt: "" }));
  $("detail").hidden = false;
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
      a.image && h("img", { class: "al-thumb", src: a.image, alt: "", onclick: () => showPicture(a.image) }),
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

// ---------- lab ----------
const SEVERITIES = ["Not classified", "Information", "Warning", "Average", "High", "Disaster"];

function age(ms) {
  const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (mins < 60) return `${mins}m`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

let labView = "tiles";
for (const b of $("lab-switch").children) b.addEventListener("click", () => { labView = b.dataset.lab; drawLab(); });

const num = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n.toLocaleString();

/** A labelled bar from 0 to 100%. The number carries the value; the bar turns
 *  amber, then red, only as a warning that it's nearly full. */
function meter(label, percent) {
  const level = percent >= 90 ? " serious" : percent >= 75 ? " warn" : "";
  return h("div", { class: "meter" },
    h("div", { class: "meter-text" }, h("span", {}, label), h("b", {}, `${percent}%`)),
    h("div", { class: "meter-track" }, h("div", { class: `meter-fill${level}`, style: `width:${Math.max(1, Math.min(100, percent))}%` })));
}

/** A number with its label under it. */
const stat = (value, label) => h("div", { class: "stat" }, h("b", {}, value), h("span", {}, label));

function tile(kind, name, t, ...body) {
  const waiting = t && t.ok === null;
  return h("div", { class: `ltile ${kind}` },
    h("div", { class: "ltile-name" }, h("span", {}, name),
      t && t.ok === false && h("span", { class: "state bad" }, "⚠ offline")),
    waiting ? h("div", { class: "muted" }, "Checking…") : body,
    t && t.ok === false && !body.flat(Infinity).some(Boolean) && h("div", { class: "lab-error" }, t.error));
}

function serviceTiles(t) {
  if (t.kind === "proxmox") {
    if (!t.nodes) return [tile("proxmox", t.name, t)];
    return t.nodes.map(n => tile("proxmox", n.name, t,
      n.online ? [meter("CPU", n.cpu), meter("Memory", n.mem)] : h("div", { class: "state bad" }, "⚠ Node offline"),
      h("div", { class: "ltile-foot" }, `${n.vms[0]}/${n.vms[1]} VMs` + (n.lxc[1] ? ` · ${n.lxc[0]}/${n.lxc[1]} LXC` : ""))));
  }
  if (t.kind === "adguard") {
    return [tile("adguard", t.name, t, t.queries !== undefined && [
      h("div", { class: "stats" }, stat(num(t.queries), "queries"), stat(`${t.percent}%`, "blocked")),
      h("div", { class: "ltile-foot" }, t.protecting ? `${t.latency_ms} ms average` : h("span", { class: "state bad" }, "⚠ Protection is off"))])];
  }
  if (t.kind === "unifi") {
    const up = t.wan === "ok";
    return [tile("unifi", t.name, t, t.aps !== undefined && [
      h("div", { class: "stats" }, stat(String(t.wifi_clients), "Wi-Fi"), stat(String(t.wired_clients), "wired"), stat(String(t.aps), t.aps === 1 ? "AP" : "APs")),
      h("div", { class: "ltile-foot" },
        t.wan ? h("span", { class: `state ${up ? "good" : "bad"}` }, up ? "✓ Internet up" : `⚠ Internet: ${t.wan}`) : "",
        t.aps_down ? h("span", { class: "state bad" }, ` · ⚠ ${t.aps_down} AP down`) : "")])];
  }
  if (t.kind === "plex") {
    return [tile("plex", t.name, t, t.streams && (t.streams.length ? t.streams.slice(0, 3).map(s =>
      h("div", { class: "stream" },
        h("div", { class: "stream-title" }, s.title),
        h("div", { class: "stream-sub" }, [s.user, s.state === "paused" ? "paused" : null, s.transcoding ? "transcoding" : null].filter(Boolean).join(" · ")),
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
      h("div", { class: "ltile-foot" }, t.pending ? h("span", { class: "state bad" }, `⚠ ${t.pending} waiting for an answer`) : `${num(t.available)} available`)])];
  }
  if (t.kind === "sabnzbd" || t.kind === "qbittorrent") {
    const sab = t.kind === "sabnzbd";
    return [tile(t.kind, t.name, t, t.down !== undefined && [
      h("div", { class: "stats" }, stat(speed(t.down), "down"), sab ? stat(String(t.queued), "queued") : stat(speed(t.up), "up")),
      h("div", { class: "ltile-foot" }, sab ? (t.paused ? "Paused" : t.queued ? `${t.left} left` : "Idle") : `${t.leeching} downloading · ${t.seeding} seeding`)])];
  }
  if (t.kind === "portainer") {
    return [tile(t.kind, t.name, t, t.running !== undefined && [
      h("div", { class: "stats" }, stat(String(t.running), "running"), stat(String(t.stopped), "stopped")),
      h("div", { class: "ltile-foot" }, t.unhealthy.length ? h("span", { class: "state bad" }, `⚠ unhealthy: ${t.unhealthy.join(", ")}`) : t.stopped ? "" : h("span", { class: "state good" }, "✓ All running"))])];
  }
  return [];
}

/** Bytes a second, in words. */
const speed = b => b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB/s` : b >= 1024 ? `${Math.round(b / 1024)} kB/s` : "0";

const openProblems = () => { labView = "problems"; drawLab(); };

function promTile(p) {
  const down = p.total === undefined ? 0 : p.total - p.up;
  const el = tile("link", "Prometheus", p, p.total !== undefined && [
    h("div", { class: "stats" }, stat(`${p.up}/${p.total}`, "targets up")),
    h("div", { class: "ltile-foot" }, down ? h("span", { class: "state bad" }, `⚠ ${down} down ›`) : h("span", { class: "state good" }, "✓ All up"))]);
  el.addEventListener("click", openProblems);
  return el;
}

function zbxTile(z) {
  const el = tile("link", "Zabbix", z, z.counts && [
    h("div", { class: "chips" }, [5, 4, 3, 2].filter(s => z.counts[s]).map(s => h("span", { class: `chip sev-${s}` }, `${z.counts[s]} ${SEVERITIES[s]}`))),
    h("div", { class: "ltile-foot" }, z.total ? `${z.total} problems ›` : h("span", { class: "state good" }, "✓ No problems"))]);
  el.addEventListener("click", openProblems);
  return el;
}

function drawLab() {
  $("tab-lab").hidden = !settings.lab;
  if (!state || !settings.lab) return;
  const prom = state.lab.prometheus, zbx = state.lab.zabbix, tiles = state.lab.tiles;
  let worst = 0;

  // Overview (tiles) and Problems (the lists); the switch shows when there are both.
  const hasProblems = !!(prom || zbx);
  if (!tiles.length) labView = "problems";
  else if (!hasProblems) labView = "tiles";
  $("lab-switch").hidden = !(tiles.length && hasProblems);
  for (const b of $("lab-switch").children) b.classList.toggle("active", b.dataset.lab === labView);
  $("lab-tiles").hidden = labView !== "tiles";
  $("lab-problems").hidden = labView !== "problems";
  put($("lab-tiles"), tiles.flatMap(serviceTiles), prom && promTile(prom), zbx && zbxTile(zbx));

  $("lab-prom").hidden = !prom;
  if (prom) {
    const known = prom.total !== undefined;
    put($("lab-prom-head"),
      h("h2", {}, "Prometheus"),
      known && h("div", { class: "big" }, h("span", { class: prom.down.length ? "" : "good" }, String(prom.up)), h("span", { class: "muted" }, ` / ${prom.total} up`)),
      prom.error && h("div", { class: "lab-error" }, prom.ok === false ? `Not answering: ${prom.error}` : ""));
    $("lab-prom-list").replaceChildren(...(known ? prom.down : []).map(t =>
      h("div", { class: "lab-row sev-4" },
        h("div", { class: "lab-main" }, h("b", {}, t.instance), h("div", { class: "lab-sub" }, t.job)))));
    if (known && !prom.down.length) $("lab-prom-list").append(h("div", { class: "empty" }, "Every target is up"));
    if (known && prom.total - prom.up > 0) worst = Math.max(worst, 3);
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
      h("div", { class: `lab-row sev-${p.severity}${p.acknowledged ? " acked" : ""}` },
        h("div", { class: "lab-main" }, h("b", {}, p.host), h("div", { class: "lab-sub" }, p.name)),
        h("div", { class: "lab-age" }, age(p.at_ms)))));
    if (known && !zbx.total) $("lab-zbx-list").append(h("div", { class: "empty" }, "No problems"));
    if (known && zbx.total > zbx.problems.length) {
      $("lab-zbx-list").append(h("div", { class: "empty" }, `and ${zbx.total - zbx.problems.length} more`));
    }
    if (known) worst = Math.max(worst, ...[5, 4].filter(s => zbx.counts[s]));
  }

  // The tab is marked while something is High or Disaster.
  const badge = $("lab-badge");
  const serious = zbx && zbx.counts ? zbx.counts[5] + zbx.counts[4] : 0;
  badge.hidden = !serious;
  badge.textContent = serious;
  badge.className = `badge sev-${worst}`;
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

async function startSaver(asked) {
  await loadPhotos();
  if (asked === true && saver && !saver.photos.length) return toast("No photos yet: copy some into the photo folder");
  if (!saver || (!saver.enabled && asked !== true) || !saver.photos.length || !$("page").hidden) return armSaver();
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
  if (state && [state.lab.prometheus, state.lab.zabbix, ...state.lab.tiles].some(s => s && s.ok === false)) notes.push("Lab offline");
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
  if (view === "streams") loadStreams();
  if (view !== "calendar" && (calMonth || calDay)) { calMonth = calDay = null; drawCalendar(); }
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
    idleTimer = setTimeout(() => { $("confirm").hidden = true; $("detail").hidden = true; $("settings").hidden = true; show("home"); }, settings.idle_home_seconds * 1000);
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

let serverStarted = null;

function listen() {
  const es = new EventSource("/api/events");
  es.addEventListener("state", e => {
    state = JSON.parse(e.data);
    state.at = Date.now();
    // The server was restarted (an update, or config.toml changed): load the new page.
    if (serverStarted && state.started !== serverStarted) return location.reload();
    serverStarted = state.started;
    drawIntercom(); drawAlerts(); drawLab(); drawWarnings(); drawPage(); drawStatus();
  });
}

async function start() {
  drawClock();
  try { settings = await get("/api/settings"); } catch { /* the defaults do */ }
  if (settings.theme !== "auto") document.documentElement.dataset.theme = settings.theme;
  $("tab-streams").hidden = !settings.streams;
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
