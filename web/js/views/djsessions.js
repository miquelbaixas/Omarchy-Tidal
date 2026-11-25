// DJ · Sesiones: lista de sesiones guardadas y editor (montar / editar una sesion)
import { state, on, get, post, toast } from "../core.js";
import { esc, closeOverlays } from "../ui.js";
import { icon } from "../icons.js";

export const SHAPES = [
  ["subida", "Subida", "De tranquilo a la cima", "M4 34 C 20 33, 34 26, 44 16 S 58 6, 64 5"],
  ["montana", "Montaña", "Sube, pico y baja", "M4 34 C 16 30, 24 8, 38 6 S 56 26, 64 30"],
  ["constante", "Constante", "Energía estable", "M4 20 C 20 18, 44 22, 64 20"],
  ["bajada", "Bajada", "Para relajarse", "M4 6 C 20 8, 34 16, 44 24 S 58 33, 64 34"],
];
const DURATIONS = [[30, "30 min"], [60, "1 h"], [90, "1 h 30"], [120, "2 h"], [180, "3 h"], [240, "4 h"]];
const SEG_MINUTES = [10, 15, 20, 30, 40, 45, 60, 75, 90, 120];
export const SEG_COLORS = ["#4ea1ff", "#ff5fa2", "#ffa24e", "#4ee08a", "#b48cff", "#33ffee"];

// [etiqueta en pantalla, busqueda en TIDAL]
const STYLES = [
  ["Blues", "Blues"], ["Jazz", "Jazz"], ["Soul", "Soul"], ["Funk", "Funk"], ["Disco", "Disco"],
  ["Hip hop", "Hip-Hop"], ["R&B", "R&B"], ["Rock", "Rock"], ["Pop", "Pop"], ["Indie", "Indie"],
  ["Electrónica", "Electronic"], ["House", "House"], ["Techno", "Techno"], ["Reggae", "Reggae"],
  ["Reggaetón", "Reggaeton"], ["Latino", "Latin"], ["Salsa", "Salsa"], ["Flamenco", "Flamenco"],
  ["Rumba", "Rumba"], ["Bossa nova", "Bossa Nova"], ["Clásica", "Classical"], ["Country", "Country"],
  ["Folk", "Folk"], ["Metal", "Metal"], ["Punk", "Punk"], ["Ambient", "Ambient"], ["Lo-fi", "Lofi"],
  ["Gospel", "Gospel"], ["Afrobeat", "Afrobeat"], ["Rock en español", "Rock en español"],
];

const shapePath = (k) => (SHAPES.find((s) => s[0] === k) || SHAPES[0])[3];
const shapeName = (k) => (SHAPES.find((s) => s[0] === k) || SHAPES[0])[1];
const fmtMin = (m) => { const h = Math.floor(m / 60), r = m % 60; return h && r ? `${h} h ${r} min` : h ? `${h} h` : `${r} min`; };
const segLabel = (s) => s.styles.map((x) => x.label).join(" y ") || "Sin estilo";

export const djTabs = (active) => `<div class="dj-tabs">
  <a class="dj-tab ${active === "cabina" ? "active" : ""}" href="#/dj/cabina">${icon("dj")}Cabina</a>
  <a class="dj-tab ${active === "sesiones" ? "active" : ""}" href="#/dj/sesiones">${icon("playlist")}Sesiones</a></div>`;

// Progreso de la creacion de un set (compartido por lista y editor)
function progressText(m) {
  return m.step === "pool" ? "Reuniendo canciones…"
    : m.step === "styles" ? `Buscando música de ${m.label}…`
    : m.step === "expand" ? "Buscando música parecida para completar…"
    : m.step === "style" ? `Analizando estilos de los artistas… ${m.done}/${m.total}`
    : "Ordenando el set…";
}

