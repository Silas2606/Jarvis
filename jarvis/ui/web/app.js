/* J.A.R.V.I.S. — the display's behaviour.
 *
 * Reads the event stream from the local server and turns it into state: what
 * the core animation does, what the transcript shows, what the panels hold.
 * Nothing here is decorative-only -- the core reacts to the actual microphone
 * level, and the panels come from the actual store.
 */

const token = new URLSearchParams(location.search).get("token") || "";
const headers = { "Content-Type": "application/json", "X-Jarvis-Token": token };

const el = (id) => document.getElementById(id);

const ui = {
  stateChip: el("state-chip"),
  stateLabel: el("state-label"),
  coreState: el("core-state"),
  coreHint: el("core-hint"),
  transcript: el("transcript"),
  log: el("log-lines"),
  input: el("input"),
  send: el("send"),
  levelBar: el("level-bar"),
  micToggle: el("mic-toggle"),
  ask: el("ask"),
  askQuestion: el("ask-question"),
};

/* ---- state ---- */

const state = {
  mode: "offline",
  level: 0,        // smoothed microphone loudness, 0..1
  turns: 0,
  tools: 0,
  tokens: 0,
  history: [],     // recent levels, for the scope
  levelAt: 0,      // when the last level arrived
  pendingQuestion: "",
};

const LABELS = {
  asleep:    { chip: "bereit",     core: "BEREIT",    hint: "Sag „Jarvis“" },
  listening: { chip: "höre zu",    core: "HÖRE ZU",   hint: "Sprich einfach weiter" },
  thinking:  { chip: "denke nach", core: "DENKE",     hint: "Einen Moment" },
  working:   { chip: "arbeite",    core: "ARBEITE",   hint: "Führe aus" },
  speaking:  { chip: "spreche",    core: "SPRECHE",   hint: "Fall mir ruhig ins Wort" },
  asking:    { chip: "rückfrage",  core: "RÜCKFRAGE", hint: "Ja oder Nein" },
  offline:   { chip: "offline",    core: "OFFLINE",   hint: "Keine Verbindung" },
};

function setMode(mode) {
  if (!LABELS[mode]) return;
  state.mode = mode;
  const label = LABELS[mode];
  ui.stateChip.dataset.state = mode;
  ui.stateLabel.textContent = label.chip;
  ui.coreState.textContent = label.core;
  ui.coreHint.textContent = label.hint;
  ui.micToggle.classList.toggle("live", mode === "listening");
}

/* ---- transcript ---- */

function addTurn(who, text, kind) {
  const welcome = ui.transcript.querySelector(".welcome");
  if (welcome) welcome.remove();

  const turn = document.createElement("div");
  turn.className = `turn ${kind || who}`;
  turn.innerHTML = `<div class="who"></div><div class="what"></div>`;
  turn.querySelector(".who").textContent = who;
  turn.querySelector(".what").textContent = text;
  ui.transcript.appendChild(turn);

  // Keep the DOM from growing without bound over a long session.
  while (ui.transcript.children.length > 120) ui.transcript.firstChild.remove();
  ui.transcript.scrollTop = ui.transcript.scrollHeight;
}

function addLog(text, kind) {
  const line = document.createElement("li");
  if (kind) line.className = kind;
  const stamp = new Date().toLocaleTimeString("de-DE", { hour12: false });
  line.innerHTML = `<span class="t"></span><span class="m"></span>`;
  line.querySelector(".t").textContent = stamp;
  line.querySelector(".m").textContent = text;
  ui.log.appendChild(line);
  while (ui.log.children.length > 200) ui.log.firstChild.remove();
  ui.log.scrollTop = ui.log.scrollHeight;
}

function bump(key, by = 1) {
  state[key] += by;
  el(`stat-${key}`).textContent =
    state[key] > 9999 ? `${Math.round(state[key] / 1000)}k` : state[key];
}

/* ---- the event stream ---- */

