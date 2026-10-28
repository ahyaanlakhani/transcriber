"use strict";
// Projects: a place that ties together the recordings, people, concepts and documents behind one piece of work (a
// group project, an assignment, a job hunt), with its own tasks. A project also gathers the decisions and the open
// follow-ups from its recordings. Deleting a project never touches a recording.

const projectsView = { list: null, detail: null, error: null, add: { kind: "recording", text: "", label: "" }, options: {} };
const PROJECT_KINDS = [["recording", "Recording"], ["person", "Person"], ["concept", "Concept"], ["document", "Document or link"]];

async function openProjects(id = null) {
  state.view = "projects";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  projectsView.detail = null;
  renderDetail();
  if (id != null) await loadProject(id); else await loadProjects();
}

async function loadProjects() {
  try {
    projectsView.list = (await api("/api/projects")).projects;
    projectsView.error = null;
  } catch (error) {
    projectsView.error = error.message;
  }
  if (state.view === "projects") renderDetail();
}

async function loadProject(id) {
  try {
    projectsView.detail = await api(`/api/projects/${id}`);
    projectsView.error = null;
  } catch (error) {
    projectsView.error = error.message;
    projectsView.detail = null;
  }
  if (state.view === "projects") renderDetail();
}

async function projectCall(path, method, body) {
  try {
    const result = await api(path, body === undefined ? { method } : jsonRequest(method, body));
    if (result && result.id && result.recordings) projectsView.detail = result;
    projectsView.error = null;
    return result;
  } catch (error) {
    projectsView.error = error.message;
  } finally {
    renderDetail();
  }
  return null;
}

function renderProjects() {
  const detail = projectsView.detail;
  $("#detail").replaceChildren(el("div", { class: "sheet-wrap brain" },
    detail ? projectPage(detail) : projectList()));
}

function projectList() {
  const name = el("input", { class: "search", placeholder: "New project (e.g. Final year project)", "aria-label": "New project name",
    onkeydown: (e) => { if (e.key === "Enter") create(); } });
  const create = async () => {
    if (!name.value.trim()) return;
    const made = await api("/api/projects", jsonRequest("POST", { name: name.value })).catch((error) => { projectsView.error = error.message; return null; });
    if (made) { projectsView.detail = made; }
    renderDetail();
  };
  const list = projectsView.list;
  return [el("div", { class: "brain-head" }, el("div", {}, el("h2", {}, "Projects"),
    el("p", { class: "muted" }, "Tie together the recordings, people, concepts and documents behind a piece of work."))),
  el("div", { class: "sheet brain-sheet projects" },
    projectsView.error ? el("p", { class: "sheet-note", role: "alert" }, projectsView.error) : null,
    el("div", { class: "project-new" }, name, el("button", { type: "button", class: "primary", onclick: create }, "Create")),
    !list ? el("p", { class: "muted" }, "Loading…") : list.length ? el("ul", { class: "project-list" }, list.map((p) => el("li", {},
      el("button", { type: "button", class: "project-row", onclick: () => loadProject(p.id) },
        el("strong", {}, p.name), p.status === "done" ? el("span", { class: "tag" }, "done") : null,
        el("span", { class: "muted" }, ` ${p.links} linked · ${p.open_tasks} open task${p.open_tasks === 1 ? "" : "s"}`),
        p.description ? el("small", { class: "muted project-desc" }, p.description) : null)))) :
      el("p", { class: "muted" }, "No projects yet. A project could be a group assignment, your final year project or a job hunt."))];
}

