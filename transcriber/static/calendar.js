"use strict";
// Calendar: each subject's class days marked recorded / only on the laptop / missing, with your recordings and
// phone files still on the laptop (found by their own date stamps).

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STATUS_TEXT = { recorded: "Recorded", laptop: "On the laptop, not added", missing: "Not recorded", upcoming: "Coming up",
  cancelled: "No class",
  today: "Today" };
const cal = { month: null, data: null, day: null, loading: false };

function monthStart(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

async function openCalendar() {
  state.view = "calendar";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  cal.month = cal.month || monthStart(new Date());
  await loadCalendar();
}

async function loadCalendar() {
  const first = cal.month;
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0);
  cal.loading = true;
  renderDetail();
  try {
    cal.data = await api(`/api/calendar?start=${isoDay(first)}&end=${isoDay(last)}`);
  } catch (error) {
    alert(error.message);
  }
  try {
    cal.sessions = (await api("/api/class-sessions")).sessions;
  } catch {
    cal.sessions = [];
  }
  cal.loading = false;
  renderDetail();
}

function shiftMonth(by) {
  cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() + by, 1);
  cal.day = null;
  loadCalendar();
}

const shortTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit",
  timeZone: cal.data?.time_zone });
const longDay = (day) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

function renderCalendar() {
  const desk = $("#detail");
  const data = cal.data;
  const title = cal.month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const head = el("div", { class: "cal-head" },
    el("h2", {}, title),
    el("div", { class: "cal-nav" },
      el("button", { type: "button", class: "quiet", "aria-label": "Previous month", onclick: () => shiftMonth(-1) }, "‹"),
      el("button", { type: "button", class: "quiet", onclick: () => { cal.month = monthStart(new Date()); cal.day = null; loadCalendar(); } }, "Today"),
      el("button", { type: "button", class: "quiet", "aria-label": "Next month", onclick: () => shiftMonth(1) }, "›")));
  if (!data) {
    desk.replaceChildren(el("div", { class: "sheet-wrap" }, el("div", { class: "sheet cal-sheet" }, head,
      el("p", { class: "muted" }, "Checking your recordings and the laptop's folders…"))));
    return;
  }
  const classesByDay = {};
  for (const c of data.classes) (classesByDay[c.date] ||= []).push(c);
  const first = cal.month;
  const lead = (first.getDay() + 6) % 7;  // Monday first
  const daysInMonth = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  const todayIso = isoDay(new Date());
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(el("div", { class: "cal-cell blank", "aria-hidden": "true" }));
  for (let n = 1; n <= daysInMonth; n++) {
    const day = isoDay(new Date(first.getFullYear(), first.getMonth(), n));
    const info = data.days[day] || { recordings: [], files: [] };
    const classes = classesByDay[day] || [];
    const loose = info.files.filter((f) => !classes.some((c) => c.files.some((x) => x.path === f.path)));
    const busy = classes.length || info.recordings.length || loose.length;
    cells.push(el("button", { type: "button", class: `cal-cell${day === todayIso ? " today" : ""}${cal.day === day ? " chosen" : ""}${busy ? "" : " empty"}`,
      onclick: () => { cal.day = cal.day === day ? null : day; renderCalendar(); }, "aria-label": `${longDay(day)}: ${
        classes.map((c) => `${c.subject} ${STATUS_TEXT[c.status]}`).join(", ") || `${info.recordings.length} recordings`}` },
      el("span", { class: "cal-date" }, n),
      classes.map((c) => el("span", { class: `cal-class ${c.status}` }, el("span", { class: "dot", "aria-hidden": "true" }), c.subject)),
      info.recordings.length ? el("span", { class: "cal-count", title: "Recordings in the app made on this day" },
        `${info.recordings.length} in the app`) : null,
      loose.length ? el("span", { class: "cal-count laptop", title: "Phone recordings found in your laptop folders that you haven't added yet" },
        `${loose.length} not added yet`) : null));
  }
  desk.replaceChildren(el("div", { class: "sheet-wrap" }, el("div", { class: "sheet cal-sheet" },
    head,
    calendarSummary(data),
    classJoinCallout(),
    el("div", { class: "cal-grid", role: "grid" },
      WEEKDAYS.map((w) => el("span", { class: "cal-weekday", role: "columnheader" }, w)), cells),
    el("div", { class: "cal-legend" }, [...["recorded", "laptop", "missing", "upcoming"], ...(data.classes.some((c) => c.status === "cancelled") ? ["cancelled"] : [])].map((s) =>
      el("span", { class: `cal-class ${s}` }, el("span", { class: "dot", "aria-hidden": "true" }), STATUS_TEXT[s]))),
    el("p", { class: "cal-explain" }, el("strong", {}, "N in the app"), ": recordings you made that day (lecture parts count separately). ",
      el("strong", { class: "laptop-text" }, "N not added yet"), ": phone recordings found in your laptop folders that aren't in the app. Click the day to add them."),
    cal.day ? dayPanel(cal.day, data, classesByDay[cal.day] || []) : null,
    calendarFooter(data))));
}

