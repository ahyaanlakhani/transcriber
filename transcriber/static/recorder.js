"use strict";
// Live recording: capture the microphone and/or the computer's sound, send 2-second pieces to the laptop app,
// and show the text as it comes back. Uses el(), icon(), api(), jsonRequest(), state and friends from app.js.

const PIECE_MS = 2000;
const POLL_MS = 1500;
const SOURCES = [
  ["meeting", "Meeting", "Zoom, Teams or Meet. The laptop records the call and your microphone for you: no sharing prompt, and lines are labelled You and Others."],
  ["mic", "Microphone", "People in the room: lectures and meetings around you."],
  ["system", "Computer sound", "Zoom, Teams, Meet or a video. The browser asks what to share: tick “Share audio”."],
  ["both", "Both", "Online calls including your own voice: computer sound mixed with your microphone."],
];

function newRecorder() {
  return {
    phase: "setup", source: localStorage.getItem("rec.source") || "mic", deviceId: localStorage.getItem("rec.mic") || "",
    title: "", subject: currentSubject()?.name || "", language: state.settings?.default_language || "en",
    privacy: state.settings?.default_privacy || "standard",
    sid: null, seq: 0, queue: [], uploading: false, segments: [], next: 0, notice: null, error: null,
    elapsedMs: 0, runningSince: null, durationSec: 0, transcribedSec: 0, level: 0, devices: [],
    media: null, streams: [], ctx: null, analyser: null, wakeLock: null, timers: [], ui: null, discarded: false,
    meetingInfo: null, levels: { you: 0, others: 0 },
  };
}

const recording = () => Boolean(state.rec && ["recording", "paused", "saving"].includes(state.rec.phase));
const recElapsed = (r) => r.elapsedMs + (r.runningSince ? performance.now() - r.runningSince : 0);

function openRecorder() {
  if (!state.rec) state.rec = newRecorder();
  state.view = "record";
  state.selectedId = null;
  setDrawer(false);
  renderList();
  renderDetail();
  if (state.rec.phase === "setup") { loadMicrophones(); loadMeetingInfo(); }
}

async function loadMeetingInfo() {
  const r = state.rec;
  try {
    r.meetingInfo = await api("/api/meeting/devices");
  } catch {
    r.meetingInfo = { available: false, reason: "Meeting recording isn't available here." };
  }
  if (state.rec === r && state.view === "record" && r.phase === "setup") renderDetail();
}

const liveBase = (r) => (r.source === "meeting" ? "/api/meeting" : "/api/live");

function meetingNote(r) {
  if (r.source !== "meeting") return null;
  const info = r.meetingInfo;
  return el("p", { class: "meeting-note" },
    info?.available === false ? info.reason
      : info?.output ? `Records your speakers (${info.output}) and your microphone (${info.input}). Headphones work best, so the call doesn't echo into your microphone. `
        : "Checking your speakers and microphone… ",
    "Tell everyone on the call you're recording.");
}

function meetingMeters(ui) {
  ui.meterYou = el("span", { class: "rec-meter-fill" });
  ui.meterOthers = el("span", { class: "rec-meter-fill" });
  return el("span", { class: "rec-meters", "aria-hidden": "true" },
    el("span", { class: "rec-meter-row" }, el("small", {}, "You"), el("span", { class: "rec-meter" }, ui.meterYou)),
    el("span", { class: "rec-meter-row" }, el("small", {}, "Others"), el("span", { class: "rec-meter" }, ui.meterOthers)));
}

async function startMeeting(r) {
  r.phase = "starting";
  try {
    const { id } = await api("/api/meeting/start", jsonRequest("POST", { title: r.title, subject: r.subject, language: r.language, privacy: r.privacy }));
    r.sid = id;
    r.phase = "recording";
    r.runningSince = performance.now();
    r.timers.push(setInterval(() => pollLive(r), POLL_MS), setInterval(() => updateRecorder(), 500));
    pollLive(r);
    renderDetail();
  } catch (error) {
    r.phase = "setup";
    r.error = error.message;
    renderDetail();
  }
}

