"use strict";

const $ = (id) => document.getElementById(id);
const COLORS = ["#1f6feb", "#2da44e", "#bf3989", "#d4790b", "#8250df", "#0a8a8a",
                "#cf222e", "#6e7781", "#9a6700", "#1b7c83", "#a475f9", "#57606a"];
const CHUNK = 1600; // 100 ms pada 16 kHz

const st = {
  sid: null, ws: null, ctx: null, stream: null,
  buf: new Float32Array(CHUNK), bufLen: 0, samplesSent: 0,
  participants: [], speakers: [], segments: new Map(), recording: false,
  live: { speaking: false, speaker_id: null }, pending: undefined, // undefined = tidak ada
  canUndo: false, toastTimer: null,
};

// ------------------------------------------------------------------ util
const fmt = (sec) => {
  sec = Math.max(0, Math.floor(sec));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(sec / 3600))}:${p(Math.floor(sec % 3600 / 60))}:${p(sec % 60)}`;
};
const fmtShort = (sec) => {
  sec = Math.floor(sec);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
};
const color = (id) => (id == null ? "#6e7781" : COLORS[(id - 1) % COLORS.length]);
const el = (tag, props = {}, ...kids) => {
  const e = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => e.append(k));
  return e;
};
const send = (obj) => st.ws && st.ws.readyState === 1 && st.ws.send(JSON.stringify(obj));
const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);

// ------------------------------------------------------------------ tema
function applyThemeLabel() {
  const dark = document.documentElement.dataset.theme === "malam";
  $("btn-theme").textContent = dark ? "☀ Siang" : "☾ Malam";
}
$("btn-theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "malam" ? "siang" : "malam";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("tema", next);
  applyThemeLabel();
});
applyThemeLabel();

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
    $("btn-stop").hidden = false;
    st.recording = true;
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
    const level = Math.min(100, Math.sqrt(data.rms) * 180);
    $("vu-fill").style.width = (100 - level) + "%";
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
    if (st.recording) toast("Koneksi ke server terputus. Rekaman yang sudah masuk tetap tersimpan.");
  };
}

// ------------------------------------------------------------ event server
function handle(msg) {
  switch (msg.type) {
    case "state":
      st.speakers = msg.speakers;
      st.participants = msg.participants;
      st.canUndo = msg.can_undo;
      st.segments = new Map(msg.segments.map((s) => [s.id, s]));
      renderTimeline();
      renderConsole();
      renderQuestions();
      updateNames();
      break;
    case "segment":
      st.segments.set(msg.segment.id, msg.segment);
      upsertRow(msg.segment);
      renderConsole();
      break;
    case "live":
      st.live = { speaking: msg.speaking, speaker_id: msg.speaker_id };
      renderConsole();
      break;
    case "new_speaker":
      if (!st.speakers.find((s) => s.id === msg.speaker_id)) {
        st.speakers.push({ id: msg.speaker_id, name: null, label: `Pembicara ${msg.speaker_id}`,
                           count: 0, talk_sec: 0 });
      }
      renderConsole();
      renderQuestions(msg);
      break;
    case "override_pending":
      st.pending = msg.speaker_id; // null = orang baru
      renderConsole();
      toast("Mendengarkan sebentar untuk mencari awal kalimatnya…");
      break;
    case "override":
      st.pending = undefined;
      renderConsole();
      flashSince(msg.from);
      toast(`✓ ${msg.label}: sejak ${fmt(msg.from)} (${msg.moved_sec} dtk dipindahkan)`, true);
      break;
  }
}

// ------------------------------------------------------------------ konsol
function talkTimes() {
  const t = {};
  for (const s of st.segments.values())
    if (s.speaker_id != null) t[s.speaker_id] = (t[s.speaker_id] || 0) + (s.end - s.start);
  return t;
}

function consoleSpeakers() {
  // Peserta bernama dulu (urutan daftar), lalu suara tanpa nama yang terdeteksi.
  return st.speakers.slice().sort((a, b) => (a.name ? 0 : 1) - (b.name ? 0 : 1) || a.id - b.id);
}

function renderConsole() {
  const box = $("console");
  // Jangan gambar ulang saat notulis sedang mengetik nama di salah satu channel.
  if (box.querySelector("input:focus:not(#in-add)")) return;
  const talk = talkTimes();
  const total = Math.max(1, ...Object.values(talk));
  const list = consoleSpeakers();
  const focused = document.activeElement && document.activeElement.id === "in-add" ?
    document.activeElement.value : null;

  const strips = list.map((spk, i) => {
    const sec = talk[spk.id] || 0;
    const fill = el("div");
    fill.style.height = Math.round((sec / total) * 100) + "%";
    fill.style.background = color(spk.id);
    const name = el("div", { className: "scribble" + (spk.name ? "" : " unnamed"),
                             textContent: spk.label, title: spk.label });
    name.style.background = color(spk.id);
    const edit = el("button", { className: "edit", textContent: "✎", title: "Ganti nama" });
    edit.onclick = (e) => { e.stopPropagation(); renameSpeaker(spk, name); };
    // div (bukan <button>) karena di dalamnya ada tombol ✎
    const strip = el("div", { className: "strip", tabIndex: 0, role: "button",
                              title: `Sekarang yang bicara: ${spk.label}` },
      el("span", { className: "key", textContent: i < 9 ? String(i + 1) : "" }), edit,
      el("div", { className: "led" }),
      el("div", { className: "meter" }, fill),
      el("div", { className: "talk mono", textContent: fmtShort(sec) }),
      name);
    if (st.live.speaking && st.live.speaker_id === spk.id) strip.classList.add("live");
    if (st.pending === spk.id) strip.classList.add("pending");
    strip.onclick = () => pressChannel(spk.id);
    return strip;
  });

  const newStrip = el("button", { className: "strip new-person",
                                  title: "Orang lain yang belum dikenal sedang bicara (N)" },
    el("span", { className: "key", textContent: "N" }),
    el("div", { className: "led" }),
    el("div", { className: "plus", textContent: "+" }),
    el("div", { className: "lbl", textContent: "Orang baru" }));
  if (st.pending === null) newStrip.classList.add("pending");
  newStrip.onclick = () => pressChannel(null);

  const input = el("input", { id: "in-add", placeholder: "Nama…" });
  input.setAttribute("list", "names");
  const addBtn = el("button", { className: "btn", textContent: "Tambah" });
  const add = () => {
    const v = input.value.trim();
    if (v) send({ type: "add_participant", name: v });
    input.value = "";
    input.blur();
  };
  addBtn.onclick = add;
  input.onkeydown = (e) => {
    if (e.key === "Enter") add();
    if (e.key === "Escape") input.blur();
  };
  const addStrip = el("div", { className: "strip add" },
    el("span", { className: "key", textContent: "P" }), input, addBtn,
    el("div", { className: "lbl", textContent: "Tambah peserta" }));

  box.replaceChildren(...strips, newStrip, addStrip);
  if (focused !== null) { input.value = focused; input.focus(); }
}

function pressChannel(target) {
  if (!st.recording) return;
  send({ type: "override", target });
  st.pending = target;
  renderConsole();
}

function renameSpeaker(spk, anchor) {
  editInline(anchor, spk.name || "", (name) => send({ type: "label", speaker_id: spk.id, name }));
}

// --------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  if (!st.sid || typing()) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    doUndo();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (/^[1-9]$/.test(e.key)) {
    const spk = consoleSpeakers()[Number(e.key) - 1];
    if (spk) { e.preventDefault(); pressChannel(spk.id); }
  } else if (e.key.toLowerCase() === "n") {
    e.preventDefault();
    pressChannel(null);
  } else if (e.key.toLowerCase() === "p") {
    e.preventDefault();
    $("in-add")?.focus();
  }
});

function doUndo() {
  if (!st.canUndo) return toast("Tidak ada koreksi untuk dibatalkan.");
  send({ type: "undo" });
  toast("Koreksi terakhir dibatalkan.");
}

function toast(text, withUndo = false) {
  const t = $("toast");
  t.replaceChildren(el("span", { textContent: text }));
  if (withUndo) {
    const b = el("button", { className: "btn", textContent: "Batalkan (Ctrl+Z)" });
    b.onclick = doUndo;
    t.append(b);
  }
  t.hidden = false;
  clearTimeout(st.toastTimer);
  st.toastTimer = setTimeout(() => (t.hidden = true), withUndo ? 9000 : 4000);
}

// ----------------------------------------------------------- garis waktu
function renderTimeline() {
  const ol = $("timeline");
  const atBottom = ol.scrollTop + ol.clientHeight >= ol.scrollHeight - 30;
  ol.replaceChildren(...[...st.segments.values()].sort((a, b) => a.start - b.start).map(rowFor));
  if (atBottom) ol.scrollTop = ol.scrollHeight;
}

function rowFor(seg) {
  const spk = st.speakers.find((s) => s.id === seg.speaker_id);
  const chip = el("button", { className: "chip", textContent: seg.speaker,
                              title: "Klik untuk mengoreksi pembicara" });
  chip.style.background = color(seg.speaker_id);
  if (seg.speaker_id == null) chip.classList.add("unknown");
  else if (spk && !spk.name) chip.classList.add("unnamed");
  chip.onclick = () => editInline(chip, seg.speaker === "?" ? "" : seg.speaker, (name) =>
    send({ type: "reassign", segment_id: seg.id, name }));
  const who = el("span", {}, chip);
  if (seg.manual) who.append(el("span", { className: "manual", textContent: "✋",
                                          title: "Dikoreksi notulis" }));
  const li = el("li", {},
    el("span", { className: "t mono", textContent: fmt(seg.start) }), who,
    el("span", { className: "dur", textContent: `${(seg.end - seg.start).toFixed(1)} dtk` }));
  li.dataset.id = seg.id;
  li.dataset.start = seg.start;
  return li;
}

function upsertRow(seg) {
  const ol = $("timeline");
  const atBottom = ol.scrollTop + ol.clientHeight >= ol.scrollHeight - 30;
  const row = rowFor(seg);
  const old = ol.querySelector(`li[data-id="${seg.id}"]`);
  if (old) old.replaceWith(row);
  else {
    const after = [...ol.children].find((li) => Number(li.dataset.start) > seg.start);
    ol.insertBefore(row, after || null);
  }
  if (atBottom) ol.scrollTop = ol.scrollHeight;
}

function flashSince(t) {
  $("timeline").querySelectorAll("li").forEach((li) => {
    if (Number(li.dataset.start) >= t - 0.05) {
      li.classList.remove("flash");
      void li.offsetWidth;
      li.classList.add("flash");
    }
  });
}

function editInline(anchor, value, onSave) {
  const input = el("input", { value, placeholder: "Nama pembicara" });
  input.setAttribute("list", "names");
  input.style.cssText = "display:inline-block;width:160px;margin:0;padding:3px 8px";
  input.onclick = (e) => e.stopPropagation();
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
  input.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  };
  input.onblur = () => finish(true);
}

// ------------------------------------------------------------- pertanyaan
function renderQuestions(newEv) {
  const box = $("questions");
  const pending = st.speakers.filter((s) => !s.name &&
    (s.count > 0 || s.talk_sec > 0 || (newEv && newEv.speaker_id === s.id) ||
     box.querySelector(`[data-sid="${s.id}"]`)));
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
    input.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") save(); };
    const card = el("div", { className: "qcard" },
      el("div", { className: "q-title",
                  textContent: `Suara baru (${fmt(start ?? 0)}), sementara "${spk.label}"` }),
      el("audio", { controls: true, preload: "none",
                    src: `/api/sessions/${st.sid}/clip/${spk.id}?v=${Date.now()}` }),
      el("div", { className: "row" }, input,
         el("button", { className: "btn primary", textContent: "Simpan", onclick: save })),
      el("span", { className: "hint",
                   textContent: "Jika ini orang yang sudah dikenal, pilih namanya: keduanya akan digabung." }));
    card.dataset.sid = spk.id;
    box.prepend(card);
  });
  const n = box.querySelectorAll(".qcard").length;
  box.querySelector(".empty").hidden = n > 0;
  $("q-count").textContent = n;
  $("q-count").className = "badge" + (n ? "" : " zero");
}

function firstSeen(spkId) {
  for (const s of [...st.segments.values()].sort((a, b) => a.start - b.start))
    if (s.speaker_id === spkId) return s.start;
  return null;
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
  st.live = { speaking: false, speaker_id: null };
  st.pending = undefined;
  renderConsole();
  $("rec-status").classList.add("stopped");
  $("btn-stop").hidden = true;
  $("vu-fill").style.width = "100%";
  $("transcribe-panel").hidden = false;
});

// ------------------------------------------------------------ transkripsi
$("btn-transcribe").addEventListener("click", async () => {
  const unnamed = st.speakers.filter((s) => !s.name && (talkTimes()[s.id] || 0) > 0).length;
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
