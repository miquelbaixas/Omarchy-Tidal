// Paginas de album, artista, playlist y mix
import { get, isFav } from "../core.js";
import { esc, fmtLong, plural, artistLinks, qualityBadge, trackListHTML, sectionHTML, registerList, contextOf, registerItem } from "../ui.js";
import { icon } from "../icons.js";

function heroHTML({ eyebrow, title, cover, byline, meta, desc }) {
  return `<div class="hero">
    <div class="hero-bg">${cover ? `<img src="${esc(cover)}" alt="">` : ""}</div>
    ${cover ? `<img class="cover" src="${esc(cover)}" alt="">` : `<div class="cover"></div>`}
    <div class="info"><div class="eyebrow">${eyebrow}</div><h1 title="${esc(title)}">${esc(title)}</h1>
      ${byline ? `<div class="byline">${byline}</div>` : ""}
      ${meta ? `<div class="meta">${meta}</div>` : ""}
      ${desc ? `<div class="desc">${desc}</div>` : ""}</div>
  </div>`;
}

export const djLink = (it) =>
  `<a class="btn secondary" href="#/dj/sesiones/desde/${it.type}/${encodeURIComponent(it.id)}/${encodeURIComponent(it.title || it.name || "")}" title="Crear una sesión DJ con esta música">${icon("dj")}Set DJ</a>`;

function actionsHTML(lid, favKind, favId, item, djItem = item) {
  const fav = favKind ? isFav(favKind, favId) : false;
  const ref = item ? registerItem(item) : "";
  return `<div class="actions">
    <button class="btn primary" data-act="play-list" data-list="${lid}">${icon("play")}Reproducir</button>
    <button class="btn secondary" data-act="shuffle-list" data-list="${lid}">${icon("shuffle")}Aleatorio</button>
    ${djItem ? djLink(djItem) : ""}
    ${favKind ? `<button class="icon-btn ${fav ? "on" : ""}" data-act="fav" data-kind="${favKind}" data-id="${esc(favId)}" title="Añadir a Mi colección">${icon(fav ? "heartFill" : "heart")}</button>` : ""}
    ${item ? `<button class="icon-btn" data-act="item-menu" data-item="${ref}" title="Más">${icon("more")}</button>` : ""}
  </div>`;
}

function stripHtml(s) {
  return String(s || "").replace(/\[wimpLink[^\]]*\]([^[]*)\[\/wimpLink\]/g, "$1").replace(/<[^>]+>/g, "");
}

