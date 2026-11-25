// DJ: creacion de sets y pantalla cabina
import { state, on, get, post, act, toast, invalidate } from "../core.js";
import { esc, fmtTime, artistLinks, closeOverlays } from "../ui.js";
import { icon } from "../icons.js";
import { currentMs } from "../player.js";
import { renderSessions, renderEditor, SEG_COLORS, djTabs } from "./djsessions.js";

const segColor = (i) => SEG_COLORS[(i ?? 0) % SEG_COLORS.length];

function goCabina() {
  if (location.hash === "#/dj/cabina") window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = "#/dj/cabina";
}


let unsubs = [];
let ticker = null;
let snap = null;

function cleanup() {
  unsubs.forEach((u) => u());
  unsubs = [];
  clearInterval(ticker);
  ticker = null;
}

export function leave() {
  cleanup();
}

// Rutas:
//   #/dj                         -> cabina si suena un set DJ; si no, sesiones
//   #/dj/cabina                  -> cabina (lo que esta sonando)
//   #/dj/sesiones                -> lista de sesiones guardadas
//   #/dj/sesiones/nueva          -> editor de una sesion nueva
//   #/dj/sesiones/<id>           -> editar una sesion
//   #/dj/sesiones/desde/<tipo>/<id>/<titulo> -> nueva sesion a partir de una pagina
export async function dj(el, sub, a, b, c, d) {
  cleanup();
  const isDj = state.player?.context?.type === "dj";
  if (sub === "cabina") return renderCabina(el);
  if (sub === "sesiones") {
    if (a === "nueva") return renderEditor(el, {}, unsubs, goCabina);
    if (a === "desde" && b) return renderEditor(el, { preset: { kind: b, id: c, title: d || "" } }, unsubs, goCabina);
    if (a) return renderEditor(el, { sessionId: a }, unsubs, goCabina);
    return renderSessions(el, unsubs, goCabina);
  }
  if (sub === "new") {  // enlaces antiguos
    return renderEditor(el, a ? { preset: { kind: a, id: b, title: c || "" } } : {}, unsubs, goCabina);
  }
  return isDj ? renderCabina(el) : renderSessions(el, unsubs, goCabina);
}

// ------------------------------------------------------------------ cabina
const LEVEL = {
  good: ["good", "Encaja", "check"],
  ok: ["ok", "Aceptable", "minus"],
  bad: ["bad", "Salto brusco", "close"],
  unknown: ["unknown", "Sin datos", "question"],
};

function chip(text, cls = "") { return text ? `<span class="dj-chip ${cls}">${esc(text)}</span>` : ""; }

function deckHTML(label, t, info, extra = "") {
  if (!t) return `<div class="deck empty"><div class="deck-label">${label}</div><div class="empty" style="padding:30px 0">Fin del set</div></div>`;
  const cover = t.album?.cover_large || t.album?.cover || "";
  return `<div class="deck">
    <div class="deck-label">${label}</div>
    <div class="deck-main">${cover ? `<img src="${esc(cover)}" alt="">` : `<div class="ph"></div>`}
      <div class="deck-titles"><div class="t">${esc(t.title)}</div><div class="s">${artistLinks(t.artists)}</div></div></div>
    <div class="deck-chips">${chip(info?.bpm ? `${info.bpm} BPM` : "BPM ?", "bpm")}${chip(info?.camelot ? `${info.camelot} · ${info.key_name || ""}` : "Tono ?", "key")}${info?.style ? `<span class="dj-chip style" style="--c:${segColor(info.seg)}">${esc(info.style)}</span>` : ""}</div>
    ${extra}
  </div>`;
}

let rootEl = null;

async function renderCabina(el) {
  el.innerHTML = `<div class="page dj-page" id="dj-cabina"><div class="loading"><div class="spinner"></div></div></div>`;
  rootEl = el.querySelector("#dj-cabina");
  snap = await get("/dj/state", { fresh: true });
  paint();
  const alive = () => rootEl && rootEl.isConnected;
  unsubs.push(on("dj", (s) => { snap = s; if (alive()) paint(); }));
  unsubs.push(on("queue", async () => {
    if (!alive()) return;
    snap = await get("/dj/state", { fresh: true });
    paint();
  }));
  unsubs.push(on("player", () => { if (alive()) paintLive(); }));
  ticker = setInterval(() => { if (alive()) paintLive(); }, 500);
}

