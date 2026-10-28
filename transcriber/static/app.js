"use strict";

// The languages in the menus come from the app (/api/status): English, Auto-detect, a mixed option, and any languages
// added in .env (EXTRA_LANGUAGES, SECOND_LANGUAGE). This is only the fallback until it answers.
const LANGUAGE_NAMES = { en: "English", auto: "Auto-detect", mixed: "English + your other language (mixed)" };
const mixedNote = () => "Gemini 3.5 Transcribe, from Google: follows switching between English and "
  + `${state.status?.second_language || "your other language"} within a sentence. The other language is written in Roman letters. `
  + "On Gemini's free tier, Google may use the audio to improve its products.";

// Menu order: English, your extra languages, Auto-detect, then the mixed option.
const languageCodes = () => ["en", ...Object.keys(LANGUAGE_NAMES).filter((code) => !["en", "auto", "mixed"].includes(code)), "auto", "mixed"];

// Fill the language menus from the app's list: English, your extra languages, Auto-detect, then the mixed option.
function applyLanguages() {
  for (const { code, name } of state.status?.languages || []) LANGUAGE_NAMES[code] = name;
  const order = languageCodes();
  for (const select of [$("#upload-language"), $("#default-language")]) {
    if (!select) continue;
    const current = select.value;
    select.replaceChildren(...order.map((code) => el("option", { value: code }, LANGUAGE_NAMES[code])));
    select.value = order.includes(current) ? current : "en";
  }
}

// "English + your other language (mixed)" needs a Gemini key in .env: greyed out, with the reason, until there is one.
function markMixed(select, note) {
  const option = select?.querySelector('option[value="mixed"]');
  if (!option) return;
  const ready = Boolean(state.status?.mixed);
  option.disabled = !ready;
  option.textContent = ready ? LANGUAGE_NAMES.mixed : `${LANGUAGE_NAMES.mixed} (add GEMINI_API_KEY to .env)`;
  if (note) { note.textContent = select.value === "mixed" ? mixedNote() : ""; note.hidden = select.value !== "mixed"; }
}
const SECTIONS = [["summary", "Summary"], ["lecture", "Lecture notes"], ["about_them", "About them"],
  ["intro", "Your intro"], ["my_questions", "Your questions"], ["missed_followups", "Missed follow-ups"],
  ["feedback", "Coaching"], ["decisions", "Decisions"], ["action_items", "To do"], ["open_questions", "Questions"]];
// Which sections each notes style shows, and what the summary is called there.
const STYLE_SECTIONS = { lecture: ["summary", "lecture", "action_items", "open_questions"],
  meeting: ["summary", "decisions", "action_items", "open_questions"],
  conversation: ["summary", "feedback", "intro", "my_questions", "missed_followups", "about_them", "action_items"] };
const STYLE_NAMES = { lecture: "Lecture", meeting: "Meeting", conversation: "Conversation" };
const SUMMARY_TITLES = { lecture: "At a glance", meeting: "Summary", conversation: "What you learned" };
const sectionsFor = (style) => STYLE_SECTIONS[style || "lecture"].map((key) =>
  [key, key === "summary" ? SUMMARY_TITLES[style || "lecture"] : SECTIONS.find(([k]) => k === key)[1]]);
// Coaching: what each section's suggestion is, by item id prefix.
const SUGGESTION_LEADS = { I: "Try", Y: "Sharper", F: "Could ask" };
const VERDICTS = {
  supported: ["check", "v-supported", "Checked against the transcript"],
  partial: ["partial", "v-partial", "Partly supported; see the checker's note"],
  unsupported: ["unsupported", "v-unsupported", "Not found in the transcript; see the checker's note"],
};
const NOTES_BUSY = new Set(["queued", "writing", "checking"]);
const RECAP_BUSY = new Set(["queued", "writing", "speaking"]);
const PROPERTIES = new Set(["value", "checked", "disabled", "hidden", "selected", "open"]);
// Binder divider colors for subjects, picked by id so a subject keeps its color.
const DIVIDER_COLORS = ["#e9a23b", "#5b9bd5", "#6cbf84", "#d9708c", "#9b7fd1", "#4fb3b3", "#e07b53", "#a3b84a"];
const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  recordings: [], subjects: [], topics: [], filter: { type: "all", id: null },
  selectedId: null, detail: null, tab: "notes", checked: new Set(),
  pendingFiles: [], settings: null, status: null, pollTimer: null,
  editingId: null, askDraft: "", showEnglish: false, askBusy: false, askPending: "",
  dialogSubject: null, dialogTopic: null, server: null, usage: null,
};

const $ = (selector) => document.querySelector(selector);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (PROPERTIES.has(key)) node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

const jsonRequest = (method, body) => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
});

function formatClock(seconds, longForm) {
  const total = Math.floor(seconds);
  const pad = (n) => String(n).padStart(2, "0");
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  return longForm ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

const formatDuration = (seconds) => (seconds == null ? "" : formatClock(seconds, seconds >= 3600));
const formatDate = (iso) => new Date(iso).toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
const formatDateTime = (iso) => `${formatDate(iso)}, ${new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
// When it was recorded if the file said so (phones stamp it), otherwise when it reached the app.
const madeAt = (rec) => rec.recorded_at || rec.created_at;
const writerOn = () => Boolean(state.status?.notes?.writer);
const notesBusy = (rec) => writerOn() && NOTES_BUSY.has(rec.notes_status);
const isBusy = (rec) => (rec.status !== "done" && rec.status !== "failed") || notesBusy(rec);
const longFormOf = (rec) => (rec.duration_sec || 0) >= 3600;

const dividerColor = (id) => DIVIDER_COLORS[(id - 1) % DIVIDER_COLORS.length];

function statusLabel(rec) {
  switch (rec.status) {
    case "queued": return "Waiting to transcribe";
    case "converting": return "Preparing audio…";
    case "transcribing": return rec.status_detail ? rec.status_detail : rec.progress_total ? `Transcribing part ${rec.progress_done + 1 > rec.progress_total ? rec.progress_total : rec.progress_done + 1} of ${rec.progress_total}…` : "Transcribing…";
    case "waiting": return rec.status_detail || "Waiting for the rate limit…";
    case "done": return "Done";
    case "failed": return "Transcription failed";
    default: return rec.status;
  }
}

function rowState(rec) {
  if (rec.status !== "done") return [statusLabel(rec), rec.status === "failed" ? "failed" : rec.status === "waiting" ? "waiting" : ""];
  if (rec.notes_status === "failed") return ["Notes failed", "failed"];
  if (notesBusy(rec)) return [rec.notes_status === "checking" ? "Checking notes…" : "Writing notes…", ""];
  return ["", ""];
}

function flash(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => { toast.hidden = true; }, 2200);
}

function setDrawer(open) {
  document.body.classList.toggle("drawer-open", open);
  $("#drawer-button").setAttribute("aria-expanded", String(open));
}

// ---- binder (subjects and topics)

async function loadNav() {
  [state.subjects, state.topics, state.people] = await Promise.all([api("/api/subjects"), api("/api/topics"),
    api("/api/people")]);
  renderNav();
}

function renderNav() {
  const { type, id } = state.filter;
  $("#nav-all").classList.toggle("active", type === "all" && !["progress", "practice", "calendar", "brain", "privacy", "today", "learn", "projects"].includes(state.view));
  $("#nav-starred").classList.toggle("active", type === "starred");
  $("#nav-nosubject").classList.toggle("active", type === "nosubject");
  $("#nav-trash").classList.toggle("active", type === "trash");
  $("#subject-nav").replaceChildren(...state.subjects.map((s) => el("li", {},
    el("button", { type: "button", class: `divider${type === "subject" && id === s.id ? " active" : ""}`,
      onclick: () => setFilter("subject", s.id) },
    el("span", { class: "tab-swatch", style: `background:${dividerColor(s.id)}` }),
    el("span", { class: "name" }, s.name), el("span", { class: "count" }, s.count)),
    el("button", { type: "button", class: "nav-edit", title: `Edit ${s.name}`, "aria-label": `Edit subject ${s.name}`,
      onclick: () => openSubjectDialog(s) }, icon("pencil")))));
  $("#topic-nav").replaceChildren(...state.topics.map((t) => el("li", {},
    el("button", { type: "button", class: `divider${type === "topic" && id === t.id ? " active" : ""}`,
      onclick: () => setFilter("topic", t.id) },
    el("span", { class: "name" }, t.name), el("span", { class: "count" }, t.count)),
    el("button", { type: "button", class: "nav-edit", title: `Edit ${t.name}`, "aria-label": `Edit topic ${t.name}`,
      onclick: () => openTopicDialog(t) }, icon("pencil")))));
  $("#topic-empty").hidden = state.topics.length > 0;
  $("#nav-progress").classList.toggle("active", state.view === "progress");
  $("#nav-practice").classList.toggle("active", state.view === "practice");
  $("#nav-calendar").classList.toggle("active", state.view === "calendar");
  $("#nav-brain")?.classList.toggle("active", state.view === "brain");
  $("#nav-privacy")?.classList.toggle("active", state.view === "privacy");
  $("#nav-today")?.classList.toggle("active", state.view === "today");
  $("#nav-learn")?.classList.toggle("active", state.view === "learn");
  $("#nav-projects")?.classList.toggle("active", state.view === "projects");
  $("#people-nav").replaceChildren(...state.people.map((p) => el("li", {},
    el("button", { type: "button", class: `divider${type === "person" && id === p.id ? " active" : ""}`,
      onclick: () => openPerson(p.id) },
    icon("person"),
    el("span", { class: "name" }, p.name), el("span", { class: "count" }, p.count)))));
  $("#people-empty").hidden = state.people.length > 0;
  if (!$("#people-options")) document.body.append(el("datalist", { id: "people-options" }));
  $("#people-options").replaceChildren(...state.people.map((p) => el("option", { value: p.name })));
  $("#subject-options").replaceChildren(...state.subjects.map((s) => el("option", { value: s.name })));
  $("#topic-options").replaceChildren(...state.topics.map((t) => el("option", { value: t.name })));
  $("#move-subject").replaceChildren(el("option", { value: "" }, "Move to: no subject"),
    ...state.subjects.map((s) => el("option", { value: s.name }, `Move to: ${s.name}`)));
  renderListHeader();
}

function currentSubject() {
  return state.filter.type === "subject" ? state.subjects.find((s) => s.id === state.filter.id) : null;
}

function renderListHeader() {
  const { type, id } = state.filter;
  const subject = currentSubject();
  const topic = type === "topic" ? state.topics.find((t) => t.id === id) : null;
  $("#list-heading").textContent = subject ? subject.name : topic ? `#${topic.name}`
    : type === "starred" ? "Starred" : type === "nosubject" ? "No subject"
      : type === "trash" ? "Recently Deleted" : "All recordings";
  const description = $("#list-description");
  const text = type === "trash" ? `Deleted recordings are kept here for ${KEEP_DELETED_DAYS} days, then erased for good.`
    : type === "nosubject" ? "Recordings not in a subject. Tick them and use Move to file them." : subject?.description || "";
  description.textContent = text;
  description.hidden = !text;
  $("#empty-trash").hidden = type !== "trash" || !state.recordings?.length;
}

const FOCUS_VIEWS = ["brain", "today", "learn", "projects", "privacy", "calendar"];

async function setFilter(type, id = null, leaveFocus = true) {
  state.filter = { type, id };
  state.checked.clear();
  if (leaveFocus && FOCUS_VIEWS.includes(state.view) && !state.detail) {  // a list of recordings was asked for: bring the column back
    state.view = "sheet";
    renderDetail();
  }
  renderNav();
  await loadRecordings();
  $("#recording-list").scrollTop = 0;  // a different list starts at its top, not where the last one was scrolled to
  schedulePoll();
}

// ---- the stack of sheets

// Background polling passes onlyIfChanged: redrawing an unchanged sheet or list every 2 s nudged the text you
// were reading, restarted players and made the glass lens jump.
async function loadRecordings({ onlyIfChanged = false } = {}) {
  const seq = (loadRecordings.seq = (loadRecordings.seq || 0) + 1);  // only the newest request may fill the list
  const params = new URLSearchParams();
  const q = $("#search").value.trim();
  if (q) params.set("q", q);
  if (state.filter.type === "subject") params.set("subject_id", state.filter.id);
  if (state.filter.type === "topic") params.set("topic_id", state.filter.id);
  if (state.filter.type === "starred") params.set("starred", "1");
  if (state.filter.type === "nosubject") params.set("no_subject", "1");
  if (state.filter.type === "trash") params.set("deleted", "1");
  if (state.filter.type === "person") params.set("person_id", state.filter.id);
  const [recordings, counts] = await Promise.all([api(`/api/recordings?${params}`), api("/api/recordings/counts")]);
  if (seq !== loadRecordings.seq) return;  // a newer request (another subject, another search) is on its way
  const fingerprint = JSON.stringify(recordings);
  const unchanged = onlyIfChanged && fingerprint === state.recordingsFingerprint;
  state.recordingsFingerprint = fingerprint;
  state.recordings = recordings;
  $("#all-count").textContent = counts.all || "";
  $("#starred-count").textContent = counts.starred || "";
  $("#nosubject-count").textContent = counts.no_subject || "";
  $("#trash-count").textContent = counts.deleted || "";
  const ids = new Set(state.recordings.map((r) => r.id));
  for (const id of [...state.checked]) if (!ids.has(id)) state.checked.delete(id);
  if (!unchanged) renderList();
  renderEngineLine();
}

