"use strict";
// Practice room: hold to talk, an AI plays the other person and answers out loud (the browser's own voice),
// then the session is saved as a recording and coached. Your side is recorded as compressed Opus.

const PRACTICE_BITRATE = 24000;  // about 0.18 MB a minute
const MIN_TURN_MS = 400;
const practice = { scenarios: null, setup: { scenario: "coffee_chat", role: "", difficulty: "neutral", personId: null },
  session: null };

function practiceVoice() {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  let saved = null;
  try { saved = localStorage.getItem("practice-voice"); } catch { /* storage off */ }
  return voices.find((v) => v.name === saved) || voices.find((v) => /natural/i.test(v.name)) || voices[0] || null;
}

function speak(text, onEnd) {
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = practiceVoice();
  if (voice) utterance.voice = voice;
  utterance.onend = utterance.onerror = () => onEnd?.();
  speechSynthesis.speak(utterance);
}

async function openPractice() {
  state.view = "practice";
  state.selectedId = null;
  state.detail = null;
  setDrawer(false);
  renderNav();
  renderList();
  if (!practice.scenarios) {
    try {
      practice.scenarios = await api("/api/practice/scenarios");
    } catch (error) {
      alert(error.message);
      return;
    }
  }
  renderDetail();
}

function renderPractice() {
  const desk = $("#detail");
  const s = practice.session;
  const sheet = (...children) => desk.replaceChildren(el("div", { class: "sheet-wrap" },
    el("div", { class: "sheet practice-sheet" }, ...children)));
  if (!practice.scenarios) return sheet(el("p", { class: "muted" }, "Loading…"));
  if (!s) return sheet(...practiceSetup());
  return sheet(...practiceSession(s));
}

function practiceSetup() {
  const setup = practice.setup;
  const chosen = practice.scenarios.find((x) => x.id === setup.scenario);
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  const current = practiceVoice();
  const cards = practice.scenarios.map((x) => el("button", { type: "button",
    class: `scenario-card${x.id === setup.scenario ? " chosen" : ""}`, "aria-pressed": String(x.id === setup.scenario),
    onclick: () => { setup.scenario = x.id; setup.role = ""; renderPractice(); } },
    el("strong", {}, x.name), el("span", {}, x.description),
    x.time_limit_sec ? el("small", {}, `${x.time_limit_sec} seconds`) : null));
  const people = state.people || [];
  return [
    el("h2", {}, "Practice room"),
    el("p", { class: "muted" }, "Hold the button (or Space) while you talk. The AI answers out loud. When you finish, the session is saved and coached like a real chat."),
    el("div", { class: "scenario-grid" }, cards),
    el("div", { class: "practice-options" },
      setup.scenario === "person"
        ? el("label", {}, "Practise with",
          el("select", { onchange: (e) => { setup.personId = Number(e.target.value) || null; } },
            el("option", { value: "" }, people.length ? "Pick someone…" : "No one in People yet"),
            people.map((p) => el("option", { value: p.id, selected: p.id === setup.personId }, p.name))))
        : el("label", {}, "Who they are",
          el("input", { value: setup.role, placeholder: chosen?.role || "", oninput: (e) => { setup.role = e.target.value; } })),
      el("label", {}, "How they'll be",
        el("select", { onchange: (e) => { setup.difficulty = e.target.value; } },
          [["friendly", "Friendly"], ["neutral", "Realistic"], ["tough", "Tough"]].map(([value, name]) =>
            el("option", { value, selected: value === setup.difficulty }, name)))),
      el("label", {}, "Their voice",
        el("select", { onchange: (e) => { try { localStorage.setItem("practice-voice", e.target.value); } catch { /* storage off */ }
          speak("Hi, this is how I'll sound."); } },
          voices.length ? voices.map((v) => el("option", { value: v.name, selected: v === current }, v.name))
            : el("option", {}, "Default voice")))),
    el("div", { class: "practice-start" },
      el("button", { type: "button", class: "primary", onclick: startPractice }, icon("mic"), "Start practising"),
      el("span", { class: "muted small" }, "Your side is kept as compressed audio (about 0.2 MB a minute) so you can hear yourself back.")),
  ];
}