async function playSet(body, progressEl, goCabina) {
  if (progressEl) progressEl.textContent = "Preparando…";
  try {
    const r = await post("/dj/create", body);
    toast(`Set de ${r.tracks} canciones (${r.minutes} min)` + (r.added ? ` · ${r.from_source} de tu selección y ${r.tracks - r.from_source} parecidas` : ""));
    goCabina();
    return true;
  } catch (e) {
    toast(e.message, true);
    return false;
  } finally {
    if (progressEl) progressEl.textContent = "";
  }
}

// ================================================================= lista
export async function renderSessions(el, unsubs, goCabina) {
  el.innerHTML = `<div class="page dj-page">${djTabs("sesiones")}
    <div class="dj-head"><div><h1 class="page-title" style="margin-bottom:6px">Sesiones</h1>
      <p class="dj-intro" style="margin:0">La estructura de un set, sin las canciones: cada vez que la pinchas, el DJ elige canciones nuevas siguiendo la receta.</p></div>
      <a class="btn primary" href="#/dj/sesiones/nueva">${icon("plus")}Nueva sesión</a></div>
    <div id="dj-now"></div>
    <div id="dj-list"><div class="loading"><div class="spinner"></div></div></div>
    <span id="dj-progress" class="dj-progress"></span></div>`;
  const root = el.querySelector(".dj-page");
  const $ = (s) => root.querySelector(s);
  let sessions = [];

  unsubs.push(on("dj_progress", (m) => { const p = $("#dj-progress"); if (p) p.textContent = progressText(m); }));

  // Set que esta sonando sin sesion guardada: ofrecer guardar su estructura
  try {
    const st = await get("/dj/state", { fresh: true });
    if (st.active && !st.active.recipe_id && state.player?.context?.type === "dj") {
      $("#dj-now").innerHTML = `<div class="now-card"><div><div class="eyebrow">Sonando ahora en la cabina</div>
        <div class="n">${esc(st.active.title.replace(/^Set DJ · /, ""))}</div></div>
        <div class="now-save"><input type="text" id="dj-now-name" maxlength="80" value="${esc(st.active.title.replace(/^Set DJ · /, ""))}">
        <button class="btn secondary small" id="dj-now-save">${icon("plus")}Guardar como sesión</button></div></div>`;
      $("#dj-now-save").onclick = async () => {
        try {
          const r = await post("/dj/sessions/save", { name: $("#dj-now-name").value });
          sessions = r.sessions;
          $("#dj-now").innerHTML = "";
          toast("Sesión guardada");
          paint();
        } catch (e) { toast(e.message, true); }
      };
    }
  } catch { /* sin set activo */ }

  function paint() {
    const box = $("#dj-list");
    if (!sessions.length) {
      box.innerHTML = `<div class="empty"><h3>Aún no tienes sesiones</h3>Crea una con «Nueva sesión»: elige la música, los tramos de estilos y la forma de la energía.</div>`;
      return;
    }
    box.innerHTML = `<div class="sessions">${sessions.map((s) => `
      <div class="session" data-id="${esc(s.id)}">
        <svg viewBox="0 0 68 40"><path d="${shapePath(s.config.shape)}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
        <div class="s-body">
          <div class="n-row"><div class="n">${esc(s.name)}</div><button class="icon-btn mini" data-rename title="Renombrar">${icon("edit")}</button></div>
          <div class="rename-row" hidden><input type="text" maxlength="80" value="${esc(s.name)}"><button class="btn primary small" data-rename-ok>Guardar</button><button class="btn secondary small" data-rename-cancel>Cancelar</button></div>
          <div class="d">${esc(s.summary)}</div>
          <div class="d">${s.plays ? `Pinchada ${s.plays} ${s.plays === 1 ? "vez" : "veces"}` : "Sin estrenar"}</div></div>
        <div class="s-actions"><button class="btn primary small" data-play>${icon("play")}Pinchar</button>
          <a class="btn secondary small" href="#/dj/sesiones/${esc(s.id)}">Editar</a>
          <button class="btn secondary small" data-del title="Borrar">${icon("trash")}</button></div>
      </div>`).join("")}</div>`;
  }

  root.addEventListener("click", async (ev) => {
    const card = ev.target.closest(".session");
    if (!card) return;
    const id = card.dataset.id;
    const t = ev.target;
    if (t.closest("[data-play]")) {
      t.closest("[data-play]").disabled = true;
      await playSet({ recipe_id: id }, $("#dj-progress"), goCabina);
      return;
    }
    if (t.closest("[data-rename]")) {
      card.querySelector(".n-row").hidden = true;
      const row = card.querySelector(".rename-row");
      row.hidden = false;
      row.querySelector("input").focus();
      row.querySelector("input").select();
      return;
    }
    if (t.closest("[data-rename-cancel]")) return paint();
    if (t.closest("[data-rename-ok]")) return rename(card);
    const del = t.closest("[data-del]");
    if (del) {
      if (del.dataset.confirm !== "1") { del.dataset.confirm = "1"; del.textContent = "¿Borrar?"; return; }
      const r = await post("/dj/sessions/delete", { id });
      sessions = r.sessions;
      toast("Sesión borrada");
      paint();
    }
  });
  root.addEventListener("keydown", (ev) => {
    const card = ev.target.closest(".session");
    if (card && ev.target.matches(".rename-row input")) {
      if (ev.key === "Enter") rename(card);
      if (ev.key === "Escape") paint();
    }
  });
  async function rename(card) {
    try {
      const r = await post("/dj/sessions/rename", { id: card.dataset.id, name: card.querySelector(".rename-row input").value });
      sessions = r.sessions;
      toast("Nombre cambiado");
      paint();
    } catch (e) { toast(e.message, true); }
  }

  sessions = await get("/dj/sessions", { fresh: true }).catch(() => []);
  paint();
}