async function loadMicrophones() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    state.rec.devices = devices.filter((d) => d.kind === "audioinput" && d.deviceId !== "communications");
  } catch {
    state.rec.devices = [];
  }
  if (state.view === "record" && state.rec.phase === "setup") renderDetail();
}

// ---- the recorder sheet

function renderRecorder() {
  const r = state.rec;
  const desk = $("#detail");
  const field = (label, ...value) => el("span", { class: "field" }, el("span", { class: "field-label" }, label), ...value);
  const locked = r.phase !== "setup" && r.phase !== "error";
  const subject = el("select", { "aria-label": "Subject", disabled: locked, onchange: (e) => { r.subject = e.target.value; } },
    el("option", { value: "" }, "None"),
    state.subjects.map((s) => el("option", { value: s.name, selected: s.name === r.subject }, s.name)));
  const privacySelect = el("select", { "aria-label": "Privacy class for this recording", disabled: locked,
    title: "Standard: services you approved may transcribe it. Private: only services you verified. Local only: nothing leaves.",
    onchange: (e) => { r.privacy = e.target.value; } },
    [["standard", "Standard"], ["private", "Private"], ["local_only", "Local only"]].map(([value, name]) =>
      el("option", { value, selected: value === r.privacy }, name)));
  const language = el("select", { "aria-label": "Language", disabled: locked, onchange: (e) => { r.language = e.target.value; } },
    languageCodes().map((code) => el("option", { value: code, selected: code === r.language,
      disabled: code === "mixed" && !state.status?.mixed }, LANGUAGE_NAMES[code])));
  const head = el("div", { class: "sheet-head" },
    el("div", { class: "title-row" },
      el("span", { class: `rec-dot${r.phase === "recording" ? " live" : ""}`, "aria-hidden": "true" }),
      el("input", { class: "title-input", value: r.title, placeholder: "Untitled live recording", "aria-label": "Title",
        disabled: locked, oninput: (e) => { r.title = e.target.value; } })),
    el("div", { class: "fields" }, field("Subject", subject), field("Language", language), field("Privacy", privacySelect)));

  const ui = {};
  let controls;
  if (!locked) {
    const sources = el("div", { class: "source-choice", role: "radiogroup", "aria-label": "What to record" },
      SOURCES.map(([key, label, hint]) => el("button", { type: "button", role: "radio", class: `source${r.source === key ? " on" : ""}`,
        disabled: key === "meeting" && r.meetingInfo?.available === false, title: key === "meeting" && r.meetingInfo?.reason ? r.meetingInfo.reason : null,
        "aria-checked": r.source === key ? "true" : "false",
        onclick: () => { r.source = key; localStorage.setItem("rec.source", key); renderDetail(); } },
      icon(key === "meeting" ? "layers" : key === "system" ? "screen" : key === "both" ? "combine" : "mic"),
      el("span", { class: "source-label" }, label), el("span", { class: "source-hint" }, hint))));
    const mic = r.source !== "system" && r.source !== "meeting" ? el("label", { class: "mic-pick" }, "Microphone",
      el("select", { onchange: (e) => { r.deviceId = e.target.value; localStorage.setItem("rec.mic", e.target.value); } },
        el("option", { value: "" }, "Default microphone"),
        r.devices.map((d, i) => el("option", { value: d.deviceId, selected: d.deviceId === r.deviceId },
          d.label || `Microphone ${i + 1}`)))) : null;
    controls = el("div", { class: "rec-setup" }, sources, mic, meetingNote(r),
      r.error ? el("p", { class: "sheet-note", role: "alert" }, r.error) : null,
      el("div", { class: "rec-actions" },
        el("button", { type: "button", class: "primary rec-start", onclick: startRecording },
          el("span", { class: "rec-dot live", "aria-hidden": "true" }), "Start recording"),
        el("span", { class: "hint" }, r.source === "meeting"
          ? "Text appears about 10–20 seconds behind. The laptop keeps recording even if you switch tabs or windows."
          : "Text appears about 10–20 seconds behind you. Keep this tab open; you can browse your library meanwhile.")));
  } else {
    ui.timer = el("span", { class: "rec-timer", "aria-live": "off" });
    ui.meter = el("span", { class: "rec-meter-fill" });
    ui.phase = el("span", { class: "rec-phase" });
    ui.pause = el("button", { type: "button", onclick: togglePause });
    controls = el("div", { class: "rec-bar" },
      ui.timer, r.source === "meeting" ? meetingMeters(ui) : el("span", { class: "rec-meter", "aria-hidden": "true" }, ui.meter),
      ui.phase, el("span", { class: "spacer" }),
      r.phase === "saving" ? null : [r.source === "meeting" ? null : ui.pause,
        el("button", { type: "button", class: "primary", onclick: stopRecording }, icon("stop"), "Stop and save"),
        el("button", { type: "button", class: "quiet", onclick: discardRecording }, "Discard")]);
  }

  ui.lines = el("div", { class: "rec-lines" });
  ui.tail = el("div", { class: "rec-tail", role: "status" });
  const sheet = el("div", { class: "sheet" }, ui.lines, ui.tail);
  desk.replaceChildren(el("div", { class: "sheet-wrap recorder" }, head, controls, sheet));
  r.ui = ui;
  r.ui.rendered = 0;
  updateRecorder(true);
}