// Classes recorded in parts: "Combined" lists each class once (its parts tucked away), "Separate" lists the original
// recordings one by one. Nothing is deleted either way.
let listMode = (() => { try { return localStorage.getItem("list.mode") === "separate" ? "separate" : "combined"; } catch { return "combined"; } })();
const isJoined = (rec) => rec.original_filename.includes(" combined: ") || rec.original_filename.endsWith("combined recordings");

function listedRecordings() {
  if ($("#search").value.trim() || state.filter.type === "trash") return state.recordings;
  return listMode === "separate" ? state.recordings.filter((rec) => !isJoined(rec))
    : state.recordings.filter((rec) => !rec.session?.combined);
}

// classes whose parts aren't joined yet (only known when the list shows every recording)
function pendingClasses() {
  if (state.filter.type !== "all") return [];
  const status = new Map(state.recordings.map((rec) => [rec.id, rec.status]));
  const classes = new Map();
  for (const rec of state.recordings) if (rec.session && !rec.session.combined) classes.set(rec.session.ids.join(), rec.session);
  return [...classes.values()].map((c) => ({ ...c, ready: c.ids.every((id) => status.get(id) === "done") }));
}

function renderListTools() {
  $("#rename-tools").hidden = state.filter.type === "trash" || !state.recordings.length;
  const hasClasses = state.filter.type !== "trash" && state.recordings.some((rec) => rec.session || isJoined(rec));
  $("#list-tools").hidden = !hasClasses;
  for (const button of document.querySelectorAll("#list-mode button")) button.setAttribute("aria-pressed", String(button.dataset.mode === listMode));
  const ready = pendingClasses().filter((c) => c.ready).length;
  const combine = $("#combine-all");
  combine.hidden = !ready;
  if (!combine.dataset.busy) {
    combine.disabled = false;
    combine.textContent = ready === 1 ? "Combine this class" : `Combine all ${ready} classes`;
  }
}

function renderList() {
  const rows = listedRecordings();
  $("#recording-list").replaceChildren(...rows.map((rec) => {
    const date = new Date(madeAt(rec));
    const [stateText, stateClass] = rowState(rec);
    return el("li", {},
      el("input", { type: "checkbox", checked: state.checked.has(rec.id), "aria-label": `Select ${rec.title}`,
        onchange: (e) => { e.target.checked ? state.checked.add(rec.id) : state.checked.delete(rec.id); renderSelectionBar(); } }),
      el("button", { type: "button", class: `sheet-row${rec.id === state.selectedId ? " selected" : ""}`,
        "aria-current": rec.id === state.selectedId ? "true" : null, onclick: () => selectRecording(rec.id) },
      el("span", { class: "sheet-date" }, el("b", {}, date.getDate()),
        el("span", {}, date.toLocaleDateString(undefined, { month: "short" }))),
      el("span", { class: "sheet-title" }, rec.title, rec.duplicate ? dupTag(rec.duplicate) : null,
        rec.session ? partTag(rec.session) : null),
      el("span", { class: "sheet-meta" }, rec.deleted_at ? deletedLine(rec)
        : [rec.subject, formatDuration(rec.duration_sec)].filter(Boolean).join(" · ") || " "),
      el("span", { class: `sheet-state ${stateClass}` }, stateText || " ")),
      rec.deleted_at ? null : starButton(rec, "row-star"));
  }));
  const empty = $("#list-empty");
  const filtered = $("#search").value.trim() || state.filter.type !== "all";
  empty.textContent = state.filter.type === "trash" ? "Nothing deleted."
    : filtered ? "Nothing matches." : "No recordings yet. Drop one above to start.";
  renderListHeader();
  empty.hidden = rows.length > 0;
  renderListTools();
  renderSelectionBar();
  placeLens();
}

// ---- Liquid glass: a lens that sits over the selected sheet and slides to the next one.
// Chromium refracts what's under it (an SVG displacement map as a backdrop filter); other browsers
// (Safari on the iPhone) get the same glass without the bending.

const LENS_RADIUS = 10;  // px, matches the lens's border-radius
const LENS_BAND = 8;     // px of rim that bends the light
const LENS_BEND = 5;     // how far the rim pulls the content, in px
const lens = { el: null, size: "", forId: null, refracts: Boolean(navigator.userAgentData?.brands?.some((b) => b.brand === "Chromium")) };

function lensElement() {
  if (lens.el) return lens.el;
  lens.el = el("li", { class: `glass-lens${lens.refracts ? " refract" : ""}`, role: "presentation", "aria-hidden": "true" });
  const list = $("#recording-list");
  new ResizeObserver(() => placeLens(false)).observe(list);
  return lens.el;
}

function placeLens(animate = true) {
  const list = $("#recording-list");
  const glass = lensElement();
  if (glass.parentNode !== list) list.append(glass);  // the list was just rebuilt
  const row = list.querySelector(".sheet-row.selected");
  if (!row) { glass.classList.remove("on"); return; }
  const listBox = list.getBoundingClientRect();
  const box = row.getBoundingClientRect();
  const x = box.left - listBox.left + list.scrollLeft;
  const y = box.top - listBox.top + list.scrollTop;
  // Slide (and wobble) only when you pick a different recording; a redraw of the same one just stays put.
  const moved = glass.classList.contains("on") && lens.forId !== state.selectedId;
  lens.forId = state.selectedId;
  glass.classList.toggle("instant", !animate || !moved);
  glass.style.setProperty("--x", `${x}px`);
  glass.style.setProperty("--y", `${y}px`);
  glass.style.width = `${box.width}px`;
  glass.style.height = `${box.height}px`;
  glass.classList.add("on");
  if (moved && animate) {  // a brief liquid stretch while it travels
    glass.classList.remove("wobble");
    void glass.offsetWidth;
    glass.classList.add("wobble");
  }
  if (lens.refracts) refractionFor(Math.round(box.width), Math.round(box.height));
}

