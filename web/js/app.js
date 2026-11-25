// Arranque: barra lateral, cabecera, rutas, delegacion de eventos e inicio de sesion
import { state, on, get, post, act, connectEvents, loadFavorites, toggleFav, AuthError, toast } from "./core.js";
import { esc, getItem, getList, playItem, playList, trackMenu, itemMenu, closeOverlays, refreshFavs, refreshPlaying, loadingHTML, errorHTML } from "./ui.js";
import { icon } from "./icons.js";
import { mountPlayerBar, mountQueue, toggleNowPlaying } from "./player.js";
import * as browse from "./views/browse.js";
import * as detail from "./views/detail.js";
import { search } from "./views/search.js";
import { collection } from "./views/collection.js";
import * as djView from "./views/dj.js";

const $ = (id) => document.getElementById(id);
const view = $("view");

// ---------------------------------------------------------------- sidebar
function mountSidebar() {
  $("sidebar").innerHTML = `
    <div class="brand">${icon("logo")}<div>TIDAL<small>Connect · Linux</small></div></div>
    <nav class="nav">
      <a href="#/" data-nav="home">${icon("home")}Inicio</a>
      <a href="#/explore" data-nav="explore">${icon("explore")}Explorar</a>
      <a href="#/dj" data-nav="dj">${icon("dj")}DJ</a>
    </nav>
    <div class="nav-title">Mi colección</div>
    <nav class="nav">
      <a href="#/collection/playlists" data-nav="collection/playlists">${icon("playlist")}Playlists</a>
      <a href="#/collection/albums" data-nav="collection/albums">${icon("album")}Álbumes</a>
      <a href="#/collection/tracks" data-nav="collection/tracks">${icon("track")}Canciones</a>
      <a href="#/collection/artists" data-nav="collection/artists">${icon("artist")}Artistas</a>
      <a href="#/collection/mixes" data-nav="collection/mixes">${icon("mix")}Mixes y radios</a>
    </nav>
    <div class="nav-title">Mis playlists</div>
    <div class="side-playlists" id="side-playlists"></div>
    <div class="side-user"><div class="avatar" id="side-avatar"></div><div class="name" id="side-name"></div>
      <button class="circle-btn" id="side-menu" title="Ajustes">${icon("settings")}</button></div>`;
  $("side-menu").onclick = (ev) => {
    const r = ev.currentTarget.getBoundingClientRect();
    import("./ui.js").then(({ openMenu }) => openMenu(r.left, r.top - 150, [
      { icon: "speaker", label: "Desconectar del altavoz", run: () => act(post("/player/disconnect", { stop: false }), "Desconectado") },
      { icon: "logout", label: "Cerrar sesión de TIDAL", run: () => act(post("/logout")) },
    ]));
  };
}

window.addEventListener("playlists-changed", () => loadMyPlaylists());

async function loadMyPlaylists() {
  try {
    const pls = await get("/my-playlists", { fresh: true });
    state.myPlaylists = pls;
    $("side-playlists").innerHTML = pls.map((p) => `<a href="#/playlist/${esc(p.id)}" data-nav="playlist/${esc(p.id)}" title="${esc(p.title)}">${esc(p.title)}</a>`).join("");
    markNav();
  } catch (e) { console.warn(e); }
}

function renderUser() {
  const u = state.status?.user;
  $("side-name").textContent = u?.name || "";
  $("side-avatar").textContent = (u?.name || "?").slice(0, 1).toUpperCase();
}

function markNav() {
  const h = location.hash.replace(/^#\/?/, "") || "home";
  const top = h.split("/")[0];
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === h || (a.dataset.nav === "dj" && top === "dj")));
}

