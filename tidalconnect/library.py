"""Contenidos de Tidal para la interfaz: inicio, busqueda, paginas y coleccion."""
import asyncio
import json
import logging
import time
from typing import Optional

import tidalapi
from tidalapi.page import PageCategoryV2, SimpleList
from tidalapi.types import AlbumOrder, ArtistOrder, ItemOrder, MixOrder, OrderDirection, PlaylistOrder

from . import config
from . import serialize as S
from .tidal_service import TidalService

log = logging.getLogger(__name__)

# Rutas "ver todo" de las secciones del inicio (la interfaz las pide por indice).
_more_cache: dict[str, object] = {}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        log.debug("llamada opcional fallida: %s", e)
        return default


def _section(cat, idx: int, prefix: str) -> Optional[dict]:
    kind = type(cat).__name__
    raw_items = list(getattr(cat, "items", None) or [])
    if kind == "TrackList":
        items, layout = S.tracks(raw_items), "tracks"
    elif kind == "ShortcutList":
        items, layout = S.items(raw_items), "shortcuts"
    else:
        items, layout = S.items(raw_items), "cards"
    if not items:
        return None
    more_key = None
    if getattr(cat, "_more", None):
        more_key = f"{prefix}:{idx}"
        _more_cache[more_key] = cat
    header = getattr(cat, "header", None)
    context = S.item(header) if header is not None and not isinstance(header, (str, dict)) else None
    title = getattr(cat, "title", "") or ""
    subtitle = getattr(cat, "subtitle", None) or getattr(cat, "description", None)
    return {
        "title": title,
        "subtitle": subtitle if subtitle and subtitle.strip() != title.strip() else None,
        "layout": layout,
        "items": items,
        "more": more_key,
        "context": context,
    }


def _page(page, prefix: str) -> dict:
    sections = []
    for i, cat in enumerate(page.categories or []):
        sec = _safe(lambda: _section(cat, i, prefix))
        if sec:
            sections.append(sec)
    return {"title": getattr(page, "title", "") or "", "sections": sections}


