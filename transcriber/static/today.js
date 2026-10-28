"use strict";
// Today: your day on one page. Today's classes, what needs attention, what you still owe from your notes, what the
// lecturer stressed lately, and what Pip is keeping in mind. All of it is worked out on this laptop from your own notes
// and calendar; no AI model is involved, and the follow-ups say how sure the notes are.

const todayView = { data: null, error: null, all: null };

const CLASS_STATUS = { recorded: "Recorded", today: "Not recorded yet", upcoming: "Coming up", missing: "Missed",
  laptop: "On the laptop, not added", cancelled: "Cancelled" };

async function openToday() {
  state.view = "today";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  renderDetail();
  await loadToday();
}

async function loadToday() {
  try {
    todayView.data = await api("/api/briefing");
    todayView.error = null;
    if (todayView.all) todayView.all = (await api("/api/follow-ups")).items;
  } catch (error) {
    todayView.error = error.message;
  }
  if (state.view === "today") renderDetail();
}

async function markFollowUp(item, next) {
  try {
    await api(`/api/follow-ups/${item.key}/${next}`, { method: "POST" });
  } catch (error) {
    todayView.error = error.message;
  }
  await loadToday();
}

function renderToday() {
  const data = todayView.data;
  const head = el("div", { class: "brain-head" }, el("div", {},
    el("h2", {}, "Today"),
    el("p", { class: "muted" }, data ? `${data.weekday}, ${new Date(`${data.date}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "long" })}` : "")));
  if (!data) {
    $("#detail").replaceChildren(el("div", { class: "sheet-wrap brain" }, head,
      el("div", { class: "sheet brain-sheet" }, el("p", { class: "muted" }, todayView.error || "Getting your day together…"))));
    return;
  }
  const clock = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: pipZone() });
  const open = (id, title) => el("button", { type: "button", class: "link", onclick: () => selectRecording(id) }, title);
  const blocks = [todayView.error ? el("p", { class: "sheet-note", role: "alert" }, todayView.error) : null];

  blocks.push(todayBlock("Classes",
    data.classes.length ? el("ul", { class: "today-list" }, data.classes.map((c) => el("li", {},
      el("strong", {}, c.subject), el("span", { class: "muted" }, ` ${clock(c.start)}–${clock(c.end)}`),
      el("span", { class: `tag today-${c.status}` }, CLASS_STATUS[c.status] || c.status),
      c.status === "today" || c.status === "missing" ? el("button", { type: "button", class: "quiet", onclick: () => openRecorder() }, "Record now") : null,
      ...c.recording_ids.map((id) => open(id, "Open"))))) : el("p", { class: "muted" }, "No classes today.")));

  if (data.needs_attention.length) blocks.push(todayBlock("Needs a look",
    el("ul", { class: "today-list" }, data.needs_attention.map((r) => el("li", {}, open(r.id, r.title), el("span", { class: "muted" }, ` · ${r.why}`))))));

  if (data.reminders.length) blocks.push(todayBlock("Reminders today",
    el("ul", { class: "today-list" }, data.reminders.map((r) => el("li", {}, el("strong", {}, r.text),
      el("span", { class: "muted" }, ` · ${clock(r.at)}`))))));

  const follow = data.follow_ups;
  const list = todayView.all || follow.top;
  blocks.push(todayBlock(`What's still open${follow.total ? ` (${follow.total}${follow.mine ? `, ${follow.mine} yours` : ""})` : ""}`,
    list.length ? [el("ul", { class: "today-list follow" }, list.map(followUpRow)),
      follow.total > follow.top.length || todayView.all ? el("button", { type: "button", class: "link", onclick: async () => {
        todayView.all = todayView.all ? null : (await api("/api/follow-ups")).items;
        renderDetail();
      } }, todayView.all ? "Show fewer" : `Show all ${follow.total}`) : null]
      : el("p", { class: "muted" }, "Nothing open from your notes in the last six weeks."),
    "From action items and questions in your notes. They're written by an AI from the recording, so check the ones marked uncertain."));

  if (data.revise.length) blocks.push(todayBlock("Worth revising",
    el("ul", { class: "today-list" }, data.revise.map((r) => el("li", {},
      el("span", {}, r.text), el("small", { class: "muted" }, ` · ${r.heading ? `${r.heading}, ` : ""}`), open(r.recording_id, r.recording)))),
    "What your lecturers stressed in the last two weeks."));

  if (data.remembered.length) blocks.push(todayBlock("Pip is keeping in mind",
    el("ul", { class: "today-list" }, data.remembered.map((m) => el("li", {}, m.text)))));

  if (data.notes_ready.length) blocks.push(todayBlock("New notes since yesterday",
    el("ul", { class: "today-list" }, data.notes_ready.map((n) => el("li", {}, n.recording_id ? open(n.recording_id, n.title) : n.title)))));

  $("#detail").replaceChildren(el("div", { class: "sheet-wrap brain" }, head, el("div", { class: "sheet brain-sheet today" }, blocks)));
}

function todayBlock(title, body, note = null) {
  return el("section", { class: "today-block" }, el("h3", {}, title), ...[body].flat(), note ? el("p", { class: "muted today-note" }, note) : null);
}

function followUpRow(item) {
  const clockAt = item.start != null ? ` at ${formatClock(item.start, item.start >= 3600)}` : "";
  const sure = item.certainty === "not checked" ? "AI-written, not checked" : item.certainty;
  return el("li", { class: item.mine ? "mine" : "" },
    el("label", { class: "today-check" },
      el("input", { type: "checkbox", "aria-label": `Done: ${item.text}`, onchange: () => markFollowUp(item, "done") }),
      el("span", {}, el("strong", {}, item.text),
        el("small", { class: "muted" }, ` · ${[item.owner ? `for ${item.owner}` : "no owner", item.due ? `by ${item.due}` : null, item.kind].filter(Boolean).join(" · ")}`))),
    el("small", { class: "muted today-source" }, el("button", { type: "button", class: "link", onclick: () => selectRecording(item.recording_id) }, `${item.recording}${clockAt}`),
      ` · ${sure}`),
    el("button", { type: "button", class: "bell-dismiss pip-cancel", title: "Not relevant", "aria-label": `Dismiss: ${item.text}`,
      onclick: () => markFollowUp(item, "dismissed") }, icon("close")));
}

document.getElementById("nav-today")?.addEventListener("click", () => openToday());
