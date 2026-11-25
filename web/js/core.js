// Comunicacion con el motor local + estado compartido de la interfaz

export class AuthError extends Error {}

const cache = new Map();
const CACHE_MS = 5 * 60 * 1000;

export async function get(path, { fresh = false } = {}) {
  const hit = cache.get(path);
  if (!fresh && hit && Date.now() - hit.t < CACHE_MS) return hit.data;
  const r = await fetch(`/api${path}`);
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) throw new AuthError("login");
  if (!r.ok) throw new Error(data.error || r.statusText);
  cache.set(path, { t: Date.now(), data });
  return data;
}

export function invalidate(prefix) {
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k);
}

export async function post(path, body = {}) {
  const r = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// ------------------------------------------------------------------ estado
const listeners = new Map();
export const state = {
  status: null,
  player: null,
  queue: { index: -1, items: [], context: null },
  devices: [],
  favorites: { track: new Set(), album: new Set(), artist: new Set(), playlist: new Set() },
  myPlaylists: [],
};

export function on(evt, fn) {
  if (!listeners.has(evt)) listeners.set(evt, new Set());
  listeners.get(evt).add(fn);
  return () => listeners.get(evt).delete(fn);
}
export function emit(evt, data) {
  for (const fn of listeners.get(evt) || []) {
    try { fn(data); } catch (e) { console.error(e); }
  }
}

export function applyStatus(st) {
  state.status = st;
  state.player = st.player;
  state.queue = st.queue;
  state.devices = st.devices;
  emit("status", st);
  emit("player", st.player);
  emit("queue", st.queue);
  emit("devices", st.devices);
}

export function connectEvents() {
  let retry = 500;
  const open = () => {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => { retry = 500; emit("online", true); };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "hello") applyStatus(m.status);
      else if (m.type === "state") { state.player = m.state; emit("player", m.state); }
      else if (m.type === "queue") { state.queue = m.queue; emit("queue", m.queue); }
      else if (m.type === "devices") { state.devices = m.devices; emit("devices", m.devices); }
      else if (m.type === "auth") { applyStatus(m.status); emit("auth", m); }
      else if (m.type === "dj") emit("dj", m.dj);
      else if (m.type === "dj_progress") emit("dj_progress", m);
      else emit(m.type, m);  // otros avisos (p. ej. de la app TIDAL DJ)
    };
    ws.onclose = () => {
      emit("online", false);
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 8000);
    };
  };
  open();
}

export async function loadFavorites() {
  try {
    const f = await get("/favorites", { fresh: true });
    for (const k of Object.keys(state.favorites)) state.favorites[k] = new Set(f[k] || []);
    emit("favorites", state.favorites);
  } catch (e) { console.warn(e); }
}

export function isFav(kind, id) {
  return state.favorites[kind]?.has(String(id));
}

export async function toggleFav(kind, id) {
  id = String(id);
  const on = !isFav(kind, id);
  state.favorites[kind][on ? "add" : "delete"](id);
  emit("favorites", state.favorites);
  try {
    await post("/favorite", { kind, id, on });
    invalidate("/collection");
    toast(on ? "Añadido a Mi colección" : "Eliminado de Mi colección");
  } catch (e) {
    state.favorites[kind][on ? "delete" : "add"](id);
    emit("favorites", state.favorites);
    toast(e.message, true);
  }
}

// ----------------------------------------------------------------- toasts
let toastTimer;
export function toast(msg, error = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast" + (error ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

export async function act(promise, okMsg) {
  try {
    const r = await promise;
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message || String(e), true);
    throw e;
  }
}