// The displacement map: neutral grey in the middle, pushing inward along the rim like the edge of a lens.
function refractionFor(width, height) {
  const size = `${width}x${height}`;
  if (lens.size === size || !width || !height) return;
  lens.size = size;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  const hx = width / 2, hy = height / 2, r = LENS_RADIUS;
  for (let py = 0; py < height; py++) {
    for (let px = 0; px < width; px++) {
      const qx = Math.abs(px + 0.5 - hx) - (hx - r), qy = Math.abs(py + 0.5 - hy) - (hy - r);
      const ox = Math.max(qx, 0), oy = Math.max(qy, 0);
      const outside = Math.hypot(ox, oy);
      const depth = r - (outside + Math.min(Math.max(qx, qy), 0));  // distance in from the edge
      let dx = 0, dy = 0;
      if (depth < LENS_BAND) {
        const t = Math.max(0, 1 - depth / LENS_BAND);
        const strength = t * t * (3 - 2 * t);  // smooth, strongest right at the rim
        let nx, ny;
        if (outside > 0) { nx = ox / outside; ny = oy / outside; } else if (qx > qy) { nx = 1; ny = 0; } else { nx = 0; ny = 1; }
        dx = -nx * Math.sign(px + 0.5 - hx) * strength;
        dy = -ny * Math.sign(py + 0.5 - hy) * strength;
      }
      const i = (py * width + px) * 4;
      image.data[i] = 128 + dx * 127;
      image.data[i + 1] = 128 + dy * 127;
      image.data[i + 2] = 128;
      image.data[i + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
  const map = canvas.toDataURL();
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true">
    <filter id="liquid-glass" x="0" y="0" width="${width}" height="${height}" filterUnits="userSpaceOnUse"
      color-interpolation-filters="sRGB">
      <feImage href="${map}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="none" result="map"/>
      <feDisplacementMap in="SourceGraphic" in2="map" scale="${LENS_BEND}" xChannelSelector="R" yChannelSelector="G"/>
    </filter></svg>`;
  let holder = document.getElementById("liquid-glass-defs");
  if (!holder) {
    holder = el("div", { id: "liquid-glass-defs" });
    document.body.append(holder);
  }
  holder.innerHTML = svg;
}

function renderSelectionBar() {
  const count = state.checked.size;
  $("#selection-bar").hidden = count === 0;
  $("#selection-count").textContent = `${count} selected`;
  $("#combine-button").disabled = count < 2;
  $("#merge-button").disabled = count < 2 || count > 5;
  const trash = state.filter.type === "trash";
  for (const id of ["#move-subject", "#move-button", "#combine-button", "#merge-button", "#delete-selected"]) $(id).hidden = trash;
  for (const id of ["#restore-selected", "#erase-selected"]) $(id).hidden = !trash;
}

function renderEngineLine() {
  const transcribing = state.recordings.filter((r) => r.status !== "done" && r.status !== "failed").length;
  const writing = state.recordings.filter((r) => r.status === "done" && notesBusy(r)).length;
  const parts = [];
  if (transcribing) parts.push(`${transcribing} transcribing`);
  if (writing) parts.push(`${writing} getting notes`);
  $("#engine-line").textContent = parts.join(" · ");
}

async function moveChecked() {
  const subject = $("#move-subject").value;
  try {
    const { moved } = await api("/api/recordings/move", jsonRequest("POST", { ids: [...state.checked], subject }));
    state.checked.clear();
    flash(`Moved ${moved} recording${moved === 1 ? "" : "s"}`);
    await loadNav();
    await loadRecordings();
    if (state.selectedId) await loadDetail(state.selectedId);
  } catch (error) {
    alert(error.message);
  }
}

async function mergeChecked() {
  const ids = [...state.checked];
  if (ids.length < 2 || ids.length > 5) return;
  if (!confirm(`Merge ${ids.length} recordings of the same session into one best-of transcript?\n\n`
    + "Use this when the same lecture or meeting was recorded on several devices (your phone, a friend's phone, the laptop). "
    + "Where they disagree, the app keeps the clearer version. The originals are kept.")) return;
  try {
    const rec = await api("/api/recordings/merge", jsonRequest("POST", { ids }));
    state.checked.clear();
    flash("Merging\u2026 this takes a minute or two.");
    await loadRecordings();
    await selectRecording(rec.id);
  } catch (error) {
    alert(error.message);
  }
}

async function combineChecked() {
  const ids = [...state.checked];
  if (ids.length < 2) return;
  const title = prompt(`Combine ${ids.length} recordings into one session, in the order they were added.\n\n`
    + "Title for the combined session (leave empty to use the first part's title):", "");
  if (title === null) return;
  try {
    const rec = await api("/api/recordings/combine", jsonRequest("POST", { ids, title }));
    state.checked.clear();
    flash("Combined. The originals are kept.");
    await loadNav();
    await loadRecordings();
    await selectRecording(rec.id);
  } catch (error) {
    alert(error.message);
  }
}

// ---- polling

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const busy = state.recordings.some(isBusy) || (state.detail && isBusy(state.detail))
    || RECAP_BUSY.has(state.detail?.recap?.status) || state.detail?.translation?.status === "working";
  if (busy) state.pollTimer = setTimeout(refresh, 2000);
}

async function refresh() {
  try {
    await loadRecordings({ onlyIfChanged: true });
    loadUsage();
    const summary = state.recordings.find((r) => r.id === state.selectedId);
    const editing = state.askBusy || state.editingId
      || document.activeElement?.closest?.(".sheet-head, .my-notes, .ask-box, .add-line, .instructions");
    const d = state.detail;
    // an open recording that isn't in the current list (opened from the Brain, a person or the calendar) still refreshes
    const detailBusy = d && (isBusy(d) || NOTES_BUSY.has(d.notes_status) || RECAP_BUSY.has(d.recap?.status)
      || d.translation?.status === "working");
    if (d && !editing && (summary ? (summary.status !== d.status || summary.progress_done !== d.progress_done
        || summary.status_detail !== d.status_detail || summary.notes_status !== d.notes_status
        || NOTES_BUSY.has(summary.notes_status) || detailBusy) : detailBusy)) {
      await loadDetail(state.selectedId, { onlyIfChanged: true });
      await loadNav();
    }
    schedulePoll();
  } catch (error) {
    console.error(error);
    state.pollTimer = setTimeout(refresh, 5000);
  }
}

// ---- the open sheet

async function selectRecording(id) {
  state.view = "sheet";
  if (state.selectedId !== id) {
    state.tab = "notes";
    state.editingId = null;
    state.askDraft = "";
    state.showEnglish = false;
  }
  state.selectedId = id;
  setDrawer(false);
  renderList();
  await loadDetail(id);
}

async function loadDetail(id, { onlyIfChanged = false } = {}) {
  try {
    const detail = await api(`/api/recordings/${id}`);
    if (state.selectedId !== id) return;
    if (onlyIfChanged && JSON.stringify(detail) === JSON.stringify(state.detail)) {
      schedulePoll();
      return;
    }
    state.detail = detail;
  } catch (error) {
    state.detail = null;
    $("#detail").replaceChildren(el("p", { class: "error" }, error.message));
    return;
  }
  renderDetail();
  schedulePoll();
}

const cornell = (cue, notes, cls = "") => el("div", { class: `cornell ${cls}`.trim() },
  el("div", { class: "cue" }, cue), el("div", { class: "notes-col" }, notes));

function renderDetail() {
  // a different recording or screen starts at the top, not where the last one was scrolled to
  const where = `${state.view}:${state.selectedId ?? ""}:${state.filter.type === "person" ? state.filter.id : ""}`;
  if (where !== renderDetail.where) { renderDetail.where = where; $("#detail").scrollTop = 0; }
  document.body.classList.toggle("brain-open", state.view === "brain" && !state.detail);  // the concept index takes the stack's place
  // screens with their own workspace don't need the recordings column beside them (it stays in All recordings)
  document.body.classList.toggle("focus-view", !state.detail && FOCUS_VIEWS.includes(state.view));
  if (state.view === "record" && state.rec) return renderRecorder();
  const rec = state.detail;
  const desk = $("#detail");
  if (!rec && state.view === "progress") return renderProgress();
  if (!rec && state.view === "practice") return renderPractice();
  if (!rec && state.view === "calendar") return renderCalendar();
  if (!rec && state.view === "brain") return renderBrain();
  if (!rec && state.view === "privacy") return renderPrivacy();
  if (!rec && state.view === "today") return renderToday();
  if (!rec && state.view === "learn") return renderLearn();
  if (!rec && state.view === "projects") return renderProjects();
  if (!rec && state.view === "person") return renderPerson();
  if (!rec) {
    desk.replaceChildren(el("div", { class: "sheet-wrap" }, el("div", { class: "sheet" },
      cornell([], el("div", { class: "empty-sheet" },
        el("p", {}, "Pick a recording from the stack, or drop audio anywhere on this page."),
        el("p", { class: "muted" }, "Each recording becomes a study sheet: the gist on top, then lecture notes by topic, key terms, what to do and what's still open, every point linked to the moment it was said."))))));
    return;
  }
  const tabs = [["notes", "Notes"], ["ask", "Ask"], ["transcript", "Transcript"], ["mine", "My notes"]];
  const tabline = el("div", { class: "tabline", role: "tablist", "aria-label": "Views of this recording" },
    tabs.map(([key, label]) => el("button", { type: "button", role: "tab", class: `tab${state.tab === key ? " active" : ""}`,
      "aria-selected": state.tab === key ? "true" : "false",
      onclick: () => { state.tab = key; state.editingId = null; renderDetail(); } }, label)),
    el("span", { class: "spacer" }), renderTabActions(rec));
  const renderers = { notes: renderNotesTab, ask: renderAskTab, transcript: renderTranscriptTab, mine: renderMyNotes };
  const body = (renderers[state.tab] || renderNotesTab)(rec);
  desk.replaceChildren(el("div", { class: "sheet-wrap" }, renderHeader(rec), tabline, el("div", { class: "sheet" }, body)));
}

// possible duplicates: a tag in the list and in the calendar, a banner on the recording itself
function dupTag(twin) {
  return el("span", { class: `dup-tag ${twin.kind}`, title: twin.of.map((o) => o.title).join(", ") },
    twin.kind === "exact" ? "Exact copy" : "Possible duplicate");
}

function dupBanner(rec) {
  const twin = rec.duplicate;
  if (!twin) return null;
  const links = twin.of.map((o, i) => [i ? ", " : "", el("button", { type: "button", class: "link",
    onclick: () => selectRecording(o.id) }, o.title)]);
  return el("div", { class: `dup-banner ${twin.kind}`, role: "status" },
    el("span", {}, twin.kind === "exact" ? "Exact copy of the same audio as " : "Possible duplicate: starts at the same time and runs the same length as "),
    links);
}

function renderHeader(rec) {
  const subjectSelect = el("select", { id: "sheet-subject", "aria-label": "Subject", onchange: (e) => changeSubject(e.target) },
    el("option", { value: "", selected: !rec.subject }, "None"),
    state.subjects.map((s) => el("option", { value: s.name, selected: s.name === rec.subject }, s.name)),
    el("option", { value: "__new__" }, "New subject…"));
  const tags = rec.topics.map((t) => t.state === "accepted"
    ? el("span", { class: "tag" }, t.name,
      el("button", { type: "button", title: "Remove topic", "aria-label": `Remove topic ${t.name}`, onclick: () => removeTopic(t.id) }, icon("close")))
    : el("span", { class: "tag suggested", title: "Suggested by the AI" }, t.name,
      el("button", { type: "button", title: "Keep this topic", "aria-label": `Keep suggested topic ${t.name}`, onclick: () => acceptTopic(t.id) }, icon("check")),
      el("button", { type: "button", title: "Dismiss", "aria-label": `Dismiss suggested topic ${t.name}`, onclick: () => removeTopic(t.id) }, icon("close"))));
  const field = (label, ...value) => el("span", { class: "field" }, el("span", { class: "field-label" }, label), ...value);
  const bin = rec.deleted_at ? el("div", { class: "bin-banner", role: "status" },
    el("span", {}, `In Recently Deleted. Erased for good on ${erasedOn(rec).toLocaleDateString(undefined, { day: "numeric", month: "long" })}.`),
    el("span", { class: "spacer" }),
    el("button", { type: "button", onclick: () => restoreRecordings([rec.id]) }, icon("restore"), "Restore"),
    el("button", { type: "button", class: "danger", onclick: () => eraseRecordings([rec.id]) }, "Delete now")) : null;
  return el("div", { class: "sheet-head" }, bin, dupBanner(rec), sessionBanner(rec), partsBanner(rec),
    el("div", { class: "title-row" },
      starButton(rec),
      el("input", { class: "title-input", id: "title-input", value: rec.title, "aria-label": "Title",
        onchange: (e) => patchRecording({ title: e.target.value }),
        onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); } }),
      el("button", { type: "button", class: "title-edit", title: "Rename", "aria-label": "Rename", onclick: startRename },
        icon("pencil")),
      rec.deleted_at ? null : el("button", { type: "button", class: "quiet suggest-name", onclick: (e) => openNameMenu(e.currentTarget) },
        "Suggest a name")),
    el("div", { class: "fields" },
      field("Subject", subjectSelect),
      rec.recorded_at ? field("Recorded", formatDateTime(rec.recorded_at)) : field("Added", formatDate(rec.created_at)),
      rec.duration_sec != null ? field("Length", formatDuration(rec.duration_sec)) : null,
      rec.original_filename ? field("Original name", el("span", { class: "original-name",
        title: "The name this file had on your phone. It stays the same when you rename the recording." }, rec.original_filename),
      el("button", { type: "button", class: "link", onclick: () => copyOriginalName(rec.original_filename) }, "Copy")) : null,
      field("Language", LANGUAGE_NAMES[rec.language]),
      field("Notes", styleSelect(rec)),
      privacyField(rec),
      rec.effective_notes_style === "conversation" ? field("With", el("input", { class: "person-input", value: rec.person || "",
        list: "people-options", autocomplete: "off", placeholder: "Who was it with?", "aria-label": "Who the chat was with",
        onchange: (e) => patchRecording({ person: e.target.value.trim() }).then(loadNav) })) : null,
      rec.part_starts?.length > 1 ? field("Parts", rec.part_starts.length) : null,
      rec.merged_from?.length ? field("Merged from", el("span", { class: "merged-from" },
        rec.merged_from.map((m) => el("button", { type: "button", title: "Open this recording",
          onclick: () => selectRecording(m.id) }, m.title, el("span", { class: "share" }, ` ${Math.round(m.share * 100)}%`))))) : null,
      rec.speakers?.length ? field("Speakers", el("span", { class: "speaker-names" },
        rec.speakers.map((s) => el("button", { type: "button", title: `Rename ${s.name}`,
          onclick: () => renameSpeaker(s) }, s.name)))) : null),
    missingSpeakers(rec) ? el("p", { class: "sheet-note" },
      "No speaker labels: your server was off when this was transcribed. Re-transcribe from the menu to try again.") : null,
    el("div", { class: "tags" }, tags,
      el("input", { class: "tag-add", placeholder: "add topic", list: "topic-options", autocomplete: "off", "aria-label": "Add a topic",
        onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); addTopic(e.target.value); } } })),
    rec.has_audio ? el("div", { class: "player" },
      el("audio", { controls: true, preload: "metadata", src: `/api/recordings/${rec.id}/audio` })) : null,
    recapStrip(rec), highlightsStrip(rec));
}

// ---- Highlights reel: the key moments, cut from the recording, in the speaker's own voice

function highlightsStrip(rec) {
  if (rec.status !== "done" || rec.notes?.status !== "ready" || !rec.notes?.content || rec.deleted_at || !rec.has_audio) return null;
  const reel = rec.highlights;
  const label = el("span", { class: "recap-label" }, "Highlights");
  const make = (text) => el("button", { type: "button", class: "quiet", onclick: (e) => makeHighlights(e.currentTarget) },
    icon("redo"), text);
  if (!reel) {
    return el("div", { class: "recap-strip" }, label,
      el("span", { class: "recap-note" }, "The key moments in their own voice, cut from this recording."), make("Make highlights"));
  }
  const src = `/api/recordings/${rec.id}/highlights.mp3?v=${encodeURIComponent(reel.made_at)}`;
  return el("div", { class: "recap-strip ready" }, label,
    el("audio", { controls: true, preload: "none", src, "aria-label": "Highlights reel" }),
    el("span", { class: "recap-note" }, `${formatClock(reel.duration_sec, false)} · ${reel.clips.length} moments`),
    el("a", { class: "button quiet", href: src, download: "" }, "Download"),
    make("Remake"),
    el("details", { class: "recap-script" }, el("summary", {}, "What's in it"),
      el("ol", { class: "reel-moments" }, reel.clips.map((clip) => el("li", {},
        citeButton(clip.start, longFormOf(rec)), " ", clip.label)))));
}

async function makeHighlights(button) {
  button.disabled = true;
  button.lastChild.textContent = "Cutting…";
  await act(() => api(`/api/recordings/${state.selectedId}/highlights`, { method: "POST" }));
  renderDetail();  // also resets the button if cutting failed
}

// ---- Audio recap: the notes as a 5-minute spoken briefing

function recapStrip(rec) {
  if (rec.status !== "done" || rec.notes?.status !== "ready" || !rec.notes?.content || rec.deleted_at) return null;
  const recap = rec.recap;
  const label = el("span", { class: "recap-label" }, "Recap");
  const make = (text) => el("button", { type: "button", class: "quiet", onclick: makeRecap }, icon("redo"), text);
  if (!recap) {
    return el("div", { class: "recap-strip" }, label,
      el("span", { class: "recap-note" }, "A five-minute spoken summary to listen to on the way."), make("Make a recap"));
  }
  if (RECAP_BUSY.has(recap.status)) {
    const doing = recap.status === "speaking" ? "Recording the voice…" : recap.status === "writing" ? "Writing the script…"
      : "Waiting to make the recap…";
    return el("div", { class: "recap-strip", role: "status" }, label, el("span", { class: "pulse", "aria-hidden": "true" }),
      el("span", {}, doing), recap.detail ? el("span", { class: "recap-note" }, recap.detail) : null);
  }
  if (recap.status === "failed") {
    return el("div", { class: "recap-strip error", role: "status" }, label,
      el("span", {}, recap.detail || "Making the recap failed."), make("Try again"));
  }
  const src = `/api/recordings/${rec.id}/recap.mp3?v=${encodeURIComponent(recap.updated_at)}`;
  return el("div", { class: "recap-strip ready" }, label,
    el("audio", { controls: true, preload: "none", src, "aria-label": "Spoken recap" }),
    el("span", { class: "recap-note" }, formatClock(recap.duration_sec || 0, false)),
    el("a", { class: "button quiet", href: src, download: "" }, "Download"),
    make("Remake"),
    recap.script ? el("details", { class: "recap-script" }, el("summary", {}, "Read the script"),
      recap.script.split(/\n+/).filter((p) => p.trim()).map((p) => el("p", {}, p))) : null);
}

async function makeRecap() {
  await act(() => api(`/api/recordings/${state.selectedId}/recap`, { method: "POST" }));
}

function fillRecapSettings() {
  const select = $("#recap-voice");
  select.replaceChildren(...Object.entries(state.settings.recap_voices || {}).map(([value, name]) =>
    el("option", { value, selected: value === state.settings.recap_voice }, name)));
  $("#recaps-on").checked = state.settings.recaps_on === "1";
  $("#recap-speed").value = state.settings.recap_speed;
  $("#recap-speed-value").textContent = `${Number(state.settings.recap_speed).toFixed(2)}×`;
}

async function playRecapSample() {
  const button = $("#recap-sample");
  button.disabled = true;
  try {
    const sample = new Audio(`/api/recaps/sample?voice=${encodeURIComponent($("#recap-voice").value)}`);
    await sample.play();
  } catch (error) {
    alert("The sample couldn't play. The voice may still be downloading the first time; try again in a minute.");
  } finally {
    button.disabled = false;
  }
}

function renderTabActions(rec) {
  const panel = el("div", { class: "menu-panel" },
    el("button", { type: "button", onclick: (e) => { e.target.closest(".menu").removeAttribute("open"); startRename(); } },
      icon("pencil"), "Rename"));
  if (rec.status === "failed") panel.append(el("button", { type: "button", onclick: retry }, icon("redo"), "Retry transcription"));
  if (rec.status === "done") {
    panel.append(
      el("a", { class: "button", href: `/api/recordings/${rec.id}/export?format=md` }, icon("download"), "Download notes + transcript (.md)"),
      el("a", { class: "button", href: `/api/recordings/${rec.id}/export?format=txt` }, icon("download"), "Download transcript (.txt)"));
    if (rec.translation?.status === "ready") {
      panel.append(
        el("a", { class: "button", href: `/api/recordings/${rec.id}/export?format=md&lang=english` }, icon("download"), "Download notes + transcript in English (.md)"),
        el("a", { class: "button", href: `/api/recordings/${rec.id}/export?format=txt&lang=english` }, icon("download"), "Download transcript in English (.txt)"));
    }
  }
  if (rec.status === "done" || rec.status === "failed") {
    const select = el("select", { "aria-label": "Language for re-transcription" },
      languageCodes().map((code) => el("option", { value: code, selected: code === rec.language,
        disabled: code === "mixed" && !state.status?.mixed }, LANGUAGE_NAMES[code])));
    const speakers = el("input", { type: "checkbox", checked: rec.want_speakers && speakersPossible(),
      disabled: !speakersPossible() });
    panel.append(el("div", { class: "menu-row" }, select,
      el("label", { class: "check", title: speakersHint() || "Ask your server who is speaking" }, speakers, "Speakers"),
      el("button", { type: "button", onclick: async () => {
        if (!confirm("Re-transcribe this recording? The transcript and AI notes will be replaced.")) return;
        await act(() => api(`/api/recordings/${rec.id}/retranscribe`,
          jsonRequest("POST", { language: select.value, speakers: speakers.checked })));
      } }, "Re-transcribe")));
  }
  if (!rec.deleted_at) panel.append(el("button", { type: "button", class: "danger", onclick: deleteRecording }, icon("trash"), "Delete recording"));
  return el("div", { class: "tab-actions" },
    rec.status === "done" ? el("button", { type: "button", class: "quiet", onclick: copyAll, title: "Copy the transcript" },
      icon("copy"), el("span", { class: "label" }, "Copy")) : null,
    el("details", { class: "menu" }, el("summary", { class: "button quiet", "aria-label": "More actions" }, icon("more")), panel));
}

function starButton(rec, extraClass = "") {
  const label = rec.starred ? `Unstar ${rec.title}` : `Star ${rec.title}`;
  return el("button", { type: "button", class: `star-button ${extraClass}${rec.starred ? " on" : ""}`.trim(),
    title: rec.starred ? "Starred (click to unstar)" : "Star", "aria-label": label,
    "aria-pressed": rec.starred ? "true" : "false",
    onclick: (e) => { e.stopPropagation(); toggleStar(rec); } }, icon("star"));
}

async function toggleStar(rec) {
  try {
    await api(`/api/recordings/${rec.id}`, jsonRequest("PATCH", { starred: !rec.starred }));
    await loadRecordings();
    if (state.selectedId) await loadDetail(state.selectedId);
  } catch (error) {
    alert(error.message);
  }
}

function startRename() {
  const input = $("#title-input");
  if (!input) return;
  input.focus();
  input.select();
}

function citeButton(seconds, longForm) {
  const time = formatClock(seconds, longForm);
  return el("button", { type: "button", class: "timestamp", title: "Play from here", "aria-label": `Play from ${time}`,
    onclick: (e) => seek(seconds, e.currentTarget.closest(".note-row, .para")) }, time);
}

function verdictMark(item, showVerdict) {
  let spec = null;
  if (item.edited) spec = ["pencil", "v-edited", "Edited; not re-checked"];
  else if (showVerdict) spec = VERDICTS[item.verdict] || ["dot", "v-unchecked", "Not checked"];
  if (!spec) return null;
  const [name, cls, label] = spec;
  return el("span", { class: `verdict ${cls}`, title: label, role: "img", "aria-label": label }, icon(name));
}

function noteRow(item, longForm, showVerdict, editable, extraClass = "") {
  if (editable && state.editingId === item.id) return cornell([], noteEditor(item), "note-row");
  const who = [item.owner && el("span", { class: "who" }, item.owner), item.due && `due ${item.due}`].filter(Boolean);
  const meta = who.length ? el("div", { class: "note-meta" }, who.flatMap((part, i) => (i ? [" · ", part] : [part]))) : null;
  return cornell(
    [verdictMark(item, showVerdict), ...item.cites.slice(0, 2).map((c) => citeButton(c, longForm))],
    [el("div", { class: `note-text${item.kind ? ` kind-${item.kind}` : ""}`,
      onclick: (e) => e.currentTarget.closest(".note-row").classList.toggle("open") },
      kindMark(item), item.term && item.kind === "definition" ? el("strong", { class: "term" }, `${item.term}: `) : null,
      el("span", { class: "ink" }, item.text), item.edited ? el("span", { class: "stamp" }, "edited") : null),
      item.suggestion ? el("p", { class: "suggestion" },
        el("span", { class: "lead" }, `${SUGGESTION_LEADS[item.id[0]] || "Try"}: `), item.suggestion) : null,
      meta,
      !item.edited && item.reason && item.verdict && item.verdict !== "supported" ? el("p", { class: "reason" }, item.reason) : null,
      editable ? el("span", { class: "note-tools" },
        el("button", { type: "button", title: "Edit", "aria-label": `Edit note: ${item.text}`,
          onclick: () => { state.editingId = item.id; renderDetail(); } }, icon("pencil")),
        el("button", { type: "button", title: "Delete", "aria-label": `Delete note: ${item.text}`,
          onclick: () => deleteNoteItem(item.id) }, icon("close"))) : null],
    `note-row ${extraClass}`.trim());
}

const KIND_LABELS = { example: "Example", fact: "Fact", open: "Open", closed: "Closed", strength: "Keep",
  improve: "Work on" };

function kindMark(item) {
  if (item.kind === "stressed") return el("span", { class: "kind-mark stressed", title: "The lecturer stressed this",
    role: "img", "aria-label": "Stressed by the lecturer" }, "★");
  return KIND_LABELS[item.kind] ? el("span", { class: "kind-mark" }, KIND_LABELS[item.kind]) : null;
}

// Lecture notes: items under their topic headings, in the order taught.
function lectureBlock(items, longForm, checked, editable) {
  const rows = [cornell(el("span", { class: "section-label" }, "Lecture notes"), [], "section-row")];
  let heading = null;
  for (const item of items) {
    if (item.heading && item.heading.toLowerCase() !== heading?.toLowerCase()) {
      heading = item.heading;
      rows.push(cornell([], el("h3", { class: "topic-heading" }, heading[0].toUpperCase() + heading.slice(1)), "topic-row"));
    }
    rows.push(noteRow(item, longForm, checked, editable));
  }
  return el("div", { class: "notes-block lecture-block" }, rows);
}

// Key terms: every definition from the lecture in one place, for quick revision.
function keyTermsBlock(items, longForm) {
  const terms = items.filter((item) => item.kind === "definition" && item.term);
  if (terms.length < 2) return null;
  return el("div", { class: "notes-block key-terms" },
    cornell(el("span", { class: "section-label" }, "Key terms"), [], "section-row"),
    terms.map((item) => cornell(item.cites.slice(0, 1).map((c) => citeButton(c, longForm)),
      el("div", { class: "note-text" }, el("strong", { class: "term" }, `${item.term}: `), item.text), "note-row")));
}

// ---- People: everyone you've had coffee chats with, and a brief before you meet them again

async function openPerson(id) {
  state.view = "person";
  state.selectedId = null;
  state.detail = null;
  state.person = null;
  await setFilter("person", id);
  setDrawer(false);
  renderDetail();
  try {
    state.person = await api(`/api/people/${id}`);
  } catch (error) {
    alert(error.message);
  }
  renderDetail();
}

function renderPerson() {
  const p = state.person;
  const desk = $("#detail");
  if (!p) { desk.replaceChildren(el("div", { class: "sheet-wrap" }, el("p", { class: "muted" }, "Loading…"))); return; }
  const when = (iso) => formatDate(iso);
  const open = (rid) => el("button", { type: "button", class: "link", onclick: () => selectRecording(rid) });
  const brief = p.brief;
  const briefBlock = brief ? el("div", { class: "brief" },
    el("p", { class: "brief-lead" }, brief.who),
    el("p", {}, brief.last_time),
    brief.promises?.length ? [el("h4", {}, "You said you'd"), el("ul", {}, brief.promises.map((t) => el("li", {}, t)))] : null,
    el("h4", {}, "Questions to ask next time"), el("ol", {}, brief.questions.map((t) => el("li", {}, t))),
    brief.reminder ? el("p", { class: "brief-reminder" }, el("span", { class: "lead" }, "Remember: "), brief.reminder) : null,
    el("p", { class: "muted small" }, `Written ${formatDateTime(p.brief_at)}`)) : null;
  desk.replaceChildren(el("div", { class: "sheet-wrap" }, el("div", { class: "sheet person-sheet" },
    el("div", { class: "person-head" },
      el("h2", {}, p.name),
      el("button", { type: "button", class: "quiet", onclick: () => editPerson(p) }, icon("pencil"), "Edit")),
    el("p", { class: "muted" }, `${p.chats.length} chat${p.chats.length === 1 ? "" : "s"}${p.last_at ? ` · last on ${when(p.last_at)}` : ""}`),
    p.note ? el("p", { class: "person-note" }, p.note) : null,
    el("section", { class: "person-block" },
      el("div", { class: "person-block-head" }, el("span", { class: "section-label" }, "Before you meet again"),
        writerOn() ? el("button", { type: "button", class: brief ? "quiet" : "primary", onclick: (e) => makeBrief(p.id, e.currentTarget) },
          icon("redo"), brief ? "Rewrite the brief" : "Brief me") : null),
      briefBlock || el("p", { class: "muted" }, "A short brief from your past chats: who they are, what you talked about, what you promised, and fresh questions to ask.")),
    p.about.length ? el("section", { class: "person-block" }, el("span", { class: "section-label" }, "What you know about them"),
      el("ul", {}, p.about.map((a) => el("li", {}, a.text, " ", Object.assign(open(a.recording_id), { textContent: a.title }))))) : null,
    p.promises.length ? el("section", { class: "person-block" }, el("span", { class: "section-label" }, "Promises"),
      el("ul", {}, p.promises.map((a) => el("li", {}, a.text, a.owner ? el("span", { class: "muted" }, ` · ${a.owner}`) : null)))) : null,
    el("section", { class: "person-block" }, el("span", { class: "section-label" }, "Chats"),
      el("ul", { class: "person-chats" }, p.chats.map((c) => el("li", {},
        Object.assign(open(c.id), { textContent: c.title }), el("span", { class: "muted" }, ` · ${when(c.when)}`))))))));
}

async function makeBrief(id, button) {
  button.disabled = true;
  button.lastChild.textContent = "Writing…";
  try {
    state.person = await api(`/api/people/${id}/brief`, { method: "POST" });
  } catch (error) {
    alert(error.message);
  }
  renderDetail();
}

async function editPerson(p) {
  const name = prompt("Name:", p.name);
  if (name === null) return;
  const note = prompt("A note about them (where you met, what they do):", p.note || "");
  if (note === null) return;
  try {
    state.person = await api(`/api/people/${p.id}`, jsonRequest("PATCH", { name, note }));
  } catch (error) {
    alert(error.message);
  }
  await loadNav();
  renderDetail();
}

// ---- Your progress: coffee-chat numbers over time

async function openProgress() {
  state.view = "progress";
  state.selectedId = null;
  state.detail = null;
  state.progress = null;
  setDrawer(false);
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  renderDetail();
  try {
    state.progress = await api("/api/coach/progress");
  } catch (error) {
    alert(error.message);
  }
  renderDetail();
}

function trendChart(title, values, { format, target, targetLabel, lowerIsBetter }) {
  const w = 300, h = 96, pad = 14;
  const max = Math.max(...values, target ?? 0) * 1.15 || 1;
  const x = (i) => values.length === 1 ? w / 2 : pad + (i * (w - 2 * pad)) / (values.length - 1);
  const y = (v) => h - pad - (v / max) * (h - 2 * pad);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "trend");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${title}: ${values.map(format).join(", ")}`);
  const add = (name, attrs) => { const node = document.createElementNS(SVG_NS, name); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); svg.append(node); return node; };
  if (target != null) add("line", { x1: pad, x2: w - pad, y1: y(target), y2: y(target), class: "trend-target" });
  if (values.length > 1) add("polyline", { points: values.map((v, i) => `${x(i)},${y(v)}`).join(" "), class: "trend-line" });
  values.forEach((v, i) => add("circle", { cx: x(i), cy: y(v), r: i === values.length - 1 ? 4 : 2.6,
    class: i === values.length - 1 ? "trend-dot last" : "trend-dot" }));
  const last = values[values.length - 1], first = values[0];
  const better = values.length > 1 && (lowerIsBetter ? last < first : last > first);
  return el("figure", { class: "trend-card" },
    el("figcaption", {}, el("span", {}, title), el("b", {}, format(last))),
    svg,
    el("p", { class: "trend-note" }, values.length > 1 ? `${better ? "Better" : "Not better yet"} than your first chat (${format(first)})` : "One chat so far",
      target != null ? ` · ${targetLabel}` : ""));
}

function renderProgress() {
  const data = state.progress;
  const desk = $("#detail");
  const wrap = (...children) => desk.replaceChildren(el("div", { class: "sheet-wrap" }, el("div", { class: "sheet progress-sheet" },
    el("h2", {}, "Your coffee-chat progress"), ...children)));
  if (!data) return wrap(el("p", { class: "muted" }, "Loading…"));
  if (!data.chats.length) {
    return wrap(el("p", { class: "muted" }, "No coached chats yet. Set a recording's Notes to Conversation (or a subject's Notes style), and its numbers show up here."));
  }
  const chats = data.chats;
  const pct = (v) => `${Math.round(v * 100)}%`;
  wrap(
    el("p", { class: "muted" }, `${chats.length} coached chat${chats.length === 1 ? "" : "s"}, oldest to newest.`),
    el("div", { class: "trend-grid" },
      trendChart("You talked", chats.map((c) => c.my_share), { format: pct, target: 0.4, targetLabel: "line: 40%", lowerIsBetter: true }),
      trendChart("Questions asked", chats.map((c) => c.questions), { format: String }),
      trendChart("Filler words a minute", chats.map((c) => c.fillers_per_min), { format: (v) => v.toFixed(1), lowerIsBetter: true })),
    data.work_on.length ? el("section", { class: "person-block" }, el("span", { class: "section-label" }, "Keep working on"),
      el("ul", {}, data.work_on.map((f) => el("li", {}, f.text, " ",
        el("button", { type: "button", class: "link", onclick: () => selectRecording(f.recording_id) }, f.title))))) : null,
    data.keep.length ? el("section", { class: "person-block" }, el("span", { class: "section-label" }, "What's working"),
      el("ul", {}, data.keep.map((f) => el("li", {}, f.text)))) : null,
    el("section", { class: "person-block" }, el("span", { class: "section-label" }, "Every chat"),
      el("table", { class: "progress-table" },
        el("thead", {}, el("tr", {}, ["Date", "With", "You talked", "Questions", "Fillers/min"].map((t) => el("th", {}, t)))),
        el("tbody", {}, [...chats].reverse().map((c) => el("tr", { onclick: () => selectRecording(c.id), tabindex: 0,
          onkeydown: (e) => { if (e.key === "Enter") selectRecording(c.id); } },
          el("td", {}, formatDate(c.when)), el("td", {}, c.person || c.title), el("td", {}, pct(c.my_share)),
          el("td", {}, c.questions), el("td", {}, c.fillers_per_min.toFixed(1))))))));
}

// ---- Conversation coach: your numbers and the thank-you message

function statsCard(rec) {
  const s = rec.conversation_stats;
  const share = Math.round(s.my_share * 100);
  const perMinute = s.my_minutes ? s.fillers / s.my_minutes : 0;
  const figure = (value, label, note) => el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label),
    note ? el("small", {}, note) : null);
  const fillers = Object.entries(s.filler_words).sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([word, n]) => `“${word}” ×${n}`).join(", ");
  return el("div", { class: "stats-card" },
    figure(`${share}%`, "you talked", share > 45 ? "aim for under 40% when you're there to learn" : "good: they did most of the talking"),
    figure(rec.notes?.content?.my_questions?.length ?? s.my_questions, "questions asked"),  // Whisper often drops "?"
    figure(formatClock(s.longest_turn_sec, false), "your longest stretch"),
    figure(s.fillers, "filler words", s.fillers ? `${perMinute.toFixed(1)} a minute${fillers ? ` · ${fillers}` : ""}` : null),
    s.wpm ? figure(s.wpm, "words a minute", s.wpm > 170 ? "fast: slow down a little" : s.wpm < 110 ? "slow: pick up the pace a bit"
      : "easy to follow (130–160 is the sweet spot)") : null,
    s.unclear?.length ? el("div", { class: "stats-unclear" }, el("span", {}, "Hard to make out — say these more clearly:"),
      el("ul", {}, s.unclear.map((u) => el("li", {}, citeButton(u.start, longFormOf(rec)), " ", u.text)))) : null,
    el("p", { class: "stats-who" }, "Who's who was worked out from what was said, not from voices. ",
      el("button", { type: "button", class: "link", onclick: swapSpeakers }, "Wrong way round? Swap")));
}

