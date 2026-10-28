"use strict";
// Learn: flashcards from your own notes on a spaced schedule, and teach-back (explain an idea in your own words and
// see what your lectures say you got right or left out). Cards come from definitions and from what the lecturer
// stressed; each one links to the recording and moment it came from.

const learnView = { tab: "review", summary: null, card: null, shown: false, error: null, loading: false,
  concepts: null, teach: { name: "", text: "", busy: false, result: null, error: null } };

// the card is rebuilt on every step; put keyboard focus where the person will want to be next
function redrawLearn(focusSelector) {
  renderDetail();
  const target = focusSelector && document.querySelector(focusSelector);
  if (target) { if (!target.matches("button, a, input")) target.tabIndex = -1; target.focus({ preventScroll: true }); }
}

async function openLearn() {
  state.view = "learn";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  renderDetail();
  await loadLearnCard();
}

async function loadLearnCard() {
  try {
    const data = await api("/api/learn/next");
    learnView.card = data.card;
    learnView.summary = data.summary;
    learnView.shown = false;
    learnView.error = null;
  } catch (error) {
    learnView.error = error.message;
  }
  if (state.view === "learn") renderDetail();
}

async function rateLearnCard(rating) {
  const card = learnView.card;
  if (!card) return;
  try {
    const data = await api(`/api/learn/cards/${card.id}/review`, jsonRequest("POST", { rating }));
    learnView.card = data.next;
    learnView.summary = data.summary;
    learnView.shown = false;
    learnView.error = null;
  } catch (error) {
    learnView.error = error.message;
  }
  redrawLearn("#learn-show");
}

async function dropLearnCard() {
  const card = learnView.card;
  if (!card) return;
  try {
    const data = await api(`/api/learn/cards/${card.id}/suspend`, { method: "POST" });
    learnView.card = data.next;
    learnView.summary = data.summary;
    learnView.shown = false;
  } catch (error) {
    learnView.error = error.message;
  }
  redrawLearn("#learn-show");
}

function renderLearn() {
  const s = learnView.summary;
  const head = el("div", { class: "brain-head" }, el("div", {}, el("h2", {}, "Learn"),
    el("p", { class: "muted" }, s ? `${s.to_do} to do today · ${s.total} cards · ${s.learned} learned · ${s.reviewed_this_week} answers this week` : "")));
  const tabs = el("div", { class: "tabline", role: "tablist", "aria-label": "Learn views" },
    [["review", "Flashcards"], ["teach", "Teach-back"]].map(([key, label]) => el("button", { type: "button", role: "tab",
      class: `tab${learnView.tab === key ? " active" : ""}`, "aria-selected": learnView.tab === key ? "true" : "false",
      onclick: () => { learnView.tab = key; renderDetail(); if (key === "teach") loadLearnConcepts(); } }, label)));
  const body = learnView.tab === "review" ? learnReview() : learnTeach();
  $("#detail").replaceChildren(el("div", { class: "sheet-wrap brain" }, head, tabs,
    el("div", { class: "sheet brain-sheet learn" }, learnView.error ? el("p", { class: "sheet-note", role: "alert" }, learnView.error) : null, body)));
}

function learnReview() {
  const card = learnView.card;
  if (!card) {
    const s = learnView.summary;
    const next = s?.next_due ? `Next one is due ${new Date(s.next_due).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}.` : "";
    return el("div", { class: "learn-done" }, el("p", {},
      s && s.total ? "You're done for today." : "No cards yet. They're made from the definitions and the points your lecturers stressed once a lecture's notes are ready."),
    next ? el("p", { class: "muted" }, next) : null);
  }
  const at = card.start != null ? ` at ${formatClock(card.start, card.start >= 3600)}` : "";
  const source = el("p", { class: "learn-source muted" }, "From ",
    el("button", { type: "button", class: "link", onclick: () => selectRecording(card.recording_id) }, `${card.recording}${at}`),
    ` · ${card.reps ? `seen ${card.reps} time${card.reps === 1 ? "" : "s"}` : "new"}`);
  const ratings = el("div", { class: "learn-ratings" }, [[1, "Again"], [2, "Hard"], [3, "Good"], [4, "Easy"]].map(([value, name]) =>
    el("button", { type: "button", class: `learn-rate rate-${value}`, onclick: () => rateLearnCard(value) },
      el("strong", {}, name), el("small", {}, card.preview[value]), el("kbd", {}, String(value)))));
  return el("div", { class: "learn-card" },
    el("p", { class: "learn-front" }, card.front),
    learnView.shown ? el("p", { class: "learn-back" }, card.back) : null,
    learnView.shown ? source : null,
    learnView.shown ? ratings : el("button", { type: "button", class: "primary", id: "learn-show",
      onclick: () => { learnView.shown = true; redrawLearn(".learn-back"); } }, "Show answer ", el("kbd", {}, "Space")),
    el("p", { class: "learn-left muted" }, `${card.left} left today`, " · ",
      el("button", { type: "button", class: "link", onclick: dropLearnCard }, "Never ask me this one")));
}