function projectPage(p) {
  const back = el("button", { type: "button", class: "link brain-back", onclick: () => { projectsView.detail = null; loadProjects(); } }, "← All projects");
  const title = el("input", { class: "title-input", value: p.name, "aria-label": "Project name",
    onchange: (e) => projectCall(`/api/projects/${p.id}`, "PATCH", { name: e.target.value }) });
  const description = el("textarea", { rows: 2, placeholder: "What is this project about?", "aria-label": "Project description",
    onchange: (e) => projectCall(`/api/projects/${p.id}`, "PATCH", { description: e.target.value }) }, p.description);
  const unlink = (kind, ref) => el("button", { type: "button", class: "bell-dismiss pip-cancel", title: "Remove from this project",
    "aria-label": "Remove from this project", onclick: () => projectCall(`/api/projects/${p.id}/links?kind=${kind}&ref=${encodeURIComponent(ref)}`, "DELETE") }, icon("close"));
  const at = (start) => (start != null ? ` at ${formatClock(start, start >= 3600)}` : "");
  const section = (title, rows, empty) => el("section", { class: "today-block" }, el("h3", {}, title),
    rows.length ? el("ul", { class: "today-list" }, rows) : el("p", { class: "muted" }, empty));
  const head = el("div", { class: "brain-head" }, el("div", {}, back, title),
    el("div", { class: "brain-actions" },
      el("button", { type: "button", class: "quiet", onclick: () => projectCall(`/api/projects/${p.id}`, "PATCH", { status: p.status === "done" ? "active" : "done" }) },
        p.status === "done" ? "Reopen" : "Mark done"),
      el("button", { type: "button", class: "quiet danger", onclick: async () => {
        if (!confirm("Delete this project? Your recordings, people and concepts are not touched.")) return;
        await api(`/api/projects/${p.id}`, { method: "DELETE" }).catch(() => {});
        projectsView.detail = null; loadProjects();
      } }, "Delete project")));
  const body = el("div", { class: "sheet brain-sheet projects" },
    projectsView.error ? el("p", { class: "sheet-note", role: "alert" }, projectsView.error) : null,
    description,
    section("Recordings", p.recordings.map((r) => el("li", {},
      r.missing ? el("span", { class: "muted" }, r.title) : el("button", { type: "button", class: "link", onclick: () => selectRecording(r.id) }, r.title),
      el("small", { class: "muted" }, r.missing ? "" : ` · ${[r.subject, formatDate(r.at), r.in_bin ? "in Recently Deleted" : null].filter(Boolean).join(" · ")}`),
      unlink("recording", r.ref))), "Nothing linked yet."),
    section("People", p.people.map((x) => el("li", {}, x.missing ? x.name : el("button", { type: "button", class: "link", onclick: () => openPerson(Number(x.ref)) }, x.name),
      unlink("person", x.ref))), "No people linked."),
    section("Concepts", p.concepts.map((c) => el("li", {}, c.missing ? c.name : el("button", { type: "button", class: "link", onclick: () => openConcept(Number(c.ref)) }, c.name),
      unlink("concept", c.ref))), "No concepts linked."),
    section("Documents and links", p.documents.map((d) => el("li", {}, /^https?:/i.test(d.ref) ? el("a", { href: d.ref, target: "_blank", rel: "noopener" }, d.label) : el("span", {}, d.label),
      /^https?:/i.test(d.ref) && d.label === d.ref ? null : el("small", { class: "muted" }, ` · ${d.ref}`), unlink("document", d.ref))), "No documents linked."),
    projectAdder(p),
    section("Decisions from its recordings", p.decisions.map((d) => el("li", {}, el("span", {}, d.text), el("small", { class: "muted" }, " · "),
      el("button", { type: "button", class: "link", onclick: () => selectRecording(d.recording_id) }, `${d.recording}${at(d.start)}`))), "No decisions in the linked notes."),
    section("Still open in its recordings", p.follow_ups.map((i) => el("li", {}, el("span", {}, i.text),
      el("small", { class: "muted" }, ` · ${[i.owner ? `for ${i.owner}` : null, i.due ? `by ${i.due}` : null].filter(Boolean).join(" · ")} · `),
      el("button", { type: "button", class: "link", onclick: () => selectRecording(i.recording_id) }, i.recording))), "Nothing open."),
    projectTasks(p));
  return [head, body];
}

