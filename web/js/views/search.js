// Resultados de busqueda con pestanas, como en TIDAL
import { get } from "../core.js";
import { esc, cardHTML, trackListHTML, sectionHTML, registerItem, routeOf } from "../ui.js";
import { icon } from "../icons.js";

const TABS = [
  ["top", "Principales"],
  ["tracks", "Canciones"],
  ["artists", "Artistas"],
  ["albums", "Álbumes"],
  ["playlists", "Playlists"],
];

export async function search(el, q, tab = "top") {
  if (!q) {
    el.innerHTML = `<div class="page"><div class="empty"><h3>Busca en TIDAL</h3>Artistas, canciones, álbumes y playlists</div></div>`;
    return;
  }
  const d = await get(`/search?q=${encodeURIComponent(q)}`);
  const tabs = `<div class="tabs">${TABS.map(([k, label]) =>
    `<a class="tab ${k === tab ? "active" : ""}" href="#/search/${encodeURIComponent(q)}/${k}">${label}</a>`).join("")}</div>`;
  let body = "";
  const ctx = { type: "search", title: `Búsqueda: ${q}` };
  if (tab === "top") {
    const top = d.top_hit;
    const topHTML = top ? (() => {
      const ref = registerItem(top);
      const img = top.cover || top.picture || top.album?.cover || "";
      const kind = { artist: "Artista", album: "Álbum", track: "Canción", playlist: "Playlist" }[top.type] || "";
      return `<div class="top-hit ${top.type}" data-item="${ref}" data-href="${top.type === "track" ? `#/album/${top.album?.id}` : routeOf(top)}">
        ${img ? `<img src="${esc(img)}" alt="">` : ""}
        <div><div class="eyebrow" style="color:var(--text-2);font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase">${kind}</div>
        <div class="t">${esc(top.title || top.name)}</div>
        <div style="color:var(--text-2)">${esc((top.artists || []).map((a) => a.name).join(", "))}</div></div>
        <button class="btn primary" style="margin-left:auto" data-act="play-item" data-item="${ref}">${icon("play")}Reproducir</button>
      </div>`;
    })() : "";
    body = `<div class="search-top">
        <div><div class="section-head"><h2>Mejor resultado</h2></div>${topHTML}</div>
        <div><div class="section-head"><h2>Canciones</h2><div class="grow"></div><a class="view-all" href="#/search/${encodeURIComponent(q)}/tracks">Ver todo</a></div>
          ${trackListHTML(d.tracks.slice(0, 4), { context: ctx, showAlbum: false, header: false })}</div>
      </div>
      ${sectionHTML({ title: "Artistas", items: d.artists.slice(0, 20) }, { viewAll: `#/search/${encodeURIComponent(q)}/artists` })}
      ${sectionHTML({ title: "Álbumes", items: d.albums.slice(0, 20) }, { viewAll: `#/search/${encodeURIComponent(q)}/albums` })}
      ${sectionHTML({ title: "Playlists", items: d.playlists.slice(0, 20) }, { viewAll: `#/search/${encodeURIComponent(q)}/playlists` })}`;
  } else if (tab === "tracks") {
    body = trackListHTML(d.tracks, { context: ctx });
  } else {
    const list = d[tab] || [];
    body = list.length ? `<div class="grid">${list.map(cardHTML).join("")}</div>` : `<div class="empty">Sin resultados</div>`;
  }
  el.innerHTML = `<div class="page"><h1 class="page-title">Resultados para “${esc(q)}”</h1>${tabs}${body}</div>`;
}
