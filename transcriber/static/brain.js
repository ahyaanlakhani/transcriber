"use strict";
// Brain: your second brain across every lecture, coffee chat and talk. Three tabs: Concepts (one page per idea, with
// every mention and a link to the exact moment), Ask (one question across everything, with numbered sources) and
// Graph (concepts, classes and people). Export to Obsidian writes the same pages as linked Markdown files.

const brain = {
  tab: "concepts", data: null, q: "", subject: "", conceptId: null, concept: null, loading: false,
  defining: new Set(), defined: new Set(),  // concepts whose definition is being / was asked for this session
  pageTab: "knowledge", sort: "important", listMode: "all", whereBy: "recording", showSources: false, aliasOpen: false,
  autoOpened: false, showHidden: false,
  ask: { draft: "", busy: false, pending: "", history: [], mode: "retrieval" },
  graph: { data: null, subject: "", since: "", loading: false, focus: null },
  exported: null,
};
const BRAIN_TABS = [["concepts", "Concepts"], ["ask", "Ask"], ["graph", "Graph"]];
const SINCE_CHOICES = [["", "All time"], ["90", "Last 3 months"], ["30", "Last 30 days"], ["7", "Last 7 days"]];

function remember(key, value) { try { localStorage.setItem(`brain.${key}`, value); } catch { /* not remembered */ } }
try {
  brain.tab = localStorage.getItem("brain.tab") || "concepts";
  brain.pageTab = localStorage.getItem("brain.page") || "knowledge";
  brain.sort = localStorage.getItem("brain.sort") || "important";
  brain.whereBy = localStorage.getItem("brain.where") || "recording";
  brain.ask.mode = localStorage.getItem("brain.askMode") || "retrieval";
} catch { /* first visit */ }
if (!BRAIN_TABS.some(([key]) => key === brain.tab)) brain.tab = "concepts";

async function openBrain(tab = null, conceptId = null) {
  state.view = "brain";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  if (tab) { brain.tab = tab; remember("tab", tab); }
  if (conceptId != null) { brain.tab = "concepts"; brain.conceptId = conceptId; brain.concept = null; }
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  renderDetail();
  await loadBrain();
  if (brain.conceptId != null) await loadConcept(brain.conceptId);
  if (brain.tab === "graph") await loadGraph();
}

const openConcept = (id) => openBrain("concepts", id);

async function loadBrain() {
  const seq = (loadBrain.seq = (loadBrain.seq || 0) + 1);  // only the newest search may fill the list
  brain.loading = true;
  const params = new URLSearchParams();
  if (brain.q) params.set("q", brain.q);
  if (brain.subject) params.set("subject_id", brain.subject);
  if (brain.showHidden) params.set("hidden", "true");
  let data;
  try {
    data = await api(`/api/brain?${params}`);
  } catch (error) {
    data = { concepts: [], error: error.message };
  }
  if (seq !== loadBrain.seq) return;
  brain.data = data;
  brain.loading = false;
  updateBrainCount();
  if (state.view === "brain") renderDetail();
}

function updateBrainCount() {
  const count = $("#brain-count");
  if (count && brain.data && !brain.q && !brain.subject) count.textContent = brain.data.concepts?.length || "";
}

async function loadConcept(id) {
  brain.conceptId = id;
  try {
    const concept = await api(`/api/brain/concepts/${id}`);
    if (brain.conceptId !== id) return;  // another concept was opened meanwhile: its answer wins
    brain.concept = concept;
  } catch (error) {
    if (brain.conceptId !== id) return;
    brain.concept = null;
    brain.conceptId = null;
    flash(error.message);
  }
  if (state.view === "brain") renderDetail();
  const c = brain.concept;  // a definition in the student's terms, written once (and again when there's more to go on)
  if (c?.definition_stale && brain.data?.writer && !brain.defining.has(c.id) && !brain.defined.has(c.id)) defineConcept(c.id);
}

async function defineConcept(id, again = false) {
  brain.defining.add(id);
  if (state.view === "brain") renderDetail();
  try {
    const page = await api(`/api/brain/concepts/${id}/definition`, { method: "POST" });
    if (brain.conceptId === id) brain.concept = page;
  } catch (error) {
    if (again) flash(error.message);  // asked for it: say why not; otherwise the notes' own line stays
  }
  brain.defining.delete(id);
  brain.defined.add(id);
  if (state.view === "brain") renderDetail();
}

// ---- the screen: the concept index on the left, the open concept in the middle, where it came up on the right, and
// one Ask box along the bottom that answers in three ways

const PAGE_TABS = [["knowledge", "Key knowledge"], ["mentions", "Mentions"], ["related", "Related"], ["people", "People"],
  ["classes", "Classes"]];
const ASK_MODES = [
  ["retrieval", "Retrieval", "What your recordings said, with sources."],
  ["synthesis", "Synthesis", "How your recordings connect and differ, with sources."],
  ["application", "Application", "What they said, then the AI's own ideas for using it, kept apart from your sources."],
];
const ANSWER_LABELS = { retrieval: "From your recordings", synthesis: "Connected across your recordings",
  application: "From your recordings, then the AI's ideas" };
const QUALITY_LABELS = { generic: "Generic heading", admin: "Course admin", concept: "Hidden by you" };
const KIND_RANK = { definition: 0, stressed: 1, fact: 2, example: 3, point: 4 };
const wideScreen = () => window.matchMedia("(min-width: 861px)").matches;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const formatShort = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const clip = (text, size) => (text.length > size ? `${text.slice(0, size - 1).trimEnd()}…` : text);

function renderBrain() {
  const data = brain.data;
  if (brain.tab === "concepts" && brain.conceptId == null && !brain.autoOpened && wideScreen() && data?.concepts?.length) {
    brain.autoOpened = true;  // on a wide screen, open the busiest concept rather than an empty page
    brain.conceptId = data.concepts[0].id;
    loadConcept(brain.conceptId);
  }
  const counts = data && !data.error ? `${plural(data.concepts.length, "concept")} from ${plural(data.recordings, "recording")}`
    + (data.checking ? ` · tidying names in ${data.checking}` : "") : "";
  const bar = el("div", { class: "brain-bar" },
    el("div", { class: "brain-switch", role: "tablist", "aria-label": "Brain views" }, BRAIN_TABS.map(([key, label]) =>
      el("button", { type: "button", role: "tab", class: `brain-switch-tab${brain.tab === key ? " active" : ""}`,
        "aria-selected": brain.tab === key ? "true" : "false", onclick: () => switchBrainTab(key) }, label))),
    el("p", { class: "brain-count muted" }, counts),
    el("div", { class: "brain-actions" },
      el("button", { type: "button", class: "quiet", title: "Build the index again from your notes. Names you merged stay merged.",
        onclick: (e) => rebuildBrain(e.currentTarget) }, icon("redo"), "Rebuild"),
      el("button", { type: "button", class: "quiet", onclick: (e) => exportVault(e.currentTarget) }, icon("download"), "Export to Obsidian")));
  const body = { concepts: conceptsView, ask: askView, graph: graphView }[brain.tab]();
  $("#detail").replaceChildren(el("div", { class: `brain brain-${brain.tab}` }, bar, vaultLine(), body));
}

