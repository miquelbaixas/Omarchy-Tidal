// Barra de reproduccion, cola, selector de altavoz y vista "sonando ahora"
import { state, on, post, get, act, isFav, toggleFav, toast } from "./core.js";
import { esc, fmtTime, artistLinks, explicitBadge, closeOverlays, trackMenu, refreshPlaying } from "./ui.js";
import { icon } from "./icons.js";

const $ = (id) => document.getElementById(id);
let dragging = null; // slider que se esta arrastrando

// ---------------------------------------------------------------- progreso
export function currentMs() {
  const p = state.player;
  if (!p) return 0;
  if (p.state !== "PLAYING") return p.progress || 0;
  const elapsed = Date.now() - p.progress_at * 1000;
  return Math.min((p.progress || 0) + Math.max(0, elapsed), p.duration || Infinity);
}

function durationMs() {
  const p = state.player;
  return p?.duration || (p?.current?.duration || 0) * 1000;
}

// ------------------------------------------------------------------ slider
function makeSlider(el, { onInput, onCommit }) {
  const pos = (ev) => {
    const r = el.getBoundingClientRect();
    return Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
  };
  el.addEventListener("pointerdown", (ev) => {
    el.setPointerCapture(ev.pointerId);
    dragging = el;
    el.classList.add("drag");
    onInput?.(pos(ev));
  });
  el.addEventListener("pointermove", (ev) => { if (dragging === el) onInput?.(pos(ev)); });
  el.addEventListener("pointerup", (ev) => {
    if (dragging !== el) return;
    dragging = null;
    el.classList.remove("drag");
    onCommit?.(pos(ev));
  });
}

function setSlider(el, frac) {
  frac = Math.min(1, Math.max(0, frac || 0));
  el.querySelector(".fill").style.width = `${frac * 100}%`;
  el.querySelector(".knob").style.left = `${frac * 100}%`;
}

const sliderHTML = (cls) => `<div class="slider ${cls}"><div class="track"><div class="fill"></div></div><div class="knob"></div></div>`;

// ------------------------------------------------------------- barra inferior
export function mountPlayerBar() {
  const bar = $("playerbar");
  bar.innerHTML = `
    <div class="pb-left"><img class="art" id="pb-art" alt="" hidden><div class="meta">
      <div class="title" id="pb-title"></div><div class="artist" id="pb-artist"></div><div class="from" id="pb-from"></div></div>
      <button class="icon-btn" id="pb-fav" hidden data-act="fav" data-kind="track"></button></div>
    <div class="pb-center">
      <div class="controls">
        <button class="ctl" id="pb-shuffle" title="Aleatorio">${icon("shuffle")}</button>
        <button class="ctl" id="pb-prev" title="Anterior">${icon("prev")}</button>
        <button class="ctl play" id="pb-play" title="Reproducir">${icon("play")}</button>
        <button class="ctl" id="pb-next" title="Siguiente">${icon("next")}</button>
        <button class="ctl" id="pb-repeat" title="Repetir">${icon("repeat")}</button>
      </div>
      <div class="progress"><span class="l" id="pb-pos">0:00</span>${sliderHTML("seek")}<span class="r" id="pb-dur">0:00</span></div>
    </div>
    <div class="pb-right">
      <span id="pb-quality"></span>
      <button class="ctl" id="pb-lyrics" title="Letra y sonando ahora">${icon("lyrics")}</button>
      <button class="ctl" id="pb-queue" title="Cola de reproducción">${icon("queue")}</button>
      <button class="device-btn" id="pb-device" title="Elegir altavoz">${icon("connect")}<span></span></button>
      <button class="ctl" id="pb-mute" title="Silenciar">${icon("volume")}</button>
      ${sliderHTML("vol")}
    </div>`;

  $("pb-play").onclick = () => act(post("/player/toggle"));
  $("pb-next").onclick = () => act(post("/player/next"));
  $("pb-prev").onclick = () => act(post("/player/previous"));
  $("pb-shuffle").onclick = () => act(post("/player/shuffle", { on: !state.player?.shuffle }));
  $("pb-repeat").onclick = () => {
    const next = { OFF: "ALL", ALL: "ONE", ONE: "OFF" }[state.player?.repeat || "OFF"];
    act(post("/player/repeat", { mode: next }));
  };
  $("pb-queue").onclick = toggleQueue;
  $("pb-lyrics").onclick = () => toggleNowPlaying();
  $("pb-art").onclick = () => toggleNowPlaying();
  $("pb-device").onclick = (ev) => openDevices(ev.currentTarget);
  $("pb-mute").onclick = () => act(post("/player/mute", { mute: !state.player?.muted }));

  const seek = bar.querySelector(".slider.seek");
  makeSlider(seek, {
    onInput: (f) => { setSlider(seek, f); $("pb-pos").textContent = fmtTime((f * durationMs()) / 1000); },
    onCommit: (f) => act(post("/player/seek", { ms: Math.round(f * durationMs()) })),
  });
  const vol = bar.querySelector(".slider.vol");
  let volTimer = null;
  makeSlider(vol, {
    onInput: (f) => {
      setSlider(vol, f);
      clearTimeout(volTimer);
      volTimer = setTimeout(() => post("/player/volume", { level: f }).catch(() => {}), 120);
    },
    onCommit: (f) => { clearTimeout(volTimer); act(post("/player/volume", { level: f })); },
  });

  on("player", renderPlayer);
  on("favorites", () => renderPlayer(state.player));
  on("devices", () => renderPlayer(state.player));
  requestAnimationFrame(tick);
}

