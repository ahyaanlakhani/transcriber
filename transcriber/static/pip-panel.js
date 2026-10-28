"use strict";
// Ask Pip: a small panel under the mascot button in the top bar. Type what you'd like ("no Machine Learning this week",
// "remind me before class on Monday") or tap a suggestion. Pip does small things right away and offers Undo.
// It never deletes recordings. Reminders also arrive as Windows notifications from the background app.

const pipPanel = { open: false, last: null };

function pipWeek(offset = 0) {  // Monday and Sunday of this (0) or next (1) week, in your time zone
  const today = pipIso(new Date(), pipZone());
  const monday = pipShift(today, -((new Date(`${today}T12:00:00`).getDay() + 6) % 7) + offset * 7);
  return [monday, pipShift(monday, 6)];
}

function pipStamp(date, tz) {  // 2026-10-19T09:45, the way Pip's reminders are written
  const day = date.toLocaleDateString("en-CA", { timeZone: tz });
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: tz, hour12: false });
  return `${day}T${time}`;
}

function pipChips() {
  const first = (state.subjects || []).find((subject) => subject.schedule?.days?.length);
  const [thisMonday, thisSunday] = pipWeek(0);
  const [nextMonday, nextSunday] = pipWeek(1);
  const chips = first ? [
    { label: `No ${first.name} this week`, actions: [{ tool: "skip_classes", subject: first.name, from: thisMonday, to: thisSunday }] },
    { label: `No ${first.name} next week`, actions: [{ tool: "skip_classes", subject: first.name, from: nextMonday, to: nextSunday }] },
    { label: `Remind me before ${first.name}`, remind: first.name }] : [];
  // the bigger jobs show a plan first
  return [...chips, { label: "Record my meeting", meeting: true }, { label: "Name my recordings", plan: "rename" }, { label: "Combine my classes", plan: "combine" },
    { label: "File unsorted recordings", plan: "file" }];
}

async function remindBeforeClass(subject) {  // 30 minutes before the next class of this subject
  const data = await pipFetch(0, 21);
  const next = [...data.classes].sort((a, b) => Date.parse(a.start) - Date.parse(b.start))
    .find((c) => c.subject === subject && ["upcoming", "today"].includes(c.status) && Date.parse(c.start) - 30 * 60_000 > Date.now());
  if (!next) throw new Error(`I can't see a coming ${subject} class to remind you about.`);
  const when = pipStamp(new Date(Date.parse(next.start) - 30 * 60_000), data.time_zone);
  return [{ tool: "remind", subject, when, text: `Record ${subject}: class starts at ${pipTime(next.start, data.time_zone)}` }];
}

function buildPipPanel() {
  const input = el("input", { type: "text", id: "pip-input", placeholder: "Tell Pip what to do…", autocomplete: "off",
    "aria-label": "Tell Pip what to do" });
  const send = el("button", { type: "submit", class: "pip-send", "aria-label": "Send to Pip", title: "Send" }, icon("send"));
  const reminders = el("div", { id: "pip-reminders", class: "pip-reminders" });
  const memory = el("div", { id: "pip-memory", class: "pip-reminders pip-memory" });
  const enabled = el("input", { type: "checkbox", id: "pip-class-reminders" });
  const panel = el("div", { id: "pip-panel", class: "bell-panel pip-panel", role: "dialog", "aria-label": "Ask Pip" },
    el("div", { class: "pip-panel-head" }, pipElement("happy"),
      el("div", {}, el("h2", {}, "Ask Pip"), el("p", { id: "pip-status", class: "pip-status" }, "Checking your classes…"))),
    el("form", { class: "pip-form", id: "pip-form", autocomplete: "off" }, input, send),
    el("div", { id: "pip-chips", class: "pip-chips" }),
    el("div", { id: "pip-result", class: "pip-result", "aria-live": "polite" }),
    reminders, memory,
    el("div", { class: "pip-foot" },
      el("label", { class: "pip-check" }, enabled, "Remind me before each class, even when the app is closed"),
      el("button", { type: "button", class: "link", id: "pip-test" }, "Send a test notification")));
  panel.hidden = true;
  const button = el("button", { type: "button", id: "pip-button", class: "icon-button", title: "Ask Pip", "aria-label": "Ask Pip",
    "aria-haspopup": "dialog", "aria-expanded": "false" }, pipElement("happy"));
  const menu = el("div", { class: "bell pip-menu", id: "pip-menu" }, button, panel);
  $("#bell").before(menu);
  button.addEventListener("click", () => (pipPanel.open ? closePip() : openPip()));
  $("#pip-form").addEventListener("submit", (e) => { e.preventDefault(); const text = input.value.trim(); if (text) askPip(text); });
  enabled.addEventListener("change", async () => {
    try {
      state.settings = await api("/api/settings", jsonRequest("PUT", { class_reminders: enabled.checked }));
    } catch (error) {
      enabled.checked = !enabled.checked;
      showPip({ say: error.message, done: [], problems: [], undo: [] });
    }
  });
  $("#pip-test").addEventListener("click", testPipNotification);
}