function connect() {
  const stream = new EventSource(`/events?token=${encodeURIComponent(token)}`);

  stream.onopen = () => {
    setMode("asleep");
    addLog("Verbindung steht");
    refreshPanels();
  };

  stream.onerror = () => {
    setMode("offline");
  };

  stream.onmessage = (message) => {
    let event;
    try { event = JSON.parse(message.data); } catch { return; }
    handle(event);
  };
}

function handle(event) {
  const { kind, text, data } = event;

  switch (kind) {
    case "state":
      setMode(text);
      break;

    case "level":
      // A number between 0 and 1; the core and scope read it every frame.
      state.level = Math.max(0, Math.min(1, parseFloat(text) || 0));
      state.levelAt = performance.now();
      ui.levelBar.style.width = `${Math.round(state.level * 100)}%`;
      break;

    case "wake":
      setMode("listening");
      addLog("Signalwort erkannt");
      break;

    case "heard":
      addTurn("Sie", text, "user");
      bump("turns");
      setMode("thinking");
      break;

    case "thinking":
      setMode("thinking");
      break;

    case "tool_start":
      setMode("working");
      addLog(`${text}(${summarise(data && data.arguments)})`, "tool");
      bump("tools");
      break;

    case "tool_end":
      if (data && data.failed) addLog(`${text} fehlgeschlagen`, "bad");
      break;

    case "speech_chunk":
      setMode("speaking");
      break;

    case "answer":
      addTurn("Jarvis", text, "jarvis");
      setMode("asleep");
      refreshPanels();
      break;

    case "confirm":
      setMode("asking");
      showQuestion(text);
      break;

    case "reminder":
      addTurn("Erinnerung", text, "note");
      addLog(`Erinnerung: ${text}`);
      refreshPanels();
      break;

    case "notice":
      addTurn("Hinweis", text, "note");
      addLog(text);
      break;

    case "error":
      addTurn("Fehler", text, "alert");
      addLog(text + (data && data.detail ? ` — ${data.detail}` : ""), "bad");
      setMode("asleep");
      break;
  }
}

function summarise(args) {
  if (!args) return "";
  return Object.entries(args)
    .slice(0, 2)
    .map(([k, v]) => `${k}=${String(v).slice(0, 22)}`)
    .join(", ");
}

/* ---- confirmation ---- */

function showQuestion(question) {
  state.pendingQuestion = question;
  ui.askQuestion.textContent = question;
  ui.ask.hidden = false;
}

function answer(yes) {
  ui.ask.hidden = true;
  fetch("/api/answer", {
    method: "POST",
    headers,
    body: JSON.stringify({ question: state.pendingQuestion, yes }),
  }).catch(() => {});
  addLog(yes ? "Bestätigt" : "Abgelehnt");
  setMode("asleep");
}

el("ask-yes").onclick = () => answer(true);
el("ask-no").onclick = () => answer(false);

/* ---- typing ---- */

function submit() {
  const text = ui.input.value.trim();
  if (!text) return;
  ui.input.value = "";
  addTurn("Sie", text, "user");
  bump("turns");
  setMode("thinking");
  fetch("/api/say", { method: "POST", headers, body: JSON.stringify({ text }) })
    .catch(() => addTurn("Fehler", "Konnte nicht gesendet werden.", "alert"));
}

ui.send.onclick = submit;
ui.input.onkeydown = (e) => { if (e.key === "Enter") submit(); };

/* ---- panels ---- */

async function refreshPanels() {
  try {
    const response = await fetch(`/api/state?token=${encodeURIComponent(token)}`);
    if (!response.ok) return;
    const snapshot = await response.json();

    el("sys-brain").textContent = snapshot.model || "—";
    el("sys-ears").textContent = snapshot.ears || "—";
    el("sys-voice").textContent = snapshot.voice || "—";
    el("sys-tools").textContent = snapshot.tools ?? "—";
    if (snapshot.tokens) { state.tokens = snapshot.tokens; el("stat-tokens").textContent = snapshot.tokens; }

    fill("tasks", snapshot.tasks);
    fill("reminders", snapshot.reminders);
    fill("events", snapshot.events);
  } catch { /* the panels simply stay as they were */ }
}