let lastTrackId = null;
let lastError = null;
let wasTaken = false;
function renderPlayer(p) {
  if (!p) return;
  if (p.error && p.error !== lastError) toast(p.error, true);
  lastError = p.error;
  if (p.taken_over && !wasTaken) toast(`Otro dispositivo está usando ${p.device?.name || "el altavoz"}`);
  wasTaken = !!p.taken_over;
  const t = p.current;
  const art = $("pb-art");
  if (t) {
    if (art.getAttribute("src") !== (t.album?.cover || "")) art.src = t.album?.cover || "";
    art.hidden = !t.album?.cover;
    $("pb-title").innerHTML = `<a class="link" href="${t.album?.id ? `#/album/${esc(t.album.id)}` : "#"}">${esc(t.title)}</a>${explicitBadge(t)}`;
    $("pb-artist").innerHTML = artistLinks(t.artists);
    $("pb-from").innerHTML = p.taken_over
      ? `<span style="color:var(--max)">Controlado desde otro dispositivo · pulsa play para recuperarlo</span>`
      : p.context?.title ? `Reproduciendo desde: ${contextLink(p.context)}` : "";
    const fav = $("pb-fav");
    fav.hidden = !t.id;
    fav.dataset.id = t.id;
    const isF = isFav("track", t.id);
    fav.classList.toggle("on", isF);
    fav.innerHTML = icon(isF ? "heartFill" : "heart");
  } else {
    art.hidden = true;
    $("pb-title").textContent = "";
    $("pb-artist").textContent = state.player?.device ? "Elige algo para escuchar" : "";
    $("pb-from").textContent = "";
    $("pb-fav").hidden = true;
  }
  const playing = p.state === "PLAYING" || p.state === "BUFFERING";
  $("pb-play").innerHTML = icon(playing ? "pause" : "play");
  $("pb-play").title = playing ? "Pausa" : "Reproducir";
  $("pb-shuffle").classList.toggle("on", !!p.shuffle);
  const rep = $("pb-repeat");
  rep.classList.toggle("on", p.repeat !== "OFF");
  rep.innerHTML = icon("repeat") + (p.repeat === "ONE" ? `<span class="one">1</span>` : "");
  rep.title = { OFF: "Repetir", ALL: "Repetir todo", ONE: "Repetir canción" }[p.repeat || "OFF"];

  // calidad real que esta sonando
  const a = p.audio;
  const q = $("pb-quality");
  if (a && t) {
    const hi = a.quality === "HI_RES_LOSSLESS" || (a.bit_depth || 0) > 16;
    const khz = a.sample_rate ? `${(a.sample_rate / 1000).toString().replace(".", ",")} kHz` : "";
    q.innerHTML = `<span class="quality-pill ${hi ? "" : "high"}" title="${esc(`${a.codec || ""} ${a.bit_depth || ""}-bit ${khz}`)}">${hi ? "MAX" : "HIGH"} ${a.bit_depth ? `${a.bit_depth}-BIT` : ""} ${khz}</span>`;
  } else q.innerHTML = "";

  // altavoz
  const dev = $("pb-device");
  const isLocal = p.device?.id === "local";
  if (dev.dataset.local !== String(isLocal)) {
    dev.dataset.local = String(isLocal);
    dev.innerHTML = `${icon(isLocal ? "laptop" : "connect")}<span></span>`;
  }
  dev.querySelector("span").textContent = p.device ? p.device.name : "Elegir altavoz";
  dev.classList.toggle("active", !!(p.device && p.connected && !p.taken_over));
  dev.classList.toggle("warn", !p.device || !!p.taken_over);
  dev.title = p.taken_over ? "Otro dispositivo está usando este altavoz" : "Elegir altavoz";

  // volumen
  if (dragging?.classList.contains("vol") !== true && p.volume != null) setSlider(document.querySelector(".slider.vol"), p.muted ? 0 : p.volume);
  $("pb-mute").innerHTML = icon(p.muted || p.volume === 0 ? "mute" : p.volume != null && p.volume < 0.5 ? "volumeLow" : "volume");
  $("pb-dur").textContent = fmtTime(durationMs() / 1000);

  if (t?.id !== lastTrackId) {
    lastTrackId = t?.id;
    document.title = t ? `${t.title} · ${(t.artists || []).map((x) => x.name).join(", ")}` : "Tidal Connect";
    if (!$("nowplaying").hidden) renderNowPlaying();
  }
  refreshPlaying();
  renderQueue();
}