// classes the phone saved in several parts: join them all at once (the originals stay, and can be shown in the list)
function classJoinCallout() {
  const waiting = (cal.sessions || []).filter((s) => !s.combined);
  if (!waiting.length) return null;
  const ready = waiting.filter((s) => s.ready);
  const parts = waiting.reduce((sum, s) => sum + s.count, 0);
  return el("div", { class: "cal-callout" },
    el("p", {}, `${waiting.length} ${waiting.length === 1 ? "class was" : "classes were"} recorded in parts (${parts} recordings). `
      + "Join each into one session with one set of notes. Your originals are kept, and “Show class parts” in the list brings them back."),
    ready.length ? el("button", { type: "button", class: "primary", onclick: (e) => combineAllClasses(ready.length, e.currentTarget) },
      ready.length === 1 ? "Combine this class" : `Combine all ${ready.length} classes`) : null,
    ready.length < waiting.length ? el("span", { class: "muted" }, ` ${waiting.length - ready.length} still transcribing.`) : null);
}

async function combineAllClasses(count, button) {
  if (!confirm(`Join ${count} ${count === 1 ? "class" : "classes"} into one session each?\n\n`
    + "Each gets new notes written, which uses some of your Groq allowance. Your originals are kept.")) return;
  button.disabled = true;
  button.textContent = "Combining…";
  try {
    const result = await api("/api/recordings/combine-classes", { method: "POST" });
    flash(`Combined ${result.combined.length} ${result.combined.length === 1 ? "class" : "classes"}.`
      + (result.skipped.length ? ` ${result.skipped.length} skipped.` : ""));
    await loadNav();
    await loadRecordings();
    if (state.view === "calendar") await loadCalendar();
  } catch (error) {
    alert(error.message);
    button.disabled = false;
    button.textContent = "Try again";
  }
}

function calendarSummary(data) {
  if (!data.subjects.length) {
    const first = state.subjects[0];
    return el("div", { class: "cal-callout" },
      el("p", {}, "Add your timetable to see which classes you recorded: edit a subject and tick the days it meets."),
      first ? el("button", { type: "button", class: "primary", onclick: () => openSubjectDialog(first) }, `Set the timetable for ${first.name}`) : null);
  }
  return el("div", { class: "cal-summary" }, data.subjects.map((s) => {
    const subject = state.subjects.find((x) => x.id === s.id);
    const when = subject?.schedule ? `${subject.schedule.days.map((d) => WEEKDAYS[d]).join(", ")} at ${subject.schedule.start}` : "";
    return el("div", { class: "cal-subject" },
      el("div", {}, el("strong", {}, s.name), el("span", { class: "muted" }, ` · ${when}`),
        el("button", { type: "button", class: "link", onclick: () => openSubjectDialog(subject) }, "edit timetable")),
      el("p", {}, s.past ? `${s.recorded} of ${s.past} classes this month recorded` : "No classes yet this month",
        s.laptop ? el("span", { class: "laptop-text" }, ` · ${s.laptop} only on the laptop`) : null,
        s.missing ? el("span", { class: "missing-text" }, ` · missing ${s.missing_dates.map(longDay).join(", ")}`) : null));
  }));
}