async function swapSpeakers() {
  if (!confirm("Swap you and the other person in the transcript, and rewrite the coaching?")) return;
  await act(() => api(`/api/recordings/${state.selectedId}/speakers/swap`, { method: "POST" }));
}

function followUpBlock(content) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content.follow_up_draft);
      flash("Message copied");
    } catch {
      alert("Couldn't copy; select the text instead.");
    }
  };
  return el("div", { class: "follow-up" },
    el("div", { class: "follow-up-head" }, el("span", { class: "section-label" }, "Follow-up message"),
      el("button", { type: "button", class: "quiet", onclick: copy }, icon("copy"), "Copy")),
    el("p", { class: "follow-up-text" }, content.follow_up_draft));
}

function styleSelect(rec) {
  const subjectStyle = state.subjects.find((s) => s.name === rec.subject)?.notes_style || "lecture";
  const follow = rec.subject ? `${STYLE_NAMES[subjectStyle]} (from ${rec.subject})` : `${STYLE_NAMES.lecture} (default)`;
  return el("select", { "aria-label": "Notes style", onchange: (e) => changeStyle(rec, e.target.value) },
    el("option", { value: "", selected: !rec.notes_style }, follow),
    Object.entries(STYLE_NAMES).map(([value, name]) => el("option", { value, selected: rec.notes_style === value }, name)));
}