function contextLink(c) {
  const href = ["album", "playlist", "mix", "artist"].includes(c.type) ? `#/${c.type}/${encodeURIComponent(c.id)}` : null;
  return href ? `<a class="link" href="${href}">${esc(c.title)}</a>` : esc(c.title);
}

function tick() {
  if (dragging?.classList.contains("seek") !== true && state.player) {
    const dur = durationMs();
    const pos = currentMs();
    setSlider(document.querySelector(".slider.seek"), dur ? pos / dur : 0);
    $("pb-pos").textContent = fmtTime(pos / 1000);
    syncLyrics(pos);
  }
  requestAnimationFrame(tick);
}

// ---------------------------------------------------------------- altavoces
const OUT_ICON = { bluetooth: "bluetooth", usb: "headphones", hdmi: "laptop", builtin: "laptop" };

async function openDevices(anchor) {
  const pop = $("popover");
  const cur = state.player?.device?.id;
  const devs = state.devices || [];
  let outputs = null;
  if (cur === "local") {
    try { outputs = await get("/local/outputs", { fresh: true }); } catch { outputs = null; }
  }
  const outHTML = outputs?.outputs?.length ? `<div class="outs">
      <button class="out ${!outputs.selected ? "active" : ""}" data-out="">${icon("check")}<span>Automática (la del sistema)</span></button>
      ${outputs.outputs.map((o) => `<button class="out ${outputs.selected === o.id ? "active" : ""}" data-out="${esc(o.id)}">${icon(OUT_ICON[o.kind] || "speaker")}<span>${esc(o.name)}</span></button>`).join("")}
    </div>` : "";
  pop.innerHTML = `<h4>Reproducir en</h4><div class="hint">En un altavoz con TIDAL Connect la música la descarga el propio altavoz, en máxima calidad. En este ordenador suena por la salida que elijas.</div>
    ${devs.map((d) => `<button class="dev ${d.id === cur ? "active" : ""} ${d.offline ? "offline" : ""}" data-dev="${esc(d.id)}">
        ${icon(d.local ? "laptop" : "speaker")}<div><div class="n">${esc(d.name)}</div><div class="m">${d.offline ? "No encontrado en la red" : d.id === cur ? (d.local ? "Seleccionado · elige la salida" : state.player?.connected ? "Conectado" : "Seleccionado") : esc(d.model || "TIDAL Connect")}</div></div></button>
        ${d.id === cur && d.local ? outHTML : ""}`).join("")}
    ${devs.length <= 1 ? `<div class="hint" style="margin-top:8px">Buscando altavoces en la red…</div>` : ""}`;
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  const pr = pop.getBoundingClientRect();
  pop.style.left = `${Math.min(r.right - pr.width, innerWidth - pr.width - 8)}px`;
  pop.style.top = `${Math.max(8, r.top - pr.height - 10)}px`;
  pop.onclick = async (ev) => {
    const o = ev.target.closest("[data-out]");
    if (o) {
      closeOverlays();
      await act(post("/local/output", { id: o.dataset.out }), "Salida cambiada");
      return;
    }
    const b = ev.target.closest("[data-dev]");
    if (!b) return;
    closeOverlays();
    if (b.dataset.dev === cur) return;
    await act(post("/device", { id: b.dataset.dev }), b.dataset.dev === "local" ? "Ahora suena en este ordenador" : null);
    if (b.dataset.dev !== "local") act(post("/player/connect"), "Conectado").catch(() => {});
  };
}