export async function album(el, id) {
  const d = await get(`/album/${id}`);
  const a = d.album;
  const totalSec = a.duration || d.tracks.reduce((s, t) => s + t.duration, 0);
  const type = (a.album_type || "ALBUM").toUpperCase() === "EP" ? "EP" : (a.album_type || "").toUpperCase() === "SINGLE" ? "Sencillo" : "Álbum";
  const multiDisc = new Set(d.tracks.map((t) => t.volume_num)).size > 1;
  const lid = registerList(d.tracks, contextOf(a));
  el.innerHTML = heroHTML({
    eyebrow: type,
    title: a.title,
    cover: a.cover_large || a.cover,
    byline: artistLinks(a.artists),
    meta: `${a.year ? `<span>${a.year}</span>·` : ""}<span>${plural(d.tracks.length, "canción", "canciones")}</span>·<span>${fmtLong(totalSec)}</span>${qualityBadge(a.quality)}${a.explicit ? `<span class="explicit">E</span>` : ""}`,
  }) + actionsHTML(lid, "album", a.id, a) +
    (multiDisc ? discsHTML(d.tracks, contextOf(a)) : trackListHTML(d.tracks, { context: contextOf(a), showAlbum: false, showCover: false, numbered: true })) +
    `<div class="page" style="margin-top:40px">
      ${a.copyright ? `<p style="color:var(--text-3);font-size:12px;margin:-16px 0 32px">${esc(a.release_date ? new Date(a.release_date).toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" }) : "")}<br>${esc(a.copyright)}</p>` : ""}
      ${d.review ? `<section class="section"><div class="section-head"><h2>Reseña</h2></div><div class="bio clamped" data-act="expand">${esc(stripHtml(d.review))}</div></section>` : ""}
      ${sectionHTML({ title: `Más de ${a.artists?.[0]?.name || ""}`, items: d.more_by_artist })}
    </div>`;
}

function discsHTML(tracks, ctx) {
  const vols = [...new Set(tracks.map((t) => t.volume_num))];
  return vols.map((v) => `<div style="padding:16px 44px 4px;color:var(--text-2);font-weight:700;display:flex;gap:8px;align-items:center">${icon("disc").replace("<svg", '<svg style="width:18px;height:18px"')}Disco ${v}</div>` +
    trackListHTML(tracks.filter((t) => t.volume_num === v), { context: ctx, showAlbum: false, showCover: false, numbered: true, header: v === vols[0] })).join("");
}

export async function artist(el, id) {
  const d = await get(`/artist/${id}`);
  const a = d.artist;
  const ctx = contextOf(a);
  const lid = registerList(d.top_tracks, { ...ctx, title: `${a.name}: populares` });
  const fav = isFav("artist", a.id);
  const ref = registerItem(a);
  el.innerHTML = `<div class="artist-hero">
      <div class="bg">${a.picture_large ? `<img src="${esc(a.picture_large)}" alt="">` : ""}</div>
      <div class="info"><div><div class="eyebrow" style="font-size:12px;font-weight:700;letter-spacing:.08em;color:var(--text-2)">ARTISTA</div>
      <h1>${esc(a.name)}</h1></div></div>
    </div>
    <div class="actions">
      <button class="btn primary" data-act="play-list" data-list="${lid}">${icon("play")}Reproducir</button>
      <button class="btn secondary" data-act="artist-radio" data-id="${esc(a.id)}" data-name="${esc(a.name)}">${icon("radio")}Radio del artista</button>
      ${djLink(a)}
      <button class="icon-btn ${fav ? "on" : ""}" data-act="fav" data-kind="artist" data-id="${esc(a.id)}" title="Seguir">${icon(fav ? "heartFill" : "heart")}</button>
      <button class="icon-btn" data-act="item-menu" data-item="${ref}" title="Más">${icon("more")}</button>
    </div>
    <div class="page">
      ${d.top_tracks.length ? `<section class="section"><div class="section-head"><h2>Canciones populares</h2></div>${trackListHTML(d.top_tracks, { context: { ...ctx, title: `${a.name}: populares` }, header: false })}</section>` : ""}
      ${sectionHTML({ title: "Álbumes", items: d.albums })}
      ${sectionHTML({ title: "EP y sencillos", items: d.singles })}
      ${sectionHTML({ title: "Aparece en", items: d.appears_on })}
      ${sectionHTML({ title: "Artistas similares", items: d.similar })}
      ${d.bio ? `<section class="section"><div class="section-head"><h2>Biografía</h2></div><div class="bio clamped" data-act="expand">${esc(stripHtml(d.bio))}</div></section>` : ""}
    </div>`;
}

export async function playlist(el, id) {
  const d = await get(`/playlist/${id}`);
  const p = d.playlist;
  const total = d.tracks.reduce((s, t) => s + t.duration, 0);
  const lid = registerList(d.tracks, contextOf(p));
  el.innerHTML = heroHTML({
    eyebrow: "Playlist",
    title: p.title,
    cover: p.cover_large || p.cover,
    byline: p.creator ? `<span>De ${esc(p.creator)}</span>` : "",
    meta: `<span>${plural(d.tracks.length, "canción", "canciones")}</span>·<span>${fmtLong(total)}</span>${p.updated ? `·<span>Actualizada ${new Date(p.updated).toLocaleDateString("es-ES")}</span>` : ""}`,
    desc: p.description ? esc(stripHtml(p.description)) : "",
  }) + actionsHTML(lid, "playlist", p.id, p) + trackListHTML(d.tracks, { context: contextOf(p) });
}

export async function mix(el, id) {
  const d = await get(`/mix/${id}`);
  const m = d.mix;
  const total = d.tracks.reduce((s, t) => s + t.duration, 0);
  const lid = registerList(d.tracks, contextOf(m));
  el.innerHTML = heroHTML({
    eyebrow: "Mix",
    title: m.title,
    cover: m.cover_large || m.cover,
    byline: m.subtitle ? `<span style="color:var(--text-2);font-weight:500">${esc(m.subtitle)}</span>` : "",
    meta: `<span>${plural(d.tracks.length, "canción", "canciones")}</span>·<span>${fmtLong(total)}</span>`,
  }) + actionsHTML(lid, null, null, null, m) + trackListHTML(d.tracks, { context: contextOf(m) });
}