async function changeStyle(rec, value) {
  const before = rec.effective_notes_style;
  await patchRecording({ notes_style: value });
  await loadDetail(rec.id);
  const after = state.detail?.effective_notes_style;
  if (after !== before && state.detail?.notes?.content && writerOn()
      && confirm(`Rewrite the notes as ${after} notes now?`)) await regenerateNotes();
}

function noteEditor(item) {
  const isAction = item.id.startsWith("A");
  const text = el("textarea", { rows: 2, value: item.text, "aria-label": "Note text" });
  const owner = isAction ? el("input", { value: item.owner || "", placeholder: "Who", "aria-label": "Who does it" }) : null;
  const due = isAction ? el("input", { value: item.due || "", placeholder: "By when", "aria-label": "Due" }) : null;
  const save = () => saveNoteItem(item.id, { text: text.value, owner: owner?.value ?? null, due: due?.value ?? null });
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
    if (e.key === "Escape") { state.editingId = null; renderDetail(); }
  });
  setTimeout(() => text.focus(), 0);
  return el("div", { class: "note-edit" }, text, isAction ? el("div", { class: "row" }, owner, due) : null,
    el("div", { class: "row" },
      el("button", { type: "button", class: "primary", onclick: save }, "Save"),
      el("button", { type: "button", class: "quiet", onclick: () => { state.editingId = null; renderDetail(); } }, "Cancel")));
}

function addLine(style) {
  const section = el("select", { "aria-label": "Section for the new point" },
    sectionsFor(style).map(([key, title]) => el("option", { value: key }, title)));
  const text = el("input", { placeholder: "Add a point of your own…", "aria-label": "New point",
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } } });
  const add = () => addNoteItem(section.value, text.value);
  return el("div", { class: "add-line" }, text, section, el("button", { type: "button", onclick: add }, icon("plus"), "Add"));
}

function instructionsBlock(rec) {
  const subject = state.subjects.find((s) => s.name === rec.subject);
  const textarea = el("textarea", { rows: 3, value: rec.instructions?.recording || "",
    placeholder: "Context for this recording only, e.g. “Recorded in three parts; the second speaker is a guest lecturer.”",
    "aria-label": "Notes instructions for this recording" });
  const save = async (regenerate) => {
    try {
      await api(`/api/recordings/${rec.id}/instructions`, jsonRequest("PUT", { text: textarea.value }));
      if (regenerate) await regenerateNotes();
      else { flash("Saved. Regenerate the notes to use it."); await loadDetail(rec.id); }
    } catch (error) {
      alert(error.message);
    }
  };
  const inherited = rec.instructions?.subject;
  return el("details", { class: "instructions", open: Boolean(rec.instructions?.recording) },
    el("summary", {}, "Instructions for the AI"),
    el("div", { class: "body" },
      inherited
        ? el("p", { class: "inherited" }, `From ${rec.subject}: ${inherited} `,
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); openSubjectDialog(subject); } }, "Edit"))
        : rec.subject ? el("p", { class: "hint" }, `Tip: instructions on the subject “${rec.subject}” apply to all its recordings. `,
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); openSubjectDialog(subject); } }, "Add them")) : null,
      textarea,
      el("div", { class: "row" },
        el("button", { type: "button", onclick: () => save(false) }, "Save"),
        writerOn() ? el("button", { type: "button", class: "primary", onclick: () => save(true) }, "Save and rewrite notes") : null)));
}

function statusStrip(rec, notes) {
  const busy = notes && NOTES_BUSY.has(notes.status) && writerOn();
  if (rec.status !== "done") {
    return el("div", { class: "sheet-status", role: "status" }, el("span", { class: "pulse", "aria-hidden": "true" }),
      `${statusLabel(rec)} The notes come right after.`);
  }
  if (!writerOn() && !notes?.content) {
    return el("div", { class: "sheet-status" },
      "Automatic notes are off (no notes model configured). Add your own points below, or ask about this recording in the Ask tab.");
  }
  if (!notes) {
    return el("div", { class: "sheet-status" }, "No notes yet.",
      el("button", { type: "button", class: "primary", onclick: regenerateNotes }, "Write notes"));
  }
  if (busy) {
    const label = notes.status === "checking" ? "Checking each point against the transcript…"
      : notes.status === "writing" ? "Writing notes…" : "Waiting to write notes…";
    return el("div", { class: "sheet-status", role: "status" }, el("span", { class: "pulse", "aria-hidden": "true" }), label,
      notes.status_detail ? el("span", { class: "detail" }, notes.status_detail) : null);
  }
  if (notes.status === "failed") {
    return el("div", { class: "sheet-status error" }, notes.status_detail || "Writing notes failed.",
      el("button", { type: "button", onclick: regenerateNotes }, icon("redo"), "Try again"));
  }
  return null;
}

function renderNotesTab(rec) {
  const notes = rec.notes;
  const longForm = longFormOf(rec);
  const busy = notes && NOTES_BUSY.has(notes.status) && writerOn();
  const parts = [statusStrip(rec, notes)];
  if (rec.status !== "done") return parts;
  const content = notes?.content;
  const editable = !busy;
  if (content && rec.conversation_stats) parts.push(cornell([], statsCard(rec)));
  if (content) {
    const checked = Boolean(notes.checker) && !notes.check_detail;
    const section = (key, title, extraClass = "") => {
      const items = content[key] || [];
      if (!items.length) return null;
      return el("div", { class: key === "summary" ? "summary-box" : "notes-block" },
        cornell(el("span", { class: "section-label" }, title), [], "section-row"),
        items.map((item) => noteRow(item, longForm, checked, editable, extraClass)));
    };
    const style = rec.effective_notes_style;
    // Notes written in the other style still show everything they have.
    const own = STYLE_SECTIONS[style || "lecture"];
    const keys = [...own.map((key) => SECTIONS.find(([k]) => k === key)),
      ...SECTIONS.filter(([key]) => !own.includes(key) && content[key]?.length)];
    const titles = Object.fromEntries(sectionsFor(style));
    if (content.attendees?.length) {
      parts.push(cornell(el("span", { class: "section-label" }, "Attendees"), el("div", { class: "note-text attendees" },
        content.attendees.join(", ")), "section-row"));
    }
    const blocks = keys.flatMap(([key, title]) => key === "lecture"
      ? [content.lecture?.length ? lectureBlock(content.lecture, longForm, checked, editable) : null,
        keyTermsBlock(content.lecture || [], longForm)]
      : [section(key, titles[key] || title)]).filter(Boolean);
    parts.push(...blocks);
    if (!blocks.length) parts.push(cornell([], el("div", { class: "note-text muted" }, "Nothing in the notes yet.")));
    if (content.missed?.length) {
      parts.push(el("div", { class: "notes-block missed" }, cornell(el("span", { class: "section-label" }, "Missed?"), [], "section-row"),
        content.missed.map((item) => noteRow(item, longForm, false, false))));
    }
  }
  if (content?.follow_up_draft) parts.push(cornell([], followUpBlock(content)));
  if (editable) parts.push(cornell([], addLine(rec.effective_notes_style)));
  parts.push(cornell([], instructionsBlock(rec)));
  if (content) {
    const checkText = notes.check_detail ? notes.check_detail : notes.checker ? `checked by ${notes.checker}` : "not checked";
    parts.push(cornell([], el("div", { class: "sheet-foot" },
      el("span", {}, `Written by ${notes.writer || "?"} · ${checkText}`), el("span", { class: "spacer" }),
      !busy && writerOn() ? el("button", { type: "button", class: "quiet", onclick: regenerateNotes }, icon("redo"), "Rewrite notes") : null)));
  }
  return parts;
}

const notesItemsUrl = (suffix = "") => `/api/recordings/${state.selectedId}/notes/items${suffix}`;

async function saveNoteItem(id, fields) {
  try {
    await api(notesItemsUrl(`/${id}`), jsonRequest("PUT", fields));
    state.editingId = null;
    await loadDetail(state.selectedId);
  } catch (error) {
    alert(error.message);
  }
}

async function deleteNoteItem(id) {
  if (!confirm("Delete this point from the notes?")) return;
  await act(() => api(notesItemsUrl(`/${id}`), { method: "DELETE" }));
}

async function addNoteItem(section, text) {
  if (!text.trim()) return;
  await act(() => api(notesItemsUrl(), jsonRequest("POST", { section, text })));
}

// ---- Ask: questions and answers on the same sheet

const CITE_PATTERN = /\[(?:(\d+):)?(\d{1,2}):(\d{2})\]/g;

function withCites(text, longForm) {
  const nodes = [];
  let last = 0;
  for (const match of text.matchAll(CITE_PATTERN)) {
    nodes.push(text.slice(last, match.index));
    const seconds = Number(match[1] || 0) * 3600 + Number(match[2]) * 60 + Number(match[3]);
    nodes.push(citeButton(seconds, longForm));
    last = match.index + match[0].length;
  }
  nodes.push(text.slice(last));
  return nodes;
}

function engineName(name) {
  if (!name) return "";
  return name.startsWith("claude-code") ? "Claude" : name.split(":").pop();
}