class Library:
    def __init__(self, tidal: TidalService):
        self.tidal = tidal

    async def home(self) -> dict:
        return await self.tidal.call(lambda s: _page(s.home(), "home"))

    async def explore(self) -> dict:
        return await self.tidal.call(lambda s: _page(s.explore(), "explore"))

    async def more(self, key: str) -> dict:
        cat = _more_cache.get(key)
        if cat is None:
            return {"title": "", "sections": []}

        def run(s):
            if isinstance(cat, PageCategoryV2):
                # tidalapi 0.8.11 llama a un Session.view_all que no existe: se pide a mano.
                j = s.request.request(
                    "GET", cat._more.api_path, base_url=s.config.api_v2_location,
                    params={"deviceType": "BROWSER", "locale": s.locale, "platform": "WEB", "limit": 50},
                ).json()
                parser = SimpleList(s)
                objs = [_safe(lambda it=it: parser.get_item(it)) for it in j.get("items", [])]
                objs = [o for o in objs if o is not None]
                items = S.items(objs)
                layout = "tracks" if items and all(x["type"] == "track" for x in items) else "grid"
                return {"title": j.get("title") or cat._more.title or getattr(cat, "title", ""),
                        "layout": layout, "items": items}
            page = cat.show_more()
            if page is None:
                return {"title": "", "sections": []}
            out = _page(page, key)
            if not out["title"]:
                out["title"] = getattr(cat, "title", "")
            # "Ver todo" se muestra como una sola cuadricula.
            merged, layout = [], "grid"
            for sec in out["sections"]:
                merged.extend(sec["items"])
                if sec["layout"] == "tracks":
                    layout = "tracks"
            return {"title": out["title"] or getattr(cat, "title", ""), "layout": layout, "items": merged}
        return await self.tidal.call(run)

    async def search(self, query: str) -> dict:
        def run(s):
            r = s.search(query, models=[tidalapi.Artist, tidalapi.Album, tidalapi.Track, tidalapi.Playlist], limit=50)
            top = r.get("top_hit")
            return {
                "top_hit": S.item(top) if top is not None else None,
                "tracks": S.tracks(r.get("tracks")),
                "albums": S.items(r.get("albums")),
                "artists": S.items(r.get("artists")),
                "playlists": S.items(r.get("playlists")),
            }
        return await self.tidal.call(run)

    async def album(self, album_id: str) -> dict:
        def run(s):
            a = s.album(album_id)
            tracks = S.tracks(a.tracks(limit=500))
            more = _safe(lambda: S.items(a.artist.get_albums(limit=12)), [])
            more = [x for x in more if x["id"] != str(a.id)][:10]
            review = _safe(lambda: a.review(), None)
            return {"album": S.album(a), "tracks": tracks, "more_by_artist": more, "review": review}
        return await self.tidal.call(run)

    async def artist(self, artist_id: str) -> dict:
        def run(s):
            ar = s.artist(artist_id)
            return {
                "artist": S.artist(ar),
                "top_tracks": _safe(lambda: S.tracks(ar.get_top_tracks(limit=10)), []),
                "albums": _safe(lambda: S.items(ar.get_albums(limit=50)), []),
                "singles": _safe(lambda: S.items(ar.get_ep_singles(limit=50)), []),
                "appears_on": _safe(lambda: S.items(ar.get_other(limit=30)), []),
                "similar": _safe(lambda: S.items(ar.get_similar()), [])[:12],
                "bio": _safe(lambda: ar.get_bio(), None),
            }
        return await self.tidal.call(run)

    async def playlist(self, playlist_id: str) -> dict:
        def run(s):
            p = s.playlist(playlist_id)
            total = getattr(p, "num_tracks", 0) or 0
            tracks, offset = [], 0
            while offset < max(total, 1) and offset < 2000:
                chunk = p.tracks(limit=100, offset=offset)
                if not chunk:
                    break
                tracks.extend(S.tracks(chunk))
                offset += 100
            return {"playlist": S.playlist(p), "tracks": tracks}
        return await self.tidal.call(run)

    async def mix(self, mix_id: str) -> dict:
        def run(s):
            m = s.mix(mix_id)
            return {"mix": S.mix(m), "tracks": S.tracks(m.items())}
        return await self.tidal.call(run)

    async def track_radio(self, track_id: str) -> dict:
        def run(s):
            t = s.track(track_id)
            return {"track": S.track(t), "tracks": S.tracks(t.get_track_radio(limit=100))}
        return await self.tidal.call(run)

    async def artist_radio(self, artist_id: str) -> list:
        return await self.tidal.call(lambda s: S.tracks(s.artist(artist_id).get_radio()))

    async def lyrics(self, track_id: str) -> dict:
        def run(s):
            ly = s.track(track_id).lyrics()
            return {"text": getattr(ly, "text", None), "synced": getattr(ly, "subtitles", None)}
        return await self.tidal.call(run)

    # ------------------------------------------------------------- coleccion
    async def collection(self, kind: str) -> list:
        def run(s):
            if kind == "tracks":
                return S.tracks(_raw_favorites(s, "tracks", s.parse_track))
            if kind == "albums":
                return S.items(_raw_favorites(s, "albums", s.parse_album))
            if kind == "artists":
                return S.items(_raw_favorites(s, "artists", s.parse_artist))
            if kind == "playlists":
                return S.items(_raw_playlists(s, "playlistsAndFavoritePlaylists"))
            if kind == "mixes":
                return S.items(_safe(lambda: s.user.favorites.mixes(
                    limit=50, order=MixOrder.DateAdded, order_direction=OrderDirection.Descending), []))
            return []
        return await self.tidal.call(run)

    async def my_playlists(self) -> list:
        return await self.tidal.call(lambda s: S.items(_raw_playlists(s, "playlists")))

    async def favorite_ids(self) -> dict:
        """IDs favoritos, para pintar los corazones."""
        def run(s):
            j = s.request.request("GET", f"users/{s.user.id}/favorites/ids").json()
            return {
                "track": [str(x) for x in j.get("TRACK", [])],
                "album": [str(x) for x in j.get("ALBUM", [])],
                "artist": [str(x) for x in j.get("ARTIST", [])],
                "playlist": [str(x) for x in j.get("PLAYLIST", [])],
            }
        return await self.tidal.call(run)

    async def set_favorite(self, kind: str, item_id: str, on: bool) -> None:
        def run(s):
            fav = s.user.favorites
            fn = {
                ("track", True): fav.add_track, ("track", False): fav.remove_track,
                ("album", True): fav.add_album, ("album", False): fav.remove_album,
                ("artist", True): fav.add_artist, ("artist", False): fav.remove_artist,
                ("playlist", True): fav.add_playlist, ("playlist", False): fav.remove_playlist,
            }[(kind, on)]
            fn(item_id)
        await self.tidal.call(run)

    # ------------------------------------------------- estilos (para el DJ)
    async def style_pool(self, query: str, label: str) -> list:
        """Canciones de un estilo: playlists editoriales de TIDAL con ese nombre (cache 7 dias)."""
        key = query.strip().lower()
        cache = _load_json(STYLE_FILE)
        hit = cache.get(key)
        if not hit or time.time() - hit.get("t", 0) > 7 * 86400:
            def find(s):
                r = s.search(query, models=[tidalapi.Playlist], limit=40)
                bad = ("atmos", "dolby", "360", "new arrivals", "best new", "novedades", "nuevos lanzamientos")
                pls = [p for p in r.get("playlists") or []
                       if getattr(p, "type", "") == "EDITORIAL"
                       and not any(b in (p.name or "").lower() for b in bad)]
                words = key.split()
                # Primero las que llevan el estilo en el nombre ("Disco Classics" para "disco").
                pls.sort(key=lambda p: 0 if all(w in (p.name or "").lower() for w in words) else 1)
                return [(str(p.id), p.name) for p in pls[:5]]
            found = await self.tidal.call(find)
            lists = await asyncio.gather(*(self.playlist(pid) for pid, _ in found), return_exceptions=True)
            tracks = []
            for res in lists:
                if isinstance(res, dict):
                    tracks.extend(res["tracks"])
            hit = {"t": time.time(), "playlists": [n for _, n in found], "tracks": tracks}
            cache[key] = hit
            _save_json(STYLE_FILE, cache)
            log.info("Estilo %r: %d canciones de %s", query, len(tracks), [n for _, n in found])
        return [{**t, "style": label} for t in hit["tracks"]]

    # --------------------------------------------------- estilo (para el DJ)
    async def artist_graph(self, artist_ids, progress=None) -> dict:
        """Artistas similares de cada artista (cache en disco, se renueva cada 30 dias)."""
        cache = _load_graph()
        now = time.time()
        ids = sorted({str(a) for a in artist_ids if a})
        missing = [a for a in ids if a not in cache or now - cache[a].get("t", 0) > 30 * 86400]
        sem = asyncio.Semaphore(6)
        done = 0

        async def one(aid: str) -> None:
            nonlocal done
            def run(s):
                j = s.request.request("GET", f"artists/{aid}/similar", params={"limit": 30}).json()
                return [str(x["id"]) for x in j.get("items", [])]
            async with sem:
                try:
                    sims = await self.tidal.call(run)
                except Exception:
                    sims = []  # algunos artistas no tienen similares (404)
            cache[aid] = {"t": now, "s": sims}
            done += 1
            if progress and (done % 25 == 0 or done == len(missing)):
                progress(done, len(missing))

        if missing:
            log.info("Consultando artistas similares de %d artistas", len(missing))
            await asyncio.gather(*(one(a) for a in missing))
            _save_graph(cache)
        return {a: set(cache[a]["s"]) for a in ids if a in cache}

    async def create_playlist(self, title: str, description: str, track_ids: list) -> dict:
        """Crea una playlist privada en la cuenta con estas canciones, en este orden."""
        ids = [str(i) for i in track_ids if i]
        if not ids:
            raise ValueError("No hay canciones que guardar")

        def run(s):
            pl = s.user.create_playlist(title[:100], description[:500])
            if getattr(pl, "public", False):
                pl.set_playlist_private()
            for i in range(0, len(ids), 100):   # de 100 en 100, siempre al final: se respeta el orden
                pl.add(ids[i:i + 100], allow_duplicates=True)
            return S.playlist(pl)
        return await self.tidal.call(run)

    async def resolve_tracks(self, kind: str, item_id: str) -> list:
        """Pistas a reproducir al pulsar play en una tarjeta."""
        if kind == "album":
            return (await self.album(item_id))["tracks"]
        if kind == "playlist":
            return (await self.playlist(item_id))["tracks"]
        if kind == "mix":
            return (await self.mix(item_id))["tracks"]
        if kind == "artist":
            return (await self.artist(item_id))["top_tracks"]
        if kind == "track":
            return [await self.tidal.call(lambda s: S.track(s.track(item_id)))]
        return []


