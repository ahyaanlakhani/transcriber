"use strict";
// Naming: suggested names for a recording, the name its file had on the phone (kept when you rename), and combining
// the parts of one class.

const nameMenu = { el: null };

function closeNameMenu() {
  nameMenu.el?.remove();
  nameMenu.el = null;
  document.removeEventListener("pointerdown", nameMenuOutside, true);
  document.removeEventListener("keydown", nameMenuKey);
}

function nameMenuOutside(e) {
  if (nameMenu.el && !nameMenu.el.contains(e.target) && !e.target.closest(".suggest-name")) closeNameMenu();
}

function nameMenuKey(e) {
  if (e.key === "Escape") closeNameMenu();
}

async function openNameMenu(button) {
  if (nameMenu.el) { closeNameMenu(); return; }
  const rid = state.selectedId;
  const menu = el("div", { class: "name-menu", role: "dialog", "aria-label": "Suggested names" },
    el("p", { class: "name-note" }, "Reading your notes…"));
  button.closest(".title-row").append(menu);
  nameMenu.el = menu;
  document.addEventListener("pointerdown", nameMenuOutside, true);
  document.addEventListener("keydown", nameMenuKey);
  let data;
  try {
    data = await api(`/api/recordings/${rid}/name-suggestions`);
  } catch (error) {
    if (nameMenu.el === menu) menu.replaceChildren(el("p", { class: "name-note" }, error.message));
    return;
  }
  if (nameMenu.el !== menu || state.selectedId !== rid) return;  // closed or moved on while it was thinking
  menu.replaceChildren(
    el("p", { class: "name-head" }, "Suggested names"),
    data.suggestions.length
      ? el("ul", {}, data.suggestions.map((s) => el("li", {}, el("button", { type: "button", onclick: () => applyName(s.title) },
        el("span", { class: "name-title" }, s.title), el("span", { class: "name-source" }, s.source)))))
      : el("p", { class: "name-note" }, "Nothing new to suggest."),
    el("p", { class: "name-original" }, "Phone name: ", el("strong", {}, data.original), ". It stays the same when you rename."));
}

async function applyName(title) {
  closeNameMenu();
  await patchRecording({ title });
}

async function copyOriginalName(name) {
  try {
    await navigator.clipboard.writeText(name);
    flash("Copied the original name.");
  } catch {
    prompt("Copy the original name:", name);
  }
}

// the pill in the list and in the calendar for a recording that is one part of a class
function partTag(session) {
  return el("span", { class: "dup-tag part", title: `One of ${session.count} recordings of the same class (${session.minutes} min in all)` },
    `Part ${session.index} of ${session.count}`);
}

function sessionBanner(rec) {
  const session = rec.session;
  if (!session || rec.deleted_at) return null;
  if (session.combined) {  // this part is already inside a joined class
    return el("div", { class: "dup-banner session", role: "status" },
      el("span", {}, `Part ${session.index} of ${session.count}, joined into `),
      el("button", { type: "button", class: "link", onclick: () => selectRecording(session.combined_id) }, session.combined_title),
      el("span", {}, "."));
  }
  return el("div", { class: "dup-banner session", role: "status" },
    el("span", {}, `Part ${session.index} of ${session.count} of the same class (${session.minutes} min in all). `
      + "Combined, they get one set of notes that runs across the pauses. Your originals are kept."),
    el("button", { type: "button", class: "quiet", onclick: (e) => combineSession(session, e.currentTarget) },
      `Combine the ${session.count} parts`));
}