function fill(name, items) {
  const list = el(`list-${name}`);
  const count = el(`count-${name}`);
  count.textContent = (items || []).length;
  list.innerHTML = "";

  if (!items || !items.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = { tasks: "nichts offen", reminders: "keine", events: "—" }[name];
    list.appendChild(empty);
    return;
  }

  for (const item of items.slice(0, 12)) {
    const row = document.createElement("li");
    if (item.due) row.classList.add("due");
    const title = document.createElement("span");
    title.textContent = item.title || String(item);
    row.appendChild(title);
    if (item.when) {
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = item.when;
      row.appendChild(when);
    }
    list.appendChild(row);
  }
}

/* ---- clock ---- */

setInterval(() => {
  const now = new Date();
  el("clock-time").textContent = now.toLocaleTimeString("de-DE", { hour12: false });
  el("clock-date").textContent = now.toLocaleDateString("de-DE", {
    weekday: "short", day: "2-digit", month: "2-digit", year: "numeric",
  });
}, 1000);

/* ---- the core ---- */

const core = el("core");
const ctx = core.getContext("2d");
const scope = el("scope");
const sctx = scope.getContext("2d");

function fit(canvas, context) {
  const ratio = window.devicePixelRatio || 1;
  const box = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, box.width * ratio);
  canvas.height = Math.max(1, box.height * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return box;
}

let coreBox = fit(core, ctx);
let scopeBox = fit(scope, sctx);
window.addEventListener("resize", () => {
  coreBox = fit(core, ctx);
  scopeBox = fit(scope, sctx);
});

const MOOD = {
  asleep:    { hue: 189, speed: 0.10, energy: 0.06 },
  listening: { hue: 142, speed: 0.30, energy: 1.00 },
  thinking:  { hue: 43,  speed: 0.85, energy: 0.42 },
  working:   { hue: 43,  speed: 1.15, energy: 0.52 },
  speaking:  { hue: 189, speed: 0.45, energy: 0.66 },
  asking:    { hue: 43,  speed: 0.22, energy: 0.30 },
  offline:   { hue: 0,   speed: 0.04, energy: 0.03 },
};

let phase = 0;
let smooth = 0;     // eased level, so the rings do not jitter
let lastFrame = performance.now();

function draw(now) {
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;

  const mood = MOOD[state.mode] || MOOD.asleep;
  phase += dt * mood.speed;

  // The listening state follows the microphone; the others breathe on their own.
  const target = state.mode === "listening"
    ? state.level
    : mood.energy * (0.55 + 0.45 * Math.sin(now / 900));
  smooth += (target - smooth) * Math.min(dt * 9, 1);

  const w = coreBox.width, h = coreBox.height;
  const cx = w / 2, cy = h / 2;
  const unit = Math.min(w, h) / 2;
  const hue = mood.hue;

  ctx.clearRect(0, 0, w, h);

  // Outer dashed ring, slowly counter-rotating.
  ring(cx, cy, unit * 0.88, -phase * 0.6, hue, 0.4, 48, 1);
  // Segmented arcs: three gaps, the HUD signature.
  arcs(cx, cy, unit * 0.76, phase, hue, 0.6 + smooth * 0.4);
  arcs(cx, cy, unit * 0.70, -phase * 1.7, hue, 0.25 + smooth * 0.3);
  // Tick ring.
  ticks(cx, cy, unit * 0.62, phase * 0.3, hue, 0.5, 72);
  // Inner ring that swells with the level.
  const swell = unit * (0.40 + smooth * 0.14);
  ring(cx, cy, swell, phase * 1.4, hue, 0.75 + smooth * 0.25, 0, 1.6);
  ring(cx, cy, swell * 0.82, -phase * 2.2, hue, 0.3 + smooth * 0.4, 24, 1);
  // Core glow.
  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, unit * (0.38 + smooth * 0.22));
  glow.addColorStop(0, `hsla(${hue}, 90%, 66%, ${0.3 + smooth * 0.34})`);
  glow.addColorStop(0.6, `hsla(${hue}, 90%, 58%, ${0.08 + smooth * 0.14})`);
  glow.addColorStop(1, `hsla(${hue}, 90%, 55%, 0)`);
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, unit * (0.38 + smooth * 0.22), 0, Math.PI * 2);
  ctx.fill();

  drawScope();
  requestAnimationFrame(draw);
}