function updateRecorder(full = false) {
  const r = state.rec;
  if (!r) return;
  renderRecordingPill();
  if (state.view !== "record" || !r.ui?.lines) return;
  const ui = r.ui;
  if (ui.timer) ui.timer.textContent = formatClock(recElapsed(r) / 1000, recElapsed(r) >= 3_600_000);
  if (ui.meter) ui.meter.style.transform = `scaleX(${Math.min(1, r.level * 3)})`;
  if (ui.meterYou) {
    ui.meterYou.style.transform = `scaleX(${Math.min(1, r.levels.you * 3)})`;
    ui.meterOthers.style.transform = `scaleX(${Math.min(1, r.levels.others * 3)})`;
  }
  if (ui.phase) ui.phase.textContent = r.phase === "paused" ? "Paused" : r.phase === "saving" ? "Saving…" : "Recording";
  if (ui.pause) ui.pause.replaceChildren(icon(r.phase === "paused" ? "mic" : "pause"), r.phase === "paused" ? "Resume" : "Pause");
  const longForm = r.durationSec >= 3600;
  const desk = $("#detail");
  const atBottom = desk.scrollHeight - desk.scrollTop - desk.clientHeight < 160;  // follow along unless you scrolled up
  if (full) { ui.lines.replaceChildren(); ui.rendered = 0; }
  for (const s of r.segments.slice(ui.rendered)) {
    ui.lines.append(cornell(el("span", { class: "timestamp static" }, formatClock(s.start, longForm)),
      el("div", { class: "note-text" }, s.speaker ? el("strong", { class: "speaker-label" }, `${s.speaker}: `) : null, s.text), "para"));
  }
  ui.rendered = r.segments.length;
  const behind = Math.max(0, r.durationSec - r.transcribedSec);
  let tail;
  if (r.phase === "setup" || r.phase === "error") tail = "Your words will appear here as you speak.";
  else if (r.phase === "saving") tail = "Transcribing the last few seconds and saving…";
  else if (r.notice) tail = r.notice;
  else if (!r.segments.length) tail = "Listening… the first lines arrive after the first pause, about 10 seconds in.";
  else tail = behind > 2 ? `Listening… ${Math.round(behind)} s waiting to be transcribed.` : "Listening…";
  ui.tail.replaceChildren(cornell([], el("div", { class: "note-text muted" },
    r.phase === "recording" ? el("span", { class: "pulse", "aria-hidden": "true" }) : null, tail)));
  if (atBottom && r.segments.length && (r.phase === "recording" || r.phase === "paused")) {
    ui.tail.scrollIntoView({ block: "nearest" });
  }
}

function renderRecordingPill() {
  const pill = $("#recording-pill");
  const show = recording() && state.view !== "record";
  pill.hidden = !show;
  if (show) {
    const r = state.rec;
    pill.replaceChildren(el("span", { class: `rec-dot${r.phase === "recording" ? " live" : ""}`, "aria-hidden": "true" }),
      r.phase === "paused" ? "Paused " : "Recording ", formatClock(recElapsed(r) / 1000, recElapsed(r) >= 3_600_000));
  }
}