const normalized = (text) => (text || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
// Same comparison as the server's apply: wording, owner and due date all count.
const itemKey = (item) => [item.text, item.owner, item.due].map(normalized).join("|");

function proposalCard(message) {
  const proposal = message.proposal;
  const current = state.detail.notes?.content || {};
  const blocks = [];
  for (const [key, title] of SECTIONS) {
    const before = current[key] || [];
    const after = proposal[key] || [];
    const beforeKeys = new Set(before.map(itemKey));
    const afterKeys = new Set(after.map(itemKey));
    const removed = before.filter((i) => !afterKeys.has(itemKey(i)));
    if (!after.length && !removed.length) continue;
    const describe = (i) => [i.text, i.owner ? ` · ${i.owner}` : "", i.due ? ` · due ${i.due}` : ""];
    blocks.push(el("h4", {}, title), el("ul", {},
      after.map((i) => el("li", { class: beforeKeys.has(itemKey(i)) ? "" : "added" }, describe(i))),
      removed.map((i) => el("li", { class: "removed" }, describe(i)))));
  }
  const pending = message.proposal_state === "pending";
  return el("div", { class: "proposal" },
    el("strong", {}, "Proposed change to the notes"),
    blocks.length ? blocks : el("p", { class: "muted" }, "No visible changes."),
    pending
      ? el("div", { class: "actions" },
        el("button", { type: "button", class: "primary", onclick: () => decideProposal(message.id, "apply") }, "Apply to notes"),
        el("button", { type: "button", class: "quiet", onclick: () => decideProposal(message.id, "dismiss") }, "Dismiss"))
      : el("p", { class: "done" }, message.proposal_state === "applied" ? "Applied to the notes." : "Dismissed."));
}

function qaRow(message, longForm) {
  if (message.role === "user") {
    return cornell(el("b", {}, "Q"), el("div", { class: "note-text multi" }, message.content), "qa q");
  }
  return cornell([el("b", {}, "A"), el("span", { class: "engine" }, engineName(message.engine))],
    [el("div", { class: "note-text multi" }, withCites(message.content, longForm)),
      message.note ? el("p", { class: "chat-note" }, message.note) : null,
      message.proposal ? proposalCard(message) : null], "qa a");
}

function renderAskTab(rec) {
  const longForm = longFormOf(rec);
  const parts = [];
  if (rec.status !== "done") return [el("div", { class: "sheet-status" }, "You can ask questions once the transcript is ready.")];
  if (!state.status?.ask) {
    parts.push(el("div", { class: "sheet-status" },
      "No AI model is set up for questions. Sign Claude Code in (run: claude auth login) or configure a notes model, then restart the app."));
  }
  parts.push(...rec.chat.map((m) => qaRow(m, longForm)));
  if (!rec.chat.length && !state.askBusy) {
    parts.push(cornell([], el("div", { class: "note-text muted" },
      "Ask anything about this recording: “What's on the exam?”, “Draft a follow-up email”, or “Make the summary three points”.")));
  }
  if (state.askBusy) {
    parts.push(cornell(el("b", {}, "Q"), el("div", { class: "note-text multi" }, state.askPending), "qa q"),
      cornell(el("b", {}, "A"), el("div", { class: "note-text" }, "Thinking…"), "qa a thinking"));
  }
  const input = el("textarea", { rows: 3, value: state.askBusy ? "" : state.askDraft, disabled: state.askBusy,
    placeholder: "Ask about this recording…", "aria-label": "Ask about this recording",
    oninput: (e) => { state.askDraft = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendAsk(); } } });
  parts.push(el("div", { class: "ask-box" }, input, el("div", { class: "row" },
    el("span", {}, state.status?.ask ? `Answered by ${engineName(state.status.ask)} · Enter to send, Shift+Enter for a new line` : ""),
    el("span", { class: "spacer" }),
    rec.chat.length ? el("button", { type: "button", class: "quiet", disabled: state.askBusy, onclick: clearChat }, "Clear") : null,
    el("button", { type: "button", class: "primary", disabled: state.askBusy, onclick: sendAsk }, icon("send"), "Ask"))));
  return parts;
}

async function sendAsk() {
  const message = state.askDraft.trim();
  if (!message || state.askBusy) return;
  const id = state.selectedId;
  state.askBusy = true;
  state.askPending = message;
  renderDetail();
  try {
    await api(`/api/recordings/${id}/chat`, jsonRequest("POST", { message }));
    state.askDraft = "";
  } catch (error) {
    alert(error.message);
  } finally {
    state.askBusy = false;
    state.askPending = "";
  }
  if (state.selectedId === id) await loadDetail(id);
}

async function decideProposal(messageId, decision) {
  await act(() => api(`/api/recordings/${state.selectedId}/chat/${messageId}/${decision}`, { method: "POST" }));
}

async function clearChat() {
  if (!confirm("Clear this conversation? Changes you applied to the notes are kept.")) return;
  await act(() => api(`/api/recordings/${state.selectedId}/chat`, { method: "DELETE" }));
}

// ---- transcript

function renderTranscriptTab(rec) {
  const longForm = longFormOf(rec);
  if (rec.status === "failed") return [el("div", { class: "sheet-status error" }, rec.status_detail || "Transcription failed.")];
  if (rec.status !== "done") {
    return [el("div", { class: "progress-line" }, el("span", { class: "pulse", "aria-hidden": "true" }), statusLabel(rec),
      rec.progress_total ? el("progress", { max: rec.progress_total, value: rec.progress_done }) : null)];
  }
  if (!rec.paragraphs.length) return [cornell([], el("div", { class: "note-text muted" }, "No speech was found in this recording."))];
  const starts = rec.part_starts?.length > 1 ? rec.part_starts : [];
  let nextPart = 0;
  const rows = [translateBar(rec)];
  let previous = null;
  // English lines go with the paragraphs by position; if the transcript has changed since, show the original
  const english = state.showEnglish && rec.translation?.status === "ready"
    && rec.translation.paragraphs.length === rec.paragraphs.length ? rec.translation.paragraphs : null;
  for (const [index, p] of rec.paragraphs.entries()) {
    while (nextPart < starts.length && p.start >= starts[nextPart] - 0.5) {
      nextPart += 1;
      rows.push(el("div", { class: "part-break" }, `Part ${nextPart}`));
      previous = null;
    }
    const turn = p.speaker_label && p.speaker_label !== previous;
    previous = p.speaker_label;
    const cue = [turn ? el("button", { type: "button", class: "speaker", title: `Rename ${p.speaker}`,
      onclick: () => renameSpeaker({ label: p.speaker_label, name: p.speaker }) }, p.speaker) : null,
    citeButton(p.start, longForm)];
    const who = turn ? el("b", { class: "who-inline" }, `${p.speaker}: `) : null;
    rows.push(cornell(cue, el("div", { class: "note-text" }, who, el("span", { class: "ink" }, english?.[index].text ?? p.text)),
      `para${turn && rows.length > 1 ? " turn" : ""}`));
  }
  return rows;
}

// English translation of the transcript: made on request, kept beside the original, and shown instead of it on a toggle
function translateBar(rec) {
  const t = rec.translation;
  const send = (method) => act(() => api(`/api/recordings/${rec.id}/translate`, { method }));
  if (t?.status === "working") {
    return el("div", { class: "translate-bar", role: "status" }, el("span", { class: "pulse", "aria-hidden": "true" }),
      `Translating to English… ${t.progress_done} of ${t.progress_total} paragraphs`,
      t.detail ? el("span", { class: "muted translate-note" }, t.detail) : null);
  }
  if (t?.status === "ready") {
    const stale = t.paragraphs.length !== rec.paragraphs.length;
    const choice = (label, english) => el("button", { type: "button", class: `link${state.showEnglish === english ? " on" : ""}`,
      "aria-pressed": String(state.showEnglish === english), onclick: () => { state.showEnglish = english; renderDetail(); } }, label);
    return el("div", { class: "translate-bar" }, choice("Original", false), " · ", choice("English", true),
      el("span", { class: "muted" }, ` translated by ${t.model}`), el("span", { class: "spacer" }),
      el("button", { type: "button", class: "link", onclick: () => {
        if (confirm("Remove the English translation? Your original transcript isn't touched, but translating again uses your notes model.")) send("DELETE");
      } }, "Remove translation"),
      t.detail ? el("span", { class: "muted translate-note" }, t.detail) : null,
      stale ? el("span", { class: "error", role: "alert" }, "The transcript changed since this was translated, so the original is shown. Translate again.") : null);
  }
  return el("div", { class: "translate-bar" },
    el("button", { type: "button", class: "quiet", onclick: async (e) => {
      e.currentTarget.disabled = true;  // a second click while it starts would only be refused
      state.showEnglish = true;
      await send("POST");
      if (!state.detail?.translation) state.showEnglish = false;  // it didn't start
    } }, "Translate to English"),
    t?.status === "failed" ? el("span", { class: "error", role: "alert" }, ` ${t.detail || "Translation failed."}`) : null);
}

// ---- speakers and your server

const speakersPossible = () => Boolean(state.server?.reachable && state.server?.speakers);

function speakersHint() {
  const server = state.server;
  if (!server?.configured) return "Needs your own server (see server/README.md).";
  if (!server.reachable) return `Your server isn't answering${server.error ? `: ${server.error}` : ""}.`;
  if (!server.speakers) return "Your server is on, but speaker labels are off there (no HF_TOKEN).";
  return "";
}

const missingSpeakers = (rec) => rec.status === "done" && rec.want_speakers && !rec.speakers?.length
  && !(rec.transcribed_by || "").startsWith("server/");

async function renameSpeaker(speaker) {
  const name = prompt(`Name for ${speaker.name} (leave empty for the default):`, speaker.name);
  if (name === null) return;
  try {
    await api(`/api/recordings/${state.selectedId}/speakers`, jsonRequest("PUT", { names: { [speaker.label]: name } }));
    await loadDetail(state.selectedId);
    if (state.detail?.notes?.content) flash("Renamed. Rewrite the notes to use the new name.");
  } catch (error) {
    alert(error.message);
  }
}

async function loadServer(refresh = false) {
  try {
    state.server = await api(`/api/server${refresh ? "?refresh=1" : ""}`);
  } catch {
    state.server = null;
  }
}

const hours = (seconds) => {
  const h = seconds / 3600;
  return h >= 10 || Number.isInteger(h) ? h.toFixed(0) : h.toFixed(1);
};

async function loadUsage() {
  try {
    state.usage = await api("/api/usage");
  } catch {
    return;
  }
  const u = state.usage;
  const day = u.day || { used_sec: u.day_sec, available_sec: u.day_limit_sec - u.day_sec, full_at: null };
  const share = day.used_sec / u.day_limit_sec;
  const meter = $("#usage");
  meter.hidden = !state.status?.api_key_configured;
  meter.classList.toggle("near", share >= 0.8 && share < 1);
  meter.classList.toggle("full", day.available_sec <= 60 || (u.hour && u.hour.available_sec <= 60));
  const back = day.full_at ? fullAgain(day.full_at) : null;
  $("#usage-label").replaceChildren(
    el("span", { class: "long" }, day.full_at
      ? `Groq · ${hours(day.used_sec)} of ${hours(u.day_limit_sec)} h used · full again in ${back.in}`
      : `Groq · all ${hours(u.day_limit_sec)} h free`),
    el("span", { class: "short" }, day.full_at ? `${hours(day.available_sec)} h free · ${back.in}` : `${hours(u.day_limit_sec)} h free`));
  $("#usage-fill").style.transform = `scaleX(${Math.min(1, share)})`;
  const perHour = 24 / (u.day_limit_sec / 3600);
  meter.title = `Free now: ${hours(day.available_sec)} of ${hours(u.day_limit_sec)} h. `
    + `Groq gives usage back steadily, about 1 hour every ${perHour.toFixed(0)} hours, not all at once. `
    + (day.full_at ? `All ${hours(u.day_limit_sec)} h are back ${back.at} (in ${back.in}). ` : "")
    + (u.hour ? `This hour: ${hours(u.hour.used_sec)} of ${hours(u.hour_limit_sec)} h used. ` : "")
    + "When a limit is reached, recordings wait and resume on their own.";
}

// "in 7 h 30 min" and "Wed 3:10 PM" for when Groq's allowance is fully back
function fullAgain(iso) {
  const at = new Date(iso);
  const minutes = Math.max(1, Math.round((at - Date.now()) / 60000));
  const h = Math.floor(minutes / 60), m = minutes % 60;
  return { in: h ? `${h} h${m ? ` ${m} min` : ""}` : `${m} min`,
    at: at.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }) };
}

function renderServerStatus() {
  const s = state.server;
  const line = $("#server-status");
  line.className = "server-status";
  if (!s?.configured) {
    line.textContent = "Not set up. Add TRANSCRIBE_SERVER_URL and TRANSCRIBE_SERVER_TOKEN to .env (see server/README.md).";
    line.classList.add("off");
  } else if (s.reachable) {
    line.textContent = `Connected · ${s.model}${s.device ? ` on ${s.device === "cuda" ? "GPU" : "CPU"}` : ""}`
      + ` · speakers ${s.speakers ? "on" : "off"}`;
  } else {
    line.textContent = `Not answering${s.error ? `: ${s.error}` : ""}`;
    line.classList.add("bad");
  }
  $("#server-test").hidden = !s?.configured;
}

// ---- my notes

function renderMyNotes(rec) {
  const saveState = el("span", { class: "save-state", "aria-live": "polite" }, "Saved automatically as you type.");
  let timer;
  const textarea = el("textarea", { value: rec.my_notes, placeholder: "Your own notes about this recording…",
    "aria-label": "My notes",
    oninput: (e) => {
      saveState.textContent = "Saving…";
      clearTimeout(timer);
      const text = e.target.value;
      timer = setTimeout(async () => {
        try {
          await api(`/api/recordings/${rec.id}/my-notes`, jsonRequest("PUT", { text }));
          rec.my_notes = text;
          saveState.textContent = "Saved";
        } catch (error) {
          saveState.textContent = `Not saved: ${error.message}`;
        }
      }, 800);
    } });
  return [el("div", { class: "my-notes" }, textarea, saveState)];
}

