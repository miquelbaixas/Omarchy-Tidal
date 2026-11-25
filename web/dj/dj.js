// TIDAL DJ: cabina con dos platos que mezcla en este ordenador
import { get, post, on, connectEvents, toast } from "/assets/js/core.js";
import { icon } from "/assets/js/icons.js";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (sec) => { sec = Math.max(0, Math.floor(sec || 0)); return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`; };
const artists = (t) => (t.artists || []).map((a) => a.name).join(", ");

let app = null;          // estado de la app (cola, platos, formas de onda...)
let eng = null;          // ultimo estado del motor
let engAt = 0;           // cuando llego
let dragging = null;     // control que se esta moviendo a mano

// ------------------------------------------------------------------ cabecera
function renderTop() {
  $("#top").innerHTML = `
    <div class="dj-brand"><svg viewBox="0 0 64 64"><circle cx="24" cy="34" r="15" fill="none" stroke="#33ffee" stroke-width="4"/><circle cx="24" cy="34" r="4" fill="#fff"/><circle cx="44" cy="30" r="11" fill="none" stroke="#ff5fa2" stroke-width="3.5"/><circle cx="44" cy="30" r="3" fill="#fff"/></svg>
      <div>TIDAL DJ<small>Mezcla en este ordenador</small></div></div>
    <div class="dj-settitle"><div class="t" id="settitle">Sin set cargado</div><div class="s" id="setsub">Carga una sesión o busca canciones para empezar</div></div>
    <button class="btn secondary" id="btn-savepl" title="Guarda la sesión (lo que ha sonado y lo que viene) como playlist de TIDAL" hidden>${icon("plus")}Guardar como playlist</button>
    <button class="btn secondary" id="btn-load">${icon("playlist")}Cargar sesión</button>
    <button class="btn secondary" id="btn-out" title="Salidas: altavoces (la mezcla) y auriculares (pre-escucha)">${icon("speaker")}<span id="outname">Salidas</span></button>
    <div class="vol">${icon("volume")}<input type="range" class="dj-range" id="vol" min="0" max="100" value="80"></div>
    <button class="bigplay" id="btn-play" title="Reproducir / pausa (Espacio)">${icon("play")}</button>`;
  $("#btn-play").onclick = () => post("/djapp/cmd/toggle").catch((e) => toast(e.message, true));
  $("#btn-load").onclick = (ev) => openSessions(ev.currentTarget);
  $("#btn-out").onclick = (ev) => openOutputs(ev.currentTarget);
  $("#btn-savepl").onclick = (ev) => openSavePlaylist(ev.currentTarget);
  const vol = $("#vol");
  vol.oninput = () => { dragging = "vol"; post("/djapp/cmd/volume", { value: vol.value / 100 }); };
  vol.onchange = () => { dragging = null; };
}

// ------------------------------------------------------------------ mezclador
function renderMixer() {
  $("#mixer").innerHTML = `
    <div><div class="mx-label">Crossfader</div>
      <input type="range" class="dj-range dj-xfader" id="xfader" min="0" max="1000" value="0">
      <div class="mx-x"><span>A</span><span>B</span></div></div>
    <div class="mx-auto"><div><div style="font-weight:700">Mezcla automática</div><div class="mx-label" style="text-transform:none;letter-spacing:0">Entra en compás y al mismo tempo</div></div>
      <button class="switch on" id="auto" title="Mezcla automática"></button></div>
    <div><div class="mx-label">Duración de la mezcla</div>
      <div class="seg small" id="bars"><button data-b="4">4 compases</button><button data-b="8">8</button><button data-b="16">16</button></div></div>
    <div><div class="mx-label">Después de mezclar</div>
      <div class="seg small" id="tempo"><button data-t="glide">Volver a su tempo</button><button data-t="hold">Mantener tempo</button></div></div>
    <div class="mx-btns"><button class="btn primary" id="mixnow" title="Mezcla completa en el próximo compás">${icon("swap")}Mezclar ya</button><button class="btn secondary" id="skip" title="Fundido corto, ahora mismo">${icon("next")}Saltar</button></div>
    <div><div class="mx-prog"><div id="mixprog"></div></div><div class="mx-mixinfo" id="mixinfo"></div></div>
    <div id="cuebox" class="cuebox" hidden><div class="mx-label">${icon("headphones")}Auriculares</div>
      <div class="cue-row"><span>Pre-escucha</span><input type="range" class="dj-range" id="cue-mix" min="0" max="100" value="0" title="Qué oyes por los auriculares: los platos con auriculares, la mezcla, o las dos cosas"><span>Mezcla</span></div>
      <div class="cue-row"><span>Volumen</span><input type="range" class="dj-range" id="cue-vol" min="0" max="100" value="80"><span></span></div></div>`;
  const x = $("#xfader");
  x.oninput = () => { dragging = "x"; post("/djapp/cmd/crossfader", { value: x.value / 1000 }); };
  x.onchange = () => { dragging = null; };
  $("#auto").onclick = () => post("/djapp/cmd/auto", { on: !(eng?.auto) });
  $("#bars").onclick = (ev) => { const b = ev.target.closest("[data-b]"); if (b) post("/djapp/cmd/mix_bars", { value: Number(b.dataset.b) }); };
  $("#tempo").onclick = (ev) => { const b = ev.target.closest("[data-t]"); if (b) post("/djapp/cmd/tempo_mode", { value: b.dataset.t }); };
  $("#mixnow").onclick = () => post("/djapp/cmd/mix_now");
  const cm = $("#cue-mix"), cv = $("#cue-vol");
  cm.oninput = () => { dragging = "cm"; post("/djapp/cmd/cue_param", { mix: cm.value / 100 }); };
  cm.onchange = () => { dragging = null; };
  cv.oninput = () => { dragging = "cv"; post("/djapp/cmd/cue_param", { volume: cv.value / 100 }); };
  cv.onchange = () => { dragging = null; };
  $("#skip").onclick = () => post("/djapp/cmd/skip");
}

// ------------------------------------------------------------------ platos
function deckHTML(d) {
  const t = app?.decks?.[d];
  if (!t) return `<div class="xd-head"><span class="xd-letter">${d}</span><span class="xd-state">Vacío</span></div>
    <div class="xd-empty">Sin canción<div class="xd-hint">Busca una canción en TIDAL y pulsa «Siguiente»</div>
      <button class="btn secondary small-btn" data-gosearch>${icon("search")}Buscar</button></div>`;
  const pts = app.points?.[d] || {};
  return `<div class="xd-head"><span class="xd-letter">${d}</span><span class="xd-state" id="st-${d}">Preparando…</span>
      <span class="xd-right"><button class="xd-drop" id="drop-${d}" hidden title="Quitar: no quiero esta. Entra la siguiente de la lista">${icon("close")}Quitar</button>
      <button class="xd-cue" id="cue-${d}" title="Pre-escucha: oír este plato por los auriculares (si no está sonando, solo lo oyes tú)">${icon("headphones")}<span>Pre-escucha</span></button></span></div>
    <div class="xd-main">${t.album?.cover ? `<img src="${esc(t.album.cover_large || t.album.cover)}" alt="">` : `<div class="ph"></div>`}
      <div class="xd-titles"><div class="t">${esc(t.title)}</div><div class="s">${esc(artists(t))}</div></div></div>
    <div class="xd-chips"><span class="xd-chip bpm" id="bpm-${d}">${pts.bpm ? `${pts.bpm.toFixed(1)} BPM` : t.bpm ? `${t.bpm} BPM` : "BPM ?"}</span>
      <span class="xd-chip key">${t.camelot ? `${esc(t.camelot)} · ${esc(t.key_name || "")}` : "Tono ?"}</span>
      ${t.style ? `<span class="xd-chip">${esc(t.style)}</span>` : ""}</div>
    <div class="xd-wave" id="wave-${d}"><canvas id="cv-${d}"></canvas></div>
    <div class="xd-time"><span class="big" id="pos-${d}">0:00</span><span id="rem-${d}">-${fmt(t.duration)}</span></div>
    <div class="xd-filter"><span>GRAVES</span><input type="range" class="dj-range" id="flt-${d}" min="-100" max="100" value="0" title="Filtro: izquierda quita agudos, derecha quita graves"><span>AGUDOS</span></div>`;
}

function renderDecks() {
  for (const d of ["A", "B"]) {
    const el = $(`#deck-${d}`);
    el.innerHTML = deckHTML(d);
    const flt = $(`#flt-${d}`);
    if (flt) {
      flt.oninput = () => { dragging = `f${d}`; post("/djapp/cmd/filter", { deck: d, value: flt.value / 100 }); };
      flt.onchange = () => { dragging = null; };
      flt.ondblclick = () => { flt.value = 0; post("/djapp/cmd/filter", { deck: d, value: 0 }); };
    }
    const drop = $(`#drop-${d}`);
    if (drop) drop.onclick = () => post("/djapp/drop-next").then(() => toast("Quitada")).catch((e) => toast(e.message, true));
    const cue = $(`#cue-${d}`);
    if (cue) cue.onclick = () => {
      if (!eng?.cue?.target) { toast("Elige primero unos auriculares en «Salidas»"); openOutputs($("#btn-out")); return; }
      post("/djapp/cmd/cue", { deck: d, on: !eng?.decks?.[d]?.cue });
    };
    const wave = $(`#wave-${d}`);
    if (wave) wave.onclick = (ev) => {
      const dur = eng?.decks?.[d]?.duration || app.points?.[d]?.duration;
      if (!dur) return;
      const r = wave.getBoundingClientRect();
      post("/djapp/cmd/seek", { deck: d, pos: ((ev.clientX - r.left) / r.width) * dur });
    };
  }
  drawWaves();
}