// ================================================================ editor
function freshModel(preset) {
  return {
    mode: preset ? "preset" : "favorites", preset, playlist: null, minutes: 120, shape: "subida",
    segments: [{ styles: [], minutes: 40 }],
    bpm_min: null, bpm_max: null, include_unknown: false, expand: true, prefer_favorites: true,
  };
}

export async function renderEditor(el, { sessionId = null, preset = null } = {}, unsubs, goCabina) {
  let session = null;
  if (sessionId) {
    const all = await get("/dj/sessions", { fresh: true }).catch(() => []);
    session = all.find((s) => s.id === sessionId) || null;
    if (!session) {
      el.innerHTML = `<div class="page dj-page">${djTabs("sesiones")}<div class="empty"><h3>Esa sesión ya no existe</h3><a class="btn secondary" href="#/dj/sesiones">Volver a Sesiones</a></div></div>`;
      return;
    }
  }
  let model = freshModel(preset);
  const pls = state.myPlaylists || [];
  el.innerHTML = `<div class="page dj-page">${djTabs("sesiones")}
    <a class="back-link" href="#/dj/sesiones">${icon("left")}Sesiones</a>
    <h1 class="page-title" style="margin-top:6px">${session ? "Editar sesión" : "Nueva sesión"}</h1>
    <div class="dj-form">
      <label class="dj-label" for="dj-name">Nombre</label>
      <input type="text" id="dj-name" class="dj-name" maxlength="80" placeholder="Por ejemplo: Viernes noche">
      <label class="dj-label">Música</label>
      <div class="seg" id="dj-mode">
        <span id="dj-preset-tab"></span>
        <button data-mode="favorites">Mis canciones</button>
        <button data-mode="playlist">Una playlist</button>
        <button data-mode="styles">Por estilos</button>
      </div>
      <div id="dj-pl-row" hidden><select id="dj-playlist" class="dj-select">${pls.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("")}</select></div>
      <div id="dj-styles" hidden>
        <p class="dj-hint">Cada tramo usa la música de sus estilos en toda la biblioteca de TIDAL. Entre tramos, el DJ tiende un puente con canciones que conectan ambos estilos.</p>
        <div id="dj-segs"></div>
        <label class="dj-check"><input type="checkbox" id="dj-favs" checked> Dar preferencia a mis favoritas de esos estilos</label>
      </div>
      <div id="dj-dur-row"><label class="dj-label">Duración</label>
        <div class="seg" id="dj-dur">${DURATIONS.map(([m, l]) => `<button data-min="${m}">${l}</button>`).join("")}</div></div>
      <label class="dj-label">Forma de la energía</label>
      <div class="shapes" id="dj-shape">${SHAPES.map(([k, n, d, path]) => `<button data-shape="${k}" class="shape">
        <svg viewBox="0 0 68 40"><path d="${path}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg>
        <div class="n">${n}</div><div class="d">${d}</div></button>`).join("")}</div>
      <details class="dj-more"><summary>Más opciones</summary>
        <div class="dj-row"><label>Tempo mínimo <input type="number" id="dj-bmin" min="40" max="220" placeholder="—"></label>
        <label>Tempo máximo <input type="number" id="dj-bmax" min="40" max="220" placeholder="—"></label></div>
        <label class="dj-check" id="dj-expand-row"><input type="checkbox" id="dj-expand" checked> Completar con música parecida si la fuente se queda corta</label>
        <label class="dj-check"><input type="checkbox" id="dj-unknown"> Incluir canciones sin datos de tempo o tonalidad</label>
      </details>
      <div class="dj-actions">
        <button class="btn primary" id="dj-save">${icon("check")}${session ? "Guardar cambios" : "Guardar sesión"}</button>
        <button class="btn secondary" id="dj-save-play">${icon("play")}Guardar y pinchar</button>
        <button class="btn secondary" id="dj-play-only" title="Crea el set sin guardar la sesión">Probar sin guardar</button>
        <span id="dj-progress" class="dj-progress"></span>
      </div>
    </div></div>`;
  // `el` es un contenedor provisional: todo se consulta desde `root`, que acaba en pantalla.
  const root = el.querySelector(".dj-page");
  const $ = (sel) => root.querySelector(sel);

  function apply() {
    $("#dj-preset-tab").innerHTML = model.preset ? `<button data-mode="preset">${esc(model.preset.title || "Esta página")}</button>` : "";
    root.querySelectorAll("#dj-mode [data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === model.mode));
    $("#dj-pl-row").hidden = model.mode !== "playlist";
    $("#dj-styles").hidden = model.mode !== "styles";
    $("#dj-dur-row").hidden = model.mode === "styles";
    $("#dj-expand-row").hidden = model.mode === "styles" || model.mode === "favorites";
    if (model.playlist) $("#dj-playlist").value = model.playlist;
    root.querySelectorAll("#dj-dur button").forEach((b) => b.classList.toggle("active", Number(b.dataset.min) === model.minutes));
    root.querySelectorAll("#dj-shape .shape").forEach((b) => b.classList.toggle("active", b.dataset.shape === model.shape));
    $("#dj-bmin").value = model.bpm_min || "";
    $("#dj-bmax").value = model.bpm_max || "";
    $("#dj-unknown").checked = !!model.include_unknown;
    $("#dj-expand").checked = model.expand !== false;
    $("#dj-favs").checked = !!model.prefer_favorites;
    renderSegs();
  }

  function renderSegs() {
    const total = model.segments.reduce((s, x) => s + x.minutes, 0);
    $("#dj-segs").innerHTML = model.segments.map((s, i) => `<div class="seg-row" data-i="${i}">
        <span class="seg-n" style="color:${SEG_COLORS[i % SEG_COLORS.length]}">Tramo ${i + 1}</span>
        <div class="seg-styles">${s.styles.map((x, j) => `<span class="style-chip" style="--c:${SEG_COLORS[i % SEG_COLORS.length]}">${esc(x.label)}<button data-rm-style="${j}" title="Quitar">${icon("close")}</button></span>`).join("")}
          <button class="add-style" data-add-style>${icon("plus")}estilo</button></div>
        <select class="seg-min" data-seg-min>${SEG_MINUTES.map((m) => `<option value="${m}" ${m === s.minutes ? "selected" : ""}>${fmtMin(m)}</option>`).join("")}</select>
        <button class="icon-btn" data-rm-seg title="Quitar tramo" ${model.segments.length === 1 ? "disabled" : ""}>${icon("trash")}</button>
      </div>${i < model.segments.length - 1 ? `<div class="seg-bridge">${icon("down")}puente</div>` : ""}`).join("") +
      `<div class="seg-foot"><button class="btn secondary small" data-add-seg>${icon("plus")}Añadir tramo</button>
       <span class="dj-progress">Duración total: ${fmtMin(total)}</span></div>`;
  }

  function readInputs() {
    model.bpm_min = Number($("#dj-bmin").value) || null;
    model.bpm_max = Number($("#dj-bmax").value) || null;
    model.include_unknown = $("#dj-unknown").checked;
    model.expand = $("#dj-expand").checked;
    model.prefer_favorites = $("#dj-favs").checked;
    model.playlist = $("#dj-playlist").value || null;
  }

  function config() {
    readInputs();
    const base = { shape: model.shape, bpm_min: model.bpm_min, bpm_max: model.bpm_max,
      include_unknown: model.include_unknown, expand: model.expand, prefer_favorites: model.prefer_favorites };
    if (model.mode === "styles") {
      const segs = model.segments.filter((s) => s.styles.length);
      if (!segs.length) throw new Error("Añade al menos un estilo a un tramo");
      return { ...base, segments: segs.map((s) => ({ label: segLabel(s), styles: s.styles, minutes: s.minutes })) };
    }
    let source = { kind: "favorites", id: "", title: "Mis canciones" };
    if (model.mode === "preset" && model.preset) source = model.preset;
    if (model.mode === "playlist") {
      const p = pls.find((x) => x.id === model.playlist) || pls[0];
      if (!p) throw new Error("No tienes playlists");
      source = { kind: "playlist", id: p.id, title: p.title };
    }
    return { ...base, source, minutes: model.minutes };
  }

  function suggestedName(cfg) {
    return cfg.segments ? cfg.segments.map((s) => s.label).join(" → ") : `${shapeName(cfg.shape)} · ${cfg.source.title}`;
  }

  function load(cfg) {
    model = { ...freshModel(null), ...cfg };
    if (cfg.mode === "styles") {
      model.mode = "styles";
      model.segments = cfg.segments.map((s) => ({ styles: s.styles, minutes: s.minutes }));
    } else if (cfg.source?.kind === "playlist" && pls.some((p) => p.id === cfg.source.id)) {
      model.mode = "playlist";
      model.playlist = cfg.source.id;
    } else if (cfg.source?.kind && cfg.source.kind !== "favorites") {
      model.mode = "preset";
      model.preset = cfg.source;
    } else model.mode = "favorites";
  }

  if (session) { load(session.config); $("#dj-name").value = session.name; }

  // ---------------------------------------------------------------- eventos
  root.addEventListener("click", async (ev) => {
    const t = ev.target;
    const modeBtn = t.closest("#dj-mode [data-mode]");
    if (modeBtn) { readInputs(); model.mode = modeBtn.dataset.mode; return apply(); }
    const dur = t.closest("#dj-dur [data-min]");
    if (dur) { model.minutes = Number(dur.dataset.min); return apply(); }
    const shp = t.closest("#dj-shape [data-shape]");
    if (shp) { model.shape = shp.dataset.shape; return apply(); }
    const row = t.closest(".seg-row");
    if (t.closest("[data-add-seg]")) { model.segments.push({ styles: [], minutes: 30 }); return renderSegs(); }
    if (row && t.closest("[data-rm-seg]")) { model.segments.splice(Number(row.dataset.i), 1); return renderSegs(); }
    const rm = t.closest("[data-rm-style]");
    if (row && rm) { model.segments[Number(row.dataset.i)].styles.splice(Number(rm.dataset.rmStyle), 1); return renderSegs(); }
    const add = t.closest("[data-add-style]");
    if (row && add) { ev.stopPropagation(); return openStylePicker(add, Number(row.dataset.i)); }
  });
  root.addEventListener("change", (ev) => {
    const row = ev.target.closest(".seg-row");
    if (row && ev.target.matches("[data-seg-min]")) {
      model.segments[Number(row.dataset.i)].minutes = Number(ev.target.value);
      renderSegs();
    }
  });

  function openStylePicker(anchor, segIndex) {
    const pop = document.getElementById("popover");
    const used = new Set(model.segments[segIndex].styles.map((x) => x.query.toLowerCase()));
    pop.innerHTML = `<h4>Añadir estilo al tramo ${segIndex + 1}</h4><div class="hint">Elige uno o escribe cualquier otro (por ejemplo «Italo disco»).</div>
      <div class="style-grid">${STYLES.map(([l, q]) => `<button class="tab ${used.has(q.toLowerCase()) ? "active" : ""}" data-style="${esc(q)}" data-label="${esc(l)}">${esc(l)}</button>`).join("")}</div>
      <div class="style-free"><input type="text" id="dj-style-free" placeholder="Otro estilo…"><button class="btn primary small" id="dj-style-free-ok">Añadir</button></div>`;
    pop.hidden = false;
    const r = anchor.getBoundingClientRect(), pr = pop.getBoundingClientRect();
    pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - pr.width - 8))}px`;
    pop.style.top = `${Math.max(8, Math.min(r.bottom + 8, innerHeight - pr.height - 8))}px`;
    const addStyle = (label, query) => {
      if (!label.trim()) return;
      const seg = model.segments[segIndex];
      if (!seg.styles.some((x) => x.query.toLowerCase() === query.toLowerCase())) seg.styles.push({ label, query });
      closeOverlays();
      renderSegs();
    };
    pop.onclick = (ev) => {
      ev.stopPropagation();
      const b = ev.target.closest("[data-style]");
      if (b) return addStyle(b.dataset.label, b.dataset.style);
      if (ev.target.closest("#dj-style-free-ok")) {
        const v = pop.querySelector("#dj-style-free").value.trim();
        addStyle(v, v);
      }
    };
    const inp = pop.querySelector("#dj-style-free");
    inp.onkeydown = (ev) => { if (ev.key === "Enter") addStyle(inp.value.trim(), inp.value.trim()); };
    inp.focus();
  }

  // ------------------------------------------------------- guardar / pinchar
  async function save() {
    const cfg = config();
    const name = $("#dj-name").value.trim() || suggestedName(cfg);
    const r = await post("/dj/sessions/save", { id: session?.id, name, config: cfg });
    session = { id: r.id, name, config: cfg };
    $("#dj-name").value = name;
    return r.id;
  }
  const busy = (on) => root.querySelectorAll(".dj-actions .btn").forEach((b) => (b.disabled = on));

  $("#dj-save").onclick = async () => {
    try {
      await save();
      toast("Sesión guardada");
      location.hash = "#/dj/sesiones";
    } catch (e) { toast(e.message, true); }
  };
  $("#dj-save-play").onclick = async () => {
    busy(true);
    try {
      const id = await save();
      await playSet({ recipe_id: id }, $("#dj-progress"), goCabina);
    } catch (e) { toast(e.message, true); }
    busy(false);
  };
  $("#dj-play-only").onclick = async () => {
    let cfg;
    try { cfg = config(); } catch (e) { return toast(e.message, true); }
    busy(true);
    await playSet(cfg, $("#dj-progress"), goCabina);
    busy(false);
  };

  unsubs.push(on("dj_progress", (m) => { const p = $("#dj-progress"); if (p) p.textContent = progressText(m); }));
  apply();
}