function paint() {
  const root = rootEl;
  if (!root || !snap) return;
  const q = state.queue || { items: [], index: -1 };
  if (!snap.active || state.player?.context?.type !== "dj") {
    root.innerHTML = `${djTabs("cabina")}<div class="empty"><h3>No hay ningún set sonando</h3>
      Pincha una de tus sesiones o crea una nueva para verla aquí.
      <div style="display:flex;gap:10px;justify-content:center;margin-top:18px"><a class="btn primary" href="#/dj/sesiones">${icon("playlist")}Ir a Sesiones</a>
      <a class="btn secondary" href="#/dj/sesiones/nueva">${icon("plus")}Nueva sesión</a></div></div>`;
    return;
  }
  const idx = q.index;
  const cur = q.items[idx], nxt = q.items[idx + 1];
  const info = (uid) => snap.items.find((x) => x.uid === uid);
  const nInfo = nxt ? info(nxt.uid) : null;
  const tr = nInfo?.transition;
  const [lvl, lvlTxt, lvlIcon] = LEVEL[tr?.level || "unknown"];
  const segs = snap.active.segments || [];
  const curSeg = cur ? info(cur.uid)?.seg : null;
  const nxtSeg = nInfo?.seg;
  const segTxt = segs.length && curSeg != null
    ? `<span class="seg-now" style="--c:${segColor(curSeg)}">Tramo ${curSeg + 1} de ${segs.length}: ${esc(segs[curSeg]?.label || "")}</span>${nxtSeg != null && nxtSeg !== curSeg ? `<span class="seg-next">Próximo tramo: ${esc(segs[nxtSeg]?.label || "")}</span>` : ""}`
    : "";
  root.innerHTML = `${djTabs("cabina")}
    <div class="dj-head"><div><div class="eyebrow">Sonando ahora</div><h1 class="page-title" style="margin:2px 0 0">${esc(snap.active.title.replace(/^Set DJ · /, ""))}</h1>
      ${segTxt ? `<div class="seg-line">${segTxt}</div>` : ""}</div>
      <div class="dj-head-r"><span class="dj-count">Canción ${idx + 1} de ${q.items.length}</span>
      <button class="btn secondary" data-dj="save" title="Guarda el set completo, en este orden, como playlist de TIDAL">${icon("playlist")}Guardar como playlist</button>
      <button class="btn secondary" data-dj="regen" title="Rehace el resto del set con otras canciones">${icon("refresh")}Regenerar desde aquí</button></div></div>
    <div class="decks">
      ${deckHTML("Ahora", cur, cur ? info(cur.uid) : null, `<div class="deck-progress"><div class="bar"><div class="fill" id="dj-fill"></div></div><div class="times"><span id="dj-pos">0:00</span><span>${fmtTime(cur?.duration)}</span></div></div>`)}
      <div class="deck-mid">
        <div class="mid-label">Cambio en</div><div class="countdown" id="dj-countdown">–:––</div>
        ${nxt ? `<div class="compat ${lvl}">${icon(lvlIcon)}<span>${lvlTxt}</span></div>
        <div class="compat-why">${esc(tr?.key || "")}${tr?.tempo ? `<br>${esc(tr.tempo)}` : ""}</div>` : ""}
      </div>
      ${deckHTML("Siguiente", nxt, nInfo, nxt ? `<button class="btn secondary small" data-dj="alts">${icon("swap")}Otra siguiente</button>` : "")}
    </div>
    <div class="dj-card"><div class="dj-card-head"><span>Curva de energía del set</span>
      <span class="legend"><i class="dash"></i>objetivo <i class="dot on"></i>sonadas <i class="dot"></i>por sonar</span></div>
      ${curveSVG(idx)}</div>
    <div class="dj-card"><div class="dj-card-head"><span>A continuación</span><span class="legend">Pasa el ratón por una canción para cambiarla, quitarla o meter otra detrás</span></div>
      ${cur ? upcomingRow(cur, info(cur.uid), true) : ""}
      ${q.items.slice(idx + 1).map((it) => upcomingRow(it, info(it.uid))).join("") || `<div class="empty" style="padding:16px">No hay más canciones en el set</div>`}</div>`;
  root.onclick = onClick;
  paintLive();
}