async function loadLearnConcepts() {
  if (learnView.concepts) return;
  try {
    learnView.concepts = (await api("/api/brain")).concepts;
  } catch { learnView.concepts = []; }
  if (state.view === "learn") renderDetail();
}

function learnTeach() {
  const t = learnView.teach;
  const list = el("datalist", { id: "learn-concepts" }, (learnView.concepts || []).map((c) => el("option", { value: c.name })));
  const name = el("input", { class: "search", list: "learn-concepts", value: t.name, placeholder: "Pick a concept (OAuth, agents…)",
    "aria-label": "Concept to explain", oninput: (e) => { t.name = e.target.value; } });
  const text = el("textarea", { rows: 6, placeholder: "Explain it in your own words, as if to a friend.", "aria-label": "Your explanation",
    oninput: (e) => { t.text = e.target.value; } }, t.text);
  const go = el("button", { type: "button", class: "primary", disabled: t.busy, onclick: checkTeachBack }, t.busy ? "Checking…" : "Check my explanation");
  const r = t.result;
  const group = (title, items, cls) => items.length ? el("div", { class: `teach-group ${cls}` }, el("h3", {}, title),
    el("ul", {}, items.map((x) => el("li", {}, x)))) : null;
  return el("div", { class: "learn-teach" }, list,
    el("p", { class: "muted" }, "Explain a concept, then see how it compares with what your own lectures said. Your notes about it and your explanation go to your notes writer, under your privacy settings."),
    name, text, go, t.error ? el("p", { class: "sheet-note", role: "alert" }, t.error) : null,
    r ? el("div", { class: "teach-result" }, el("p", { class: "teach-feedback" }, r.feedback),
      group("You got right", r.right, "right"), group("Your notes also say", r.missing, "missing"), group("Doesn't match your notes", r.wrong, "wrong")) : null);
}

async function checkTeachBack() {
  const t = learnView.teach;
  const match = (learnView.concepts || []).find((c) => c.name.toLowerCase() === t.name.trim().toLowerCase());
  t.error = null;
  if (!match) { t.error = "Pick one of your concepts from the list."; renderDetail(); return; }
  if (!t.text.trim()) { t.error = "Write your explanation first."; renderDetail(); return; }
  t.busy = true;
  renderDetail();
  try {
    t.result = await api("/api/learn/teach-back", jsonRequest("POST", { concept_id: match.id, explanation: t.text }));
  } catch (error) {
    t.error = error.message;
  }
  t.busy = false;
  renderDetail();
}

document.addEventListener("keydown", (event) => {
  if (state.view !== "learn" || learnView.tab !== "review" || !learnView.card) return;
  const target = event.target;
  if (target.closest?.("input, textarea, select")) return;
  // the keys belong to the card: with focus on the sidebar, a link or any other button they must keep their own meaning
  if (target !== document.body && !target.closest?.(".learn-card")) return;
  if (!learnView.shown && event.key === " " && !target.closest?.("button")) { event.preventDefault(); learnView.shown = true; redrawLearn(".learn-back"); return; }
  if (learnView.shown && ["1", "2", "3", "4"].includes(event.key)) rateLearnCard(Number(event.key));
});

document.getElementById("nav-learn")?.addEventListener("click", () => openLearn());