// ---------------------------------------------------------------- cola
function toggleQueue() {
  const panel = $("queue-panel");
  panel.hidden = !panel.hidden;
  $("pb-queue").classList.toggle("on", !panel.hidden);
  renderQueue();
}

function qrowHTML(it, cls) {
  return `<div class="qrow ${cls}" data-uid="${esc(it.uid)}" draggable="${cls === "next"}">
    ${it.album?.cover ? `<img loading="lazy" src="${esc(it.album.cover)}" alt="">` : ""}
    <div class="titles"><div class="t">${esc(it.title)}</div><div class="s">${esc((it.artists || []).map((a) => a.name).join(", "))}</div></div>
    ${cls === "next" ? `<button class="x" data-qremove="${esc(it.uid)}" title="Quitar">${icon("close")}</button>` : ""}
  </div>`;
}

function renderQueue() {
  const panel = $("queue-panel");
  if (panel.hidden) return;
  const q = state.queue || { items: [], index: -1 };
  const idx = q.index;
  const cur = q.items[idx];
  const next = q.items.slice(idx + 1);
  const key = JSON.stringify([idx, q.items.length, q.items.map((x) => x.uid).join(",")]);
  if (panel.dataset.key === key) return;
  panel.dataset.key = key;
  panel.innerHTML = `<div class="qp-head"><h3>Cola de reproducción</h3><button class="circle-btn" data-qclose>${icon("close")}</button></div>
    <div class="qp-body">
      ${cur ? `<div class="qp-label">Sonando ahora</div>${qrowHTML(cur, "current")}` : `<div class="empty">La cola está vacía</div>`}
      ${q.context?.title ? `<div class="qp-label" style="text-transform:none;letter-spacing:0;font-size:12px">Reproduciendo desde ${esc(q.context.title)}</div>` : ""}
      ${next.length ? `<div class="qp-label"><span>A continuación · ${next.length}</span><button data-qclear>Borrar</button></div>${next.map((it) => qrowHTML(it, "next")).join("")}` : ""}
    </div>`;
}