GRAPH_FILE = config.CONFIG_DIR / "artist_graph.json"
STYLE_FILE = config.CONFIG_DIR / "style_cache.json"


def _load_json(path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_json(path, data: dict) -> None:
    try:
        config.write_private_json(path, data)
    except OSError:
        log.exception("No se pudo guardar %s", path)


def _load_graph() -> dict:
    try:
        return json.loads(GRAPH_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_graph(cache: dict) -> None:
    try:
        config.write_private_json(GRAPH_FILE, cache)
    except OSError:
        log.exception("No se pudo guardar la cache de artistas similares")


def _raw_favorites(s, kind: str, parse, cap: int = 10000) -> list:
    """Favoritos mas recientes primero, en bloques de 100, sin peticiones extra."""
    out, off = [], 0
    while off < cap:
        j = s.request.request("GET", f"users/{s.user.id}/favorites/{kind}",
                              params={"limit": 100, "offset": off, "order": "DATE", "orderDirection": "DESC"}).json()
        for it in j.get("items", []):
            obj = _safe(lambda it=it: parse(it.get("item", it)))
            if obj is not None:
                out.append(obj)
        off += 100
        if off >= (j.get("totalNumberOfItems") or 0) or not j.get("items"):
            break
    return out


def _raw_playlists(s, endpoint: str) -> list:
    """Playlists del usuario en bloques de 50.

    tidalapi pide cada playlist por separado (una peticion por elemento); aqui se
    parsea la respuesta del listado directamente.
    """
    out, off = [], 0
    while off < 3000:
        j = s.request.request("GET", f"users/{s.user.id}/{endpoint}", params={"limit": 50, "offset": off}).json()
        for it in j.get("items", []):
            data = it.get("playlist", it)
            p = _safe(lambda data=data: s.parse_playlist(data))
            if p is not None:
                out.append(p)
        off += 50
        if off >= (j.get("totalNumberOfItems") or 0):
            break
    return out


def _paged(fetch, step: int = 50, cap: int = 2000) -> list:
    out, off = [], 0
    while off < cap:
        chunk = fetch(off)
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < step:
            break
        off += step
    return out