// ---- capture

async function captureStreams(r) {
  const streams = [];
  if (r.source !== "mic") {
    let display;
    try {
      display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true, systemAudio: "include" });
    } catch (error) {
      throw new Error(error.name === "NotAllowedError" ? "Sharing was cancelled." : `Couldn't capture the computer's sound: ${error.message}`);
    }
    if (!display.getAudioTracks().length) {
      display.getTracks().forEach((t) => t.stop());
      throw new Error("No sound was shared. Choose the meeting's tab or “Entire screen” and tick “Share audio”.");
    }
    display.getVideoTracks().forEach((t) => { t.enabled = false; });
    display.getAudioTracks()[0].addEventListener("ended", () => {
      if (!recording()) return;
      if (r.source === "system") { r.notice = "Sharing stopped, so the recording was saved."; stopRecording(); }
      else { r.notice = "Computer sound stopped being shared; still recording your microphone."; updateRecorder(); }
    });
    streams.push(display);
  }
  if (r.source !== "system") {
    try {
      streams.push(await navigator.mediaDevices.getUserMedia({ audio: {
        deviceId: r.deviceId ? { exact: r.deviceId } : undefined,
        echoCancellation: r.source === "both", noiseSuppression: true, autoGainControl: true } }));
    } catch (error) {
      streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
      throw new Error(error.name === "NotAllowedError"
        ? "The browser blocked the microphone. Allow it with the microphone icon in the address bar, then try again."
        : error.name === "NotFoundError" || error.name === "OverconstrainedError"
          ? "That microphone wasn't found. Pick another one, or plug it in."
          : `Couldn't open the microphone: ${error.message}`);
    }
  }
  return streams;
}