async function startPractice() {
  const setup = practice.setup;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch {
    alert("The microphone isn't available. Allow it for this page and try again.");
    return;
  }
  let info;
  try {
    info = await api("/api/practice/sessions", jsonRequest("POST", { scenario: setup.scenario, role: setup.role,
      difficulty: setup.difficulty, person_id: setup.personId }));
  } catch (error) {
    stream.getTracks().forEach((t) => t.stop());
    alert(error.message);
    return;
  }
  const mimeType = ["audio/webm;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t));
  const whole = new MediaRecorder(stream, { mimeType, audioBitsPerSecond: PRACTICE_BITRATE });
  const chunks = [];
  whole.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  whole.start(1000);
  practice.session = { info, stream, mimeType, whole, chunks, t0: performance.now(), turns: [], status: "ready",
    timer: null, turnRecorder: null, done: false };
  if (info.opener) {
    practice.session.turns.push({ who: "them", text: info.opener });
    practice.session.status = "speaking";
    speak(info.opener, () => setPracticeStatus("ready"));
  }
  practice.session.timer = setInterval(updatePracticeClock, 250);
  document.addEventListener("keydown", practiceKey);
  document.addEventListener("keyup", practiceKey);
  renderPractice();
}

const elapsedSec = () => (performance.now() - practice.session.t0) / 1000;

function updatePracticeClock() {
  const clock = $("#practice-clock");
  const s = practice.session;
  if (!clock || !s) return;
  const limit = s.info.time_limit_sec;
  const seconds = limit ? Math.max(0, limit - elapsedSec()) : elapsedSec();
  clock.textContent = formatClock(seconds, false);
  clock.classList.toggle("urgent", Boolean(limit) && seconds <= 10);
  if (limit && seconds === 0) clock.textContent = "Time's up";
}

function setPracticeStatus(status) {
  if (!practice.session) return;
  practice.session.status = status;
  const line = $("#practice-status");
  if (line) line.textContent = PRACTICE_STATUS[status] || "";
  $("#talk-button")?.classList.toggle("live", status === "listening");
}

const PRACTICE_STATUS = { ready: "Hold to talk", listening: "Listening… let go when you're done",
  thinking: "Thinking…", speaking: "Speaking… hold to interrupt" };

function practiceSession(s) {
  return [
    el("div", { class: "practice-head" },
      el("div", {}, el("h2", {}, s.info.name), el("p", { class: "muted" }, `With ${s.info.persona}`)),
      el("span", { id: "practice-clock", class: "practice-clock" }, "00:00")),
    el("ol", { id: "practice-turns", class: "practice-turns", "aria-live": "polite" },
      s.turns.map((t) => el("li", { class: `bubble ${t.who}` }, el("span", { class: "who" }, t.who === "you" ? "You" : s.info.persona), t.text))),
    s.done ? el("p", { class: "practice-note" }, "They've wrapped up. End the session to get your coaching.") : null,
    el("div", { class: "talk-row" },
      el("button", { type: "button", id: "talk-button", class: "talk-button", "aria-label": "Hold to talk",
        onpointerdown: (e) => { e.preventDefault(); startTurn(); }, onpointerup: endTurn, onpointerleave: endTurn },
      icon("mic")),
      el("p", { id: "practice-status", class: "practice-status" }, PRACTICE_STATUS[s.status])),
    el("div", { class: "practice-actions" },
      el("button", { type: "button", class: "primary", onclick: finishPractice }, "End and get coached"),
      el("button", { type: "button", class: "quiet", onclick: discardPractice }, "Discard")),
  ];
}

function practiceKey(e) {
  if (e.code !== "Space" || e.target.closest?.("input, textarea, select") || !practice.session) return;
  e.preventDefault();
  if (e.type === "keydown" && !e.repeat) startTurn();
  if (e.type === "keyup") endTurn();
}

function startTurn() {
  const s = practice.session;
  if (!s || s.turnRecorder || s.status === "thinking") return;
  speechSynthesis.cancel();
  const recorder = new MediaRecorder(s.stream, { mimeType: s.mimeType, audioBitsPerSecond: PRACTICE_BITRATE });
  const parts = [];
  recorder.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
  s.turnRecorder = { recorder, parts, start: elapsedSec(), startedAt: performance.now() };
  recorder.start();
  setPracticeStatus("listening");
}

function endTurn() {
  const s = practice.session;
  const turn = s?.turnRecorder;
  if (!turn) return;
  s.turnRecorder = null;
  turn.recorder.onstop = () => sendTurn(turn);
  turn.recorder.stop();
}

async function sendTurn(turn) {
  const s = practice.session;
  if (!s) return;
  if (performance.now() - turn.startedAt < MIN_TURN_MS) { setPracticeStatus("ready"); return; }
  setPracticeStatus("thinking");
  const form = new FormData();
  form.append("audio", new Blob(turn.parts, { type: s.mimeType }), "turn.webm");
  form.append("start_sec", turn.start.toFixed(2));
  let result;
  try {
    result = await api(`/api/practice/sessions/${s.info.id}/turn`, { method: "POST", body: form });
  } catch (error) {
    flash(error.message);
    setPracticeStatus("ready");
    return;
  }
  if (practice.session !== s) return;
  if (!result.you) { flash("Didn't catch that. Try again a little louder."); setPracticeStatus("ready"); return; }
  s.turns.push({ who: "you", text: result.you }, { who: "them", text: result.reply });
  s.done = s.done || result.done;
  s.status = "speaking";
  renderPractice();
  $("#practice-turns")?.lastElementChild?.scrollIntoView({ block: "nearest" });
  speak(result.reply, () => setPracticeStatus("ready"));
}

function stopPracticeMedia(s) {
  clearInterval(s.timer);
  speechSynthesis.cancel();
  document.removeEventListener("keydown", practiceKey);
  document.removeEventListener("keyup", practiceKey);
  s.stream.getTracks().forEach((t) => t.stop());
}

async function finishPractice() {
  const s = practice.session;
  if (!s) return;
  if (s.whole.state !== "inactive") await new Promise((resolve) => { s.whole.onstop = resolve; s.whole.stop(); });
  stopPracticeMedia(s);
  setPracticeStatus("thinking");
  const form = new FormData();
  form.append("audio", new Blob(s.chunks, { type: s.mimeType }), "session.webm");
  try {
    const rec = await api(`/api/practice/sessions/${s.info.id}/finish`, { method: "POST", body: form });
    practice.session = null;
    flash("Saved. Your coaching is being written.");
    await loadRecordings();
    await selectRecording(rec.id);
  } catch (error) {
    alert(error.message);
  }
}

async function discardPractice() {
  const s = practice.session;
  if (!s || !confirm("Throw this practice session away?")) return;
  stopPracticeMedia(s);
  if (s.whole.state !== "inactive") s.whole.stop();
  practice.session = null;
  try { await api(`/api/practice/sessions/${s.info.id}`, { method: "DELETE" }); } catch { /* already gone */ }
  renderPractice();
}

speechSynthesis.addEventListener?.("voiceschanged", () => { if (state.view === "practice" && !practice.session) renderPractice(); });
document.getElementById("nav-practice")?.addEventListener("click", openPractice);