async function switchBrainTab(key) {
  brain.tab = key;
  remember("tab", key);
  renderDetail();
  if (key === "graph" && !brain.graph.data) await loadGraph();
  if (key === "ask") $(".dock-input")?.focus();
}

function vaultLine() {
  const data = brain.data;
  const done = brain.exported;
  if (!data?.vault_folder || !done) return null;
  return el("p", { class: "brain-vault muted" }, `Exported ${done.files} pages to `, el("span", { class: "vault-path" }, data.vault_folder),
    " · ", el("button", { type: "button", class: "link", onclick: openVault }, "open folder"),
    " · ", el("button", { type: "button", class: "link", onclick: changeVaultFolder }, "change folder"));
}

function conceptsView() {
  const open = brain.conceptId != null;
  const main = open ? conceptPage() : el("section", { class: "concept-main concept-empty" },
    el("p", { class: "muted" }, brain.data?.concepts?.length ? "Pick a concept to see everything you heard about it." : ""));
  return el("div", { class: `brain-body${open ? " has-concept" : ""}` },
    conceptIndex(), main, open && brain.concept ? conceptRail(brain.concept) : null, askDock());
}

function askView() {
  return el("div", { class: "brain-body ask-body" }, conceptIndex(),
    el("section", { class: "concept-main" }, el("div", { class: "sheet brain-sheet" }, brainConversation())), askDock());
}

function graphView() {
  return el("div", { class: "brain-graph-view" }, el("div", { class: "sheet brain-sheet" }, brainGraph()));
}

// ---- the concept index

function subjectIdOf(name) {
  return state.subjects.find((s) => s.name === name)?.id ?? null;
}

function swatch(subjectName, large = false) {
  const id = subjectName ? subjectIdOf(subjectName) : null;
  return el("span", { class: `concept-swatch${large ? " large" : ""}`, "aria-hidden": "true",
    style: id ? `background:${dividerColor(id)}` : "" });
}

function openConceptHere(id) {
  brain.conceptId = id;
  brain.concept = null;
  brain.showSources = false;
  brain.aliasOpen = false;
  if (brain.tab !== "concepts") { brain.tab = "concepts"; remember("tab", "concepts"); }
  renderDetail();
  if (!wideScreen()) $("#detail").scrollTo({ top: 0 });
  loadConcept(id);
}

function conceptIndex() {
  const data = brain.data;
  const total = data?.concepts?.length ?? 0;
  const search = el("input", { type: "search", class: "search", value: brain.q, placeholder: "Search concepts…",
    "aria-label": "Search concepts", oninput: (e) => { brain.q = e.target.value; clearTimeout(conceptIndex.timer);
      conceptIndex.timer = setTimeout(async () => { await loadBrain(); const box = $(".concept-index input[type=search]");
        box?.focus(); box?.setSelectionRange(box.value.length, box.value.length); }, 250); } });
  const filters = el("div", { class: "index-filters", role: "tablist", "aria-label": "Which concepts" },
    [["all", `All${total && !brain.subject ? ` ${total}` : ""}`], ["recent", "Recent"], ["subject", "By subject"]].map(([key, label]) =>
      el("button", { type: "button", role: "tab", class: `index-filter${brain.listMode === key ? " active" : ""}`,
        "aria-selected": brain.listMode === key ? "true" : "false", onclick: () => {
          brain.listMode = key;
          remember("list", key);
          if (key !== "subject" && brain.subject) { brain.subject = ""; loadBrain(); } else renderDetail();
        } }, label)));
  const pick = brain.listMode === "subject" ? el("select", { "aria-label": "Only concepts from one subject",
    onchange: (e) => { brain.subject = e.target.value; loadBrain(); } },
  el("option", { value: "" }, "Every subject"),
  state.subjects.map((s) => el("option", { value: s.id, selected: String(s.id) === String(brain.subject) }, s.name))) : null;
  let list;
  if (!data) list = el("p", { class: "muted index-note" }, "Reading your notes…");
  else if (data.error) list = el("p", { class: "error index-note" }, data.error);
  else if (!data.concepts.length) {
    list = el("p", { class: "muted index-note" }, brain.q || brain.subject ? "No concepts match."
      : "Concepts show up here once your recordings have notes: key terms, lecture topics, and topics you accept.");
  } else {
    const items = brain.listMode === "recent" ? [...data.concepts].sort((a, b) => (b.last_at || "").localeCompare(a.last_at || ""))
      : data.concepts;
    const max = Math.max(...items.map((c) => c.mentions), 1);
    list = el("div", { class: "concept-entries" }, items.map((c) => el("button", { type: "button",
      class: `concept-entry${c.id === brain.conceptId ? " active" : ""}${c.hidden ? " is-hidden" : ""}`, "aria-current": c.id === brain.conceptId ? "true" : null,
      onclick: () => openConceptHere(c.id) },
    swatch(c.subjects[0]),
    el("span", { class: "concept-entry-text" },
      el("span", { class: "concept-entry-name" }, c.name),
      el("span", { class: "concept-entry-meta" }, [c.hidden ? QUALITY_LABELS[c.quality] || "Hidden by you" : c.subjects[0], brain.listMode === "recent" && c.last_at ? formatShort(c.last_at)
        : plural(c.recordings, "recording")].filter(Boolean).join(" · "))),
    el("span", { class: "concept-entry-count", title: `${plural(c.mentions, "mention")} in ${plural(c.recordings, "recording")}` },
      el("b", {}, c.mentions), el("span", { class: "meter" }, el("span", { style: `width:${Math.max(10, Math.round((c.mentions / max) * 100))}%` }))))));
  }
  const hiddenCount = data?.hidden_count || 0;
  const toggle = hiddenCount && !brain.q ? el("button", { type: "button", class: "link index-hidden",
    title: "Generic headings like “Process” and course admin like “Assignments” stay indexed and searchable, just out of this list.",
    onclick: () => { brain.showHidden = !brain.showHidden; loadBrain(); } },
  brain.showHidden ? `Hide the ${hiddenCount} generic and admin entries` : `Show ${hiddenCount} hidden (generic headings and course admin)`) : null;
  return el("aside", { class: "concept-index", "aria-label": "Concepts" },
    el("div", { class: "concept-index-head" }, el("h2", {}, "Concepts")), search, filters, pick, list, toggle);
}

