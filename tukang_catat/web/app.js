"use strict";

const $ = (id) => document.getElementById(id);
const COLORS = ["#1f6feb", "#2da44e", "#bf3989", "#d4790b", "#8250df", "#0a8a8a",
                "#cf222e", "#6e7781", "#9a6700", "#1b7c83", "#a475f9", "#57606a"];
const CHUNK = 1600; // 100 ms pada 16 kHz

const st = {
  sid: null, ws: null, ctx: null, stream: null,
  buf: new Float32Array(CHUNK), bufLen: 0, samplesSent: 0,
  participants: [], speakers: [], segments: new Map(), recording: false,
};

// ------------------------------------------------------------------ util
const fmt = (sec) => {
  sec = Math.floor(sec);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(sec / 3600))}:${p(Math.floor(sec % 3600 / 60))}:${p(sec % 60)}`;
};
const color = (id) => (id == null ? "#6e7781" : COLORS[(id - 1) % COLORS.length]);
const el = (tag, props = {}, ...kids) => {
  const e = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => e.append(k));
  return e;
};
const send = (obj) => st.ws && st.ws.readyState === 1 && st.ws.send(JSON.stringify(obj));

// -------------------------------------------------------------- persiapan
$("in-threshold").addEventListener("input", (e) => {
  $("th-value").textContent = Number(e.target.value).toFixed(2);
});

$("btn-start").addEventListener("click", async () => {
  const btn = $("btn-start");
  btn.disabled = true;
  $("setup-error").hidden = true;
  try {
    st.participants = $("in-participants").value.split("\n").map((s) => s.trim()).filter(Boolean);
    const res = await fetch("/api/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: $("in-title").value, participants: st.participants,
                             threshold: Number($("in-threshold").value) }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    st.sid = (await res.json()).sid;
    await startAudio();
    openSocket();
    $("view-setup").hidden = true;
    $("view-live").hidden = false;
    $("rec-status").hidden = false;
    st.recording = true;
    updateNames();
  } catch (err) {
    $("setup-error").textContent = "Gagal memulai: " + err.message;
    $("setup-error").hidden = false;
    btn.disabled = false;
  }
});

async function startAudio() {
  st.stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: true },
  });
  st.ctx = new AudioContext();
  await st.ctx.audioWorklet.addModule("/static/recorder-worklet.js");
  const src = st.ctx.createMediaStreamSource(st.stream);
  const node = new AudioWorkletNode(st.ctx, "recorder-processor");
  node.port.onmessage = ({ data }) => {
    $("meter-fill").style.width = Math.min(100, data.rms * 400) + "%";
    pushSamples(data.samples);
  };
  src.connect(node);
}

function pushSamples(samples) {
  let i = 0;
  while (i < samples.length) {
    const n = Math.min(CHUNK - st.bufLen, samples.length - i);
    st.buf.set(samples.subarray(i, i + n), st.bufLen);
    st.bufLen += n;
    i += n;
    if (st.bufLen === CHUNK) {
      if (st.ws && st.ws.readyState === 1 && st.recording) {
        st.ws.send(st.buf.slice().buffer);
        st.samplesSent += CHUNK;
        $("timer").textContent = fmt(st.samplesSent / 16000);
      }
      st.bufLen = 0;
    }
  }
}

function openSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  st.ws = new WebSocket(`${proto}://${location.host}/ws/${st.sid}`);
  st.ws.onmessage = ({ data }) => handle(JSON.parse(data));
  st.ws.onclose = () => {
    if (st.recording) alertBar("Koneksi ke server terputus. Rekaman yang sudah masuk tetap tersimpan.");
  };
}

function alertBar(text) {
  $("progress-msg").textContent = text;
  $("transcribe-panel").hidden = false;
}