function projectTasks(p) {
  const input = el("input", { class: "search", placeholder: "Add a task", "aria-label": "New task",
    onkeydown: (e) => { if (e.key === "Enter" && input.value.trim()) projectCall(`/api/projects/${p.id}/tasks`, "POST", { text: input.value }); } });
  return el("section", { class: "today-block" }, el("h3", {}, "Tasks"),
    p.tasks.length ? el("ul", { class: "today-list" }, p.tasks.map((t) => el("li", {},
      el("label", { class: "today-check" }, el("input", { type: "checkbox", checked: !!t.done, "aria-label": t.text,
        onchange: async (e) => { await api(`/api/project-tasks/${t.id}`, jsonRequest("PATCH", { done: e.target.checked })); loadProject(p.id); } }),
      el("span", { class: t.done ? "task-done" : "" }, t.text)),
      el("button", { type: "button", class: "bell-dismiss pip-cancel", "aria-label": `Delete task: ${t.text}`,
        onclick: async () => { await api(`/api/project-tasks/${t.id}`, { method: "DELETE" }); loadProject(p.id); } }, icon("close"))))) :
      el("p", { class: "muted" }, "No tasks yet."),
    input);
}

function projectAdder(p) {
  const add = projectsView.add;
  const kind = el("select", { "aria-label": "What to link", onchange: (e) => { add.kind = e.target.value; add.text = ""; loadProjectOptions(add.kind).then(renderDetail); } },
    PROJECT_KINDS.map(([value, name]) => el("option", { value, selected: add.kind === value }, name)));
  const options = projectsView.options[add.kind] || [];
  const list = el("datalist", { id: "project-options" }, options.map((o) => el("option", { value: o.name })));
  const input = el("input", { class: "search", list: add.kind === "document" ? null : "project-options", value: add.text,
    placeholder: add.kind === "document" ? "A web address or a file path" : `Find a ${add.kind}…`, "aria-label": "What to link",
    oninput: (e) => { add.text = e.target.value; } });
  const label = add.kind === "document" ? el("input", { class: "search", placeholder: "Name (optional)", value: add.label, "aria-label": "Name of the document",
    oninput: (e) => { add.label = e.target.value; } }) : null;
  const go = el("button", { type: "button", class: "quiet", onclick: async () => {
    const text = add.text.trim();
    const match = add.kind === "document" ? { ref: text } : options.find((o) => o.name.toLowerCase() === text.toLowerCase());
    if (!match) { projectsView.error = `Pick a ${add.kind} from the list.`; renderDetail(); return; }
    const done = await projectCall(`/api/projects/${p.id}/links`, "POST", { kind: add.kind, ref: String(match.ref), label: add.label });
    if (done) { add.text = ""; add.label = ""; renderDetail(); }
  } }, "Link it");
  loadProjectOptions(add.kind).then((loaded) => { if (loaded && !options.length) renderDetail(); });
  return el("section", { class: "today-block project-add" }, el("h3", {}, "Link something"),
    el("div", { class: "project-add-row" }, kind, input, label, go), list);
}

async function loadProjectOptions(kind) {
  if (kind === "document" || projectsView.options[kind]) return false;
  try {
    if (kind === "recording") {
      projectsView.options.recording = (await api("/api/recordings")).map((r) => ({ ref: r.id, name: r.title }));
    } else if (kind === "person") {
      projectsView.options.person = (state.people || []).map((x) => ({ ref: x.id, name: x.name }));
    } else if (kind === "concept") {
      projectsView.options.concept = (await api("/api/brain")).concepts.map((c) => ({ ref: c.id, name: c.name }));
    }
  } catch { projectsView.options[kind] = []; }
  return true;
}

document.getElementById("nav-projects")?.addEventListener("click", () => openProjects());