// ---- one concept

function conceptItems(c) {
  return c.recordings.flatMap((g) => g.items.filter((i) => i.kind !== "topic").map((i) => ({ ...i, recording_id: g.recording_id,
    recording: g.title, subject: g.subject, person: g.person, at: g.at, long_form: g.long_form })));
}

function knowledgeGroups(c) {
  const groups = new Map();
  for (const item of conceptItems(c)) {
    const key = (item.heading || "").trim().toLowerCase() || "\u0000";
    if (!groups.has(key)) groups.set(key, { title: item.heading?.trim() || "Other mentions", items: [] });
    groups.get(key).items.push(item);
  }
  const list = [...groups.values()].map((g) => {
    g.items.sort((a, b) => (KIND_RANK[a.kind] ?? 5) - (KIND_RANK[b.kind] ?? 5) || b.at.localeCompare(a.at));
    g.best = g.items[0];
    g.sources = new Set(g.items.map((i) => i.recording_id)).size;
    g.stressed = g.items.some((i) => i.kind === "stressed");
    g.newest = g.items.reduce((m, i) => (i.at > m ? i.at : m), "");
    return g;
  });
  return brain.sort === "newest" ? list.sort((a, b) => b.newest.localeCompare(a.newest))
    : list.sort((a, b) => b.stressed - a.stressed || b.items.length - a.items.length || b.newest.localeCompare(a.newest));
}

function conceptPage() {
  const c = brain.concept;
  const back = el("button", { type: "button", class: "link brain-back only-narrow-block",
    onclick: () => { brain.conceptId = null; brain.concept = null; renderDetail(); } }, "← All concepts");
  if (!c) return el("section", { class: "concept-main" }, back, el("p", { class: "muted" }, "Opening…"));
  const first = c.classes[0];
  const crumb = el("p", { class: "concept-crumb" }, "Concepts", first ? [" › ", el("button", { type: "button", class: "link",
    onclick: () => { brain.listMode = "subject"; brain.subject = String(first.id); loadBrain(); } }, first.name)] : null);
  const meta = [c.classes.map((s) => s.name).join(", "), plural(c.mention_count, "mention"), plural(c.recordings.length, "recording"),
    c.people.length ? plural(c.people.length, "person").replace("persons", "people") : null].filter(Boolean).join(" · ");
  const head = el("header", { class: "concept-head" }, back, crumb,
    el("div", { class: "concept-title-row" }, swatch(first?.name, true), el("h2", {}, c.name),
      el("div", { class: "concept-actions" },
        el("button", { type: "button", onclick: () => renameConcept(c) }, icon("pencil"), "Edit"), conceptMenu(c))),
    el("p", { class: "concept-facts" }, meta), aliasLine(c),
    c.hidden ? el("p", { class: "concept-hidden-note" }, `Kept out of your concept list as ${c.visibility === "hidden" ? "you asked" : `a ${(QUALITY_LABELS[c.quality] || "").toLowerCase()}`}. `,
      el("button", { type: "button", class: "link", onclick: () => setConceptVisibility(c, "shown") }, "Show it in the list")) : null);
  const groups = knowledgeGroups(c);
  const counts = { knowledge: groups.length, mentions: c.mention_count, related: c.related.length, people: c.people.length,
    classes: c.classes.length };
  const tabs = el("div", { class: "tabline", role: "tablist", "aria-label": "About this concept" },
    PAGE_TABS.map(([key, label]) => el("button", { type: "button", role: "tab", class: `tab${brain.pageTab === key ? " active" : ""}`,
      "aria-selected": brain.pageTab === key ? "true" : "false", onclick: () => { brain.pageTab = key; remember("page", key); renderDetail(); } },
    label, el("span", { class: "tab-count" }, ` ${counts[key]}`))));
  const sort = brain.pageTab === "knowledge" && groups.length > 1 ? el("div", { class: "knowledge-tools" }, el("select", {
    class: "knowledge-sort", "aria-label": "Order", onchange: (e) => { brain.sort = e.target.value; remember("sort", e.target.value); renderDetail(); } },
  el("option", { value: "important", selected: brain.sort !== "newest" }, "Most important first"),
  el("option", { value: "newest", selected: brain.sort === "newest" }, "Newest first"))) : null;
  const body = { knowledge: () => knowledgeRows(c, groups), mentions: () => mentionRows(c), related: () => relatedRows(c),
    people: () => peopleRows(c), classes: () => classRows(c) }[brain.pageTab]();
  return el("section", { class: "concept-main" }, head, tabs,
    el("div", { class: "sheet brain-sheet concept-sheet" }, definitionBox(c), sort, body, conceptNote(c)));
}

function conceptMenu(c) {
  const writer = brain.data?.writer;
  const panel = el("div", { class: "menu-panel" },
    writer ? el("button", { type: "button", onclick: (e) => { e.currentTarget.closest("details").open = false; defineConcept(c.id, true); } },
      icon("redo"), "Rewrite the definition") : null,
    el("button", { type: "button", onclick: () => { brain.graph.focus = `c${c.id}`; switchBrainTab("graph"); } }, icon("brain"), "Show on the map"),
    el("button", { type: "button", onclick: (e) => { e.currentTarget.closest("details").open = false; setConceptVisibility(c, c.hidden ? "shown" : "hidden"); } },
      icon(c.hidden ? "restore" : "close"), c.hidden ? "Show in the concept list" : "Hide from the concept list"),
    c.visibility ? el("button", { type: "button", onclick: (e) => { e.currentTarget.closest("details").open = false; setConceptVisibility(c, "auto"); } },
      icon("redo"), "Let the app decide again") : null,
    el("div", { class: "menu-row" },
      el("input", { list: "concept-options", placeholder: "Same idea as…", "aria-label": "The concept this is the same as",
        onkeydown: (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); mergeConcept(c, e.target.value); } } }),
      el("button", { type: "button", class: "quiet", onclick: (e) => mergeConcept(c, e.currentTarget.previousSibling.value) }, "Merge")),
    el("datalist", { id: "concept-options" }, (brain.data?.concepts || []).filter((o) => o.id !== c.id).map((o) => el("option", { value: o.name }))));
  return el("details", { class: "menu" }, el("summary", { class: "button quiet", "aria-label": "More for this concept" }, icon("more")), panel);
}

