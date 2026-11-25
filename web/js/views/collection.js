// Mi coleccion: playlists, albumes, canciones, artistas y mixes
import { get } from "../core.js";
import { cardHTML, trackListHTML, registerList, plural } from "../ui.js";
import { icon } from "../icons.js";

const TITLES = { playlists: "Playlists", albums: "Álbumes", tracks: "Canciones", artists: "Artistas", mixes: "Mixes y radios" };

export async function collection(el, kind) {
  const items = await get(`/collection/${kind}`, { fresh: true });
  const title = TITLES[kind] || "Mi colección";
  let body;
  if (!items.length) {
    body = `<div class="empty"><h3>Aún no hay nada aquí</h3>Pulsa el corazón en cualquier ${kind === "tracks" ? "canción" : "elemento"} para añadirlo.</div>`;
  } else if (kind === "tracks") {
    const ctx = { type: "collection", id: "tracks", title: "Mis canciones" };
    const lid = registerList(items, ctx);
    body = `<div class="actions" style="padding:0 0 24px">
        <button class="btn primary" data-act="play-list" data-list="${lid}">${icon("play")}Reproducir</button>
        <button class="btn secondary" data-act="shuffle-list" data-list="${lid}">${icon("shuffle")}Aleatorio</button>
        <span style="color:var(--text-2);margin-left:8px">${plural(items.length, "canción", "canciones")}</span>
      </div>${trackListHTML(items, { context: ctx })}`;
  } else {
    body = `<div class="grid">${items.map(cardHTML).join("")}</div>`;
  }
  el.innerHTML = `<div class="page"><div class="eyebrow" style="color:var(--text-2);font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-top:8px">Mi colección</div>
    <h1 class="page-title" style="margin-top:4px">${title}</h1>${body}</div>`;
}
