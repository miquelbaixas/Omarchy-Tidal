// Inicio, Explorar y "Ver todo"
import { get } from "../core.js";
import { esc, sectionHTML, cardHTML, trackListHTML } from "../ui.js";

function greeting() {
  const h = new Date().getHours();
  return h < 6 ? "Buenas noches" : h < 13 ? "Buenos días" : h < 21 ? "Buenas tardes" : "Buenas noches";
}

export async function home(el) {
  const d = await get("/home");
  el.innerHTML = `<div class="page"><h1 class="page-title">${greeting()}</h1>
    ${d.sections.map((s) => sectionHTML(s)).join("")}</div>`;
}

export async function explore(el) {
  const d = await get("/explore");
  el.innerHTML = `<div class="page"><h1 class="page-title">Explorar</h1>
    ${d.sections.map((s) => sectionHTML(s)).join("") || `<div class="empty">Sin contenido</div>`}</div>`;
}

export async function more(el, key) {
  const d = await get(`/more/${encodeURIComponent(key)}`);
  const body = d.layout === "tracks"
    ? trackListHTML(d.items, { context: { type: "section", title: d.title } })
    : `<div class="grid">${(d.items || []).map(cardHTML).join("")}</div>`;
  el.innerHTML = `<div class="page"><h1 class="page-title">${esc(d.title)}</h1>${body}</div>`;
}