function aliasLine(c) {
  const add = brain.aliasOpen
    ? el("input", { class: "alias-input", placeholder: "Another name for it", "aria-label": "Another name for this concept",
      onkeydown: (e) => {
        if (e.key === "Enter") { e.preventDefault(); addAlias(c, e.target.value); }
        if (e.key === "Escape") { brain.aliasOpen = false; renderDetail(); }
      } })
    : el("button", { type: "button", class: "link alias-add", onclick: () => { brain.aliasOpen = true; renderDetail(); $(".alias-input")?.focus(); } },
      icon("plus"), "Add a name");
  return el("div", { class: "concept-aliases" }, el("span", { class: "field-label" }, "Also called"),
    c.aliases.length ? c.aliases.map((a) => el("span", { class: "alias" }, a,
      el("button", { type: "button", class: "alias-remove", title: `Stop treating “${a}” as this concept`, "aria-label": `Remove the name ${a}`,
        onclick: () => removeAlias(c, a) }, icon("close")))) : el("span", { class: "muted" }, "no other names yet"), add);
}

async function setConceptVisibility(c, visibility) {
  try {
    brain.concept = await api(`/api/brain/concepts/${c.id}`, jsonRequest("PATCH", { visibility }));
    await loadBrain();
  } catch (error) {
    flash(error.message);
  }
}

async function addAlias(c, name) {
  if (!name.trim()) return;
  try {
    brain.concept = await api(`/api/brain/concepts/${c.id}/aliases`, jsonRequest("POST", { alias: name }));
    brain.aliasOpen = false;
  } catch (error) {
    flash(error.message);
  }
  renderDetail();
}

async function removeAlias(c, name) {
  try {
    brain.concept = await api(`/api/brain/concepts/${c.id}/aliases?alias=${encodeURIComponent(name)}`, { method: "DELETE" });
  } catch (error) {
    flash(error.message);
  }
  renderDetail();
}

function definitionBox(c) {
  const defining = brain.defining.has(c.id);
  const sources = conceptItems(c).sort((a, b) => (KIND_RANK[a.kind] ?? 5) - (KIND_RANK[b.kind] ?? 5)).slice(0, 14);
  const foot = el("p", { class: "definition-source" },
    c.definition_by === "model" ? "Written from your notes" : "Straight from your notes",
    brain.data?.writer ? [" · ", el("button", { type: "button", class: "link", disabled: defining, onclick: () => defineConcept(c.id, true) },
      defining ? "writing…" : c.definition_by === "model" ? "rewrite" : "write one")] : null,
    sources.length ? [" · ", el("button", { type: "button", class: "link", "aria-expanded": brain.showSources ? "true" : "false",
      onclick: () => { brain.showSources = !brain.showSources; renderDetail(); } },
    brain.showSources ? "hide sources" : `show sources (${sources.length})`)] : null);
  const list = brain.showSources ? el("ol", { class: "definition-sources" }, sources.map((i) => el("li", {},
    i.start != null ? momentButton(i.recording_id, i.start, i.long_form) : null, " ", el("span", {}, i.text),
    el("span", { class: "muted" }, ` · ${i.recording}`)))) : null;
  return el("div", { class: "summary-box concept-definition" }, cornell(
    el("span", { class: "section-label" }, "Definition"),
    el("div", {}, el("p", { class: `note-text${c.definition ? "" : " muted"}` },
      defining && !c.definition ? "Writing a definition from your notes…" : c.definition || "No definition in the notes yet."), foot, list)));
}

function knowledgeRows(c, groups) {
  if (!groups.length) return cornell([], el("p", { class: "note-text muted" }, "Nothing in the notes about this yet."));
  return groups.map((g) => {
    const best = g.best;
    const rest = g.items.slice(1);
    return cornell(
      [best.start != null ? momentButton(best.recording_id, best.start, best.long_form) : null,
        el("span", { class: "knowledge-count" }, plural(g.sources, "source"))],
      el("div", { class: "knowledge" },
        el("p", { class: "knowledge-title" }, g.stressed ? el("span", { class: "kind-mark stressed", role: "img", "aria-label": "Stressed",
          title: "The lecturer stressed it" }, "★") : null, g.title),
        el("p", { class: "note-text" }, mentionText(best, c)),
        el("p", { class: "knowledge-from" }, [best.subject || (best.person ? `with ${best.person}` : null), best.recording, formatShort(best.at)]
          .filter(Boolean).join(" · ")),
        rest.length ? el("details", { class: "knowledge-more" }, el("summary", {}, `${plural(rest.length, "more note")} on this`),
          el("ul", {}, rest.map((i) => el("li", {}, i.start != null ? momentButton(i.recording_id, i.start, i.long_form) : null, " ",
            mentionText(i, c), el("span", { class: "muted" }, ` · ${i.recording}`))))) : null),
      "note-row knowledge-row");
  });
}

function mentionRows(c) {
  const rows = [];
  for (const group of c.recordings) {
    rows.push(cornell([], el("div", { class: "concept-source" },
      el("button", { type: "button", class: "link source-title", onclick: () => selectRecording(group.recording_id) }, group.title),
      el("span", { class: "muted" }, ` · ${formatShort(group.at)}${group.subject ? ` · ${group.subject}` : group.person ? ` · with ${group.person}` : ""}`)),
    "topic-row"));
    for (const item of group.items) {
      rows.push(cornell(item.start != null ? momentButton(group.recording_id, item.start, group.long_form) : [],
        el("div", { class: "note-text" }, kindMark(item), mentionText(item, c)), "note-row"));
    }
  }
  return rows;
}

function relatedRows(c) {
  if (!c.related.length) return cornell([], el("p", { class: "note-text muted" }, "Nothing else comes up in the same recordings yet."));
  return c.related.map((r) => cornell(el("span", { class: "knowledge-count" }, `${r.together}×`),
    el("div", { class: "note-text" }, el("button", { type: "button", class: "link", onclick: () => openConceptHere(r.id) }, r.name),
      el("span", { class: "muted" }, ` · together in ${plural(r.together, "recording")}`)), "note-row"));
}

function peopleRows(c) {
  if (!c.people.length) return cornell([], el("p", { class: "note-text muted" }, "It hasn't come up in a chat with anyone yet."));
  return c.people.map((p) => {
    const chats = c.recordings.filter((g) => g.person_id === p.id);
    return cornell(el("span", { class: "knowledge-count" }, plural(chats.length, "chat")),
      el("div", { class: "note-text" }, el("button", { type: "button", class: "link", onclick: () => openPerson(p.id) }, p.name),
        el("span", { class: "muted" }, chats.length ? ` · ${chats.map((g) => g.title).join(", ")}` : "")), "note-row");
  });
}