// ----------------------------------------------------------------- topbar
function mountTopbar() {
  $("topbar").innerHTML = `
    <button class="circle-btn" id="nav-back" title="Atrás">${icon("left")}</button>
    <button class="circle-btn" id="nav-fwd" title="Adelante">${icon("right")}</button>
    <div class="search">${icon("search").replace("<svg", '<svg class="icon"')}
      <input id="search" type="search" placeholder="Buscar" autocomplete="off" spellcheck="false">
      <button class="clear" id="search-clear" hidden>${icon("close")}</button></div>
    <div class="spacer"></div>`;
  $("nav-back").onclick = () => history.back();
  $("nav-fwd").onclick = () => history.forward();
  const input = $("search");
  let t;
  input.addEventListener("input", () => {
    $("search-clear").hidden = !input.value;
    clearTimeout(t);
    t = setTimeout(() => {
      const q = input.value.trim();
      if (q.length >= 2) location.hash = `#/search/${encodeURIComponent(q)}`;
    }, 350);
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && input.value.trim()) { clearTimeout(t); location.hash = `#/search/${encodeURIComponent(input.value.trim())}`; }
    if (ev.key === "Escape") { input.value = ""; $("search-clear").hidden = true; input.blur(); }
  });
  $("search-clear").onclick = () => { input.value = ""; $("search-clear").hidden = true; input.focus(); };
}

// ----------------------------------------------------------------- router
const scrollPos = new Map();
let renderSeq = 0;

async function route() {
  closeOverlays();
  if (!$("nowplaying").hidden) toggleNowPlaying(false);
  const hash = location.hash || "#/";
  const parts = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const [name, a, b, c, d] = parts;
  const seq = ++renderSeq;
  djView.leave();
  view.innerHTML = loadingHTML();
  markNav();
  if (name !== "search") {
    const input = $("search");
    if (document.activeElement !== input) { input.value = ""; $("search-clear").hidden = true; }
  }
  try {
    const target = document.createElement("div");
    switch (name) {
      case "": case "home": await browse.home(target); break;
      case "explore": await browse.explore(target); break;
      case "more": await browse.more(target, a); break;
      case "search": $("search").value = a || ""; await search(target, a, b || "top"); break;
      case "album": await detail.album(target, a); break;
      case "artist": await detail.artist(target, a); break;
      case "playlist": await detail.playlist(target, a); break;
      case "mix": await detail.mix(target, a); break;
      case "collection": await collection(target, a); break;
      case "dj": await djView.dj(target, ...parts.slice(1)); break;
      default: target.innerHTML = `<div class="empty"><h3>Página no encontrada</h3></div>`;
    }
    if (seq !== renderSeq) return;
    view.replaceChildren(...target.childNodes);
    view.scrollTop = scrollPos.get(hash) || 0;
    refreshPlaying(view);
  } catch (e) {
    if (seq !== renderSeq) return;
    if (e instanceof AuthError) return showLogin();
    view.innerHTML = errorHTML(e);
  }
}

view.addEventListener("scroll", () => scrollPos.set(location.hash || "#/", view.scrollTop), { passive: true });

// ------------------------------------------------------ delegacion de eventos
document.addEventListener("click", (ev) => {
  const menu = $("menu"), pop = $("popover");
  if (!menu.hidden && !ev.target.closest("#menu")) menu.hidden = true;
  if (!pop.hidden && !ev.target.closest("#popover") && !ev.target.closest("#pb-device")) pop.hidden = true;

  const actEl = ev.target.closest("[data-act]");
  if (actEl) {
    const a = actEl.dataset.act;
    ev.preventDefault();
    ev.stopPropagation();
    switch (a) {
      case "play-item": return playItem(getItem(actEl.dataset.item));
      case "play-row": {
        const row = actEl.closest(".trow");
        return playList(row.dataset.list, Number(row.dataset.i));
      }
      case "play-list": return playList(actEl.dataset.list, 0, { shuffle: false });
      case "shuffle-list": return playList(actEl.dataset.list, 0, { shuffle: true });
      case "fav": return toggleFav(actEl.dataset.kind, actEl.dataset.id);
      case "track-menu": {
        const row = actEl.closest(".trow");
        const t = getList(row.dataset.list).tracks[Number(row.dataset.i)];
        const r = actEl.getBoundingClientRect();
        return trackMenu(t, r.left - 200, r.bottom + 4);
      }
      case "item-menu": {
        const r = actEl.getBoundingClientRect();
        return itemMenu(getItem(actEl.dataset.item), r.left, r.bottom + 4);
      }
      case "artist-radio": return act(post("/radio", { kind: "artist", id: actEl.dataset.id, title: `Radio de ${actEl.dataset.name}` }));
      case "scroll": {
        const c = $(actEl.dataset.target);
        return c?.scrollBy({ left: Number(actEl.dataset.dir) * c.clientWidth * 0.85, behavior: "smooth" });
      }
      case "expand": return actEl.classList.toggle("clamped");
    }
    return;
  }
  if (ev.target.closest("a, button, input")) return;
  const nav = ev.target.closest("[data-href]");
  if (nav) location.hash = nav.dataset.href;
});

