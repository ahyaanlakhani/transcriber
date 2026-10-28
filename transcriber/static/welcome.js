"use strict";
// Pip, the app's mascot. When the app opens (or reloads) Pip shows your class plan: this week's classes, whether each
// was recorded, a Record now button, and a way in. While the app stays open Pip also nudges you when a class is
// about to start (or has started) and nothing is recording.

const PIP_SVG = `<svg class="pip" viewBox="0 0 120 124" focusable="false">
  <g class="pip-waves"><path d="M17 42a20 20 0 0 0 0 26"/><path d="M8 35a31 31 0 0 0 0 40"/><path d="M103 42a20 20 0 0 1 0 26"/><path d="M112 35a31 31 0 0 1 0 40"/></g>
  <g class="pip-bob">
    <path class="pip-stand" d="M27 64c0 21 14 33 33 33s33-12 33-33"/>
    <path class="pip-stand" d="M60 97v12M43 111h34"/>
    <rect class="pip-body" x="35" y="8" width="50" height="78" rx="25"/>
    <path class="pip-grille" d="M52 79h16M54 83h12"/>
    <ellipse class="pip-face" cx="60" cy="48" rx="21" ry="24"/>
    <g class="pip-eyes"><ellipse cx="51" cy="44" rx="4.6" ry="6"/><ellipse cx="69" cy="44" rx="4.6" ry="6"/>
      <circle class="pip-glint" cx="52.4" cy="41.8" r="1.6"/><circle class="pip-glint" cx="70.4" cy="41.8" r="1.6"/></g>
    <circle class="pip-cheek" cx="44" cy="55" r="3.6"/><circle class="pip-cheek" cx="76" cy="55" r="3.6"/>
    <path class="pip-smile" d="M53 56q7 7 14 0"/>
    <ellipse class="pip-oh" cx="60" cy="59" rx="3.4" ry="4.4"/>
  </g></svg>`;

const LONG_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const pip = { dialog: null, nudge: null, timer: null };

function pipElement(mood) {
  const holder = el("span", { class: "pip-holder", "aria-hidden": "true" });
  holder.innerHTML = PIP_SVG;
  holder.firstElementChild.dataset.mood = mood;
  return holder;
}

// ---- dates, in your time zone (the calendar's setting)

const pipZone = () => state.settings?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone;
const pipIso = (date, tz) => date.toLocaleDateString("en-CA", { timeZone: tz });
function pipShift(iso, days) {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
const pipTime = (iso, tz) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: tz });
const pipDay = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

function pipFetch(from = -7, to = 7) {
  const today = pipIso(new Date(), pipZone());
  return api(`/api/calendar?start=${pipShift(today, from)}&end=${pipShift(today, to)}&files=false`);
}

// ---- what Pip says (from your timetable and what's in the app)

function pipMessage(data, now = Date.now()) {
  const tz = data.time_zone;
  const today = pipIso(new Date(now), tz);
  const classes = [...data.classes].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
  if (!classes.length) {
    return { mood: "calm", text: "Add your timetable and I'll remind you to record each class. Open a subject and tick the days it meets." };
  }
  const todays = classes.filter((c) => c.date === today);
  const live = todays.find((c) => c.status === "today" && now >= Date.parse(c.start) - 15 * 60_000 && now <= Date.parse(c.end));
  if (live) {
    const began = now >= Date.parse(live.start);
    return { mood: "nudge", live, text: `${live.subject} ${began ? "started at" : "starts at"} ${pipTime(live.start, tz)} and nothing is recording. Tap Record now so none of it is lost!` };
  }
  const over = todays.find((c) => c.status === "today" && now > Date.parse(c.end));
  if (over) return { mood: "calm", text: `I didn't hear ${over.subject} today. If you recorded it on your phone, send it to the app.` };
  const soon = todays.find((c) => c.status === "today");
  if (soon) return { mood: "happy", text: `${soon.subject} is at ${pipTime(soon.start, tz)} today. I'll remind you when it's time.` };
  if (todays.length && todays.every((c) => c.status === "cancelled")) return { mood: "calm", text: `No ${todays[0].subject} today. Enjoy the break!` };
  if (todays.length) return { mood: "happy", text: "Today's class is recorded. Nice work!" };
  const missed = classes.filter((c) => c.status === "missing").pop();
  if (missed) return { mood: "calm", text: `I don't have ${missed.subject} from ${pipDay(missed.date)}. If it's on your phone, send it to the app.` };
  const next = classes.find((c) => c.status === "upcoming");
  if (next) return { mood: "happy", text: `No class today. Next up: ${next.subject}, ${pipDay(next.date)} at ${pipTime(next.start, tz)}.` };
  return { mood: "calm", text: "Nothing coming up. Enjoy the break!" };
}