async function openPip() {
  pipPanel.open = true;
  $("#pip-panel").hidden = false;
  $("#pip-button").setAttribute("aria-expanded", "true");
  $("#pip-class-reminders").checked = state.settings?.class_reminders !== "0";
  renderPipChips();
  loadPipReminders();
  loadPipMemory();
  document.addEventListener("pointerdown", pipOutside, true);
  document.addEventListener("keydown", pipKey);
  $("#pip-input").focus();
  try {
    $("#pip-status").textContent = pipMessage(await pipFetch()).text;
  } catch {
    $("#pip-status").textContent = "Tell me what you'd like, or tap a suggestion.";
  }
}

function closePip() {
  pipPanel.open = false;
  $("#pip-panel").hidden = true;
  $("#pip-button").setAttribute("aria-expanded", "false");
  document.removeEventListener("pointerdown", pipOutside, true);
  document.removeEventListener("keydown", pipKey);
}

function pipOutside(e) {
  if (!$("#pip-menu").contains(e.target)) closePip();
}

function pipKey(e) {
  if (e.key === "Escape") { closePip(); $("#pip-button").focus(); }
}

function renderPipChips() {
  $("#pip-chips").replaceChildren(...pipChips().map((chip) => el("button", { type: "button", class: "pip-chip-button",
    onclick: () => quickPip(chip) }, chip.label)));
}

// a bigger change, shown before anything happens: what it would do, and Do it / Not now
function planCard(plan) {
  const card = el("div", { class: "pip-plan" },
    el("strong", {}, plan.title),
    plan.note ? el("p", { class: "pip-plan-note" }, plan.note) : null,
    el("ul", { class: "pip-plan-list" }, plan.items.map((item) => el("li", {}, item.label))),
    el("div", { class: "pip-plan-actions" },
      el("button", { type: "button", class: "primary", onclick: () => applyPlan(plan, card) }, "Do it"),
      el("button", { type: "button", class: "quiet", onclick: () => card.remove() }, "Not now")));
  return card;
}

async function applyPlan(plan, card) {
  card.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  try {
    const res = await api("/api/pip/apply", jsonRequest("POST", { kind: plan.kind, apply: plan.apply }));
    showPip(res);
    await afterPipChange(res);
  } catch (error) {
    showPip({ say: error.message, done: [], problems: [], undo: [] });
  }
}