export function mountQueue() {
  const panel = $("queue-panel");
  panel.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-qclose]")) return toggleQueue();
    if (ev.target.closest("[data-qclear]")) return act(post("/queue/clear"));
    const rm = ev.target.closest("[data-qremove]");
    if (rm) return act(post("/queue/remove", { uid: rm.dataset.qremove }));
  });
  panel.addEventListener("dblclick", (ev) => {
    const row = ev.target.closest(".qrow");
    if (row) act(post("/queue/jump", { uid: row.dataset.uid }));
  });
  panel.addEventListener("contextmenu", (ev) => {
    const row = ev.target.closest(".qrow");
    if (!row) return;
    ev.preventDefault();
    const it = state.queue.items.find((x) => x.uid === row.dataset.uid);
    if (it) trackMenu(it, ev.clientX, ev.clientY);
  });
  // arrastrar para reordenar
  let dragUid = null;
  panel.addEventListener("dragstart", (ev) => {
    const row = ev.target.closest(".qrow.next");
    if (!row) return;
    dragUid = row.dataset.uid;
    row.classList.add("dragging");
    ev.dataTransfer.effectAllowed = "move";
  });
  panel.addEventListener("dragend", (ev) => ev.target.closest(".qrow")?.classList.remove("dragging"));
  panel.addEventListener("dragover", (ev) => {
    const row = ev.target.closest(".qrow.next");
    if (!row || !dragUid) return;
    ev.preventDefault();
    panel.querySelectorAll(".drop-before").forEach((x) => x.classList.remove("drop-before"));
    row.classList.add("drop-before");
  });
  panel.addEventListener("drop", (ev) => {
    const row = ev.target.closest(".qrow.next");
    panel.querySelectorAll(".drop-before").forEach((x) => x.classList.remove("drop-before"));
    if (!row || !dragUid || row.dataset.uid === dragUid) return;
    ev.preventDefault();
    const items = state.queue.items;
    const from = items.findIndex((x) => x.uid === dragUid);
    let to = items.findIndex((x) => x.uid === row.dataset.uid);
    if (from < to) to -= 1;
    act(post("/queue/move", { uid: dragUid, to }));
    dragUid = null;
  });
  on("queue", () => { $("queue-panel").dataset.key = ""; renderQueue(); refreshPlaying(); });
}

// ----------------------------------------------------------- sonando ahora
let lyrics = null; // {lines:[{t,text}], plain}
let lyricsFor = null;
let npTab = "lyrics";

export function toggleNowPlaying(force) {
  const np = $("nowplaying");
  np.hidden = force !== undefined ? !force : !np.hidden;
  if (!np.hidden) renderNowPlaying();
}

function parseLRC(s) {
  const out = [];
  for (const line of String(s || "").split("\n")) {
    const m = line.match(/^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/);
    if (m) out.push({ t: (Number(m[1]) * 60 + Number(m[2])) * 1000, text: m[3].trim() });
  }
  return out;
}

async function loadLyrics(id) {
  if (lyricsFor === id) return;
  lyricsFor = id;
  lyrics = null;
  try {
    const d = await get(`/lyrics/${id}`);
    const lines = parseLRC(d.synced);
    lyrics = { lines, plain: d.text };
  } catch { lyrics = { lines: [], plain: null }; }
  if (lyricsFor === id) renderNpPanel();
}