function pipGreeting() {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

// ---- the welcome: a calendar of this week and a way into the app

function weekPlan(data) {
  const tz = data.time_zone;
  const today = pipIso(new Date(), tz);
  const offset = (new Date(`${today}T12:00:00`).getDay() + 6) % 7;  // Monday first
  const monday = pipShift(today, -offset);
  const days = Array.from({ length: 7 }, (_, i) => pipShift(monday, i));
  return el("ol", { class: "pip-week", "aria-label": "This week's classes" }, days.map((iso) => {
    const classes = data.classes.filter((c) => c.date === iso);
    const label = `${pipDay(iso)}: ${classes.map((c) => `${c.subject}, ${STATUS_TEXT[c.status] || c.status}`).join("; ") || "no class"}`;
    return el("li", { class: `pip-day${iso === today ? " today" : ""}`, "aria-label": label },
      el("span", { class: "pip-dow", "aria-hidden": "true" }, new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" })),
      el("span", { class: "pip-num", "aria-hidden": "true" }, Number(iso.slice(8))),
      classes.map((c) => el("span", { class: `pip-chip ${c.status}`, "aria-hidden": "true", title: `${c.subject}: ${STATUS_TEXT[c.status] || c.status}` },
        el("span", { class: "dot" }), pipTime(c.start, tz))));
  }));
}

function routineLines() {
  return (state.subjects || []).filter((s) => s.schedule?.days?.length).map((s) =>
    el("li", {}, el("strong", {}, s.name), ` · every ${s.schedule.days.map((d) => LONG_WEEKDAYS[d]).join(" and ")} at ${s.schedule.start}`));
}

function closeWelcome() {
  pip.dialog?.close();
}

async function showWelcome() {
  if (pip.dialog) return;
  let data = null;
  try {
    data = await pipFetch();
  } catch { /* the greeting still shows without the plan */ }
  const say = data ? pipMessage(data) : { mood: "happy", text: "Welcome back!" };
  const routine = routineLines();
  const go = el("button", { type: "button", class: "primary", onclick: closeWelcome }, "Continue to the app");
  const dialog = el("dialog", { id: "welcome-dialog", class: "welcome", "aria-labelledby": "welcome-title" },
    el("div", { class: "welcome-head" }, pipElement(say.mood),
      el("div", { class: "welcome-hello" }, el("h2", { id: "welcome-title" }, `${pipGreeting()}!`), el("p", { class: "pip-bubble" }, say.text))),
    data ? el("section", { class: "welcome-plan" },
      el("h3", {}, "This week"),
      weekPlan(data),
      el("div", { class: "pip-legend" }, [...["recorded", "laptop", "missing", "upcoming"], ...(data.classes.some((c) => c.status === "cancelled") ? ["cancelled"] : [])].map((s) =>
        el("span", { class: `pip-chip ${s}` }, el("span", { class: "dot", "aria-hidden": "true" }), STATUS_TEXT[s]))),
      routine.length ? el("ul", { class: "pip-routine" }, routine) : null) : null,
    el("menu", {},
      el("button", { type: "button", class: "link", onclick: () => { closeWelcome(); openCalendar(); } }, "Open the full calendar"),
      el("span", { class: "spacer" }),
      el("button", { type: "button", class: "record-now", onclick: () => { closeWelcome(); openRecorder(); } },
        el("span", { class: "rec-dot", "aria-hidden": "true" }), "Record now"),
      go),
    el("p", { class: "pip-hint" }, "Press Enter to continue"));
  dialog.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.target.closest("button")) { e.preventDefault(); closeWelcome(); }
  });
  dialog.addEventListener("close", () => { dialog.remove(); pip.dialog = null; startNudges(); });
  document.body.append(dialog);
  pip.dialog = dialog;
  dialog.showModal();
  go.focus();
}

// ---- a nudge while the app is open: class is starting and nothing is recording

function nudgeKey(c) { return `pip.nudged.${c.subject_id}.${c.date}`; }
function nudged(c) { try { return sessionStorage.getItem(nudgeKey(c)) === "1"; } catch { return false; } }

function hideNudge(c) {
  if (c) { try { sessionStorage.setItem(nudgeKey(c), "1"); } catch { /* it just may come back */ } }
  pip.nudge?.remove();
  pip.nudge = null;
}

function showNudge(say) {
  const c = say.live;
  pip.nudge = el("aside", { class: "pip-nudge", role: "status" }, pipElement("nudge"),
    el("div", { class: "pip-nudge-text" }, el("strong", {}, `${c.subject} time`), el("p", {}, say.text),
      el("div", { class: "pip-nudge-actions" },
        el("button", { type: "button", class: "record-now", onclick: () => { hideNudge(c); openRecorder(); } },
          el("span", { class: "rec-dot", "aria-hidden": "true" }), "Record now"),
        el("button", { type: "button", class: "quiet", onclick: () => hideNudge(c) }, "Not now"))));
  document.body.append(pip.nudge);
}

async function checkNudge() {
  const busy = typeof recording === "function" && recording();
  if (busy) { hideNudge(); return; }
  if (document.querySelector("dialog[open]")) return;
  let data;
  try {
    data = await pipFetch(0, 0);
  } catch {
    return;
  }
  const say = pipMessage(data);
  if (!say.live || nudged(say.live)) { if (pip.nudge) hideNudge(); return; }
  if (!pip.nudge) showNudge(say);
}

function startNudges() {
  if (pip.timer) return;
  checkNudge();
  pip.timer = setInterval(checkNudge, 60_000);
}

window.showWelcome = showWelcome;
if (window.appReady) showWelcome();