async function combineSession(session, button) {
  button.disabled = true;
  try {
    const rec = await api("/api/recordings/combine", jsonRequest("POST", { ids: session.ids, title: session.title }));
    flash("Combined. The originals are kept.");
    await loadNav();
    await loadRecordings();
    await selectRecording(rec.id);
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
}

// a joined class: the recordings it was made from, each one to open on its own
function partsBanner(rec) {
  if (!rec.parts?.length) return null;
  return el("div", { class: "dup-banner parts", role: "status" },
    el("span", {}, `Joined from ${rec.parts.length} recordings. Open one to see it on its own: `),
    rec.parts.map((part, i) => [i ? ", " : "", el("button", { type: "button", class: "link", onclick: () => selectRecording(part.id) },
      `${part.title}${part.minutes ? ` (${part.minutes} min)` : ""}`)]));
}

// "Rename all" / "Rename standard names": a topic name for each recording in the list you are looking at, written from
// its notes, which you review before anything changes. "Standard" means the phone's own names: "New Recording 33",
// "Untitled" or an address like "University Rd".
async function renameRecordings(scope, button) {
  const label = button.textContent;
  const buttons = [...document.querySelectorAll("#rename-tools button")];
  const params = new URLSearchParams({ scope });
  const { type, id } = state.filter;
  if (type === "subject") params.set("subject_id", id);
  if (type === "topic") params.set("topic_id", id);
  if (type === "starred") params.set("starred", "1");
  if (type === "nosubject") params.set("no_subject", "1");
  if (type === "person") params.set("person_id", id);
  buttons.forEach((b) => { b.disabled = true; });
  button.textContent = "Reading your notes…";
  let data;
  try {
    data = await api(`/api/class-name-suggestions?${params}`);
  } catch (error) {
    alert(error.message);
    return;
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    button.textContent = label;
  }
  if (!data.classes.length) {
    flash(scope === "standard" ? "Nothing here still has a standard name." : "No finished recordings here to rename.");
    return;
  }
  const rows = data.classes.map((c) => {
    const box = el("input", { type: "checkbox", checked: true, "aria-label": `Rename ${c.title}` });
    const input = el("input", { type: "text", value: c.suggestion, autocomplete: "off", "aria-label": `New name for ${c.title}` });
    return { c, box, input, item: el("li", { class: "class-name-row" }, box,
      el("div", {}, input, el("span", { class: "class-was" }, `Now: ${c.title}`))) };
  });
  const cancel = el("button", { type: "button", class: "quiet", onclick: () => dialog.close() }, "Cancel");
  const apply = el("button", { type: "submit", class: "primary" }, "Rename the ticked recordings");
  const form = el("form", { method: "dialog" },
    el("h2", { id: "rename-title" }, scope === "standard" ? "Replace standard names" : "Rename recordings"),
    el("p", { class: "hint" }, "Edit any name, and untick the ones to leave as they are. Each recording keeps its original phone name, so you can still match it to the phone."),
    data.warning ? el("p", { class: "sheet-note", role: "alert", id: "rename-warning" }, `${data.warning}, so some names below come from the subject and date instead of your notes. Try again later for names from what was taught.`) : null,
    el("ul", { class: "class-name-list" }, rows.map((r) => r.item)),
    el("menu", {}, cancel, el("span", { class: "spacer" }), apply));
  const dialog = el("dialog", { class: "class-names", "aria-labelledby": "rename-title", "aria-describedby": data.warning ? "rename-warning" : null }, form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    apply.disabled = true;
    const chosen = rows.filter((r) => r.box.checked && r.input.value.trim() && r.input.value.trim() !== r.c.title);
    let renamed = 0;
    try {
      for (const r of chosen) {
        await api(`/api/recordings/${r.c.id}`, jsonRequest("PATCH", { title: r.input.value.trim() }));
        renamed += 1;
      }
    } catch (error) {
      alert(`${error.message} (${renamed} of ${chosen.length} renamed before it stopped.)`);
    }
    dialog.close();
    await loadNav();
    await loadRecordings();
    if (chosen.some((r) => r.c.id === state.selectedId)) await selectRecording(state.selectedId);
    flash(`Renamed ${renamed} ${renamed === 1 ? "recording" : "recordings"}.`);
  });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  rows[0].input.focus();
  rows[0].input.select();
}