function pipOpen(target) {
  const { view, subject, start, title } = target;  // "show me the calendar", "open Machine Learning", "record my meeting"
  closePip();
  if (view === "meeting") {  // the laptop records the call: Pip sets it up, and starts it when you asked
    if (!recording()) {
      state.rec = newRecorder();
      state.rec.source = "meeting";
      state.rec.title = title || "";
      if (subject) state.rec.subject = subject;
    }
    openRecorder();
    if (start && !recording()) startRecording();
    return;
  }
  if (view === "concept" && target.id != null) { openConcept(target.id); return; }
  if (view === "brain") { openBrain(); return; }
  const wanted = (subject || "").toLowerCase();
  const match = wanted && (state.subjects || []).find((s) => s.name.toLowerCase() === wanted);
  if (match) { setFilter("subject", match.id); return; }
  const go = { calendar: openCalendar, record: openRecorder, progress: openProgress, practice: openPractice,
    starred: () => setFilter("starred"), trash: () => setFilter("trash"), all: () => setFilter("all") }[view];
  if (go) go();
}

function showPip(res) {
  pipPanel.last = res;
  $("#pip-result").replaceChildren(el("div", {},  // el() takes lists and blanks; replaceChildren doesn't
    res.say ? el("p", { class: "pip-say" }, res.say) : null,
    res.answer ? pipAnswer(res.answer) : null,
    res.done.map((d) => el("p", { class: "pip-done" }, icon("check"), d.summary)),
    res.problems.map((p) => el("p", { class: "pip-problem" }, p)),
    (res.plans || []).map(planCard),
    res.undo.length ? el("button", { type: "button", class: "quiet", onclick: () => undoPip(res.undo) }, "Undo") : null));
}

// a question about your lectures, answered from the brain: the answer, then its sources (each opens at the moment)
function pipAnswer(answer) {
  const sources = answer.sources || [];
  return el("div", { class: "pip-answer" },
    el("p", {}, withSourceLinks(answer.answer, sources)),
    sources.length ? el("ol", { class: "pip-sources" }, sources.slice(0, 5).map((s) => el("li", { value: s.n },
      el("button", { type: "button", class: "link", onclick: () => { closePip(); openSource(s); } }, s.title),
      s.start != null ? el("span", { class: "muted" }, ` · ${formatClock(s.start, s.long_form)}`) : null))) : null,
    el("button", { type: "button", class: "link", onclick: () => { closePip(); brain.ask.draft = answer.question; openBrain("ask"); } },
      "Ask more in Brain"));
}

async function afterPipChange(res) {
  if (!res.done.length) return;
  await loadNav();  // the subjects carry their timetables
  await loadRecordings();  // names, subjects and combined classes may have changed
  if (state.selectedId && state.view !== "calendar") await selectRecording(state.selectedId);
  if (state.view === "calendar") await loadCalendar();
  loadPipReminders();
  try { $("#pip-status").textContent = pipMessage(await pipFetch()).text; } catch { /* the status just stays */ }
}

async function askPip(text) {
  $("#pip-result").replaceChildren(el("p", { class: "pip-say muted" }, "Thinking…"));
  try {
    const res = await api("/api/pip/command", jsonRequest("POST", { text }));
    if (res.done.length || res.plans?.length || res.open || res.answer) $("#pip-input").value = "";
    showPip(res);
    await afterPipChange(res);
    if (res.open) pipOpen(res.open);
  } catch (error) {
    showPip({ say: error.message, done: [], problems: [], undo: [] });
  }
}

async function quickPip(chip) {
  $("#pip-result").replaceChildren(el("p", { class: "pip-say muted" }, "On it…"));
  try {
    if (chip.meeting) {
      closePip();
      pipOpen({ view: "meeting", start: true });
      return;
    }
    if (chip.plan) {
      showPip(await api("/api/pip/plan", jsonRequest("POST", { kind: chip.plan })));
      return;
    }
    const actions = chip.actions || await remindBeforeClass(chip.remind);
    const res = await api("/api/pip/act", jsonRequest("POST", { actions }));
    showPip(res);
    await afterPipChange(res);
  } catch (error) {
    showPip({ say: error.message, done: [], problems: [], undo: [] });
  }
}