function classRows(c) {
  if (!c.classes.length) return cornell([], el("p", { class: "note-text muted" }, "None of these recordings has a subject."));
  return c.classes.map((s) => cornell(el("span", { class: "knowledge-count" }, plural(s.count, "recording")),
    el("div", { class: "note-text" }, el("span", { class: "tab-swatch", style: `background:${dividerColor(s.id)}` }), " ",
      el("button", { type: "button", class: "link", onclick: () => setFilter("subject", s.id) }, s.name)), "note-row"));
}

function conceptNote(c) {
  const status = el("span", { class: "note-saved muted", "aria-live": "polite" });
  const box = el("textarea", { rows: 3, placeholder: "Your own note on this concept. Only you see it.", "aria-label": `Your note on ${c.name}`,
    oninput: (e) => {
      clearTimeout(conceptNote.timer);
      status.textContent = "";
      const text = e.target.value;
      conceptNote.timer = setTimeout(async () => {
        try {
          const page = await api(`/api/brain/concepts/${c.id}`, jsonRequest("PATCH", { note: text }));
          if (brain.conceptId === c.id) brain.concept.note = page.note;
          status.textContent = "Saved";
        } catch (error) {
          status.textContent = error.message;
        }
      }, 600);
    } }, c.note || "");
  return cornell(el("span", { class: "section-label" }, "My note"), el("div", { class: "my-notes concept-note" }, box, status), "concept-note-row");
}

// ---- the rail: a small map, and where it came up

function conceptRail(c) {
  const shown = brain.whereBy;
  const filters = el("div", { class: "index-filters", role: "tablist", "aria-label": "Group by" },
    [["recording", "By recording"], ["subject", "By subject"], ["person", "By person"]].map(([key, label]) =>
      el("button", { type: "button", role: "tab", class: `index-filter${shown === key ? " active" : ""}`, "aria-selected": shown === key ? "true" : "false",
        onclick: () => { brain.whereBy = key; remember("where", key); renderDetail(); } }, label)));
  return el("aside", { class: "concept-rail", "aria-label": "Around this concept" },
    el("section", { class: "rail-block" },
      el("div", { class: "rail-head" }, el("h3", {}, "Concept map"),
        el("button", { type: "button", class: "link", onclick: () => { brain.graph.focus = `c${c.id}`; switchBrainTab("graph"); } }, "Full map →")),
      egoGraph(c)),
    el("section", { class: "rail-block" }, el("h3", {}, "Where it came up"), filters, whereList(c, shown),
      c.mention_count > 4 ? el("button", { type: "button", class: "link rail-more", onclick: () => { brain.pageTab = "mentions"; renderDetail(); } },
        `All ${plural(c.mention_count, "mention")} →`) : null));
}

function egoGraph(c) {
  const near = c.related.slice(0, 8);
  if (!near.length) return el("p", { class: "muted rail-empty" }, "No related concepts yet.");
  const width = 280, height = 210, cx = width / 2, cy = height / 2;
  const svg = document.createElementNS(SVG_NS, "svg");
  const make = (name, attrs, parent = svg) => { const node = document.createElementNS(SVG_NS, name); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); parent.append(node); return node; };
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "ego-graph");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", `${c.name} and the concepts that come up with it`);
  const most = Math.max(...near.map((r) => r.together));
  const spots = near.map((r, i) => {
    const angle = -Math.PI / 2 + (i / near.length) * Math.PI * 2;
    return { r, x: cx + Math.cos(angle) * 78, y: cy + Math.sin(angle) * 70, cos: Math.cos(angle), sin: Math.sin(angle) };
  });
  for (const s of spots) make("line", { x1: cx, y1: cy, x2: s.x, y2: s.y, "stroke-width": 0.8 + (s.r.together / most) * 2 });
  for (const s of spots) {
    const node = make("g", { class: "ego-node", tabindex: 0, role: "button", "aria-label": `Open ${s.r.name}` });
    make("circle", { cx: s.x, cy: s.y, r: 4 + (s.r.together / most) * 3 }, node);
    const label = make("text", { x: s.x + (s.cos > 0.3 ? 9 : s.cos < -0.3 ? -9 : 0), y: s.y + (Math.abs(s.cos) <= 0.3 ? (s.sin < 0 ? -10 : 17) : 4),
      "text-anchor": s.cos > 0.3 ? "start" : s.cos < -0.3 ? "end" : "middle" }, node);
    label.textContent = clip(s.r.name, 20);
    make("title", {}, node).textContent = `${s.r.name} · together in ${plural(s.r.together, "recording")}`;
    node.addEventListener("click", () => openConceptHere(s.r.id));
    node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openConceptHere(s.r.id); } });
  }
  const centre = make("g", { class: "ego-center" });
  make("circle", { cx, cy, r: 9 }, centre);
  make("text", { x: cx, y: cy + 24, "text-anchor": "middle" }, centre).textContent = clip(c.name, 22);
  return svg;
}

function whereRow(group, c) {
  const item = group.items.find((i) => i.kind !== "topic") || group.items[0];
  const date = new Date(group.at);
  return el("div", { class: "where-row" },
    el("span", { class: "sheet-date" }, el("b", {}, date.getDate()), el("span", {}, date.toLocaleDateString(undefined, { month: "short" }))),
    el("div", { class: "where-text" },
      el("button", { type: "button", class: "link where-title", onclick: () => selectRecording(group.recording_id) }, group.title),
      item ? el("p", { class: "where-quote" }, `“${clip(item.text, 120)}”`) : null),
    item?.start != null ? momentButton(group.recording_id, item.start, group.long_form) : null);
}

function whereList(c, by) {
  const groups = [...c.recordings].sort((a, b) => b.at.localeCompare(a.at));
  if (by === "recording") return el("div", { class: "where-list" }, groups.slice(0, 4).map((g) => whereRow(g, c)));
  const buckets = new Map();
  for (const g of groups) {
    const name = by === "subject" ? g.subject || "No subject" : g.person || null;
    if (!name) continue;
    if (!buckets.has(name)) buckets.set(name, []);
    buckets.get(name).push(g);
  }
  if (!buckets.size) return el("p", { class: "muted rail-empty" }, by === "person" ? "None of these were chats with someone." : "No subjects yet.");
  return el("div", { class: "where-list" }, [...buckets.entries()].map(([name, list]) => el("div", { class: "where-group" },
    el("h4", {}, by === "subject" && name !== "No subject" ? swatch(name) : null, name, el("span", { class: "muted" }, ` · ${list.length}`)),
    list.slice(0, 3).map((g) => whereRow(g, c)))));
}

// a concept's name, highlighted where the note says it
function mentionText(item, concept) {
  const names = [concept.name, ...concept.aliases].filter((n) => n.length > 1).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "[\\s-]+"));
  const pattern = new RegExp(`(${names.join("|")})`, "ig");
  const parts = item.text.split(pattern);
  return parts.map((part, i) => (i % 2 ? el("mark", { class: "concept-mark" }, part) : part));
}