function drawWaves() {
  for (const d of ["A", "B"]) {
    const cv = $(`#cv-${d}`);
    if (!cv) continue;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w) continue;
    const dpr = devicePixelRatio || 1;
    if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const wave = app?.waveforms?.[d] || [];
    const st = eng?.decks?.[d] || {};
    const dur = st.duration || app?.points?.[d]?.duration || 0;
    let pos = st.pos || 0;
    if (st.playing && eng && !eng.paused) pos += (performance.now() - engAt) / 1000 * (st.rate || 1);
    const color = d === "A" ? "#33ffee" : "#ff5fa2";
    if (!wave.length) {
      ctx.fillStyle = "#55555b"; ctx.font = "12px Inter"; ctx.fillText(app?.decks?.[d] ? "Descargando y analizando el ritmo…" : "", 10, h / 2 + 4);
      continue;
    }
    const n = wave.length, bw = w / n, played = dur ? pos / dur : 0;
    for (let i = 0; i < n; i++) {
      const v = wave[i] * (h / 2 - 4);
      ctx.fillStyle = i / n < played ? color : "#4a4a52";
      ctx.fillRect(i * bw, h / 2 - v, Math.max(1, bw - 0.3), v * 2);
    }
    const pts = app.points?.[d] || {};
    const mark = (t, c) => { if (t == null || !dur) return; ctx.fillStyle = c; ctx.fillRect((t / dur) * w, 0, 2, h); };
    mark(pts.in, "rgba(255,255,255,.55)");
    mark(st.mix_out ?? pts.out, "#ffd432");
    if (dur) { ctx.fillStyle = "#fff"; ctx.fillRect(played * w - 1, 0, 2, h); }
    const pe = $(`#pos-${d}`), re = $(`#rem-${d}`);
    if (pe) pe.textContent = fmt(pos);
    if (re && dur) re.textContent = `-${fmt(dur - pos)}`;
  }
}

