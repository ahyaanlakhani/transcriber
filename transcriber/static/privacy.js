"use strict";
// Privacy: what may leave this laptop. Every recording has a class (Standard, Private, Local only). A cloud service
// is used for a recording only when you have approved that exact setup for its class. Nothing is approved until
// you do it here. Reading, searching, the calendar and exports never need a cloud service and always keep working.

const privacyView = { data: null, error: null, busy: false };

const PRIVACY_BLURB = {
  standard: "Any service you have approved can read it. This is how recordings start.",
  private: "Only services you have checked keep nothing and don't train. Claude through your subscription can't qualify.",
  local_only: "Never leaves this laptop. Notes and live text from a cloud service are switched off for it.",
};
const PRIVACY_WHY = {
  not_approved: "that service isn't approved yet", private_not_verified: "Private needs a service you confirmed keeps nothing",
  private_impossible: "that service can't be used for Private recordings", local_only: "it is set to Local only",
  unknown_provenance: "the app can't trace where it was combined from", unknown_provider: "the app doesn't know that service",
  unknown_source: "a recording behind it is gone", no_scope: "the app couldn't tell what it draws on",
  no_context: "the app couldn't tell how private it is", invalid_class: "a class wasn't recognised", allowed: "approved",
};
const PRIVACY_FEATURE = {
  transcribe: "Transcription", notes: "Notes", recap: "Audio recap", naming: "Name suggestion", ask: "Ask about a recording",
  "ask-all": "Ask across everything", "brain-merge": "Concept names", "brain-define": "Concept definition", brief: "People brief",
  live: "Live text", practice: "Practice room", pip: "Pip", unknown: "Unknown",
};

async function openPrivacy() {
  state.view = "privacy";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  if (state.filter.type === "person") await setFilter("all", null, false); else { renderNav(); renderList(); }
  renderDetail();
  await loadPrivacy();
}

async function loadPrivacy() {
  try {
    privacyView.data = await api("/api/privacy");
    privacyView.error = null;
  } catch (error) {
    privacyView.error = error.message;
  }
  if (state.view === "privacy") renderDetail();
}

async function privacyCall(path, options) {
  privacyView.busy = true;
  try {
    privacyView.data = await api(path, options);
    privacyView.error = null;
  } catch (error) {
    privacyView.error = error.message;
  }
  privacyView.busy = false;
  if (state.view === "privacy") renderDetail();
}

function renderPrivacy() {
  const data = privacyView.data;
  const head = el("div", { class: "brain-head" },
    el("div", {}, el("h2", {}, "Privacy"),
      el("p", { class: "muted" }, "Nothing goes to a cloud service unless the recording's class and your approval both allow it.")));
  if (!data) {
    $("#detail").replaceChildren(el("div", { class: "sheet-wrap brain" }, head,
      el("div", { class: "sheet brain-sheet" }, el("p", { class: "muted" }, privacyView.error || "Loading…"))));
    return;
  }
  const anyApproved = data.providers.some((p) => p.approved_standard);
  const body = [
    privacyView.error ? el("p", { class: "sheet-note", role: "alert" }, privacyView.error) : null,
    anyApproved ? null : el("p", { class: "privacy-banner", role: "status" },
      "Nothing is approved yet, so transcription, notes and Ask are paused. Reading, searching and exporting still work. ",
      "Approve the services below when you are ready."),
    privacyClasses(data), privacyProviders(data), privacyUntraced(data), privacyLog(data),
  ];
  $("#detail").replaceChildren(el("div", { class: "sheet-wrap brain" }, head, el("div", { class: "sheet brain-sheet privacy" }, body)));
}

function privacyClasses(data) {
  const start = el("select", { "aria-label": "Class new recordings start with",
    onchange: async (e) => {
      try { await api("/api/settings", jsonRequest("PUT", { default_privacy: e.target.value })); privacyView.error = null; }
      catch (error) { privacyView.error = error.message; }
      await loadPrivacy();
    } },
    ["standard", "private"].map((key) => el("option", { value: key, selected: data.default === key },
      data.classes.find((c) => c.key === key).label)));
  return el("section", { class: "privacy-block" }, el("h3", {}, "Classes"),
    el("ul", { class: "privacy-classes" }, data.classes.map((c) => el("li", {},
      el("strong", {}, c.label), el("span", { class: "privacy-count" }, `${data.counts[c.key]}`),
      el("span", { class: "muted" }, PRIVACY_BLURB[c.key])))),
    el("label", { class: "privacy-start" }, "New recordings start as ", start));
}