function momentButton(rid, seconds, longForm) {
  const time = formatClock(seconds, longForm);
  return el("button", { type: "button", class: "timestamp", title: "Open the recording and play from here",
    "aria-label": `Play from ${time}`, onclick: () => openAt(rid, seconds) }, time);
}

// open a recording and play from a moment (after its sheet and player are on the page)
async function openAt(rid, seconds) {
  await selectRecording(rid);
  const player = $("#detail audio");
  if (!player) return;
  const go = () => { player.currentTime = seconds; player.play().catch(() => {}); };
  if (player.readyState >= 1) go(); else player.addEventListener("loadedmetadata", go, { once: true });
}

async function renameConcept(c) {
  const name = prompt("Concept name (a name another concept already has merges the two):", c.name);
  if (!name || name.trim() === c.name) return;
  try {
    brain.concept = await api(`/api/brain/concepts/${c.id}`, jsonRequest("PATCH", { name }));
    brain.conceptId = brain.concept.id;
    await loadBrain();
  } catch (error) {
    alert(error.message);
  }
}

async function mergeConcept(c, name) {
  const other = (brain.data?.concepts || []).find((o) => o.name.toLowerCase() === (name || "").trim().toLowerCase());
  if (!other) { flash("Pick a concept from the list"); return; }
  if (!confirm(`Make “${c.name}” part of “${other.name}”?\n\nIts mentions move there, and “${c.name}” is kept as another name for it.`)) return;
  try {
    brain.concept = await api(`/api/brain/concepts/${c.id}/merge`, jsonRequest("POST", { into: other.id }));
    brain.conceptId = other.id;
    await loadBrain();
  } catch (error) {
    alert(error.message);
  }
}

// ---- Ask across everything: the box along the bottom, and the conversation in the Ask view

function askSuggestions(c) {
  if (c) {
    return [[`What did my lectures say about ${c.name}?`, "retrieval"],
      c.related[0] ? [`How does ${c.name} relate to ${c.related[0].name}?`, "synthesis"] : null,
      [`How could I use ${c.name} in my own work?`, "application"],
      c.recordings.some((g) => g.items.some((i) => i.kind === "stressed")) ? [`What was stressed about ${c.name}?`, "retrieval"] : null]
      .filter(Boolean);
  }
  const top = brain.data?.concepts || [];
  return [top[0] ? [`What did we say about ${top[0].name}?`, "retrieval"] : null,
    top[0] && top[1] ? [`How do ${top[0].name} and ${top[1].name} connect?`, "synthesis"] : null,
    ["What was stressed for the exam lately?", "retrieval"]].filter(Boolean);
}

function askDock() {
  const a = brain.ask;
  const c = brain.tab === "concepts" ? brain.concept : null;
  const mode = ASK_MODES.find((m) => m[0] === a.mode) || ASK_MODES[0];
  const input = el("textarea", { rows: 1, class: "dock-input", value: a.busy ? "" : a.draft, disabled: a.busy,
    placeholder: c ? `Ask about ${c.name}, or anything in your lectures, meetings and notes…` : "Ask a question about your lectures, meetings or notes…",
    "aria-label": "Ask across all your recordings",
    oninput: (e) => { a.draft = e.target.value; e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; },
    onkeydown: (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendBrainAsk(); } } });
  const engine = brain.data?.ask ? `Answered by ${engineName(brain.data.ask)}` : "No AI model is set up, so you'll get the passages that match best";
  return el("section", { class: "ask-dock", "aria-label": "Ask across all your recordings" },
    el("div", { class: "dock-head" }, el("span", { class: "dock-pip" }, pipElement(a.busy ? "thinking" : "happy")),
      el("h3", {}, "Ask across all your recordings"),
      el("div", { class: "dock-modes index-filters", role: "radiogroup", "aria-label": "How to answer" }, ASK_MODES.map(([key, label, hint]) =>
        el("button", { type: "button", role: "radio", "aria-checked": a.mode === key ? "true" : "false", title: hint,
          class: `index-filter${a.mode === key ? " active" : ""}`, onclick: () => { a.mode = key; remember("askMode", key); renderDetail(); $(".dock-input")?.focus(); } },
        label)))),
    el("p", { class: "dock-hint muted" }, `${mode[2]} ${engine}.`),
    el("div", { class: "dock-row" }, input,
      el("button", { type: "button", class: "primary dock-send", disabled: a.busy, "aria-label": "Ask", onclick: sendBrainAsk }, icon("send"))),
    a.busy ? null : el("p", { class: "dock-try" }, el("span", { class: "muted" }, "Try: "), askSuggestions(c).map(([text, m], i) => [i ? el("span", { class: "muted" }, " · ") : null,
      el("button", { type: "button", class: "link", onclick: () => { a.mode = m; a.draft = text; sendBrainAsk(); } }, text)])));
}

function brainConversation() {
  const a = brain.ask;
  const parts = [];
  for (const turn of a.history) parts.push(...brainQa(turn));
  if (!a.history.length && !a.busy) {
    parts.push(cornell([], el("div", { class: "note-text muted" },
      "One question across every lecture, chat and talk. Retrieval says what was said, Synthesis connects what different recordings said, and Application adds the AI's own ideas, kept apart from your sources.")));
  }
  if (a.busy) {
    parts.push(cornell(el("b", {}, "Q"), el("div", { class: "note-text multi" }, a.pending), "qa q"),
      cornell(el("b", {}, "A"), el("div", { class: "note-text" }, "Searching your recordings…"), "qa a thinking"));
  }
  if (a.history.length && !a.busy) {
    parts.push(cornell([], el("p", { class: "note-text" }, el("button", { type: "button", class: "link", onclick: () => { a.history = []; renderDetail(); } },
      "Clear this conversation"))));
  }
  return parts;
}

function splitApplying(text) {
  const at = text.search(/applying it:/i);
  return at < 0 ? [text, null] : [text.slice(0, at).trim(), text.slice(at).replace(/^applying it:\s*/i, "").trim()];
}

function brainQa(turn) {
  const sources = turn.sources || [];
  const mode = turn.mode || "retrieval";
  const [said, applying] = mode === "application" ? splitApplying(turn.answer) : [turn.answer, null];
  return [cornell(el("b", {}, "Q"), el("div", { class: "note-text multi" }, turn.question), "qa q"),
    cornell([el("b", {}, "A"), turn.engine ? el("span", { class: "engine" }, engineName(turn.engine)) : null],
      [turn.engine ? el("p", { class: "answer-mode" }, ANSWER_LABELS[mode]) : null,
        el("div", { class: "note-text multi" }, withSourceLinks(said, sources)),
        applying ? el("div", { class: "applying" }, el("p", { class: "applying-label" }, "The AI's own ideas, not from your recordings"),
          el("div", { class: "note-text multi" }, applying)) : null,
        turn.note ? el("p", { class: "chat-note" }, turn.note) : null,
        sources.length ? sourceList(sources) : null], "qa a")];
}

