"use strict";
// The bell: what happened to your recordings, newest first. A small glass panel over the page (Apple keeps glass
// for floating controls like this one, never for content).

const NOTE_KINDS = {
  added: ["upload", "Added"],
  transcribed: ["check", "Transcribed"],
  failed: ["unsupported", "Couldn't transcribe"],
  notes_ready: ["note", "Notes ready"],
  notes_failed: ["unsupported", "Notes failed"],
  recap_ready: ["speaker", "Recap ready"],
  duplicate: ["partial", "Possible duplicate"],
  reminder: ["calendar", "Reminder from Pip"],
};
const bell = { data: { items: [], unseen: 0 }, open: false };

function timeAgo(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return new Date(iso).toLocaleDateString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

async function loadNotifications() {
  try {
    bell.data = await api("/api/notifications");
  } catch {
    return;
  }
  renderBell();
}

function renderBell() {
  const unseen = bell.data.unseen;
  const badge = $("#bell-badge");
  badge.hidden = !unseen;
  badge.textContent = unseen > 99 ? "99+" : unseen;
  $("#bell-button").setAttribute("aria-label", unseen ? `Notifications, ${unseen} new` : "Notifications");
  if (bell.open) renderBellPanel();
}

function renderBellPanel() {
  const items = bell.data.items;
  $("#bell-list").replaceChildren(...(items.length ? items.map((n) => {
    const [iconName, verb] = NOTE_KINDS[n.kind] || ["dot", n.kind];
    const failed = n.kind.endsWith("failed");
    return el("li", { class: `bell-item${n.seen ? "" : " unseen"}${failed ? " failed" : ""}` },
      el("button", { type: "button", class: "bell-open", disabled: !n.recording_id && n.kind !== "reminder",
        title: n.kind === "reminder" ? "Open the calendar" : n.recording_id ? "Open this recording" : "This recording was deleted",
        onclick: () => { closeBell(); if (n.recording_id) selectRecording(n.recording_id); else if (n.kind === "reminder") openCalendar(); } },
      el("span", { class: "bell-icon", "aria-hidden": "true" }, icon(iconName)),
      el("span", { class: "bell-text" },
        el("strong", {}, n.title),
        el("span", { class: "bell-meta" }, [verb, n.subject, timeAgo(n.at)].filter(Boolean).join(" · ")),
        n.detail && (failed || n.kind === "duplicate") ? el("span", { class: failed ? "bell-detail" : "bell-detail calm" }, n.detail) : null),
      n.seen ? null : el("span", { class: "bell-dot", "aria-label": "New" })),
      el("button", { type: "button", class: "bell-dismiss", "aria-label": `Dismiss: ${verb} ${n.title}`,
        onclick: () => dismissNotification(n.id) }, icon("close")));
  }) : [el("li", { class: "bell-empty" }, "You're all caught up.")]));
  $("#bell-clear").hidden = !items.length;
}

async function openBell() {
  bell.open = true;
  $("#bell-panel").hidden = false;
  $("#bell-button").setAttribute("aria-expanded", "true");
  renderBellPanel();
  document.addEventListener("pointerdown", bellOutside, true);
  document.addEventListener("keydown", bellKey);
  await loadNotifications();
  if (bell.data.unseen) {  // they've been seen now; the dots stay until the panel is opened again
    try {
      const after = await api("/api/notifications/seen", { method: "POST" });
      bell.data.unseen = after.unseen;
      $("#bell-badge").hidden = true;
    } catch { /* try again next time */ }
  }
}

function closeBell() {
  bell.open = false;
  $("#bell-panel").hidden = true;
  $("#bell-button").setAttribute("aria-expanded", "false");
  document.removeEventListener("pointerdown", bellOutside, true);
  document.removeEventListener("keydown", bellKey);
}

function bellOutside(e) {
  if (!$("#bell").contains(e.target)) closeBell();
}

function bellKey(e) {
  if (e.key === "Escape") { closeBell(); $("#bell-button").focus(); }
}

async function dismissNotification(id) {
  try {
    await api(`/api/notifications/${id}`, { method: "DELETE" });
  } catch (error) {
    alert(error.message);
  }
  await loadNotifications();
}

async function clearNotifications() {
  try {
    await api("/api/notifications", { method: "DELETE" });
  } catch (error) {
    alert(error.message);
  }
  await loadNotifications();
}

$("#bell-button").addEventListener("click", () => (bell.open ? closeBell() : openBell()));
$("#bell-clear").addEventListener("click", clearNotifications);
loadNotifications();
setInterval(loadNotifications, 20_000);