// ---- actions

async function act(fn) {
  try {
    await fn();
    await loadRecordings();
    if (state.selectedId) await loadDetail(state.selectedId);
  } catch (error) {
    alert(error.message);
  }
}

async function patchRecording(fields) {
  await act(() => api(`/api/recordings/${state.selectedId}`, jsonRequest("PATCH", fields)));
  await loadNav();
  renderDetail();  // again, now that the subject list includes any subject this change created
}

async function changeSubject(select) {
  let name = select.value;
  if (name === "__new__") {
    name = (prompt("Name of the new subject:") || "").trim();
    if (!name) { renderDetail(); return; }
  }
  await patchRecording({ subject: name });
}

const recordingTopicsUrl = (suffix = "") => `/api/recordings/${state.selectedId}/topics${suffix}`;

async function addTopic(name) {
  if (!name.trim()) return;
  await act(() => api(recordingTopicsUrl(), jsonRequest("POST", { name })));
  await loadNav();
}

async function acceptTopic(topicId) {
  await act(() => api(recordingTopicsUrl(`/${topicId}`), { method: "PUT" }));
  await loadNav();
}

async function removeTopic(topicId) {
  await act(() => api(recordingTopicsUrl(`/${topicId}`), { method: "DELETE" }));
  await loadNav();
}

async function regenerateNotes() {
  const content = state.detail?.notes?.content;
  const edited = content && SECTIONS.some(([key]) => (content[key] || []).some((item) => item.edited));
  if (edited && !confirm("Rewriting the notes replaces your edits. Continue?")) return;
  await act(() => api(`/api/recordings/${state.selectedId}/notes/regenerate`, { method: "POST" }));
}

const retry = () => act(() => api(`/api/recordings/${state.selectedId}/retry`, { method: "POST" }));

// ---- Recently Deleted: deleting is undoable for KEEP_DELETED_DAYS days

const KEEP_DELETED_DAYS = 30;
const DAY_MS = 86_400_000;

function erasedOn(rec) {
  return new Date(new Date(rec.deleted_at).getTime() + KEEP_DELETED_DAYS * DAY_MS);
}

function deletedLine(rec) {
  const ago = Math.floor((Date.now() - new Date(rec.deleted_at).getTime()) / DAY_MS);
  const left = Math.max(0, Math.ceil((erasedOn(rec).getTime() - Date.now()) / DAY_MS));
  return `Deleted ${ago === 0 ? "today" : ago === 1 ? "yesterday" : `${ago} days ago`} · erased in ${left} day${left === 1 ? "" : "s"}`;
}

async function afterBinChange(message) {
  state.checked.clear();
  if (state.selectedId && !state.recordings.some((r) => r.id === state.selectedId && !r.deleted_at)) {
    state.selectedId = null;
    state.detail = null;
  }
  flash(message);
  await loadNav();
  await loadRecordings();
  if (state.selectedId) await loadDetail(state.selectedId); else renderDetail();
}

async function deleteRecording() {
  try {
    await api(`/api/recordings/${state.selectedId}`, { method: "DELETE" });
  } catch (error) {
    alert(error.message);
    return;
  }
  state.selectedId = null;
  state.detail = null;
  await afterBinChange(`Moved to Recently Deleted. It's kept for ${KEEP_DELETED_DAYS} days.`);
}

async function deleteSelected() {
  const ids = [...state.checked];
  if (!ids.length) return;
  try {
    await api("/api/recordings/delete", jsonRequest("POST", { ids }));
  } catch (error) {
    alert(error.message);
    return;
  }
  if (ids.includes(state.selectedId)) { state.selectedId = null; state.detail = null; }
  await afterBinChange(`${ids.length} moved to Recently Deleted.`);
}

async function restoreRecordings(ids) {
  try {
    for (const id of ids) await api(`/api/recordings/${id}/restore`, { method: "POST" });
  } catch (error) {
    alert(error.message);
  }
  await afterBinChange(ids.length === 1 ? "Restored." : `${ids.length} restored.`);
}

async function eraseRecordings(ids) {
  const what = ids.length === 1 ? "this recording" : `these ${ids.length} recordings`;
  if (!confirm(`Delete ${what} for good? The audio, transcript and notes are erased and can't be brought back.`)) return;
  try {
    for (const id of ids) await api(`/api/recordings/${id}?forever=1`, { method: "DELETE" });
  } catch (error) {
    alert(error.message);
  }
  if (ids.includes(state.selectedId)) { state.selectedId = null; state.detail = null; }
  await afterBinChange("Deleted for good.");
}

async function emptyTrash() {
  const count = state.recordings.length;
  if (!confirm(`Erase all ${count} recording${count === 1 ? "" : "s"} in Recently Deleted for good? This can't be undone.`)) return;
  try {
    await api("/api/trash", { method: "DELETE" });
  } catch (error) {
    alert(error.message);
    return;
  }
  state.selectedId = null;
  state.detail = null;
  await afterBinChange("Recently Deleted is empty.");
}

function seek(seconds, row) {
  const player = $("#detail audio");
  if (!player) return;
  player.currentTime = seconds;
  player.play().catch(() => {});
  if (row) {  // the highlighter sweep across the line you jumped to
    document.querySelectorAll(".swept").forEach((node) => node.classList.remove("swept"));
    void row.offsetWidth;
    row.classList.add("swept");
  }
}

async function copyAll() {
  try {
    const response = await fetch(`/api/recordings/${state.selectedId}/export?format=txt`);
    await navigator.clipboard.writeText(await response.text());
    flash("Transcript copied");
  } catch (error) {
    alert(`Could not copy: ${error.message}`);
  }
}

// ---- subject and topic dialogs

function openSubjectDialog(subject = null) {
  state.dialogSubject = subject;
  $("#subject-dialog-title").textContent = subject ? "Edit subject" : "New subject";
  $("#subject-name").value = subject?.name || "";
  $("#subject-description").value = subject?.description || "";
  $("#subject-instructions").value = subject?.instructions || "";
  $("#subject-style").value = subject?.notes_style || "lecture";
  fillTimetable(subject);
  $("#subject-delete").hidden = !subject;
  $("#subject-error").hidden = true;
  $("#subject-dialog").showModal();
}

async function saveSubject(event) {
  event.preventDefault();
  const body = { name: $("#subject-name").value, description: $("#subject-description").value,
    instructions: $("#subject-instructions").value, notes_style: $("#subject-style").value,
    schedule: timetableFromDialog() };
  try {
    // A new subject is created first, then given its notes style and timetable.
    const id = state.dialogSubject?.id ?? (await api("/api/subjects", jsonRequest("POST", body))).id;
    await api(`/api/subjects/${id}`, jsonRequest("PATCH", body));
    $("#subject-dialog").close();
    await loadNav();
    await loadRecordings();
    if (state.selectedId) await loadDetail(state.selectedId);
    if (state.view === "calendar") await loadCalendar();
  } catch (error) {
    $("#subject-error").textContent = error.message;
    $("#subject-error").hidden = false;
  }
}

async function deleteSubject() {
  const subject = state.dialogSubject;
  if (!subject || !confirm(`Delete the subject "${subject.name}"? Its ${subject.count} recording(s) are kept, without a subject.`)) return;
  try {
    await api(`/api/subjects/${subject.id}`, { method: "DELETE" });
  } catch (error) {
    alert(error.message);
    return;
  }
  $("#subject-dialog").close();
  if (state.filter.type === "subject" && state.filter.id === subject.id) state.filter = { type: "all", id: null };
  await loadNav();
  await loadRecordings();
  if (state.selectedId) await loadDetail(state.selectedId);
}

function openTopicDialog(topic) {
  state.dialogTopic = topic;
  $("#topic-name").value = topic.name;
  $("#topic-error").hidden = true;
  $("#topic-dialog").showModal();
}

async function saveTopic(event) {
  event.preventDefault();
  try {
    const { id } = await api(`/api/topics/${state.dialogTopic.id}`, jsonRequest("PATCH", { name: $("#topic-name").value }));
    $("#topic-dialog").close();
    if (state.filter.type === "topic" && state.filter.id === state.dialogTopic.id) state.filter.id = id;
    await loadNav();
    await loadRecordings();
    if (state.selectedId) await loadDetail(state.selectedId);
  } catch (error) {
    $("#topic-error").textContent = error.message;
    $("#topic-error").hidden = false;
  }
}

async function deleteTopic() {
  const topic = state.dialogTopic;
  if (!topic || !confirm(`Delete the topic "${topic.name}" from all recordings?`)) return;
  try {
    await api(`/api/topics/${topic.id}`, { method: "DELETE" });
  } catch (error) {
    alert(error.message);
    return;
  }
  $("#topic-dialog").close();
  if (state.filter.type === "topic" && state.filter.id === topic.id) state.filter = { type: "all", id: null };
  await loadNav();
  await loadRecordings();
  if (state.selectedId) await loadDetail(state.selectedId);
}

// ---- upload

function openUploadDialog(fileList) {
  state.pendingFiles = [...fileList];
  if (!state.pendingFiles.length) return;
  const today = new Date().toLocaleDateString("en-CA");
  $("#upload-files").replaceChildren(...state.pendingFiles.map((file) => el("label", {},
    el("span", { class: "file-name" }, file.name),
    el("input", { name: "title", autocomplete: "off", value: `${file.name.replace(/\.[^.]+$/, "")} — ${today}`, "aria-label": `Title for ${file.name}` }))));
  // A real dropdown: Edge treats free-text suggestion boxes as autofill fields ("Manage personal info") and its
  // menu could leave the box empty.
  const subjectSelect = $("#upload-subject");
  subjectSelect.replaceChildren(el("option", { value: "" }, "No subject"),
    ...state.subjects.map((s) => el("option", { value: s.name }, s.name)), el("option", { value: "__new__" }, "New subject…"));
  subjectSelect.value = currentSubject()?.name || "";
  enhanceSelect(subjectSelect);
  $("#upload-new-subject").value = "";
  $("#upload-new-subject-row").hidden = true;
  subjectSelect.onchange = () => {
    $("#upload-new-subject-row").hidden = subjectSelect.value !== "__new__";
    if (subjectSelect.value === "__new__") $("#upload-new-subject").focus();
  };
  $("#upload-language").value = state.settings?.default_language || "en";
  markMixed($("#upload-language"), $("#upload-mixed-note"));
  $("#upload-language").onchange = () => markMixed($("#upload-language"), $("#upload-mixed-note"));
  $("#upload-error").hidden = true;
  setUploadSpeakers();
  $("#upload-dialog").showModal();
  loadServer().then(setUploadSpeakers);
}

// ---- the app's own dropdown: a styled list over a real <select> (which keeps the value and change events).
// Windows draws native select lists plainly; free-text boxes with suggestions get Edge's autofill menu instead.