function privacyProviders(data) {
  const cards = data.providers.map((p) => {
    const send = (standard, priv) => privacyCall("/api/privacy/providers", jsonRequest("PUT",
      { config_id: p.config_id, standard, private: priv, verified: priv }));
    const standard = el("input", { type: "checkbox", checked: p.approved_standard, disabled: !p.known || privacyView.busy,
      onchange: (e) => (e.target.checked ? send(true, false) : privacyCall(
        `/api/privacy/providers?config_id=${encodeURIComponent(p.config_id)}`, { method: "DELETE" })) });
    const priv = el("input", { type: "checkbox", checked: p.approved_private, disabled: !p.private_possible || privacyView.busy,
      onchange: (e) => send(true, e.target.checked) });
    return el("li", { class: "privacy-provider" },
      el("div", { class: "privacy-provider-head" }, el("strong", {}, p.label), el("span", { class: "tag" },
        p.locality === "self_hosted" ? "your server" : p.locality === "cloud" ? "cloud" : "unknown"),
      el("span", { class: "privacy-roles muted" }, p.roles.join(" · "))),
      el("code", { class: "privacy-config" }, p.config_id),
      el("p", { class: "muted privacy-note" }, p.note),
      el("label", { class: "privacy-check" }, standard, " Allow for Standard recordings"),
      el("label", { class: "privacy-check" }, priv, p.private_possible
        ? " I checked this setup keeps nothing and doesn't train: allow it for Private recordings"
        : " Can't be used for Private recordings"));
  });
  return el("section", { class: "privacy-block" }, el("h3", {}, "Services in use"),
    data.providers.length ? el("ul", { class: "privacy-providers" }, cards)
      : el("p", { class: "muted" }, "No cloud service is set up, so nothing can leave this laptop."),
    el("p", { class: "muted privacy-fine" }, "Your approval is a record of what you checked, not proof. A provider's own terms decide what it keeps."));
}

function privacyUntraced(data) {
  if (!data.untraced.length) return null;
  return el("section", { class: "privacy-block" }, el("h3", {}, "Combined before the app kept track"),
    el("p", { class: "muted" }, "These were joined from parts the app can't trace. They stay off cloud services until you confirm where they came from."),
    el("ul", { class: "privacy-untraced" }, data.untraced.map((r) => el("li", {},
      el("button", { type: "button", class: "link", onclick: () => selectRecording(r.id) }, r.title),
      el("button", { type: "button", class: "quiet", onclick: async () => {
        await api(`/api/recordings/${r.id}/privacy/confirm`, { method: "POST" });
        await loadPrivacy();
      } }, "I checked this")))));
}

function privacyLog(data) {
  const rows = data.log.slice(0, 40).map((row) => el("tr", { class: row.decision },
    el("td", {}, new Date(row.at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })),
    el("td", {}, PRIVACY_FEATURE[row.feature] || row.feature),
    el("td", {}, el("code", {}, row.config_id)),
    el("td", {}, `${row.recording_ids.length}`),
    el("td", {}, row.decision === "allowed" ? "Sent" : `Held back: ${PRIVACY_WHY[row.reason] || row.reason}`)));
  return el("section", { class: "privacy-block" }, el("h3", {}, "Recent activity"),
    el("p", { class: "muted" }, "Only what, when and where. No audio or text is ever stored here."),
    rows.length ? el("div", { class: "privacy-log" }, el("table", {},
      el("thead", {}, el("tr", {}, ["When", "What", "Service", "Recordings", "Result"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))) : el("p", { class: "muted" }, "Nothing has been sent yet."));
}

// A recording's class, on its own sheet.
function privacyField(rec) {
  const info = rec.privacy || { class: rec.privacy_classification || "standard", effective: "standard", traced: true };
  const labels = { standard: "Standard", private: "Private", local_only: "Local only" };
  const select = el("select", { "aria-label": "Privacy class", onchange: async (e) => {
    try {
      await api(`/api/recordings/${rec.id}/privacy`, jsonRequest("PUT", { classification: e.target.value }));
    } catch (error) { alert(error.message); }
    await loadDetail(rec.id);
  } }, Object.entries(labels).map(([value, name]) => el("option", { value, selected: info.class === value }, name)));
  const notes = [];
  if (info.effective !== info.class) notes.push(`Counts as ${labels[info.effective]} because of what it was made from.`);
  if (!info.traced) notes.push("Where it was combined from can't be traced: it stays off cloud services until you confirm.");
  return el("span", { class: "field" }, el("span", { class: "field-label" }, "Privacy"), select,
    notes.length ? el("span", { class: "muted privacy-field-note" }, notes.join(" ")) : null,
    info.traced ? null : el("button", { type: "button", class: "link", onclick: async () => {
      await api(`/api/recordings/${rec.id}/privacy/confirm`, { method: "POST" });
      await loadDetail(rec.id);
    } }, "I checked this"));
}

// Settings holds the entry point: a one-line summary and a button to the full controls.
async function fillPrivacySummary() {
  const line = $("#privacy-summary");
  line.textContent = "";
  try {
    const data = await api("/api/privacy");
    const approved = data.providers.filter((p) => p.approved_standard).length;
    const strict = data.counts.private + data.counts.local_only;
    line.textContent = `${approved} of ${data.providers.length} cloud services approved`
      + (strict ? ` · ${strict} recording${strict === 1 ? "" : "s"} set to Private or Local only` : "")
      + (approved ? "" : ". Cloud features are paused until you approve one.");
  } catch { /* the summary is optional */ }
}

document.getElementById("open-privacy")?.addEventListener("click", () => {
  document.getElementById("settings-dialog").close();
  openPrivacy();
});