function renderNowPlaying() {
  const np = $("nowplaying");
  const p = state.player;
  const t = p?.current;
  if (!t) {
    np.innerHTML = `<button class="circle-btn np-close" data-np-close>${icon("down")}</button><div class="np-inner"><div class="empty">No suena nada</div></div>`;
    np.onclick = (ev) => ev.target.closest("[data-np-close]") && toggleNowPlaying(false);
    return;
  }
  const cover = t.album?.cover_large || t.album?.cover || "";
  const a = p.audio;
  np.innerHTML = `<div class="np-bg">${cover ? `<img src="${esc(cover)}" alt="">` : ""}</div>
    <button class="circle-btn np-close" data-np-close title="Cerrar">${icon("down")}</button>
    <div class="np-inner">
      ${cover ? `<img class="np-cover" src="${esc(cover)}" alt="">` : "<div></div>"}
      <div class="np-side">
        <div class="np-title">${esc(t.title)}</div>
        <div class="np-artist">${artistLinks(t.artists)}</div>
        ${t.album?.title ? `<div class="np-album"><a class="link" href="#/album/${esc(t.album.id)}">${esc(t.album.title)}</a></div>` : ""}
        ${a ? `<div class="np-tech">${a.bit_depth ? `<span class="quality-pill ${(a.bit_depth || 0) > 16 ? "" : "high"}">${(a.bit_depth || 0) > 16 ? "MAX" : "HIGH"}</span>` : ""}<span>${esc((a.codec || "").toUpperCase())} ${a.bit_depth || ""}-bit / ${a.sample_rate ? a.sample_rate / 1000 : ""} kHz · en ${esc(p.device?.name || "")}</span></div>` : ""}
        <div class="np-tabs"><button class="tab ${npTab === "lyrics" ? "active" : ""}" data-np-tab="lyrics">Letra</button><button class="tab ${npTab === "queue" ? "active" : ""}" data-np-tab="queue">A continuación</button></div>
        <div class="np-panel" id="np-panel"></div>
      </div>
    </div>`;
  np.onclick = (ev) => {
    if (ev.target.closest("[data-np-close]")) return toggleNowPlaying(false);
    const tab = ev.target.closest("[data-np-tab]");
    if (tab) { npTab = tab.dataset.npTab; renderNowPlaying(); return; }
    const row = ev.target.closest(".qrow");
    if (row && ev.detail >= 2) act(post("/queue/jump", { uid: row.dataset.uid }));
    if (ev.target.closest("a")) toggleNowPlaying(false);
  };
  if (t.id) loadLyrics(t.id);
  renderNpPanel();
}

function renderNpPanel() {
  const panel = document.getElementById("np-panel");
  if (!panel) return;
  if (npTab === "queue") {
    const q = state.queue;
    const next = (q.items || []).slice(q.index + 1, q.index + 40);
    panel.innerHTML = next.length ? next.map((it) => qrowHTML(it, "")).join("") : `<div class="empty">No hay más canciones</div>`;
    return;
  }
  if (!lyrics) { panel.innerHTML = `<div class="loading"><div class="spinner"></div></div>`; return; }
  if (lyrics.lines.length) {
    panel.innerHTML = `<div class="lyrics">${lyrics.lines.map((l, i) => `<p data-li="${i}">${esc(l.text) || "♪"}</p>`).join("")}</div>`;
  } else if (lyrics.plain) {
    panel.innerHTML = `<div class="lyrics plain">${lyrics.plain.split("\n").map((l) => `<p>${esc(l) || "&nbsp;"}</p>`).join("")}</div>`;
  } else {
    panel.innerHTML = `<div class="empty">No hay letra para esta canción</div>`;
  }
  lastLyric = -1;
}

let lastLyric = -1;
function syncLyrics(pos) {
  if ($("nowplaying").hidden || npTab !== "lyrics" || !lyrics?.lines?.length) return;
  let i = -1;
  for (let k = 0; k < lyrics.lines.length; k++) { if (lyrics.lines[k].t <= pos + 250) i = k; else break; }
  if (i === lastLyric) return;
  lastLyric = i;
  const panel = document.getElementById("np-panel");
  panel?.querySelectorAll(".lyrics p.on").forEach((p) => p.classList.remove("on"));
  const el = panel?.querySelector(`[data-li="${i}"]`);
  if (el) {
    el.classList.add("on");
    // Desplaza solo el panel de la letra (scrollIntoView moveria toda la vista).
    panel.scrollTo({ top: el.offsetTop - panel.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
  }
}

export { toggleFav };
