"use strict";

// The settings page. It is drawn from what the server says can be changed
// (/api/config): sections, their fields and the current values. Secrets are
// never sent here; a field only knows whether one is saved.

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

async function api(path, body) {
  const resp = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw Object.assign(new Error(data.error || `Failed (${resp.status})`), { status: resp.status });
  return data;
}

const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* not remembered, that's all */ } },
};

const TAB_NAMES = { home: "Home", calendar: "Calendar", alerts: "Alerts", lab: "Lab", intercom: "Intercom" };
const HELP = [["use", "Use it for"], ["give", "Give it"], ["get", "You get"], ["limits", "Limits"], ["more", "More"]];

let config = null, shown = null, dirty = false, readers = [], ids = 0;

// ---------- one control per kind of field ----------
// Each returns [element, read], where read() gives the value to save.
function control(field, value, id) {
  const kind = field.type;
  if (kind === "bool" || kind === "network") {
    const box = h("input", { type: "checkbox", id, checked: !!value });
    return [h("div", { class: "check" }, box), () => box.checked];
  }
  if (kind === "choice") {
    const select = h("select", { id }, field.options.map(([v, label], i) => h("option", { value: i, selected: v === value }, label)));
    return [select, () => field.options[select.value][0]];
  }
  if (kind === "int" || kind === "float") {
    const input = h("input", { type: "number", id, value, min: field.min, max: field.max, step: kind === "int" ? 1 : "any",
      placeholder: field.placeholder, inputmode: kind === "int" ? "numeric" : "decimal" });
    return [h("div", { class: "inline" }, input, field.unit && h("span", { class: "unit" }, field.unit)),
      () => input.value.trim() === "" ? null : Number(input.value)];
  }
  if (kind === "text") {
    const input = h("input", { type: "text", id, value, placeholder: field.placeholder, maxlength: field.maxlen || 2000,
      class: field.url ? "mono" : "", autocapitalize: "off", spellcheck: "false" });
    return [input, () => input.value];
  }
  if (kind === "secret") {
    // Empty means "keep what is saved". Clear has to be asked for.
    const saved = value && value.set;
    const keep = `Saved${value && value.hint ? ` (${value.hint})` : ""}. Type to replace it`;
    const input = h("input", { type: "password", id, class: "mono", autocomplete: "new-password",
      placeholder: saved ? keep : field.placeholder });
    let clear = false;
    const button = saved && h("button", { type: "button", class: "small", onclick: () => {
      clear = !clear;
      input.disabled = clear;
      input.value = "";
      input.placeholder = clear ? "Will be removed when you save" : keep;
      button.textContent = clear ? "Keep" : "Clear";
      touched();
    } }, "Clear");
    return [h("div", { class: "secret" }, input, button), () => clear ? "" : input.value ? input.value : null];
  }
  if (kind === "list") {
    const input = h("input", { type: "text", id, value: (value || []).join(", "), placeholder: field.placeholder,
      autocapitalize: "off", spellcheck: "false" });
    return [input, () => input.value.split(/[,\n]/).map(v => v.trim()).filter(Boolean)];
  }
  if (kind === "hours") {
    const from = h("input", { type: "number", id, min: 0, max: 23, value: value[0], placeholder: "22", "aria-label": "Dim from (hour)" });
    const to = h("input", { type: "number", min: 0, max: 23, value: value[1], placeholder: "7", "aria-label": "Dim until (hour)" });
    return [h("div", { class: "inline" }, from, h("span", { class: "unit" }, "until"), to),
      () => from.value === "" && to.value === "" ? [] : [Number(from.value), to.value === "" ? null : Number(to.value)]];
  }
  if (kind === "tabs") return tabsControl(value);
  if (kind === "rows") return rowsControl(field, value);
  return [h("span", {}, "Set in config.toml"), null];
}

function tabsControl(value) {
  const all = config.tabs;
  let order = value.length ? [...value, ...all.filter(t => !value.includes(t))] : [...all];
  const on = new Set(value.length ? value : all);
  const box = h("div", {});
  const draw = () => box.replaceChildren(...order.map((tab, i) => h("div", { class: "tabrow" },
    h("input", { type: "checkbox", checked: on.has(tab), "aria-label": `Show ${TAB_NAMES[tab]}`,
      onchange: e => { e.target.checked ? on.add(tab) : on.delete(tab); } }),
    h("span", {}, TAB_NAMES[tab]),
    h("button", { type: "button", class: "small", disabled: i === 0, "aria-label": `Move ${TAB_NAMES[tab]} earlier`,
      onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(); touched(); } }, "Up"),
    h("button", { type: "button", class: "small", disabled: i === order.length - 1, "aria-label": `Move ${TAB_NAMES[tab]} later`,
      onclick: () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(); touched(); } }, "Down"))));
  draw();
  return [box, () => {
    const list = order.filter(t => on.has(t));
    return list.join() === all.join() ? [] : list;       // everything, in the usual order: no list needed
  }];
}

function rowsControl(field, rows) {
  const box = h("div", { class: "rows" });
  const cards = [];
  const add = (row, fresh) => {
    const subs = [];
    const card = h("div", { class: "rowcard card" },
      h("div", { class: "rowhead" }, h("span", {}, fresh ? "New" : row.name || row.url && row.url.hint || field.label),
        h("button", { type: "button", class: "small warn", onclick: () => { cards.splice(cards.indexOf(entry), 1); card.remove(); touched(); } }, "Remove")),
      field.fields.map(sub => {
        const blank = sub.default !== undefined ? sub.default : sub.type === "choice" ? sub.options[0][0] : sub.type === "list" ? [] : "";
        const [el, read] = fieldRow(sub, fresh ? blank : row[sub.key]);
        subs.push([sub.key, read]);
        return el;
      }));
    const entry = () => Object.fromEntries([...(fresh ? [] : [["_row", row._row]]), ...subs.map(([key, read]) => [key, read()])]);
    cards.push(entry);
    box.insertBefore(card, more);
  };
  const more = h("button", { type: "button", onclick: () => { add({}, true); touched(); } }, `Add ${field.label === "Calendars" ? "a calendar" : field.label}`);
  box.append(more);
  for (const row of rows) add(row, false);
  return [box, () => cards.map(entry => entry())];
}