// ------------------------------------------------------------------ efectos y sonidos
const FX = [
  ["echo", "Eco", "1", "Repite el sonido al compás. Al quitarlo, la cola se apaga sola"],
  ["reverb", "Reverb", "2", "Como si sonara en una sala grande"],
  ["flanger", "Flanger", "3", "Barrido de «avión», al compás"],
  ["radio", "Radio", "4", "Suena como una radio vieja o un teléfono"],
  ["lofi", "Lo-fi", "5", "Sonido de videojuego antiguo"],
  ["gate", "Corte", "6", "Corta el sonido a golpes"],
  ["roll", "Repetir", "7", "Repite un trozo corto al compás. Al soltar, la canción sigue donde iba"],
  ["brake", "Freno", "8", "Para la canción como un disco. Si la siguiente está lista, entra de golpe"],
];
const SND = [
  ["horn", "Bocina", "Q", "Suena al momento"],
  ["siren", "Sirena", "W", "Suena al momento"],
  ["laser", "Láser", "E", "Suena al momento"],
  ["impact", "Impacto", "R", "Golpe grave en el próximo tiempo"],
  ["riser", "Subidón", "T", "2 compases subiendo, desde el próximo compás"],
  ["snare", "Redoble", "Y", "Redoble de 2 compases que se acelera, desde el próximo compás"],
  ["scratch", "Rascar", "U", "Rascado hecho con la canción que suena"],
];
const HOLD_MS = 350;
const held = {};

function renderFx() {
  $("#fx").innerHTML = `
    <div class="fx-group">
      <div class="fx-head"><div class="mx-label">Efectos</div>
        <div class="fx-ctl"><span>En</span><div class="seg small" id="fx-target"><button data-v="all">Mezcla</button><button data-v="A">A</button><button data-v="B">B</button></div></div>
        <div class="fx-ctl"><span>Golpes</span><div class="seg small" id="fx-beats"><button data-v="0.25">¼</button><button data-v="0.5">½</button><button data-v="1">1</button><button data-v="2">2</button></div></div>
        <div class="fx-ctl grow"><span>Intensidad</span><input type="range" class="dj-range" id="fx-amt" min="0" max="100" value="60"></div>
      </div>
      <div class="pads fx">${FX.map(([k, n, key, tip]) => `<button class="pad" data-fx="${k}" title="${esc(tip)} (tecla ${key}; mantén pulsado para que dure solo mientras aprietas)">${n}<kbd>${key}</kbd></button>`).join("")}</div>
    </div>
    <div class="fx-group">
      <div class="fx-head"><div class="mx-label">Sonidos</div>
        <div class="fx-ctl grow"><span>Volumen</span><input type="range" class="dj-range" id="sfx-vol" min="0" max="100" value="80"></div>
      </div>
      <div class="pads snd">${SND.map(([k, n, key, tip]) => `<button class="pad" data-snd="${k}" title="${esc(tip)} (tecla ${key})">${n}<kbd>${key}</kbd></button>`).join("")}</div>
    </div>`;
  $("#fx-target").onclick = (ev) => { const b = ev.target.closest("[data-v]"); if (b) post("/djapp/cmd/fx_param", { target: b.dataset.v }); };
  $("#fx-beats").onclick = (ev) => { const b = ev.target.closest("[data-v]"); if (b) post("/djapp/cmd/fx_param", { beats: Number(b.dataset.v) }); };
  const amt = $("#fx-amt"), sv = $("#sfx-vol");
  amt.oninput = () => { dragging = "amt"; post("/djapp/cmd/fx_param", { amount: amt.value / 100 }); };
  amt.onchange = () => { dragging = null; };
  sv.oninput = () => { dragging = "sv"; post("/djapp/cmd/fx_param", { sfx_volume: sv.value / 100 }); };
  sv.onchange = () => { dragging = null; };
  for (const p of document.querySelectorAll("[data-fx]")) {
    p.onpointerdown = (ev) => { ev.preventDefault(); p.setPointerCapture(ev.pointerId); fxDown(p.dataset.fx); };
    p.onpointerup = () => fxUp(p.dataset.fx);
    p.onpointercancel = () => fxUp(p.dataset.fx);
  }
  for (const p of document.querySelectorAll("[data-snd]")) p.onpointerdown = (ev) => { ev.preventDefault(); sound(p.dataset.snd); };
}