function enhanceSelect(select, { compact = false } = {}) {
  let picker = select.picker;
  if (!picker) {
    if (!select.parentNode) return;
    const button = el("button", { type: "button", class: "picker-button", "aria-haspopup": "listbox", "aria-expanded": "false" },
      el("span", { class: "picker-value" }), icon("chevron"));
    const menu = el("ul", { class: "picker-menu", role: "listbox", tabindex: -1, hidden: true });
    const wrap = el("div", { class: `picker${compact ? " compact" : ""}` }, button, menu);
    select.after(wrap);
    select.classList.add("picker-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    button.setAttribute("aria-label", select.getAttribute("aria-label")
      || select.closest("label")?.firstChild?.textContent?.trim() || "Choose");
    picker = select.picker = { button, menu, wrap, active: 0, compact };
    const items = () => [...menu.querySelectorAll("[role=option]:not([aria-disabled=true])")];
    const highlight = (index) => {
      const list = items();
      if (!list.length) return;
      picker.active = (index + list.length) % list.length;
      menu.querySelectorAll("[role=option]").forEach((item) => item.classList.toggle("active", item === list[picker.active]));
      list[picker.active].scrollIntoView({ block: "nearest" });
    };
    const outside = (e) => { if (!wrap.contains(e.target)) close(false); };
    const onScroll = (e) => { if (!menu.contains(e.target)) close(false); };
    const onResize = () => close(false);
    const close = (focus = true) => {
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
      if (focus) button.focus();
    };
    // The menu is fixed to the window so a scrolling or clipped container can't cut it off; it opens upward when
    // there's no room below.
    const place = () => {
      const box = button.getBoundingClientRect();
      const width = Math.max(box.width, compact ? 256 : 0);
      menu.style.minWidth = `${width}px`;
      menu.style.maxWidth = `${Math.min(window.innerWidth - 16, Math.max(width, 420))}px`;
      menu.style.left = "0px";
      menu.style.top = "0px";
      const height = menu.offsetHeight;
      const below = window.innerHeight - box.bottom - 12;
      const top = below >= height || box.top < height + 12 ? box.bottom + 6 : box.top - 6 - height;
      const left = Math.min(Math.max(8, box.left), window.innerWidth - menu.offsetWidth - 8);
      menu.style.top = `${Math.max(8, top)}px`;
      menu.style.left = `${left}px`;
    };
    const choose = (value) => {
      const option = [...select.options].find((o) => o.value === value);
      if (!option || option.disabled) return;
      select.value = value;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      picker.render();
      close(value !== "__new__");
    };
    const open = () => {
      if (select.disabled) return;
      picker.render();
      menu.hidden = false;
      place();
      button.setAttribute("aria-expanded", "true");
      const list = items();
      highlight(Math.max(0, list.findIndex((i) => i.dataset.value === select.value)));
      menu.focus({ preventScroll: true });
      document.addEventListener("pointerdown", outside, true);
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onResize);
    };
    button.addEventListener("click", () => (menu.hidden ? open() : close()));
    button.addEventListener("keydown", (e) => { if (["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); open(); } });
    menu.addEventListener("keydown", (e) => {
      const list = items();
      if (e.key === "ArrowDown") { e.preventDefault(); highlight(picker.active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(picker.active - 1); }
      else if (e.key === "Home") { e.preventDefault(); highlight(0); }
      else if (e.key === "End") { e.preventDefault(); highlight(list.length - 1); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (list[picker.active]) choose(list[picker.active].dataset.value); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === "Tab") close(false);
      else if (e.key.length === 1) {
        const from = picker.active + 1;
        const order = [...list.slice(from), ...list.slice(0, from)];
        const hit = order.find((i) => i.textContent.trim().toLowerCase().startsWith(e.key.toLowerCase()));
        if (hit) highlight(list.indexOf(hit));
      }
    });
    picker.choose = choose;
    // Keep the button in step when the app sets the value or rebuilds the options.
    for (const prop of ["value", "selectedIndex"]) {
      const native = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
      Object.defineProperty(select, prop, { configurable: true, get() { return native.get.call(this); },
        set(v) { native.set.call(this, v); picker.render(); } });
    }
    new MutationObserver(() => picker.render()).observe(select, { childList: true, subtree: true, attributes: true,
      attributeFilter: ["disabled", "selected", "label"] });
  }
  picker.render = () => {
    const selected = select.options[select.selectedIndex] || select.options[0];
    picker.button.querySelector(".picker-value").textContent = selected?.textContent || "";
    picker.button.classList.toggle("placeholder", !selected?.value);
    picker.button.disabled = select.disabled;
    if (picker.menu.hidden && picker.menu.childElementCount) return;  // the list is rebuilt when it opens
    picker.menu.replaceChildren(...[...select.options].map((option, i) => {
      const isNew = option.value === "__new__";
      const isSelected = option === selected;
      return el("li", { role: "option", id: `${select.id || "picker"}-opt-${i}`, "data-value": option.value,
        "aria-selected": String(isSelected), "aria-disabled": option.disabled ? "true" : null,
        class: `${isNew ? "picker-new" : ""}${!option.value ? " picker-none" : ""}${option.disabled ? " disabled" : ""}`,
        onpointermove: (e) => { if (!option.disabled) {
          picker.menu.querySelectorAll("[role=option]").forEach((c) => c.classList.toggle("active", c === e.currentTarget)); } },
        onclick: () => picker.choose(option.value) },
      isNew ? icon("plus") : null, el("span", { class: "picker-label" }, option.textContent),
      isSelected ? el("span", { class: "picker-chip" }, "Selected") : null);
    }));
  };
  picker.render();
}

// Every dropdown in the app gets the styled menu, including ones drawn later (sheets, dialogs, panels).
function enhanceSelects(root) {
  const selects = root.matches?.("select") ? [root] : [...(root.querySelectorAll?.("select") || [])];
  for (const select of selects) {
    if (select.multiple || select.size > 1 || select.hasAttribute("data-native") || select.picker) continue;
    enhanceSelect(select, { compact: Boolean(select.closest(".field")) });
  }
}
enhanceSelects(document);
new MutationObserver((changes) => {
  for (const change of changes) for (const node of change.addedNodes) if (node.nodeType === 1) enhanceSelects(node);
}).observe(document.body, { childList: true, subtree: true });

function setUploadSpeakers() {
  const box = $("#upload-speakers");
  box.disabled = !speakersPossible();
  if (box.disabled) box.checked = false;
  const hint = speakersHint();
  $("#upload-speakers-hint").textContent = hint || "Your server labels who is speaking; you can rename them later.";
}

async function submitUpload(event) {
  event.preventDefault();
  const form = new FormData();
  state.pendingFiles.forEach((file) => form.append("files", file));
  document.querySelectorAll("#upload-files input[name=title]").forEach((input) => form.append("titles", input.value));
  const subject = $("#upload-subject").value;
  form.append("subject", subject === "__new__" ? $("#upload-new-subject").value.trim() : subject);
  form.append("language", $("#upload-language").value);
  form.append("speakers", $("#upload-speakers").checked ? "1" : "");
  const submit = $("#upload-submit");
  submit.disabled = true;
  submit.textContent = "Uploading…";
  try {
    const created = await api("/api/recordings", { method: "POST", body: form });
    $("#upload-dialog").close();
    await loadNav();
    await loadRecordings();
    if (state.view === "calendar") await loadCalendar();
    if (created.length) await selectRecording(created[0].id);
    schedulePoll();
  } catch (error) {
    $("#upload-error").textContent = error.message;
    $("#upload-error").hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Transcribe";
  }
}

// ---- settings

const glossaryLength = (text) => text.split(/[,\n]/).map((t) => t.trim()).filter(Boolean).join(", ").length;

function updateVocabularyCount() {
  const max = state.settings.vocabulary_max_chars;
  const used = glossaryLength($("#vocabulary").value);
  const note = $("#vocabulary-count");
  note.textContent = used > max
    ? `${used} / ${max} characters. Terms past the limit are ignored, so keep the most important first.`
    : `${used} / ${max} characters`;
  note.classList.toggle("warning", used > max);
}

function openSettings() {
  $("#vocabulary").value = state.settings.vocabulary;
  $("#default-language").value = state.settings.default_language;
  markMixed($("#default-language"), null);
  $("#default-privacy").value = state.settings.default_privacy || "standard";
  fillPrivacySummary();
  const notes = state.status?.notes || {};
  $("#writer-status").textContent = notes.writer || "off (no key in .env)";
  $("#checker-status").textContent = notes.checker || "off (add GEMINI_API_KEY to .env)";
  $("#ask-status").textContent = state.status?.ask ? `${engineName(state.status.ask)} (${state.status.ask})` : "off";
  $("#prefer-server").checked = state.settings.prefer_server === "1";
  $("#groq-hour-limit").value = Number(state.settings.groq_hour_limit_sec) / 3600;
  $("#groq-day-limit").value = Number(state.settings.groq_day_limit_sec) / 3600;
  const u = state.usage;
  $("#usage-totals").textContent = u
    ? `Free now: ${hours(u.day?.available_sec ?? 0)} h${u.day?.full_at ? `, all back ${fullAgain(u.day.full_at).at}` : ""}. `
      + `Sent to Groq: ${hours(u.day_sec)} h in the last 24 hours, ${hours(u.week_sec)} h this week, ${hours(u.month_sec)} h in 30 days.`
    : "";
  updateVocabularyCount();
  renderServerStatus();
  fillRecapSettings();
  $("#settings-dialog").showModal();
}

async function testServer() {
  const button = $("#server-test");
  button.disabled = true;
  button.textContent = "Testing…";
  await loadServer(true);
  renderServerStatus();
  button.disabled = false;
  button.textContent = "Test";
}

async function saveSettings(event) {
  event.preventDefault();
  try {
    state.settings = await api("/api/settings", jsonRequest("PUT", {
      vocabulary: $("#vocabulary").value, default_language: $("#default-language").value,
      prefer_server: $("#prefer-server").checked, default_privacy: $("#default-privacy").value,
      groq_hour_limit_sec: Math.round(Number($("#groq-hour-limit").value) * 3600),
      groq_day_limit_sec: Math.round(Number($("#groq-day-limit").value) * 3600),
      recaps_on: $("#recaps-on").checked, recap_voice: $("#recap-voice").value,
      recap_speed: Number($("#recap-speed").value),
    }));
    $("#settings-dialog").close();
    flash("Settings saved");
    loadUsage();
  } catch (error) {
    alert(error.message);
  }
}

// ---- wiring

function wireEvents() {
  const zone = $("#drop-zone");
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add("dragging"); });
  }
  for (const type of ["dragleave", "drop"]) zone.addEventListener(type, () => zone.classList.remove("dragging"));
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => { e.preventDefault(); openUploadDialog(e.dataTransfer.files); });
  $("#file-input").addEventListener("change", (e) => { openUploadDialog(e.target.files); e.target.value = ""; });

  $("#drawer-button").addEventListener("click", () => setDrawer(!document.body.classList.contains("drawer-open")));
  document.addEventListener("click", (e) => {
    if (document.body.classList.contains("drawer-open") && !e.target.closest("#library, #drawer-button, dialog")) setDrawer(false);
    if (!e.target.closest(".menu")) document.querySelectorAll(".menu[open]").forEach((m) => m.removeAttribute("open"));
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setDrawer(false); });

  $("#record-button").addEventListener("click", () => openRecorder());
  $("#list-mode").addEventListener("click", (e) => {
    const mode = e.target.closest("button")?.dataset.mode;
    if (!mode || mode === listMode) return;
    listMode = mode;
    try { localStorage.setItem("list.mode", mode); } catch { /* the choice just isn't remembered */ }
    renderList();
  });
  $("#rename-all").addEventListener("click", (e) => renameRecordings("all", e.currentTarget));
  $("#rename-standard").addEventListener("click", (e) => renameRecordings("standard", e.currentTarget));
  $("#combine-all").addEventListener("click", (e) => {
    const button = e.currentTarget;
    const ready = pendingClasses().filter((c) => c.ready).length;
    if (!ready) return;
    button.dataset.busy = "1";  // polling redraws the list while it works; keep the button as it is
    combineAllClasses(ready, button).finally(() => { delete button.dataset.busy; renderListTools(); });
  });
  $("#recording-pill").addEventListener("click", () => openRecorder());
  $("#nav-all").addEventListener("click", () => setFilter("all"));
  $("#nav-starred").addEventListener("click", () => setFilter("starred"));
  $("#nav-nosubject").addEventListener("click", () => setFilter("nosubject"));
  $("#nav-trash").addEventListener("click", () => setFilter("trash"));
  $("#nav-progress").addEventListener("click", openProgress);
  $("#delete-selected").addEventListener("click", deleteSelected);
  $("#restore-selected").addEventListener("click", () => restoreRecordings([...state.checked]));
  $("#erase-selected").addEventListener("click", () => eraseRecordings([...state.checked]));
  $("#empty-trash").addEventListener("click", emptyTrash);
  $("#new-subject").addEventListener("click", () => openSubjectDialog());
  $("#subject-form").addEventListener("submit", saveSubject);
  $("#subject-cancel").addEventListener("click", () => $("#subject-dialog").close());
  $("#subject-delete").addEventListener("click", deleteSubject);
  $("#topic-form").addEventListener("submit", saveTopic);
  $("#topic-cancel").addEventListener("click", () => $("#topic-dialog").close());
  $("#topic-delete").addEventListener("click", deleteTopic);
  $("#move-button").addEventListener("click", moveChecked);
  $("#combine-button").addEventListener("click", combineChecked);
  $("#merge-button").addEventListener("click", mergeChecked);
  $("#clear-selection").addEventListener("click", () => { state.checked.clear(); renderList(); });

  $("#upload-form").addEventListener("submit", submitUpload);
  $("#upload-cancel").addEventListener("click", () => $("#upload-dialog").close());
  $("#backup-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    $("#backup-result").textContent = "Backing up…";
    try {
      const done = await api("/api/backup", jsonRequest("POST", { include_audio: $("#backup-audio").checked }));
      $("#backup-result").textContent = `Saved ${done.recordings} recordings (${done.size_mb} MB) to ${done.folder}`;
    } catch (error) {
      $("#backup-result").textContent = error.message;
    }
    button.disabled = false;
  });
  $("#settings-button").addEventListener("click", openSettings);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#settings-cancel").addEventListener("click", () => $("#settings-dialog").close());
  $("#vocabulary").addEventListener("input", updateVocabularyCount);
  $("#server-test").addEventListener("click", testServer);
  $("#recap-sample").addEventListener("click", playRecapSample);
  $("#recap-speed").addEventListener("input", (e) => {
    $("#recap-speed-value").textContent = `${Number(e.target.value).toFixed(2)}×`;
  });

  let searchTimer;
  $("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadRecordings().then(schedulePoll), 250);
  });
}

async function init() {
  wireEvents();
  renderDetail();
  state.status = await api("/api/status");
  applyLanguages();
  $("#key-banner").hidden = state.status.api_key_configured;
  state.settings = await api("/api/settings");
  await loadNav();
  await loadRecordings();
  await Promise.all([loadUsage(), loadServer()]);
  setInterval(loadUsage, 60_000);
  schedulePoll();
  window.appReady = true;
  window.showWelcome?.();  // Pip's welcome and class plan (welcome.js)
}

init().catch((error) => {
  document.body.prepend(el("p", { class: "error banner" }, `Could not reach the app: ${error.message}`));
});

// Tab lists: Left and Right move between the tabs, Home and End jump to the first and last, and focus stays on the
// tab you land on (every tab list redraws its screen when a tab is chosen, which would otherwise drop focus).
document.addEventListener("keydown", (event) => {
  const tab = event.target.closest?.('[role="tab"]');
  if (!tab || event.altKey || event.ctrlKey || event.metaKey || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const list = tab.closest('[role="tablist"]');
  const tabs = list ? [...list.querySelectorAll('[role="tab"]')] : [];
  if (tabs.length < 2) return;
  const here = tabs.indexOf(tab);
  const step = event.key === "ArrowRight" ? 1 : -1;
  const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : tabs[(here + step + tabs.length) % tabs.length];
  event.preventDefault();
  const label = list.getAttribute("aria-label");
  next.click();
  setTimeout(() => {
    const again = [...document.querySelectorAll('[role="tablist"]')].find((x) => x.getAttribute("aria-label") === label);
    (again?.querySelector('[role="tab"][aria-selected="true"]') || next).focus();
  }, 0);
});