/** A label, its control and its hint. */
function fieldRow(field, value) {
  const id = `f${++ids}`;
  const [el, read] = control(field, value, id);
  const labelled = !["tabs", "rows"].includes(field.type);
  return [h("div", { class: "field" },
    labelled ? h("label", { for: id }, field.label) : h("div", { class: "label" }, field.label),
    h("div", { class: "control" }, el, field.hint && h("p", { class: "hint" }, field.hint))), read];
}

// ---------- a section ----------
function draw(id) {
  const section = config.sections.find(s => s.id === id) || config.sections[0];
  shown = section.id;
  store.set("section", shown);
  dirty = false;
  readers = [];
  for (const b of $("sections").children) b.setAttribute("aria-current", b.dataset.id === shown ? "page" : "false");

  const help = h("details", { class: "help card", open: store.get(`help:${shown}`) === "open",
    ontoggle: e => store.set(`help:${shown}`, e.target.open ? "open" : "closed") },
    h("summary", {}, "How this page works"),
    h("div", {}, HELP.filter(([key]) => section.help[key]).map(([key, label]) => h("p", {}, h("b", {}, label), " ", section.help[key]))));

  const restarts = section.fields.some(f => !f.live);
  put($("section"),
    h("h1", {}, section.title),
    h("p", {}, section.description),
    help,
    restarts && h("p", { class: "restarts" }, "Saving a change on this page restarts TouchFrame. The screen comes back in a few seconds."),
    section.fields.map(field => {
      const out = [];
      if (field.type === "rows") {
        // A list of things gets a heading of its own, and no label column.
        const [el, read] = control(field, field.value);
        readers.push([field.key, read]);
        return [h("h2", {}, field.label), field.hint && h("p", { class: "hint" }, field.hint), el];
      }
      if (field.heading) out.push(h("h2", {}, field.heading));
      const [el, read] = fieldRow(field, field.value);
      if (field.heading) el.classList.add("first");
      readers.push([field.key, read]);
      out.push(el);
      return out;
    }));
  const first = $("section").querySelector(".field");
  if (first && !first.previousElementSibling.matches(".field")) first.classList.add("first");
  say("");
}

function touched() { dirty = true; say(""); }
function say(text, bad = false) { $("saved").textContent = text; $("saved").className = bad ? "error" : ""; }

/** Wait for the server to come back after a restart. */
async function returned() {
  await new Promise(done => setTimeout(done, 1500));
  for (let i = 0; i < 30; i++) {
    try { return (await api("/api/admin/session")).paired ? "back" : "locked"; }
    catch { await new Promise(done => setTimeout(done, 1000)); }
  }
  return "gone";
}

async function afterRestart(done) {
  say("TouchFrame is restarting...");
  const how = await returned();
  if (how === "gone") return say("TouchFrame can't be reached any more. If you turned off \"Reachable from other devices\", that is expected.", true);
  if (how === "locked") return gate();
  await load();
  say(done);
}

async function save(e) {
  e.preventDefault();
  $("save").disabled = true;
  try {
    const result = await api("/api/config", Object.fromEntries(readers.map(([key, read]) => [key, read()])));
    if (result.restart) await afterRestart("Saved, and restarted.");
    else { await load(); say("Saved."); }
  } catch (err) {
    if (err.status === 401) gate();
    else say(err.message, true);
  }
  $("save").disabled = false;
}

async function load() {
  config = await api("/api/config");
  $("sections").replaceChildren(...config.sections.map(s => h("button", { type: "button", "data-id": s.id,
    onclick: () => { if (!dirty || confirm("Leave this page without saving?")) draw(s.id); } }, s.title)));
  draw(shown || store.get("section"));
}

// ---------- getting in ----------
function gate() {
  $("app").hidden = true;
  $("signout").hidden = true;
  $("gate").hidden = false;
  $("code").focus();
}

async function enter() {
  const session = await api("/api/admin/session");
  if (!session.paired) return gate();
  $("gate").hidden = true;
  $("app").hidden = false;
  $("signout").hidden = session.local;        // the Pi itself needs no code, so has nothing to sign out of
  await load();
}

$("gate").addEventListener("submit", async e => {
  e.preventDefault();
  $("gate-error").textContent = "";
  try {
    await api("/api/admin/pair", { code: $("code").value });
    $("code").value = "";
    await enter();
  } catch (err) {
    $("gate-error").textContent = err.message;
  }
});
$("signout").addEventListener("click", async () => { await api("/api/admin/signout", {}).catch(() => {}); gate(); });
$("section").addEventListener("submit", save);
$("section").addEventListener("input", touched);
$("restart").addEventListener("click", async () => {
  if (!confirm("Restart TouchFrame now?")) return;
  try { await api("/api/admin/restart", {}); await afterRestart("Restarted."); } catch (err) { say(err.message, true); }
});
$("reset").addEventListener("click", async () => {
  if (!confirm("Forget every change made here and on the screen, and go back to config.toml?")) return;
  try { await api("/api/admin/reset", {}); await afterRestart("Reset to config.toml."); } catch (err) { say(err.message, true); }
});
window.addEventListener("beforeunload", e => { if (dirty) e.preventDefault(); });

enter().catch(err => { $("gate").hidden = false; $("gate-error").textContent = err.message; });