const fxIsOn = (k) => k === "roll" ? !!eng?.fx?.roll : k === "brake" ? !!eng?.fx?.brake : !!eng?.fx?.on?.includes(k);
const padOf = (k) => document.querySelector(`[data-fx="${k}"]`);

function fxDown(k) {
  if (held[k]) return;
  if (k === "brake") { padOf(k)?.classList.add("on"); post("/djapp/cmd/fx", { name: k, on: true }); return; }
  const wasOn = fxIsOn(k);
  held[k] = { t: performance.now(), wasOn };
  if (!wasOn) { padOf(k)?.classList.add("on"); post("/djapp/cmd/fx", { name: k, on: true }); }
}

function fxUp(k) {
  const h = held[k];
  if (!h) return;
  delete held[k];
  // pulsar un efecto encendido lo apaga; mantenerlo pulsado lo deja solo mientras se aprieta
  if (h.wasOn || performance.now() - h.t > HOLD_MS) {
    padOf(k)?.classList.remove("on");
    post("/djapp/cmd/fx", { name: k, on: false });
  }
}

function sound(k) {
  const p = document.querySelector(`[data-snd="${k}"]`);
  if (p) { p.classList.add("hit"); setTimeout(() => p.classList.remove("hit"), 250); }
  post("/djapp/cmd/sfx", { name: k });
}

function applyFx() {
  const f = eng?.fx;
  if (!f) return;
  document.querySelectorAll("#fx-target [data-v]").forEach((b) => b.classList.toggle("active", b.dataset.v === f.target));
  document.querySelectorAll("#fx-beats [data-v]").forEach((b) => b.classList.toggle("active", Number(b.dataset.v) === f.beats));
  if (dragging !== "amt") $("#fx-amt").value = Math.round(f.amount * 100);
  if (dragging !== "sv") $("#sfx-vol").value = Math.round(f.sfx_volume * 100);
  for (const [k] of FX) if (!held[k]) padOf(k)?.classList.toggle("on", fxIsOn(k));
  for (const [k] of SND) document.querySelector(`[data-snd="${k}"]`)?.classList.toggle("on", (f.sounds || []).includes(k));
}

// ------------------------------------------------------------------ estado en vivo
function applyEngine() {
  if (!eng) return;
  const paused = eng.paused;
  $("#btn-play").innerHTML = icon(paused ? "play" : "pause");
  if (dragging !== "x") $("#xfader").value = Math.round((eng.xfader || 0) * 1000);
  if (dragging !== "vol") $("#vol").value = Math.round((eng.volume ?? 0.8) * 100);
  $("#auto").classList.toggle("on", !!eng.auto);
  document.querySelectorAll("#bars [data-b]").forEach((b) => b.classList.toggle("active", Number(b.dataset.b) === eng.mix_bars));
  document.querySelectorAll("#tempo [data-t]").forEach((b) => b.classList.toggle("active", b.dataset.t === eng.tempo_mode));
  for (const d of ["A", "B"]) {
    const st = eng.decks?.[d] || {};
    const el = $(`#deck-${d}`);
    el.classList.toggle("live", !!st.playing && !paused);
    const s = $(`#st-${d}`);
    if (s) s.textContent = st.loading ? "Preparando…" : st.playing ? (eng.master === d ? "Sonando" : "Entrando") : st.loaded ? (eng.master === d ? "Lista: pulsa play" : "Siguiente") : app?.decks?.[d] ? "Descargando…" : "Vacío";
    const b = $(`#bpm-${d}`);
    if (b && st.bpm) b.textContent = Math.abs((st.rate || 1) - 1) > 0.002 ? `${st.bpm} BPM (orig. ${st.bpm_orig?.toFixed?.(1) ?? "?"})` : `${st.bpm} BPM`;
    const f = $(`#flt-${d}`);
    if (f && dragging !== `f${d}`) f.value = Math.round((st.filter || 0) * 100);
    const drop = $(`#drop-${d}`);
    if (drop) drop.hidden = !(app?.decks?.[d] && !st.playing && eng.master !== d);
    const cue = $(`#cue-${d}`);
    if (cue) { cue.classList.toggle("on", !!st.cue); cue.classList.toggle("prev", !!st.preview); }
  }
  const mp = $("#mixprog"), mi = $("#mixinfo");
  if (eng.mix) {
    mp.style.width = `${Math.round(eng.mix.progress * 100)}%`;
    mi.textContent = `Mezclando ${eng.mix.from} → ${eng.mix.to}`;
  } else {
    mp.style.width = "0";
    const m = eng.decks?.[eng.master] || {};
    const left = m.mix_out != null && m.playing ? m.mix_out - m.pos : null;
    if (eng.mix_at != null && m.playing) mi.textContent = `Mezcla en el próximo compás (${fmt(Math.max(0, eng.mix_at - m.pos))})`;
    else mi.textContent = left != null && left > 0 && eng.auto ? `Próxima mezcla en ${fmt(left)}` : eng.auto ? "" : "Mezcla automática apagada";
  }
  applyFx();
  const c = eng.cue || {};
  $("#cuebox").hidden = !c.target;
  if (dragging !== "cm") $("#cue-mix").value = Math.round((c.mix ?? 0) * 100);
  if (dragging !== "cv") $("#cue-vol").value = Math.round((c.volume ?? 0.8) * 100);
  // si cambia la cancion que suena, las sugerencias se rehacen
  const nowId = eng.decks?.[eng.master]?.id || null;
  if (nowId && nowId !== fitBase && tab === "fit") loadFit();
}