function upcomingRow(it, inf, now = false) {
  const lv = LEVEL[inf?.transition?.level || "unknown"];
  const t = esc(it.title);
  return `<div class="up-row ${now ? "now" : ""}">
    ${it.album?.cover ? `<img loading="lazy" src="${esc(it.album.cover)}" alt="">` : `<div class="ph"></div>`}
    <div class="titles"><div class="t">${esc(it.title)}</div><div class="s">${esc((it.artists || []).map((a) => a.name).join(", "))}</div></div>
    <span class="num">${inf?.bpm ? `${inf.bpm} BPM` : "?"}</span>
    <span class="key">${inf?.camelot ? `${inf.camelot} · ${esc(inf.key_name || "")}` : "?"}</span>
    <span class="lv ${lv[0]}" title="${esc([lv[1], inf?.transition?.key, inf?.transition?.tempo].filter(Boolean).join(" · "))}">${now ? "" : icon(lv[2])}</span>
    <span class="up-acts">
      ${now ? `<span class="now-tag">Sonando</span>` : `<button class="icon-btn" data-edit="replace" data-uid="${esc(it.uid)}" data-title="${t}" title="Cambiar por otra">${icon("swap")}</button>`}
      <button class="icon-btn" data-edit="insert" data-uid="${esc(it.uid)}" data-title="${t}" title="Meter una canción detrás">${icon("plus")}</button>
      ${now ? "" : `<button class="icon-btn" data-edit="remove" data-uid="${esc(it.uid)}" data-title="${t}" title="Quitar del set">${icon("close")}</button>`}
    </span>
  </div>`;
}