async function undoPip(undo) {
  try {
    const res = await api("/api/pip/undo", jsonRequest("POST", { undo }));
    showPip({ ...res, undo: [] });
    await afterPipChange(res);
  } catch (error) {
    showPip({ say: error.message, done: [], problems: [], undo: [] });
  }
}

async function loadPipReminders() {
  let list = [];
  try {
    list = (await api("/api/reminders")).reminders;
  } catch { /* the list just stays empty */ }
  const when = (iso) => new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short",
    hour: "numeric", minute: "2-digit", timeZone: pipZone() });
  const row = (r) => el("li", {},
    el("span", {}, el("strong", {}, r.text), el("small", {}, ` · ${when(r.at)}`)),
    el("button", { type: "button", class: "bell-dismiss pip-cancel", "aria-label": `Cancel the reminder: ${r.text}`,
      onclick: async () => { await api(`/api/reminders/${r.id}`, { method: "DELETE" }); loadPipReminders(); } }, icon("close")));
  $("#pip-reminders").replaceChildren(...(list.length ? [el("h3", {}, "Coming up"), el("ul", {}, list.map(row))] : []));
}

// What Pip remembers: only what you asked for or approved. Each item says where it came from and when it fades.
async function loadPipMemory(suggest = false) {
  let data = { active: [], proposed: [], expired: [] };
  try {
    data = await api(`/api/pip/memory${suggest ? "?suggest=true" : ""}`);
  } catch { /* the list just stays empty */ }
  const call = async (path, method) => {
    try { await api(path, { method }); } catch (error) { showPip({ say: error.message, done: [], problems: [], undo: [] }); }
    loadPipMemory();
  };
  const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const note = (item) => [item.kind, item.source ? `from ${item.source}` : "you told me",
    item.expires_at ? (item.status === "approved" && Date.parse(item.expires_at) < Date.now() ? `faded ${day(item.expires_at)}` : `until ${day(item.expires_at)}`) : ""]
    .filter(Boolean).join(" · ");
  const row = (item, buttons) => el("li", {}, el("span", {}, el("strong", {}, item.text), el("small", {}, ` · ${note(item)}`)),
    el("span", { class: "pip-memory-actions" }, ...buttons));
  const forget = (item, label = "Forget") => el("button", { type: "button", class: "bell-dismiss pip-cancel",
    "aria-label": `${label}: ${item.text}`, title: label, onclick: () => call(`/api/pip/memory/${item.id}`, "DELETE") }, icon("close"));
  const keep = (item, label) => el("button", { type: "button", class: "link", onclick: () => call(`/api/pip/memory/${item.id}/approve`, "POST") }, label);
  const sections = [];
  if (data.active.length) sections.push(el("h3", {}, "What I remember"), el("ul", {}, data.active.map((i) => row(i, [forget(i)]))));
  if (data.proposed.length) sections.push(el("h3", {}, "Want me to remember these?"),
    el("ul", {}, data.proposed.map((i) => row(i, [keep(i, "Yes"), forget(i, "No")]))));
  if (data.expired.length) sections.push(el("h3", {}, "Faded"),
    el("ul", {}, data.expired.map((i) => row(i, [keep(i, "Keep"), forget(i)]))));
  if (!sections.length) sections.push(el("h3", {}, "What I remember"),
    el("p", { class: "pip-say muted" }, "Nothing yet. Say “remember that I want an A in Machine Learning” and I will, until it fades."));
  sections.push(el("button", { type: "button", class: "link", onclick: () => loadPipMemory(true) }, "Suggest from my notes"));
  $("#pip-memory").replaceChildren(...sections);
}

async function testPipNotification() {
  try {
    const { sent } = await api("/api/reminders/test", { method: "POST" });
    showPip({ say: sent ? "Sent! Look for my notification at the corner of your screen." : "I couldn't show a Windows notification on this computer.",
      done: [], problems: [], undo: [] });
  } catch (error) {
    showPip({ say: error.message, done: [], problems: [], undo: [] });
  }
}

buildPipPanel();