function withSourceLinks(text, sources) {
  return text.split(/(\[\d+\])/g).map((part) => {
    const match = part.match(/^\[(\d+)\]$/);
    const src = match && sources.find((s) => s.n === Number(match[1]));
    if (!src) return part;
    return el("button", { type: "button", class: "source-cite", title: `${src.title}${src.start != null ? ` at ${formatClock(src.start, src.long_form)}` : ""}`,
      onclick: () => openSource(src) }, part);
  });
}

function sourceList(sources) {
  const kinds = { notes: "notes", transcript: "transcript", mine: "my notes" };
  return el("ol", { class: "brain-sources" }, sources.map((s) => el("li", { value: s.n },
    el("div", { class: "source-head" },
      el("button", { type: "button", class: "link", onclick: () => openSource(s) }, s.title),
      el("span", { class: "muted" }, ` · ${formatShort(s.at)}${s.subject ? ` · ${s.subject}` : s.person ? ` · with ${s.person}` : ""} · ${kinds[s.source] || s.source}`),
      s.start != null ? momentButton(s.recording_id, s.start, s.long_form) : null),
    el("p", { class: "source-text" }, snippet(s.text)))));
}

const snippet = (text) => {
  const body = text.replace(/^\[[^\]]*\]\n/, "").replace(/\s+/g, " ").trim();
  return body.length > 260 ? `${body.slice(0, 257)}…` : body;
};

const openSource = (s) => (s.start != null ? openAt(s.recording_id, s.start) : selectRecording(s.recording_id));

async function sendBrainAsk() {
  const a = brain.ask;
  const question = a.draft.trim();
  if (!question || a.busy) return;
  if (brain.tab !== "ask") { brain.tab = "ask"; remember("tab", "ask"); }
  a.busy = true;
  a.pending = question;
  renderDetail();
  try {
    a.history.push(await api("/api/brain/ask", jsonRequest("POST", { question, mode: a.mode })));
    a.draft = "";
  } catch (error) {
    alert(error.message);
  } finally {
    a.busy = false;
    a.pending = "";
  }
  if (state.view === "brain") {
    renderDetail();
    const last = document.querySelectorAll(".brain-ask .qa.q");
    last[last.length - 1]?.scrollIntoView({ block: "start", behavior: "smooth" });
    $(".dock-input")?.focus();
  }
}

// ---- Graph

async function loadGraph() {
  const g = brain.graph;
  g.loading = true;
  if (state.view === "brain") renderDetail();
  const params = new URLSearchParams();
  if (g.subject) params.set("subject_id", g.subject);
  if (g.since) params.set("since", new Date(Date.now() - Number(g.since) * 86_400_000).toISOString());
  try {
    g.data = await api(`/api/brain/graph?${params}`);
    layoutGraph(g.data);
  } catch (error) {
    g.data = { nodes: [], edges: [], error: error.message };
  }
  g.loading = false;
  if (state.view === "brain") renderDetail();
}

// A small force layout: nodes push apart, mentions pull together, everything drifts to the middle. Seeded, so the
// same brain draws the same map each time.
function layoutGraph(data, width = 900, height = 620) {
  const nodes = data.nodes;
  let seed = 7;
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  nodes.forEach((n, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    const r = n.type === "concept" ? 160 : 260;
    n.x = width / 2 + Math.cos(angle) * r + random() * 20;
    n.y = height / 2 + Math.sin(angle) * r + random() * 20;
    n.vx = 0; n.vy = 0;
    n.r = n.type === "concept" ? 5 + Math.sqrt(n.weight) * 4 : 7 + Math.sqrt(n.weight) * 2.5;
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = data.edges.map((e) => ({ a: byId.get(e.source), b: byId.get(e.target), w: e.weight })).filter((l) => l.a && l.b);
  for (let tick = 0; tick < 320; tick++) {
    const heat = 1 - tick / 320;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = random() - 0.5; dy = random() - 0.5; d2 = 0.5; }
        const d = Math.sqrt(d2);
        const push = (2600 / d2) + (d < a.r + b.r + 14 ? (a.r + b.r + 14 - d) * 0.5 : 0);
        const fx = (dx / d) * push, fy = (dy / d) * push;
        a.vx -= fx; a.vy -= fy; b.vx += fx; b.vy += fy;
      }
    }
    for (const l of links) {
      const dx = l.b.x - l.a.x, dy = l.b.y - l.a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const pull = (d - 70) * 0.02 * Math.min(3, 1 + Math.log(l.w));
      const fx = (dx / d) * pull, fy = (dy / d) * pull;
      l.a.vx += fx; l.a.vy += fy; l.b.vx -= fx; l.b.vy -= fy;
    }
    for (const n of nodes) {
      n.vx += (width / 2 - n.x) * 0.006;
      n.vy += (height / 2 - n.y) * 0.008;
      n.x += Math.max(-30, Math.min(30, n.vx * heat));
      n.y += Math.max(-30, Math.min(30, n.vy * heat));
      n.vx *= 0.55; n.vy *= 0.55;
    }
  }
  const pad = 40;  // fit the drawing to the box, keeping its proportions
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = Math.min((width - 2 * pad) / Math.max(1, maxX - minX), (height - 2 * pad) / Math.max(1, maxY - minY), 3);
  for (const n of nodes) {
    n.x = (width - (maxX - minX) * scale) / 2 + (n.x - minX) * scale;
    n.y = (height - (maxY - minY) * scale) / 2 + (n.y - minY) * scale;
  }
  data.width = width;
  data.height = height;
  data.links = links;
}