// doble clic en una fila: reproducir desde ahi (como en TIDAL)
document.addEventListener("dblclick", (ev) => {
  const row = ev.target.closest(".trow");
  if (row && !ev.target.closest("button, a")) playList(row.dataset.list, Number(row.dataset.i));
});

document.addEventListener("contextmenu", (ev) => {
  const row = ev.target.closest(".trow");
  if (row) {
    ev.preventDefault();
    return trackMenu(getList(row.dataset.list).tracks[Number(row.dataset.i)], ev.clientX, ev.clientY);
  }
  const card = ev.target.closest("[data-item]");
  if (card && !ev.target.closest("#queue-panel")) {
    ev.preventDefault();
    const it = getItem(card.dataset.item);
    if (it?.type === "track") return trackMenu(it, ev.clientX, ev.clientY);
    if (it) itemMenu(it, ev.clientX, ev.clientY);
  }
});

document.addEventListener("keydown", (ev) => {
  const typing = ev.target.closest("input, textarea");
  if ((ev.ctrlKey && ev.key === "f") || (!typing && ev.key === "/")) { ev.preventDefault(); $("search").focus(); $("search").select(); return; }
  if (typing) return;
  if (ev.key === " ") { ev.preventDefault(); act(post("/player/toggle")); }
  else if (ev.altKey && ev.key === "ArrowLeft") history.back();
  else if (ev.altKey && ev.key === "ArrowRight") history.forward();
  else if (ev.ctrlKey && ev.key === "ArrowRight") act(post("/player/next"));
  else if (ev.ctrlKey && ev.key === "ArrowLeft") act(post("/player/previous"));
  else if (ev.key === "Escape") { closeOverlays(); if (!$("nowplaying").hidden) toggleNowPlaying(false); }
});

on("favorites", () => refreshFavs());

// Ningun fallo silencioso: cualquier error no capturado se muestra.
window.addEventListener("unhandledrejection", (ev) => {
  console.error(ev.reason);
  toast(`Algo ha fallado: ${ev.reason?.message || ev.reason}`, true);
});
window.addEventListener("error", (ev) => {
  if (ev.message) toast(`Algo ha fallado: ${ev.message}`, true);
});

// ------------------------------------------------------------ inicio sesion
function showLogin() {
  const box = $("login");
  box.hidden = false;
  box.innerHTML = `<div class="box">${icon("logo").replace("<svg", '<svg class="logo"')}
    <h1>Conecta tu cuenta de TIDAL</h1>
    <p>Autoriza esta app en la web oficial de TIDAL. Tu contraseña nunca pasa por aquí.</p>
    <button class="btn primary" id="login-go">Iniciar sesión</button>
    <div id="login-code"></div></div>`;
  $("login-go").onclick = async () => {
    try {
      const d = await post("/login");
      $("login-code").innerHTML = `<p style="margin:28px 0 0">Abre este enlace y confirma el código:</p>
        <div class="code">${esc(d.code)}</div>
        <a class="link" href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.url)}</a>
        <p style="margin-top:18px;font-size:12px">Esperando autorización… (caduca en ${Math.round(d.expires_in / 60)} min)</p>`;
      window.open(d.url, "_blank", "noopener");
    } catch (e) { toast(e.message, true); }
  };
}

on("auth", (m) => {
  if (m.ok) { $("login").hidden = true; start(); }
  else if (!state.status?.logged_in) showLogin();
});

// ---------------------------------------------------------------- arranque
let started = false;
function start() {
  renderUser();
  loadMyPlaylists();
  loadFavorites();
  if (!started) {
    started = true;
    window.addEventListener("hashchange", route);
  }
  route();
}

mountSidebar();
mountTopbar();
mountPlayerBar();
mountQueue();
connectEvents();

let first = true;
on("status", (st) => {
  if (!first) return;
  first = false;
  if (st.logged_in) start();
  else showLogin();
});