// ------------------------------------------------------------ event server
function handle(msg) {
  if (msg.type === "state") {
    st.speakers = msg.speakers;
    st.segments = new Map(msg.segments.map((s) => [s.id, s]));
    renderTimeline(true);
    renderSpeakers();
    renderQuestions();
    updateNames();
  } else if (msg.type === "segment") {
    st.segments.set(msg.segment.id, msg.segment);
    upsertRow(msg.segment);
    bumpSpeaker(msg.segment.speaker_id);
  } else if (msg.type === "new_speaker") {
    if (!st.speakers.find((s) => s.id === msg.speaker_id)) {
      st.speakers.push({ id: msg.speaker_id, name: null, label: `Pembicara ${msg.speaker_id}`,
                         count: 0, first: msg.start });
    }
    renderSpeakers();
    renderQuestions(msg);
  }
}

function bumpSpeaker(id) {
  const spk = st.speakers.find((s) => s.id === id);
  if (spk) { spk.count += 1; renderSpeakers(); }
}

// ----------------------------------------------------------- garis waktu
function renderTimeline(full) {
  const ol = $("timeline");
  if (full) ol.replaceChildren();
  [...st.segments.values()].sort((a, b) => a.id - b.id).forEach(upsertRow);
}

function upsertRow(seg) {
  const ol = $("timeline");
  let li = ol.querySelector(`li[data-id="${seg.id}"]`);
  const atBottom = ol.scrollTop + ol.clientHeight >= ol.scrollHeight - 30;
  const spk = st.speakers.find((s) => s.id === seg.speaker_id);
  const chip = el("button", { className: "chip", textContent: seg.speaker,
                              title: "Klik untuk mengoreksi pembicara" });
  chip.style.background = color(seg.speaker_id);
  if (seg.speaker_id == null) chip.classList.add("unknown");
  else if (spk && !spk.name) chip.classList.add("unnamed");
  chip.onclick = () => editInline(chip, seg.speaker === "?" ? "" : seg.speaker, (name) =>
    send({ type: "reassign", segment_id: seg.id, name }));
  const row = [
    el("span", { className: "t", textContent: fmt(seg.start) }),
    el("span", {}, chip),
    el("span", { className: "dur", textContent: `${(seg.end - seg.start).toFixed(1)} dtk` }),
  ];
  if (li) li.replaceChildren(...row);
  else {
    li = el("li", {}, ...row);
    li.dataset.id = seg.id;
    ol.append(li);
    if (atBottom) ol.scrollTop = ol.scrollHeight;
  }
}

function editInline(anchor, value, onSave) {
  const input = el("input", { value, placeholder: "Nama pembicara" });
  input.setAttribute("list", "names");
  input.style.cssText = "display:inline-block;width:180px;margin:0;padding:3px 8px";
  anchor.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim()) onSave(input.value.trim());
    else input.replaceWith(anchor);
  };
  input.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
  input.onblur = () => finish(true);
}

// ------------------------------------------------------------- pertanyaan
function renderQuestions(newEv) {
  const box = $("questions");
  const pending = st.speakers.filter((s) => !s.name &&
    (s.count > 0 || (newEv && newEv.speaker_id === s.id)));
  // hapus kartu yang sudah terjawab / tergabung
  box.querySelectorAll(".qcard").forEach((card) => {
    if (!pending.find((s) => String(s.id) === card.dataset.sid)) card.remove();
  });
  pending.forEach((spk) => {
    if (box.querySelector(`[data-sid="${spk.id}"]`)) return;
    const start = newEv && newEv.speaker_id === spk.id ? newEv.start : firstSeen(spk.id);
    const input = el("input", { placeholder: "Ketik atau pilih nama" });
    input.setAttribute("list", "names");
    const save = () => input.value.trim() &&
      send({ type: "label", speaker_id: spk.id, name: input.value.trim() });
    input.onkeydown = (e) => e.key === "Enter" && save();
    const card = el("div", { className: "qcard" },
      el("div", { className: "q-title",
                  textContent: `Suara baru terdeteksi (${fmt(start ?? 0)}), sementara "${spk.label}"` }),
      el("audio", { controls: true, preload: "none",
                    src: `/api/sessions/${st.sid}/clip/${spk.id}` }),
      el("div", { className: "row" }, input,
         el("button", { className: "btn primary", textContent: "Simpan", onclick: save })),
      el("span", { className: "hint",
                   textContent: "Jika ini orang yang sudah dikenal, pilih namanya: keduanya akan digabung." }));
    card.dataset.sid = spk.id;
    box.prepend(card);
  });
  box.querySelector(".empty").hidden = box.querySelectorAll(".qcard").length > 0;
  const n = box.querySelectorAll(".qcard").length;
  $("q-count").textContent = n;
  $("q-count").className = "badge" + (n ? "" : " zero");
}