function brainGraph() {
  const g = brain.graph;
  const filters = cornell([], el("div", { class: "brain-tools" },
    el("select", { "aria-label": "Subject", onchange: (e) => { g.subject = e.target.value; g.focus = null; loadGraph(); } },
      el("option", { value: "" }, "Every subject"),
      state.subjects.map((s) => el("option", { value: s.id, selected: String(s.id) === String(g.subject) }, s.name))),
    el("select", { "aria-label": "Period", onchange: (e) => { g.since = e.target.value; g.focus = null; loadGraph(); } },
      SINCE_CHOICES.map(([value, label]) => el("option", { value, selected: value === g.since }, label)))));
  if (!g.data || g.loading && !g.data.nodes?.length) return [filters, cornell([], el("p", { class: "note-text muted" }, "Drawing the map…"))];
  if (g.data.error) return [filters, el("div", { class: "sheet-status error" }, g.data.error)];
  if (!g.data.nodes.length) return [filters, cornell([], el("p", { class: "note-text muted" }, "Nothing to draw yet: concepts appear once your recordings have notes."))];
  const legend = el("p", { class: "graph-legend" },
    el("span", { class: "key concept" }), "concept (bigger: in more recordings) ",
    el("span", { class: "key class" }), "subject ", el("span", { class: "key person" }), "person ",
    el("span", { class: "key recording" }), "talk with no subject",
    g.data.shown_concepts < g.data.total_concepts ? el("span", { class: "muted" }, ` · the ${g.data.shown_concepts} busiest of ${g.data.total_concepts} concepts`) : null);
  return [filters, el("div", { class: "graph-box" }, graphSvg(g.data)), legend];
}

function graphSvg(data) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${data.width} ${data.height}`);
  svg.setAttribute("class", "brain-graph");
  svg.setAttribute("role", "group");  // its nodes are controls, so the picture is a group of them, not one image
  svg.setAttribute("aria-label", `Map of ${data.shown_concepts} concepts and where they came up. Every concept is also in the Concepts list.`);
  const make = (name, attrs, parent = svg) => { const node = document.createElementNS(SVG_NS, name); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); parent.append(node); return node; };
  const edges = make("g", { class: "edges" });
  const edgeEls = data.links.map((l) => ({ l, el: make("line", { x1: l.a.x, y1: l.a.y, x2: l.b.x, y2: l.b.y, "stroke-width": Math.min(4, 0.8 + l.w * 0.6) }, edges) }));
  const labelled = new Set([...data.nodes].filter((n) => n.type === "concept").sort((a, b) => b.weight - a.weight).slice(0, 28).map((n) => n.id));
  const group = make("g", { class: "nodes" });
  const nodeEls = [];
  for (const n of [...data.nodes].sort((a, b) => (a.type === "concept") - (b.type === "concept"))) {
    const node = make("g", { class: `node ${n.type}`, tabindex: 0, role: "button", "aria-label": `${n.label}: ${n.type === "concept" ? `in ${n.weight} recordings` : n.type}` }, group);
    if (n.type === "class") {
      make("rect", { x: n.x - n.r, y: n.y - n.r, width: n.r * 2, height: n.r * 2, rx: 3, style: `fill:${dividerColor(n.ref)}` }, node);
    } else {
      make("circle", { cx: n.x, cy: n.y, r: n.r }, node);
    }
    make("title", {}, node).textContent = n.type === "concept" ? `${n.label} · ${n.weight} recording${n.weight === 1 ? "" : "s"}` : n.label;
    const showLabel = () => {
      const label = make("text", { x: n.x, y: n.y + n.r + 12, "text-anchor": "middle" }, node);
      label.textContent = n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label;
      return label;
    };
    const alwaysLabelled = n.type !== "concept" || labelled.has(n.id);
    if (alwaysLabelled) showLabel();
    const open = () => openGraphNode(n);
    node.addEventListener("click", open);
    node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    node.addEventListener("mouseenter", () => highlightGraph(svg, n.id));
    node.addEventListener("focus", () => highlightGraph(svg, n.id));
    node.addEventListener("blur", () => highlightGraph(svg, null));
    nodeEls.push({ n, el: node, alwaysLabelled, showLabel });
  }
  svg.addEventListener("mouseleave", () => highlightGraph(svg, null));
  svg._graph = { edgeEls, nodeEls };
  highlightGraph(svg, brain.graph.focus);
  return svg;
}

// Change which node and neighbours are lit by changing classes in place. The map is not redrawn: redrawing would
// remove the node that has keyboard focus and a keyboard user could never get past the first node.
function highlightGraph(svg, focus) {
  brain.graph.focus = focus;
  const { edgeEls, nodeEls } = svg._graph;
  const near = new Set(focus ? [focus, ...edgeEls.filter(({ l }) => l.a.id === focus || l.b.id === focus).flatMap(({ l }) => [l.a.id, l.b.id])] : []);
  for (const { l, el } of edgeEls) el.setAttribute("class", focus ? (l.a.id === focus || l.b.id === focus ? "on" : "off") : "");
  for (const item of nodeEls) {
    const { n, el } = item;
    el.setAttribute("class", `node ${n.type}${focus ? (near.has(n.id) ? " on" : " off") : ""}`);
    const label = el.querySelector("text");
    if (near.has(n.id) && !label) item.showLabel();
    else if (!near.has(n.id) && !item.alwaysLabelled && label) label.remove();
  }
}

function openGraphNode(n) {
  if (n.type === "concept") openConcept(n.ref);
  else if (n.type === "class") setFilter("subject", n.ref);
  else if (n.type === "person") openPerson(n.ref);
  else selectRecording(n.ref);
}

// ---- rebuild and export

async function rebuildBrain(button) {
  button.disabled = true;
  try {
    const result = await api("/api/brain/rebuild", { method: "POST" });
    flash(`Rebuilt from ${result.indexed} recording${result.indexed === 1 ? "" : "s"}`);
    brain.graph.data = null;
    await loadBrain();
    if (brain.conceptId != null) await loadConcept(brain.conceptId);
    if (brain.tab === "graph") await loadGraph();
  } catch (error) {
    alert(error.message);
  }
  button.disabled = false;
}

async function exportVault(button, folder = null) {
  button && (button.disabled = true);
  try {
    brain.exported = await api("/api/brain/export", jsonRequest("POST", folder ? { folder } : {}));
    if (brain.data) brain.data.vault_folder = brain.exported.folder;
    const kept = brain.exported.kept_edited?.length;
    flash(`Wrote ${brain.exported.files} pages. Open the folder in Obsidian as a vault.`
      + (kept ? ` ${kept} page${kept === 1 ? "" : "s"} you edited ${kept === 1 ? "was" : "were"} left as you wrote ${kept === 1 ? "it" : "them"}.` : ""));
  } catch (error) {
    alert(error.message);
  }
  if (button) button.disabled = false;
  if (state.view === "brain") renderDetail();
}

async function changeVaultFolder() {
  const answer = prompt("Folder for your Obsidian vault (it's created if it isn't there; your own files in it are never touched):",
    brain.data?.vault_folder || "");
  if (!answer?.trim()) return;
  await exportVault(null, answer.trim());
}

async function openVault() {
  try {
    await api("/api/brain/open-vault", { method: "POST" });
  } catch (error) {
    alert(error.message);
  }
}

document.getElementById("nav-brain")?.addEventListener("click", () => openBrain());