function ring(cx, cy, r, rotation, hue, alpha, dashes, width) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rotation);
  ctx.beginPath();
  ctx.arc(0, 0, r, 0, Math.PI * 2);
  ctx.strokeStyle = `hsla(${hue}, 85%, 64%, ${alpha})`;
  ctx.lineWidth = width;
  if (dashes) ctx.setLineDash([r * 0.03, r * 0.05]);
  ctx.stroke();
  ctx.restore();
}

function arcs(cx, cy, r, rotation, hue, alpha) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rotation);
  ctx.lineWidth = 2.6;
  ctx.lineCap = "round";
  ctx.shadowBlur = 12;
  ctx.shadowColor = `hsla(${hue}, 90%, 60%, 0.6)`;
  const spans = [[0, 1.5], [2.1, 3.3], [3.9, 5.6]];
  for (const [from, to] of spans) {
    ctx.beginPath();
    ctx.arc(0, 0, r, from, to);
    ctx.strokeStyle = `hsla(${hue}, 85%, 62%, ${alpha})`;
    ctx.stroke();
  }
  ctx.restore();
}

function ticks(cx, cy, r, rotation, hue, alpha, count) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rotation);
  ctx.strokeStyle = `hsla(${hue}, 85%, 64%, ${alpha})`;
  ctx.lineWidth = 1.2;
  for (let i = 0; i < count; i++) {
    const long = i % 6 === 0;
    const a = (i / count) * Math.PI * 2;
    const inner = r * (long ? 0.93 : 0.96);
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
    ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
    ctx.stroke();
  }
  ctx.restore();
}

function drawScope() {
  const w = scopeBox.width, h = scopeBox.height;
  if (!w || !h) return;

  // A stalled stream must decay to silence rather than hold its last value,
  // which would draw a solid block and read as continuous sound.
  if (performance.now() - state.levelAt > 400) {
    state.level *= 0.82;
    if (state.level < 0.01) state.level = 0;
    ui.levelBar.style.width = `${Math.round(state.level * 100)}%`;
  }

  state.history.push(state.level);
  while (state.history.length > 72) state.history.shift();

  const mid = h / 2;
  const n = state.history.length;
  sctx.clearRect(0, 0, w, h);

  // The envelope of the signal, mirrored about the centre and filled -- what
  // a recording looks like, rather than a zig-zag between alternating points.
  const top = (i, v) => mid - v * mid * 0.88;
  const bottom = (i, v) => mid + v * mid * 0.88;

  sctx.beginPath();
  state.history.forEach((v, i) => {
    const x = (i / Math.max(n - 1, 1)) * w;
    i ? sctx.lineTo(x, top(i, v)) : sctx.moveTo(x, top(i, v));
  });
  for (let i = n - 1; i >= 0; i--) {
    const x = (i / Math.max(n - 1, 1)) * w;
    sctx.lineTo(x, bottom(i, state.history[i]));
  }
  sctx.closePath();

  const fill = sctx.createLinearGradient(0, 0, w, 0);
  fill.addColorStop(0, "rgba(56, 220, 242, 0.05)");
  fill.addColorStop(1, "rgba(56, 220, 242, 0.34)");
  sctx.fillStyle = fill;
  sctx.fill();

  sctx.strokeStyle = "rgba(56, 220, 242, 0.85)";
  sctx.lineWidth = 1.1;
  sctx.stroke();

  sctx.strokeStyle = "rgba(56, 220, 242, 0.18)";
  sctx.lineWidth = 1;
  sctx.beginPath();
  sctx.moveTo(0, mid);
  sctx.lineTo(w, mid);
  sctx.stroke();
}

/* ---- go ---- */

setMode("offline");
connect();
requestAnimationFrame(draw);
setInterval(refreshPanels, 30000);
ui.input.focus();