function curveSVG(idx) {
  const W = 1000, H = 150, P = 14;
  const n = snap.items.length;
  if (!n) return "";
  const x = (i) => P + (W - 2 * P) * (n === 1 ? 0.5 : i / (n - 1));
  const y = (e) => H - P - (H - 2 * P) * e;
  const tg = snap.targets;
  const path = tg.map((e, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(e).toFixed(1)}`).join(" ");
  const dots = snap.items.map((it, i) => {
    const e = it.energy ?? tg[i] ?? 0.5;
    const cls = i < idx ? "played" : i === idx ? "now" : "next";
    return `<circle class="${cls} ${it.energy == null ? "unk" : ""}" cx="${x(i).toFixed(1)}" cy="${y(e).toFixed(1)}" r="${i === idx ? 7 : 4.5}"/>`;
  }).join("");
  // Bandas de color por tramo (recorrido de estilos)
  let bands = "", labels = "";
  const segs = snap.active?.segments || [];
  if (segs.length) {
    const step = n > 1 ? (W - 2 * P) / (n - 1) : W;
    let s = 0;
    for (let i = 1; i <= n; i++) {
      if (i === n || snap.items[i].seg !== snap.items[s].seg) {
        const seg = snap.items[s].seg ?? 0;
        const x0 = Math.max(0, x(s) - step / 2), x1 = Math.min(W, x(i - 1) + step / 2);
        bands += `<rect x="${x0.toFixed(1)}" y="0" width="${(x1 - x0).toFixed(1)}" height="${H}" fill="${segColor(seg)}" opacity=".13"/>`;
        labels += `<div class="band-label" style="left:${(x0 / W) * 100}%;width:${((x1 - x0) / W) * 100}%;color:${segColor(seg)}">${esc(segs[seg]?.label || "")}</div>`;
        s = i;
      }
    }
  }
  return `<div class="curve-wrap">${labels ? `<div class="band-labels">${labels}</div>` : ""}<svg class="curve" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Curva de energía">
    ${bands}<path d="${path}" class="target"/>${idx >= 0 ? `<line class="nowline" x1="${x(idx)}" y1="4" x2="${x(idx)}" y2="${H - 4}"/>` : ""}${dots}</svg></div>`;
}

function paintLive() {
  const p = state.player;
  const cur = p?.current;
  const fill = rootEl?.querySelector("#dj-fill");
  if (!fill || !cur) return;
  const dur = p.duration || (cur.duration || 0) * 1000;
  const pos = currentMs();
  fill.style.width = `${dur ? Math.min(100, (pos / dur) * 100) : 0}%`;
  rootEl.querySelector("#dj-pos").textContent = fmtTime(pos / 1000);
  rootEl.querySelector("#dj-countdown").textContent = dur ? fmtTime(Math.max(0, dur - pos) / 1000) : "–:––";
}

async function onClick(ev) {
  const e = ev.target.closest("[data-edit]");
  if (e) {
    ev.stopPropagation();
    if (e.dataset.edit === "remove") await act(post("/queue/remove", { uid: e.dataset.uid }), `«${e.dataset.title}» quitada del set`).catch(() => {});
    else openPicker(e, e.dataset.edit, e.dataset.uid, e.dataset.title);
    return;
  }
  const b = ev.target.closest("[data-dj]");
  if (!b) return;
  ev.stopPropagation();
  if (b.dataset.dj === "regen") {
    b.disabled = true;
    await act(post("/dj/regenerate"), "Set regenerado desde aquí").catch(() => {});
    b.disabled = false;
  } else if (b.dataset.dj === "alts") {
    openAlternatives(b);
  } else if (b.dataset.dj === "save") {
    openSavePlaylist(b);
  }
}

async function openAlternatives(anchor) {
  const pop = document.getElementById("popover");
  pop.innerHTML = `<h4>Otra siguiente</h4><div class="hint">Canciones que también encajan después de la actual</div><div class="loading" style="padding:20px 0"><div class="spinner"></div></div>`;
  pop.hidden = false;
  place(pop, anchor);
  let alts = [];
  try { alts = await post("/dj/alternatives"); } catch (e) { toast(e.message, true); }
  pop.innerHTML = `<h4>Otra siguiente</h4><div class="hint">Canciones que también encajan después de la actual</div>
    ${alts.length ? alts.map((t, i) => `<button class="dev alt" data-alt="${i}">
      ${t.album?.cover ? `<img src="${esc(t.album.cover)}" alt="">` : ""}<div style="min-width:0"><div class="n">${esc(t.title)}</div>
      <div class="m">${esc((t.artists || []).map((a) => a.name).join(", "))} · ${t.bpm} BPM · ${esc(t.camelot || "")}</div>
      <div class="m">${esc([LEVEL[t.transition?.level || "unknown"][1], t.transition?.key, t.transition?.tempo].filter(Boolean).join(" · "))}</div></div></button>`).join("")
      : `<div class="hint">No hay alternativas que encajen</div>`}`;
  place(pop, anchor);
  pop.onclick = async (ev) => {
    const btn = ev.target.closest("[data-alt]");
    if (!btn) return;
    closeOverlays();
    await act(post("/dj/replace-next", { track: alts[Number(btn.dataset.alt)] }), "Siguiente canción cambiada");
  };
}

function openSavePlaylist(anchor) {
  const pop = document.getElementById("popover");
  const n = (state.queue?.items || []).length;
  const def = `${(snap.active?.title || "Set DJ").replace(/^Set DJ · /, "")} · ${new Date().toLocaleDateString("es-ES")}`;
  pop.innerHTML = `<h4>Guardar como playlist</h4>
    <div class="hint">Se guarda en tu colección de TIDAL como playlist privada, con las ${n} canciones del set en este orden (las que han sonado y las que vienen).</div>
    <div class="pick-search"><input id="pl-name" maxlength="100" value="${esc(def)}" aria-label="Nombre de la playlist"></div>
    <div style="display:flex;justify-content:flex-end;margin-top:6px"><button class="btn primary small" id="pl-save">${icon("check")}Guardar</button></div>`;
  pop.hidden = false;
  place(pop, anchor);
  const input = pop.querySelector("#pl-name");
  setTimeout(() => { input.focus(); input.select(); }, 0);
  const save = async () => {
    const btn = pop.querySelector("#pl-save");
    btn.disabled = true;
    btn.textContent = "Guardando…";
    try {
      const r = await post("/dj/save-playlist", { name: input.value.trim() });
      invalidate("/my-playlists");
      invalidate("/collection");
      window.dispatchEvent(new Event("playlists-changed"));
      pop.innerHTML = `<h4>Playlist guardada</h4><div class="hint">«${esc(r.playlist.title)}» · ${r.tracks} canciones, en tu colección de TIDAL.</div>
        <a class="btn secondary small" href="#/playlist/${esc(r.playlist.id)}">${icon("playlist")}Abrir la playlist</a>`;
      place(pop, anchor);
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
      btn.innerHTML = `${icon("check")}Guardar`;
    }
  };
  pop.querySelector("#pl-save").onclick = save;
  input.onkeydown = (ev) => { if (ev.key === "Enter") save(); };
  pop.onclick = (ev) => { if (ev.target.closest("a")) closeOverlays(); };
}

// Cambiar una cancion del set o meter otra: las que encajan ahi + busqueda en todo TIDAL
async function openPicker(anchor, mode, uid, title) {
  const pop = document.getElementById("popover");
  const head = mode === "insert" ? `Meter detrás de «${esc(title)}»` : `Cambiar «${esc(title)}»`;
  const hint = mode === "insert" ? "Canciones que encajan entre esta y la siguiente, o busca cualquier otra" : "Canciones que encajan en ese hueco, o busca cualquier otra";
  pop.innerHTML = `<h4>${head}</h4><div class="hint">${hint}</div>
    <div class="pick-search">${icon("search")}<input id="pick-q" type="search" placeholder="Buscar en TIDAL" autocomplete="off"></div>
    <div id="pick-list"><div class="loading" style="padding:20px 0"><div class="spinner"></div></div></div>`;
  pop.classList.add("picker");
  pop.hidden = false;
  const mo = new MutationObserver(() => { if (pop.hidden) { pop.classList.remove("picker"); mo.disconnect(); } });
  mo.observe(pop, { attributes: true, attributeFilter: ["hidden"] });
  place(pop, anchor);
  let shown = [];
  const list = pop.querySelector("#pick-list");
  const show = (tracks, empty) => {
    shown = tracks;
    list.innerHTML = tracks.length ? tracks.map((t, i) => `<button class="dev alt ${t.available === false ? "na" : ""}" data-pick="${i}">
      ${t.album?.cover ? `<img src="${esc(t.album.cover)}" alt="">` : ""}<div style="min-width:0"><div class="n">${esc(t.title)}</div>
      <div class="m">${esc((t.artists || []).map((a) => a.name).join(", "))}${t.bpm ? ` · ${t.bpm} BPM` : ""}${t.camelot ? ` · ${esc(t.camelot)}` : ""}</div>
      <div class="m">${esc([LEVEL[t.transition?.level || "unknown"][1], t.transition?.key, t.transition?.tempo].filter(Boolean).join(" · "))}</div></div></button>`).join("")
      : `<div class="hint">${empty}</div>`;
    place(pop, anchor);
  };
  const alts = await post("/dj/alternatives", { uid, mode }).catch(() => []);
  show(alts, "No hay canciones del set que encajen ahí: busca otra");
  const input = pop.querySelector("#pick-q");
  let timer = null;
  input.oninput = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    timer = setTimeout(async () => {
      if (!q) return show(alts, "No hay canciones del set que encajen ahí: busca otra");
      list.innerHTML = `<div class="loading" style="padding:20px 0"><div class="spinner"></div></div>`;
      try {
        const r = await post("/dj/search", { q, uid, mode });
        if (input.value.trim() === q) show(r, "Sin resultados");
      } catch (e) { list.innerHTML = `<div class="hint">${esc(e.message)}</div>`; }
    }, 350);
  };
  setTimeout(() => input.focus(), 0);
  pop.onclick = async (ev) => {
    const btn = ev.target.closest("[data-pick]");
    if (!btn) return;
    const track = shown[Number(btn.dataset.pick)];
    closeOverlays();
    pop.classList.remove("picker");
    if (mode === "insert") await act(post("/dj/insert", { uid, track }), `«${track.title}» añadida al set`).catch(() => {});
    else await act(post("/dj/replace", { uid, track }), "Canción cambiada").catch(() => {});
  };
}

function place(pop, anchor) {
  const r = anchor.getBoundingClientRect();
  const pr = pop.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(r.right - pr.width, innerWidth - pr.width - 8))}px`;
  pop.style.top = `${Math.max(8, Math.min(r.bottom + 8, innerHeight - pr.height - 8))}px`;
}