function dayPanel(day, data, classes) {
  const info = data.days[day] || { recordings: [], files: [] };
  const fileRow = (file, subjectId) => el("li", {},
    el("button", { type: "button", class: "link", title: "Play it in the laptop's audio player; nothing is uploaded",
      onclick: () => openLaptopFile(file, false) }, file.name),
    el("span", { class: "muted" }, ` · ${shortTime(file.recorded_at)} · ${Math.round(file.duration_sec / 60)} min`),
    el("button", { type: "button", class: "quiet", title: "Open its folder with this file selected",
      onclick: () => openLaptopFile(file, true) }, "Show in folder"),
    el("button", { type: "button", class: "quiet", onclick: (e) => addLaptopFile(file, subjectId, e.currentTarget) }, icon("upload"), "Add to the app"));
  const recRow = (r) => el("li", {}, el("button", { type: "button", class: "link", onclick: () => selectRecording(r.id) }, r.title), r.duplicate ? dupTag(r.duplicate) : null,
    el("span", { class: "muted" }, ` · ${shortTime(r.start)}${r.minutes ? ` · ${r.minutes} min` : ""}${r.subject ? ` · ${r.subject}` : ""}`));
  const inClass = new Set(classes.flatMap((c) => c.files.map((f) => f.path)));
  return el("section", { class: "cal-day" },
    el("h3", {}, longDay(day)),
    classes.map((c) => el("div", { class: `cal-day-class ${c.status}` },
      el("p", {}, el("strong", {}, `${c.subject} ${shortTime(c.start)}–${shortTime(c.end)}`), ` · ${STATUS_TEXT[c.status]}`),
      c.unfiled_ids.length ? el("p", {}, "A recording at class time isn't filed under this subject. ",
        el("button", { type: "button", class: "quiet", onclick: () => fileUnder(c.unfiled_ids, c.subject) }, `File under ${c.subject}`)) : null,
      c.files.length ? el("ul", {}, c.files.map((f) => fileRow(f, c.subject_id))) : null,
      c.status === "missing" ? el("p", { class: "muted" }, "Nothing in the app or in the laptop folders from this class. If it's still on your phone, send it with Send to Transcriber.") : null)),
    info.recordings.length ? [el("h4", {}, "In the app"), el("ul", {}, info.recordings.map(recRow))] : null,
    info.files.some((f) => !inClass.has(f.path)) ? [el("h4", {}, "On the laptop, not in the app yet"),
      el("ul", {}, info.files.filter((f) => !inClass.has(f.path)).map((f) => fileRow(f, null)))] : null,
    !classes.length && !info.recordings.length && !info.files.length ? el("p", { class: "muted" }, "Nothing on this day.") : null,
    el("p", { class: "cal-elsewhere" }, "Recorded somewhere else? ",
      el("button", { type: "button", class: "link", onclick: chooseRecording }, "Choose a recording…"),
      " from any folder, and it's added the same way."));
}

// a recording outside the calendar's folders: pick it the same way as any upload
function chooseRecording() {
  $("#file-input").click();
}

async function openLaptopFile(file, reveal) {
  try {
    await api("/api/calendar/open", jsonRequest("POST", { path: file.path, reveal }));
  } catch (error) {
    alert(error.message);
  }
}

function calendarFooter(data) {
  return el("p", { class: "cal-footer muted" },
    `Checking ${data.folders.join(", ")} for phone recordings · times in ${data.time_zone} `,
    el("button", { type: "button", class: "link", onclick: changeCalendarFolders }, "change folders"), " · ",
    el("button", { type: "button", class: "link", onclick: changeTimeZone }, "change time zone"));
}

async function addLaptopFile(file, subjectId, button) {
  button.disabled = true;
  try {
    await api("/api/calendar/import", jsonRequest("POST", { path: file.path, subject_id: subjectId }));
    flash(`Added “${file.name}”. It's being transcribed.`);
    await loadRecordings();
    await loadCalendar();
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
}

async function fileUnder(ids, subject) {
  try {
    await api("/api/recordings/move", jsonRequest("POST", { ids, subject }));
    await loadNav();
    await loadRecordings();
    await loadCalendar();
  } catch (error) {
    alert(error.message);
  }
}

async function changeCalendarFolders() {
  const current = cal.data?.folders.join("; ") || "";
  const answer = prompt("Folders to check for phone recordings (separate several with ;):", current);
  if (answer === null) return;
  try {
    await api("/api/settings", jsonRequest("PUT", { scan_folders: answer.split(";").map((s) => s.trim()).filter(Boolean).join("\n") }));
    await loadCalendar();
  } catch (error) {
    alert(error.message);
  }
}

async function changeTimeZone() {
  const answer = prompt("Your time zone (for class times and days), e.g. Europe/London:", cal.data?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  if (!answer) return;
  try {
    await api("/api/settings", jsonRequest("PUT", { time_zone: answer.trim() }));
    await loadCalendar();
  } catch (error) {
    alert(error.message);
  }
}

// ---- the timetable in the subject dialog

function fillTimetable(subject) {
  const schedule = subject?.schedule;
  for (const box of document.querySelectorAll("#subject-days input")) box.checked = Boolean(schedule?.days.includes(Number(box.value)));
  $("#subject-start").value = schedule?.start || "10:00";
  $("#subject-minutes").value = schedule?.minutes || 60;
  $("#subject-from").value = schedule?.from || "";
  $("#subject-until").value = schedule?.until || "";
}

function timetableFromDialog() {
  const days = [...document.querySelectorAll("#subject-days input:checked")].map((b) => Number(b.value));
  return { days, start: $("#subject-start").value || "10:00", minutes: Number($("#subject-minutes").value) || 60,
    from: $("#subject-from").value || null, until: $("#subject-until").value || null };
}

document.getElementById("nav-calendar")?.addEventListener("click", openCalendar);