// ------------------------------------------------------------------ listas: a continuacion, buscar, encajan
let tab = "queue";
let lists = {};          // filas mostradas por panel (para saber que cancion es cada boton)
let searchRes = null;    // ultimo resultado de busqueda
let drill = null;        // album / artista / playlist abierto dentro de la busqueda
let fitBase = null;
let dragFrom = null;

function setTab(t) {
  tab = t;
  document.querySelectorAll("#tabs [data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  for (const k of ["queue", "search", "fit"]) $(`#tab-${k}`).hidden = k !== t;
  if (t === "search") setTimeout(() => $("#q").focus(), 0);
  if (t === "fit") loadFit();
}

function fitHTML(f) {
  if (!f) return `<span class="fit none"></span>`;
  const txt = [f.tempo, f.level === "unknown" ? "" : f.key?.replace(/ \S+→\S+$/, "")].filter(Boolean).join(" · ");
  return `<span class="fit ${f.level}" title="${esc([f.tempo, f.key].filter(Boolean).join(" · "))}"><i></i>${esc(txt || "sin datos")}</span>`;
}

function trackRows(list, name, { queue = false } = {}) {
  lists[name] = list;
  if (!list.length) return `<div class="xd-empty" style="padding:24px 0">${queue ? "No hay más canciones en la lista. Busca una y pulsa «Siguiente» o «+»" : "Sin resultados"}</div>`;
  const base = app?.pos || 0;
  return list.map((t, i) => `<div class="trow ${t.available === false ? "na" : ""}" data-list="${name}" data-i="${i}" ${queue ? `draggable="true" data-idx="${base + i}"` : ""}>
      ${queue ? `<span class="grip" title="Arrastra para cambiar el orden">⋮⋮</span><span class="n">${i + 1}</span>` : ""}
      ${t.album?.cover ? `<img loading="lazy" src="${esc(t.album.cover)}" alt="">` : `<div class="ph"></div>`}
      <div class="tt"><div class="t">${esc(t.title)}</div><div class="s">${esc(artists(t))}</div></div>
      <span class="m">${t.bpm ? `${t.bpm} BPM` : ""}</span><span class="m">${t.camelot ? `${esc(t.camelot)} · ${esc(t.key_name || "")}` : ""}</span>
      ${queue ? `<span></span>` : fitHTML(t.fit)}
      <button class="mini primary" data-act="next" title="Que sea la siguiente en sonar">Siguiente</button>
      ${queue ? `<button class="mini icon" data-act="rm" title="Quitar de la lista">${icon("close")}</button>`
              : `<button class="mini icon" data-act="queue" title="Añadir al final de la lista">${icon("plus")}</button>`}
    </div>`).join("");
}

function renderQueue() {
  const q = (app?.queue || []).slice(app?.pos || 0);
  $("#queue").innerHTML = trackRows(q.slice(0, 60), "queue", { queue: true });
}

async function trackAction(btn) {
  const row = btn.closest("[data-list]");
  const t = lists[row.dataset.list]?.[Number(row.dataset.i)];
  if (!t) return;
  const act = btn.dataset.act;
  try {
    if (act === "next") {
      const body = { track: t };
      if (row.dataset.idx) body.from_index = Number(row.dataset.idx);
      const r = await post("/djapp/next", body);
      toast(r.where === "first" ? `«${t.title}» lista: pulsa play` : r.where === "queue" ? `«${t.title}» entrará al acabar esta mezcla` : `«${t.title}» será la siguiente`);
    } else if (act === "queue") {
      await post("/djapp/queue/add", { tracks: [t] });
      toast(`«${t.title}» añadida al final de la lista`);
    } else if (act === "rm") {
      await post("/djapp/queue/remove", { index: Number(row.dataset.idx) });
    }
  } catch (e) { toast(e.message, true); }
}

let searchTimer = null;
function onSearchInput() {
  clearTimeout(searchTimer);
  const q = $("#q").value.trim();
  searchTimer = setTimeout(() => runSearch(q), 350);
}

async function runSearch(q) {
  drill = null;
  if (!q) { searchRes = null; renderSearch(); return; }
  $("#sres").innerHTML = `<div class="xd-empty" style="padding:24px 0">Buscando…</div>`;
  try {
    const r = await get(`/djapp/search?q=${encodeURIComponent(q)}`);
    if ($("#q").value.trim() !== q) return;   // ya se escribio otra cosa
    searchRes = r;
    renderSearch();
  } catch (e) { $("#sres").innerHTML = `<div class="xd-empty">${esc(e.message)}</div>`; }
}

function cardHTML(x, kind) {
  const img = kind === "artist" ? x.picture : x.cover;
  const sub = kind === "artist" ? "Artista" : kind === "album" ? `Álbum · ${esc((x.artists || [])[0]?.name || "")}` : `Playlist${x.creator ? ` · ${esc(x.creator)}` : ""}`;
  return `<button class="scard" data-kind="${kind}" data-id="${esc(x.id)}" data-title="${esc(x.name || x.title)}">
    ${img ? `<img loading="lazy" src="${esc(img)}" alt="" class="${kind === "artist" ? "round" : ""}">` : `<div class="ph ${kind === "artist" ? "round" : ""}"></div>`}
    <div class="t">${esc(x.name || x.title)}</div><div class="s">${sub}</div></button>`;
}

function renderSearch() {
  const el = $("#sres");
  if (drill) {
    el.innerHTML = `<div class="drill"><button class="mini" data-back>${icon("left")}Resultados</button><div class="dt">${esc(drill.title)}</div>
        ${drill.kind === "artist" ? `<button class="mini" data-radio="${esc(drill.id)}">${icon("radio")}Radio del artista</button>` : ""}</div>
      ${drill.tracks ? trackRows(drill.tracks, "drill") : `<div class="xd-empty" style="padding:24px 0">Cargando…</div>`}`;
    return;
  }
  if (!searchRes) {
    el.innerHTML = `<div class="xd-empty" style="padding:24px 0">Busca en todo TIDAL, esté o no en tus listas. Junto a cada canción verás si encaja con la que suena.</div>`;
    return;
  }
  const r = searchRes;
  const sec = (title, items, kind) => items?.length ? `<div class="ssec"><div class="mx-label">${title}</div><div class="scards">${items.map((x) => cardHTML(x, kind)).join("")}</div></div>` : "";
  el.innerHTML = `${trackRows(r.tracks.slice(0, 30), "search")}
    ${sec("Artistas", r.artists, "artist")}${sec("Álbumes", r.albums, "album")}${sec("Playlists", r.playlists, "playlist")}`;
}

async function openDrill(kind, id, title) {
  drill = { kind, id, title, tracks: null };
  renderSearch();
  $("#tab-search").scrollIntoView({ block: "nearest" });
  try {
    const r = await get(`/djapp/list?kind=${kind}&id=${encodeURIComponent(id)}`, { fresh: true });
    if (drill?.id !== id) return;
    drill.tracks = r.tracks;
    renderSearch();
  } catch (e) { toast(e.message, true); }
}

async function loadFit() {
  const el = $("#fitres");
  const nowId = eng?.decks?.[eng?.master]?.id || null;
  fitBase = nowId;
  if (!nowId) { el.innerHTML = `<div class="xd-empty" style="padding:24px 0">Cuando suene una canción, aquí verás otras que encajan detrás (tempo, tono y estilo).</div>`; return; }
  el.innerHTML = `<div class="xd-empty" style="padding:24px 0">Buscando canciones que encajen…</div>`;
  try {
    const r = await get("/djapp/suggest", { fresh: true });
    if (fitBase !== nowId) return;
    el.innerHTML = `<div class="drill"><div class="dt">Encajan después de «${esc(r.base?.title || "")}»</div><button class="mini" data-refit>${icon("refresh")}Otras</button></div>${trackRows(r.tracks, "fit")}`;
  } catch (e) { el.innerHTML = `<div class="xd-empty">${esc(e.message)}</div>`; }
}

function renderLists() {
  renderQueue();
  const h = (app?.history || []).slice().reverse();
  $("#history").innerHTML = h.length ? h.map((t) => `<div class="hrow">${t.album?.cover ? `<img src="${esc(t.album.cover)}" alt="">` : ""}<span>${esc(t.title)} · ${esc((t.artists || [])[0]?.name || "")}</span></div>`).join("")
    : `<div class="xd-empty" style="padding:20px 0">Aún nada</div>`;
  $("#settitle").textContent = app?.title || "Sin set cargado";
  $("#btn-savepl").hidden = !((app?.history || []).length || (app?.queue || []).length || app?.decks?.A || app?.decks?.B);
  const left = (app?.queue || []).slice(app?.pos || 0);
  $("#setsub").textContent = app?.queue?.length ? `${left.length} por sonar · ${Math.round(left.reduce((s, t) => s + (t.duration || 0), 0) / 60)} min`
    : app?.title ? "Elige cada canción: busca y pulsa «Siguiente»" : "Carga una sesión o busca canciones para empezar";
}

function renderPanels() {
  $("#lists-main").innerHTML = `
    <div class="dj-tabs" id="tabs"><button data-tab="queue" class="active">A continuación</button><button data-tab="search">${icon("search")}Buscar en TIDAL</button><button data-tab="fit">Encajan con la que suena</button></div>
    <div id="tab-queue"><div id="queue"></div></div>
    <div id="tab-search" hidden><div class="srch">${icon("search")}<input id="q" type="search" placeholder="Canción, artista, álbum o playlist" autocomplete="off" spellcheck="false"></div><div id="sres"></div></div>
    <div id="tab-fit" hidden><div id="fitres"></div></div>`;
  $("#tabs").onclick = (ev) => { const b = ev.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); };
  $("#q").oninput = onSearchInput;
  $("#q").onkeydown = (ev) => { if (ev.key === "Enter") { clearTimeout(searchTimer); runSearch($("#q").value.trim()); } };
  renderSearch();
  const lm = $("#lists-main");
  lm.addEventListener("dragstart", (ev) => { const r = ev.target.closest("[data-idx]"); if (!r) return; dragFrom = Number(r.dataset.idx); r.classList.add("dragging"); ev.dataTransfer.effectAllowed = "move"; });
  lm.addEventListener("dragend", (ev) => { ev.target.closest("[data-idx]")?.classList.remove("dragging"); document.querySelectorAll(".trow.over").forEach((x) => x.classList.remove("over")); });
  lm.addEventListener("dragover", (ev) => { const r = ev.target.closest("[data-idx]"); if (r && dragFrom != null) { ev.preventDefault(); document.querySelectorAll(".trow.over").forEach((x) => x !== r && x.classList.remove("over")); r.classList.add("over"); } });
  lm.addEventListener("drop", (ev) => {
    const r = ev.target.closest("[data-idx]");
    if (!r || dragFrom == null) return;
    ev.preventDefault();
    const to = Number(r.dataset.idx);
    if (to !== dragFrom) post("/djapp/queue/move", { from: dragFrom, to });
    dragFrom = null;
  });
}

