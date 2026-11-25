// Utilidades de pintado: tarjetas, listas de canciones, secciones y menus
import { icon } from "./icons.js";
import { state, post, act, isFav, toggleFav, toast } from "./core.js";

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function fmtLong(sec) {
  sec = Math.floor(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export function artistLinks(artists = [], cls = "link") {
  return artists.map((a) => `<a class="${cls}" href="#/artist/${esc(a.id)}">${esc(a.name)}</a>`).join(", ");
}

export function qualityBadge(q) {
  if (q === "MAX") return `<span class="q max">MAX</span>`;
  if (q === "ATMOS") return `<span class="q atmos">ATMOS</span>`;
  return "";
}

export const explicitBadge = (t) => (t.explicit ? `<span class="explicit" title="Explícito">E</span>` : "");

export const routeOf = (it) => `#/${it.type}/${encodeURIComponent(it.id)}`;

// ------------------------------------------------------------ registro de listas
// Las filas y tarjetas guardan una referencia a su lista; asi un clic sabe que reproducir.
const lists = new Map();
let listSeq = 0;
export function registerList(tracks, context) {
  const id = `l${++listSeq}`;
  lists.set(id, { tracks, context });
  if (lists.size > 200) lists.delete(lists.keys().next().value);
  return id;
}
export const getList = (id) => lists.get(id);

const items = new Map();
let itemSeq = 0;
export function registerItem(it) {
  const id = `i${++itemSeq}`;
  items.set(id, it);
  if (items.size > 3000) items.delete(items.keys().next().value);
  return id;
}
export const getItem = (id) => items.get(id);

// ------------------------------------------------------------------ tarjetas
function subtitle(it) {
  switch (it.type) {
    case "album": return `${artistLinksPlain(it.artists)}`;
    case "playlist": return esc(it.creator ? `De ${it.creator}` : it.num_tracks ? plural(it.num_tracks, "canción", "canciones") : "");
    case "mix": return esc(it.subtitle || "");
    case "artist": return "Artista";
    case "track": return artistLinksPlain(it.artists);
    default: return "";
  }
}
const artistLinksPlain = (artists = []) => esc(artists.map((a) => a.name).join(", "));

export function cardHTML(it) {
  const ref = registerItem(it);
  const img = it.cover || it.picture || it.album?.cover || "";
  const href = it.type === "track" ? (it.album?.id ? `#/album/${it.album.id}` : "#") : routeOf(it);
  return `<div class="card ${it.type}" data-item="${ref}" data-href="${href}">
    <div class="art">${img ? `<img loading="lazy" src="${esc(img)}" alt="">` : ""}<div class="shade"></div>
      <button class="play" data-act="play-item" data-item="${ref}" title="Reproducir">${icon("play")}</button></div>
    <div class="t" title="${esc(it.title || it.name)}">${esc(it.title || it.name)}</div>
    <div class="s">${it.type === "album" && it.year ? `${esc(it.year)} · ` : ""}${subtitle(it)}</div>
  </div>`;
}

export function shortcutHTML(it) {
  const ref = registerItem(it);
  const img = it.cover || it.picture || "";
  return `<div class="shortcut" data-item="${ref}" data-href="${routeOf(it)}">
    ${img ? `<img loading="lazy" src="${esc(img)}" alt="">` : `<div style="width:60px"></div>`}
    <div class="t">${esc(it.title || it.name)}</div>
    <button class="play" data-act="play-item" data-item="${ref}" title="Reproducir">${icon("play")}</button>
  </div>`;
}

let carSeq = 0;
export function sectionHTML({ title, subtitle: sub, items, layout = "cards", more, context }, opts = {}) {
  if (!items?.length) return "";
  const cid = `car${++carSeq}`;
  const head = `<div class="section-head">
      ${context ? `<div class="ctx">${context.cover || context.picture ? `<img src="${esc(context.cover || context.picture)}" alt="">` : ""}<div><div class="eyebrow">${esc(title)}</div><h2><a class="link" href="${routeOf(context)}">${esc(context.title || context.name)}</a></h2></div></div>`
        : `<div><h2>${esc(title)}</h2>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>`}
      <div class="grow"></div>
      ${more ? `<a class="view-all" href="#/more/${encodeURIComponent(more)}">Ver todo</a>` : opts.viewAll ? `<a class="view-all" href="${opts.viewAll}">Ver todo</a>` : ""}
      ${layout === "cards" ? `<div class="carousel-nav"><button class="circle-btn" data-act="scroll" data-target="${cid}" data-dir="-1">${icon("left")}</button><button class="circle-btn" data-act="scroll" data-target="${cid}" data-dir="1">${icon("right")}</button></div>` : ""}
    </div>`;
  if (layout === "shortcuts") return `<section class="section">${head}<div class="shortcuts">${items.map(shortcutHTML).join("")}</div></section>`;
  if (layout === "tracks") return `<section class="section">${head}${trackListHTML(items, { context: opts.context || { type: "section", title }, compact: true, header: false })}</section>`;
  if (layout === "grid") return `<section class="section">${head}<div class="grid">${items.map(cardHTML).join("")}</div></section>`;
  return `<section class="section">${head}<div class="carousel" id="${cid}">${items.map(cardHTML).join("")}</div></section>`;
}

// ------------------------------------------------------------ lista de pistas
export function trackListHTML(tracks, { context = null, showAlbum = true, showCover = true, numbered = false, header = true, compact = false } = {}) {
  if (!tracks?.length) return `<div class="empty">No hay canciones</div>`;
  const lid = registerList(tracks, context);
  const cur = currentTrackId();
  const playing = state.player?.state === "PLAYING";
  const rows = tracks.map((t, i) => {
    const isCur = cur && cur === t.id;
    const fav = isFav("track", t.id);
    const numCell = isCur
      ? `<span class="eq ${playing ? "" : "paused"}"><i></i><i></i><i></i></span>`
      : `<span class="n">${numbered ? (t.track_num ?? i + 1) : i + 1}</span>`;
    return `<div class="trow ${isCur ? "playing" : ""} ${t.available === false ? "unavailable" : ""}" data-list="${lid}" data-i="${i}" data-tid="${esc(t.id)}">
      <div class="num">${numCell}<button class="pb" data-act="play-row" title="Reproducir">${icon("play")}</button></div>
      <div class="main-col">${showCover && t.album?.cover ? `<img loading="lazy" src="${esc(t.album.cover)}" alt="">` : ""}
        <div class="titles"><div class="title"><span class="tx">${esc(t.title)}</span>${explicitBadge(t)}${qualityBadge(t.quality)}</div>
        <div class="artists">${artistLinks(t.artists)}</div></div></div>
      ${showAlbum ? `<div class="album">${t.album?.id ? `<a class="link" href="#/album/${esc(t.album.id)}">${esc(t.album.title)}</a>` : ""}</div>` : ""}
      <div class="dur">${fmtTime(t.duration)}</div>
      <div class="tools"><button class="icon-btn ${fav ? "on" : ""}" data-act="fav" data-kind="track" data-id="${esc(t.id)}" title="Favorito">${icon(fav ? "heartFill" : "heart")}</button>
        <button class="icon-btn" data-act="track-menu" title="Más">${icon("more")}</button></div>
    </div>`;
  }).join("");
  const head = header ? `<div class="head"><div style="text-align:center">#</div><div>Título</div>${showAlbum ? `<div class="album">Álbum</div>` : ""}<div class="r">Duración</div><div></div></div>` : "";
  return `<div class="tracks ${showAlbum ? "" : "no-album"} ${compact ? "compact" : ""}">${head}${rows}</div>`;
}

export function currentTrackId() {
  const p = state.player;
  return p?.current?.id || null;
}

// Refresca el resaltado de la cancion actual sin repintar la vista.
export function refreshPlaying(root = document) {
  const cur = currentTrackId();
  const playing = state.player?.state === "PLAYING";
  root.querySelectorAll(".trow").forEach((row) => {
    const isCur = cur && row.dataset.tid === cur;
    const was = row.classList.contains("playing");
    if (isCur !== was || isCur) {
      row.classList.toggle("playing", !!isCur);
      const num = row.querySelector(".num");
      const n = row.querySelector(".num .n, .num .eq");
      if (isCur) {
        const html = `<span class="eq ${playing ? "" : "paused"}"><i></i><i></i><i></i></span>`;
        if (n) n.outerHTML = html; else num.insertAdjacentHTML("afterbegin", html);
      } else if (n && n.classList.contains("eq")) {
        n.outerHTML = `<span class="n">${Number(row.dataset.i) + 1}</span>`;
      }
    }
  });
}

export function refreshFavs(root = document) {
  root.querySelectorAll('[data-act="fav"]').forEach((b) => {
    const on = isFav(b.dataset.kind, b.dataset.id);
    b.classList.toggle("on", on);
    b.innerHTML = icon(on ? "heartFill" : "heart");
  });
}

// -------------------------------------------------------------------- menus
export function openMenu(x, y, entries) {
  const m = document.getElementById("menu");
  m.innerHTML = entries.map((e, i) => (e === "-" ? "<hr>" : `<button data-mi="${i}">${icon(e.icon || "")}<span>${esc(e.label)}</span></button>`)).join("");
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.min(x, innerWidth - r.width - 8)}px`;
  m.style.top = `${Math.min(y, innerHeight - r.height - 8)}px`;
  m.onclick = (ev) => {
    const b = ev.target.closest("[data-mi]");
    if (!b) return;
    closeOverlays();
    entries[Number(b.dataset.mi)].run();
  };
}

export function closeOverlays() {
  document.getElementById("menu").hidden = true;
  document.getElementById("popover").hidden = true;
}

export function trackMenu(t, x, y) {
  const entries = [
    { icon: "playNext", label: "Reproducir a continuación", run: () => act(post("/queue/add", { tracks: [t], next: true }), "Se reproducirá a continuación") },
    { icon: "addQueue", label: "Añadir a la cola", run: () => act(post("/queue/add", { tracks: [t], next: false }), "Añadida a la cola") },
    "-",
    { icon: "radio", label: "Ir a la radio de la canción", run: () => act(post("/radio", { kind: "track", id: t.id })) },
    { icon: isFav("track", t.id) ? "heartFill" : "heart", label: isFav("track", t.id) ? "Quitar de Mi colección" : "Añadir a Mi colección", run: () => toggleFav("track", t.id) },
    "-",
  ];
  if (t.album?.id) entries.push({ icon: "album", label: "Ir al álbum", run: () => (location.hash = `#/album/${t.album.id}`) });
  for (const a of (t.artists || []).slice(0, 3)) entries.push({ icon: "artist", label: `Ir a ${a.name}`, run: () => (location.hash = `#/artist/${a.id}`) });
  entries.push("-", { icon: "share", label: "Copiar enlace de TIDAL", run: () => copyLink(`https://tidal.com/browse/track/${t.id}`) });
  openMenu(x, y, entries);
}

export function itemMenu(it, x, y) {
  const label = { album: "álbum", playlist: "playlist", mix: "mix", artist: "artista" }[it.type] || "";
  const entries = [
    { icon: "play", label: "Reproducir", run: () => playItem(it) },
    { icon: "shuffle", label: "Reproducir en aleatorio", run: () => playItem(it, { shuffle: true }) },
    { icon: "playNext", label: "Reproducir a continuación", run: () => act(post("/queue/add", { kind: it.type, id: it.id, next: true }), "Se reproducirá a continuación") },
    { icon: "addQueue", label: "Añadir a la cola", run: () => act(post("/queue/add", { kind: it.type, id: it.id, next: false }), "Añadido a la cola") },
  ];
  if (it.type === "artist") entries.push({ icon: "radio", label: "Radio del artista", run: () => act(post("/radio", { kind: "artist", id: it.id, title: `Radio de ${it.name}` })) });
  if (["album", "artist", "playlist"].includes(it.type)) {
    const on = isFav(it.type, it.id);
    entries.push("-", { icon: on ? "heartFill" : "heart", label: on ? "Quitar de Mi colección" : "Añadir a Mi colección", run: () => toggleFav(it.type, it.id) });
  }
  entries.push("-", { icon: "share", label: `Copiar enlace del ${label}`, run: () => copyLink(`https://tidal.com/browse/${it.type}/${it.id}`) });
  openMenu(x, y, entries);
}

async function copyLink(url) {
  try { await navigator.clipboard.writeText(url); toast("Enlace copiado"); } catch { toast(url); }
}

export function contextOf(it) {
  return { type: it.type, id: String(it.id), title: it.title || it.name };
}

export function playItem(it, { shuffle = false } = {}) {
  if (it.type === "track") return act(post("/play", { tracks: [it], index: 0, context: null }));
  return act(post("/play", { kind: it.type, id: it.id, index: 0, shuffle, context: contextOf(it) }));
}

export function playList(lid, index, { shuffle } = {}) {
  const l = getList(lid);
  if (!l) return;
  const body = { tracks: l.tracks, index, context: l.context };
  if (shuffle !== undefined) body.shuffle = shuffle;
  return act(post("/play", body));
}

export const loadingHTML = () => `<div class="loading"><div class="spinner"></div></div>`;
export const errorHTML = (e) => `<div class="empty"><h3>No se ha podido cargar</h3>${esc(e.message || e)}</div>`;