async function startRecording() {
  const r = state.rec;
  r.error = null;
  if (r.source === "meeting") return startMeeting(r);
  if (!window.MediaRecorder || !navigator.mediaDevices) {
    r.error = "This browser can't record. Use Chrome or Edge.";
    return renderDetail();
  }
  r.phase = "starting";
  try {
    r.streams = await captureStreams(r);
    r.ctx = new AudioContext();
    const mix = r.ctx.createMediaStreamDestination();
    r.analyser = r.ctx.createAnalyser();
    r.analyser.fftSize = 1024;
    for (const stream of r.streams) {
      if (!stream.getAudioTracks().length) continue;
      const node = r.ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
      node.connect(mix);
      node.connect(r.analyser);
    }
    const mimeType = ["audio/webm;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t));
    if (!mimeType) throw new Error("This browser can't record WebM audio. Use Chrome or Edge.");
    r.media = new MediaRecorder(mix.stream, { mimeType, audioBitsPerSecond: 48000 });
    const { id } = await api("/api/live", jsonRequest("POST", { title: r.title, subject: r.subject, language: r.language, privacy: r.privacy }));
    r.sid = id;
    r.media.addEventListener("dataavailable", (e) => {
      if (e.data.size) { r.queue.push({ seq: r.seq++, blob: e.data }); pumpUploads(r); }
    });
    r.media.addEventListener("stop", () => { if (!r.discarded) finishRecording(r); });
    r.media.start(PIECE_MS);
    r.phase = "recording";
    r.runningSince = performance.now();
    r.timers.push(setInterval(() => pollLive(r), POLL_MS), setInterval(() => updateRecorder(), 500));
    meterLoop(r);
    keepAwake(r);
    window.addEventListener("beforeunload", warnBeforeLeaving);
    renderDetail();
  } catch (error) {
    stopCapture(r);
    r.phase = "setup";
    r.error = error.message;
    if (r.sid) { api(`/api/live/${r.sid}`, { method: "DELETE" }).catch(() => {}); r.sid = null; }
    renderDetail();
  }
}

function meterLoop(r) {
  const data = new Float32Array(r.analyser.fftSize);
  const tick = () => {
    if (state.rec !== r || !r.analyser || r.phase === "saving") return;
    r.analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += v * v;
    r.level = r.phase === "paused" ? 0 : Math.sqrt(sum / data.length);
    if (r.ui?.meter && state.view === "record") r.ui.meter.style.transform = `scaleX(${Math.min(1, r.level * 3)})`;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

async function keepAwake(r) {
  try {
    r.wakeLock = await navigator.wakeLock?.request("screen");
  } catch { /* not available: fine */ }
}

document.addEventListener("visibilitychange", () => {
  const r = state.rec;
  if (document.visibilityState === "visible" && recording() && (!r.wakeLock || r.wakeLock.released)) keepAwake(r);
});

function warnBeforeLeaving(event) {
  if (recording()) { event.preventDefault(); event.returnValue = ""; }
}

async function pumpUploads(r) {
  if (r.uploading) return;
  r.uploading = true;
  while (r.queue.length) {
    const { seq, blob } = r.queue[0];
    try {
      await api(`/api/live/${r.sid}/chunks/${seq}`, { method: "PUT", body: blob });
      r.queue.shift();
      if (r.notice?.startsWith("Can't reach")) r.notice = null;
    } catch (error) {
      if (/^404/.test(error.message) || /ended/.test(error.message)) { r.queue = []; break; }
      r.notice = "Can't reach the app right now. Recording continues; the audio is sent when it's back.";
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  r.uploading = false;
}

async function pollLive(r) {
  if (!r.sid || r.phase === "saving") return;
  try {
    const body = await api(`${liveBase(r)}/${r.sid}?after=${r.next}`);
    if (body.levels) r.levels = body.levels;
    r.segments.push(...body.segments);
    r.next = body.next;
    r.durationSec = body.duration_sec ?? r.durationSec;
    r.transcribedSec = body.transcribed_sec ?? r.transcribedSec;
    if (!r.notice?.startsWith("Can't reach the app")) r.notice = body.notice;
    updateRecorder();
  } catch { /* the next poll will try again */ }
}

function togglePause() {
  const r = state.rec;
  if (r.phase === "recording") {
    r.media.pause();
    r.elapsedMs = recElapsed(r);
    r.runningSince = null;
    r.phase = "paused";
  } else if (r.phase === "paused") {
    r.media.resume();
    r.runningSince = performance.now();
    r.phase = "recording";
  }
  updateRecorder();
}

function stopRecording() {
  const r = state.rec;
  if (!r || r.phase === "saving" || (!r.media && r.source !== "meeting")) return;
  r.elapsedMs = recElapsed(r);
  r.runningSince = null;
  r.phase = "saving";
  if (state.view === "record") renderDetail();
  if (r.media && r.media.state !== "inactive") r.media.stop();  // fires the last piece, then "stop"
  else finishRecording(r);
}

async function finishRecording(r) {
  stopCapture(r);
  while (r.queue.length || r.uploading) await new Promise((resolve) => setTimeout(resolve, 200));
  try {
    const { recording_id: rid } = await api(`${liveBase(r)}/${r.sid}/finish`, { method: "POST" });
    endSession(r);
    flash("Saved. AI notes will follow.");
    await loadNav();
    await loadRecordings();
    await selectRecording(rid);
  } catch (error) {
    endSession(r);
    alert(`Couldn't save the live recording: ${error.message}`);
    await loadRecordings();
    state.view = "sheet";
    renderDetail();
  }
}

async function discardRecording() {
  const r = state.rec;
  if (!confirm("Discard this recording? The audio and text so far are deleted.")) return;
  r.phase = "saving";
  r.discarded = true;
  const sid = r.sid;
  stopCapture(r);
  endSession(r);
  try { await api(`${liveBase(r)}/${sid}`, { method: "DELETE" }); } catch { /* already gone */ }
  state.view = "sheet";
  renderDetail();
}

function stopCapture(r) {
  try { if (r.media && r.media.state !== "inactive") r.media.stop(); } catch { /* already stopped */ }
  r.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  r.streams = [];
  r.ctx?.close().catch(() => {});
  r.ctx = null;
  r.analyser = null;
  r.wakeLock?.release().catch(() => {});
  r.wakeLock = null;
}

function endSession(r) {
  r.timers.forEach(clearInterval);
  r.timers = [];
  window.removeEventListener("beforeunload", warnBeforeLeaving);
  if (state.rec === r) state.rec = null;
  renderRecordingPill();
}