function firstSeen(spkId) {
  for (const s of [...st.segments.values()].sort((a, b) => a.id - b.id))
    if (s.speaker_id === spkId) return s.start;
  return null;
}

// -------------------------------------------------------------- pembicara
function renderSpeakers() {
  const ul = $("speakers");
  ul.replaceChildren();
  st.speakers.filter((s) => s.count > 0 || s.name).forEach((spk) => {
    const chip = el("span", { className: "chip" + (spk.name ? "" : " unnamed"), textContent: spk.label });
    chip.style.background = color(spk.id);
    const btn = el("button", { className: "btn ghost", textContent: "Ganti nama" });
    btn.onclick = () => editInline(btn, spk.name || "", (name) =>
      send({ type: "label", speaker_id: spk.id, name }));
    ul.append(el("li", {}, el("span", {}, chip, " ",
      el("span", { className: "count", textContent: `${spk.count}× bicara` })), btn));
  });
}

function updateNames() {
  const names = new Set([...st.participants, ...st.speakers.filter((s) => s.name).map((s) => s.name)]);
  $("names").replaceChildren(...[...names].map((n) => el("option", { value: n })));
}

// ------------------------------------------------------------------ stop
$("btn-stop").addEventListener("click", () => {
  if (!st.recording) return;
  st.recording = false;
  if (st.bufLen && st.ws && st.ws.readyState === 1) st.ws.send(st.buf.slice(0, st.bufLen).buffer);
  send({ type: "stop" });
  st.stream.getTracks().forEach((t) => t.stop());
  st.ctx.close();
  $("rec-status").classList.add("stopped");
  $("btn-stop").hidden = true;
  $("meter-fill").style.width = "0";
  $("transcribe-panel").hidden = false;
});

// ------------------------------------------------------------ transkripsi
$("btn-transcribe").addEventListener("click", async () => {
  const unnamed = st.speakers.filter((s) => !s.name && s.count > 0).length;
  $("btn-transcribe").disabled = true;
  $("progress").hidden = false;
  $("progress-msg").textContent = unnamed
    ? `${unnamed} pembicara belum diberi nama dan akan ditulis sebagai "Pembicara N".` : "Memulai...";
  const res = await fetch(`/api/sessions/${st.sid}/transcribe`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: $("in-model").value, language: $("in-lang").value }),
  });
  if (!res.ok) {
    $("progress-msg").textContent = "Gagal: " + ((await res.json()).detail || res.statusText);
    $("btn-transcribe").disabled = false;
    return;
  }
  poll();
});

async function poll() {
  const job = await (await fetch(`/api/sessions/${st.sid}/transcribe`)).json();
  $("progress-fill").style.width = Math.round((job.progress || 0) * 100) + "%";
  $("progress-msg").textContent = job.message || "";
  if (job.status === "running") return setTimeout(poll, 1000);
  if (job.status === "error") {
    $("progress-msg").textContent = "Gagal: " + job.error;
    $("btn-transcribe").disabled = false;
    return;
  }
  $("progress-msg").textContent = "Transkrip selesai. Berikan berkas .md ini ke Claude untuk dibuatkan notula.";
  $("dl-md").href = `/api/sessions/${st.sid}/files/transkrip.md`;
  $("dl-wav").href = `/api/sessions/${st.sid}/files/audio.wav`;
  $("downloads").hidden = false;
  $("btn-transcribe").disabled = false;
  $("btn-transcribe").textContent = "Transkripsi ulang";
}

$("btn-copy").addEventListener("click", async () => {
  const text = await (await fetch(`/api/sessions/${st.sid}/files/transkrip.md`)).text();
  await navigator.clipboard.writeText(text);
  $("btn-copy").textContent = "Tersalin ✓";
  setTimeout(() => ($("btn-copy").textContent = "Salin transkrip"), 2000);
});