document.addEventListener("click", (ev) => {
  const act = ev.target.closest("[data-act]");
  if (act) { trackAction(act); return; }
  const card = ev.target.closest(".scard");
  if (card) { openDrill(card.dataset.kind, card.dataset.id, card.dataset.title); return; }
  if (ev.target.closest("[data-back]")) { drill = null; renderSearch(); return; }
  const radio = ev.target.closest("[data-radio]");
  if (radio) { openDrill("artist_radio", radio.dataset.radio, `Radio: ${drill?.title || ""}`); return; }
  if (ev.target.closest("[data-refit]")) { loadFit(); return; }
  if (ev.target.closest("[data-gosearch]")) { setTab("search"); $("#lists-main").scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  const pop = $("#popover");
  if (!pop.hidden && !ev.target.closest("#popover") && !ev.target.closest("#btn-load") && !ev.target.closest("#btn-out") && !ev.target.closest("#btn-savepl") && !ev.target.closest(".xd-cue")) { pop.hidden = true; pop.classList.remove("outpop"); }
});

// ------------------------------------------------------------------ sesiones / salida
function placePop(pop, anchor) {
  const r = anchor.getBoundingClientRect(), pr = pop.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(r.right - pr.width, innerWidth - pr.width - 8))}px`;
  pop.style.top = `${r.bottom + 8}px`;
}

async function openSessions(anchor) {
  const pop = $("#popover");
  const sessions = await get("/dj/sessions", { fresh: true }).catch(() => []);
  pop.innerHTML = `<h4>Cargar en la cabina</h4><div class="hint">Tus sesiones de la app TIDAL Connect. El DJ elige las canciones y aquí se mezclan de verdad.</div>
    <div class="sess-list">
      <button class="sess-item" data-free><div class="n">Sesión libre</div><div class="m">Sin lista: tú eliges cada canción con el buscador</div></button>
      ${sessions.map((s) => `<button class="sess-item" data-sess="${esc(s.id)}"><div class="n">${esc(s.name)}</div><div class="m">${esc(s.summary)}</div></button>`).join("")}
      <button class="sess-item" data-quick="favorites"><div class="n">Mis canciones · Subida · 1 h</div><div class="m">Un set rápido con tus favoritas</div></button>
    </div>`;
  pop.hidden = false;
  placePop(pop, anchor);
  pop.onclick = async (ev) => {
    const b = ev.target.closest("[data-sess],[data-quick],[data-free]");
    if (!b) return;
    pop.hidden = true;
    if (b.dataset.free !== undefined) {
      try { await post("/djapp/load", { free: true }); toast("Sesión libre: busca una canción y pulsa «Siguiente»"); setTab("search"); } catch (e) { toast(e.message, true); }
      return;
    }
    toast("Preparando el set…");
    const body = b.dataset.sess ? { recipe_id: b.dataset.sess } : { source: { kind: "favorites", title: "Mis canciones" }, minutes: 60, shape: "subida" };
    try {
      const r = await post("/djapp/load", body);
      toast(`Set de ${r.tracks} canciones listo: descargando y analizando…`);
    } catch (e) { toast(e.message, true); }
  };
}

function openSavePlaylist(anchor) {
  const pop = $("#popover");
  const def = `${app?.title || "Sesión DJ"} · ${new Date().toLocaleDateString("es-ES")}`;
  pop.innerHTML = `<h4>Guardar como playlist</h4>
    <div class="hint">Se guarda en tu colección de TIDAL como playlist privada: lo que ha sonado, lo que está en los platos y lo que viene, en este orden.</div>
    <div class="srch" style="margin:10px 0"><input id="pl-name" maxlength="100" value="${esc(def)}" aria-label="Nombre de la playlist"></div>
    <div style="display:flex;justify-content:flex-end"><button class="btn primary" id="pl-save">${icon("check")}Guardar</button></div>`;
  pop.hidden = false;
  placePop(pop, anchor);
  const input = $("#pl-name");
  setTimeout(() => { input.focus(); input.select(); }, 0);
  const save = async () => {
    const btn = $("#pl-save");
    btn.disabled = true;
    btn.textContent = "Guardando…";
    try {
      const r = await post("/djapp/save-playlist", { name: input.value.trim() });
      pop.hidden = true;
      toast(`Playlist «${r.playlist.title}» guardada en TIDAL (${r.tracks} canciones)`);
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
      btn.innerHTML = `${icon("check")}Guardar`;
    }
  };
  $("#pl-save").onclick = save;
  input.onkeydown = (ev) => { if (ev.key === "Enter") save(); };
}

const kindIcon = (k) => k === "bluetooth" ? "bluetooth" : k === "usb" ? "headphones" : "laptop";

async function openOutputs(anchor) {
  const pop = $("#popover");
  const d = await get("/local/outputs", { fresh: true }).catch(() => ({ outputs: [] }));
  const sel = app?.output || null, cue = app?.cue_output || null;
  const row = (o, attr, active) => `<button class="out ${active ? "active" : ""}" data-${attr}="${esc(o.id)}">${icon(kindIcon(o.kind))}<span>${esc(o.name)}</span></button>`;
  pop.innerHTML = `<h4>Salidas de audio</h4>
    <div class="out-sec"><div class="mx-label">${icon("speaker")}Altavoces · la mezcla, lo que oye todo el mundo</div>
      <div class="outs" style="margin:0;border:0;padding:0">
        <button class="out ${!sel ? "active" : ""}" data-master="">${icon("check")}<span>Automática (la del sistema)</span></button>
        ${d.outputs.map((o) => row(o, "master", sel === o.id)).join("")}</div></div>
    <div class="out-sec"><div class="mx-label">${icon("headphones")}Auriculares · pre-escucha, solo para ti</div>
      <div class="outs" style="margin:0;border:0;padding:0">
        <button class="out ${!cue ? "active" : ""}" data-cue="">${icon("close")}<span>Sin pre-escucha</span></button>
        ${d.outputs.map((o) => row(o, "cue", cue === o.id)).join("")}</div></div>
    <div class="hint">Tienen que ser dos aparatos distintos: por ejemplo, el DMP-A6 por USB para la mezcla y la toma de auriculares del portátil, o los altavoces del portátil y unos auriculares Bluetooth o USB. Los auriculares Bluetooth llevan algo de retraso: sirven para escuchar, no para cuadrar golpes de oído.</div>`;
  pop.classList.add("outpop");
  pop.hidden = false;
  placePop(pop, anchor);
  pop.onclick = async (ev) => {
    const m = ev.target.closest("[data-master]"), c = ev.target.closest("[data-cue]");
    if (!m && !c) return;
    pop.hidden = true;
    pop.classList.remove("outpop");
    try {
      if (m) { await post("/djapp/output", { id: m.dataset.master }); app.output = m.dataset.master || null; }
      else { await post("/djapp/cue-output", { id: c.dataset.cue }); app.cue_output = c.dataset.cue || null; }
    } catch (e) { toast(e.message, true); return; }
    updateOutName(d.outputs);
    if (app.cue_output && app.output && app.cue_output === app.output) toast("Altavoces y auriculares son el mismo aparato: lo que pre-escuches se oirá también en la mezcla", true);
    else toast(m ? "Salida de la mezcla cambiada" : app.cue_output ? "Pre-escucha lista: pulsa «Pre-escucha» en un plato" : "Pre-escucha apagada");
  };
}

async function updateOutName(list) {
  const outs = list || (await get("/local/outputs", { fresh: true }).catch(() => ({ outputs: [] }))).outputs;
  const o = outs.find((x) => x.id === app?.output);
  const c = outs.find((x) => x.id === app?.cue_output);
  $("#outname").textContent = `${o ? o.name : "Salida automática"}${c ? ` · auriculares: ${c.name}` : ""}`;
}

// ------------------------------------------------------------------ arranque
function applyApp(a) {
  const prevDecks = JSON.stringify([app?.decks?.A?.id, app?.decks?.B?.id, !!app?.waveforms?.A?.length, !!app?.waveforms?.B?.length]);
  app = a;
  if (a.engine && Object.keys(a.engine).length) { eng = a.engine; engAt = performance.now(); }
  const now = JSON.stringify([a.decks?.A?.id, a.decks?.B?.id, !!a.waveforms?.A?.length, !!a.waveforms?.B?.length]);
  if (prevDecks !== now) renderDecks();
  renderLists();
  applyEngine();
}

renderTop();
renderMixer();
renderFx();
renderPanels();
connectEvents();
on("djapp", (m) => applyApp(m.app));
on("djapp_state", (m) => { eng = m.engine; engAt = performance.now(); applyEngine(); });
on("djapp_error", (m) => toast(m.msg, true));
on("online", (ok) => { if (ok) get("/djapp/state", { fresh: true }).then(applyApp).catch(() => {}); });
get("/djapp/state", { fresh: true }).then((a) => { applyApp(a); renderDecks(); updateOutName(); }).catch((e) => toast(e.message, true));
(function loop() { drawWaves(); requestAnimationFrame(loop); })();

const FX_KEYS = Object.fromEntries(FX.map(([k, , key]) => [key, k]));
const SND_KEYS = Object.fromEntries(SND.map(([k, , key]) => [key.toLowerCase(), k]));
document.addEventListener("keydown", (ev) => {
  if (ev.target.closest("input, textarea") || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === " ") { ev.preventDefault(); post("/djapp/cmd/toggle"); return; }
  if (ev.repeat) return;
  if (FX_KEYS[ev.key]) { ev.preventDefault(); fxDown(FX_KEYS[ev.key]); return; }
  if (SND_KEYS[ev.key.toLowerCase()]) { ev.preventDefault(); sound(SND_KEYS[ev.key.toLowerCase()]); }
});
document.addEventListener("keyup", (ev) => { if (FX_KEYS[ev.key]) fxUp(FX_KEYS[ev.key]); });
