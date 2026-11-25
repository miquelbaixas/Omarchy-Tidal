"""Servidor local (solo 127.0.0.1): API JSON + WebSocket de eventos + interfaz web."""
import asyncio
import json
import logging
import time
import uuid
import weakref
from typing import Optional

from aiohttp import WSMsgType, web

from . import config
from . import dj
from .discovery import Discovery
from .djapp import DJApp
from .library import Library
from .local_output import list_outputs
from .player import LOCAL_DEVICE, LOCAL_ID, Player
from .streams import StreamResolver
from .tidal_service import TidalService

log = logging.getLogger(__name__)

ALLOWED_ORIGINS = {f"http://{config.HOST}:{config.PORT}", f"http://localhost:{config.PORT}"}


@web.middleware
async def guard(request: web.Request, handler):
    # Evita que paginas web abiertas en el navegador manden ordenes a la app.
    origin = request.headers.get("Origin")
    if request.path.startswith(("/api/", "/ws")) and origin and origin not in ALLOWED_ORIGINS:
        return web.json_response({"error": "origen no permitido"}, status=403)
    try:
        resp = await handler(request)
        if request.path.startswith("/assets/"):
            # Revalida siempre: tras actualizar la app se ven los cambios sin vaciar cache.
            resp.headers["Cache-Control"] = "no-cache"
        return resp
    except web.HTTPException:
        raise
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        log.exception("Error en %s", request.path)
        return web.json_response({"error": str(e) or type(e).__name__}, status=500)


class App:
    def __init__(self):
        settings = config.load_settings()
        self.tidal = TidalService(locale=settings.get("locale") or "es_ES")
        self.library = Library(self.tidal)
        self.player = Player(self.tidal)
        self.streams = StreamResolver(self.tidal)
        self.player.streams = self.streams
        self.djapp = DJApp(self.streams, self.broadcast, self._pause_main)
        self.discovery = Discovery(on_change=self._devices_changed)
        self.dj = dj.DJState()
        self._dj_lock = asyncio.Lock()
        self.sockets: "weakref.WeakSet[web.WebSocketResponse]" = weakref.WeakSet()
        self.player.listeners.append(self.broadcast)
        self.tidal.on_tokens_changed = self._tokens_changed

    # ---------------------------------------------------------------- eventos
    def broadcast(self, event: dict) -> None:
        data = json.dumps(event)
        for ws in list(self.sockets):
            if not ws.closed:
                asyncio.ensure_future(_send(ws, data))

    def _devices_changed(self) -> None:
        dev = self.player.device
        if dev and dev.get("id") in self.discovery.devices:
            found = self.discovery.devices[dev["id"]]
            if (found["host"], found["port"]) != (dev.get("host"), dev.get("port")):
                self.player.device = found
                config.save_settings(device=found)
        self.broadcast({"type": "devices", "devices": self.devices()})

    def _tokens_changed(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop:
            asyncio.ensure_future(self.player.update_server_info())
        else:
            asyncio.run_coroutine_threadsafe(self.player.update_server_info(), self.loop)

    def devices(self) -> list:
        devs = list(self.discovery.devices.values())
        saved = self.player.device
        if saved and saved.get("id") not in self.discovery.devices and saved.get("id") != LOCAL_ID:
            devs.append({**saved, "offline": True})
        # "Este ordenador" siempre disponible, el primero de la lista
        return [dict(LOCAL_DEVICE, local=True)] + sorted(devs, key=lambda d: d.get("name") or "")

    def status(self) -> dict:
        return {
            "logged_in": self.tidal.logged_in,
            "user": self.tidal.user_info(),
            "devices": self.devices(),
            "player": self.player.snapshot(),
            "queue": self.player.queue_snapshot(),
        }

    # ---------------------------------------------------------------- arranque
    async def on_startup(self, app: web.Application) -> None:
        self.loop = asyncio.get_running_loop()
        await self.discovery.start()
        asyncio.ensure_future(self.player.try_resume())

    async def on_shutdown(self, app: web.Application) -> None:
        # Cierra las ventanas conectadas para que el apagado no espere a nadie.
        for ws in list(self.sockets):
            await ws.close(code=1001, message=b"server shutdown")

    async def on_cleanup(self, app: web.Application) -> None:
        await self.discovery.stop()
        if self.player.client:
            await self.player.disconnect(stop=False)
        await self.player.local.shutdown()
        await self.djapp.shutdown()
        await self.streams.close()

    def build(self) -> web.Application:
        app = web.Application(middlewares=[guard])
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/ws", self.ws)
        r.add_get("/api/status", self.h_status)
        r.add_post("/api/login", self.h_login)
        r.add_post("/api/logout", self.h_logout)
        lib = self.library
        r.add_get("/api/home", self._data(lambda q: lib.home()))
        r.add_get("/api/explore", self._data(lambda q: lib.explore()))
        r.add_get("/api/more/{key}", self._data(lambda q: lib.more(q.match_info["key"])))
        r.add_get("/api/search", self._data(lambda q: lib.search(q.query.get("q", ""))))
        r.add_get("/api/album/{id}", self._data(lambda q: lib.album(q.match_info["id"])))
        r.add_get("/api/artist/{id}", self._data(lambda q: lib.artist(q.match_info["id"])))
        r.add_get("/api/playlist/{id}", self._data(lambda q: lib.playlist(q.match_info["id"])))
        r.add_get("/api/mix/{id}", self._data(lambda q: lib.mix(q.match_info["id"])))
        r.add_get("/api/lyrics/{id}", self._data(lambda q: lib.lyrics(q.match_info["id"])))
        r.add_get("/api/collection/{kind}", self._data(lambda q: lib.collection(q.match_info["kind"])))
        r.add_get("/api/my-playlists", self._data(lambda q: lib.my_playlists()))
        r.add_get("/api/favorites", self._data(lambda q: lib.favorite_ids()))
        r.add_post("/api/favorite", self.h_favorite)
        r.add_post("/api/play", self.h_play)
        r.add_post("/api/radio", self.h_radio)
        r.add_post("/api/queue/{action}", self.h_queue)
        r.add_post("/api/player/{action}", self.h_player)
        r.add_post("/api/device", self.h_device)
        r.add_get("/dj-app", self.dj_index)
        r.add_get("/dj-app/", self.dj_index)
        r.add_get("/api/djapp/state", self.h_djapp_state)
        r.add_post("/api/djapp/load", self.h_djapp_load)
        r.add_post("/api/djapp/cmd/{action}", self.h_djapp_cmd)
        r.add_post("/api/djapp/queue/{action}", self.h_djapp_queue)
        r.add_post("/api/djapp/output", self.h_djapp_output)
        r.add_post("/api/djapp/cue-output", self.h_djapp_cue_output)
        r.add_get("/api/djapp/search", self.h_djapp_search)
        r.add_get("/api/djapp/list", self.h_djapp_list)
        r.add_get("/api/djapp/suggest", self.h_djapp_suggest)
        r.add_post("/api/djapp/next", self.h_djapp_next)
        r.add_post("/api/djapp/drop-next", self.h_djapp_drop_next)
        r.add_get("/local/stream/{id}", self.h_local_stream)
        r.add_get("/api/local/outputs", self.h_local_outputs)
        r.add_post("/api/local/output", self.h_local_output)
        r.add_get("/api/dj/state", self.h_dj_state)
        r.add_post("/api/dj/create", self.h_dj_create)
        r.add_post("/api/dj/alternatives", self.h_dj_alternatives)
        r.add_post("/api/dj/replace-next", self.h_dj_replace_next)
        r.add_post("/api/dj/replace", self.h_dj_replace)
        r.add_post("/api/dj/insert", self.h_dj_insert)
        r.add_post("/api/dj/search", self.h_dj_search)
        r.add_post("/api/dj/save-playlist", self.h_dj_save_playlist)
        r.add_post("/api/djapp/save-playlist", self.h_djapp_save_playlist)
        r.add_post("/api/dj/regenerate", self.h_dj_regenerate)
        r.add_get("/api/dj/sessions", self.h_dj_recipes)
        r.add_post("/api/dj/sessions/save", self.h_dj_recipe_save)
        r.add_post("/api/dj/sessions/delete", self.h_dj_recipe_delete)
        r.add_post("/api/dj/sessions/rename", self.h_dj_recipe_rename)
        r.add_post("/api/settings", self.h_settings)
        r.add_static("/assets/", config.WEB_DIR, show_index=False)
        app.on_startup.append(self.on_startup)
        app.on_shutdown.append(self.on_shutdown)
        app.on_cleanup.append(self.on_cleanup)
        return app

    # --------------------------------------------------------------- handlers
    def _data(self, make):
        """Envuelve una consulta a la biblioteca en un handler que exige sesion."""
        async def handler(request):
            if not self.tidal.logged_in:
                return web.json_response({"error": "login"}, status=401)
            return web.json_response(await make(request))
        return handler

    async def index(self, request):
        resp = web.FileResponse(config.WEB_DIR / "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def h_status(self, request):
        return web.json_response(self.status())

    async def h_login(self, request):
        async def done(ok: bool):
            self.broadcast({"type": "auth", "ok": ok, "status": self.status()})
        return web.json_response(await self.tidal.start_login(done))

    async def h_logout(self, request):
        await self.player.disconnect(stop=False)
        await self.tidal.logout()
        self.broadcast({"type": "auth", "ok": False, "status": self.status()})
        return web.json_response({"ok": True})

    async def h_favorite(self, request):
        b = await request.json()
        await self.library.set_favorite(b["kind"], str(b["id"]), bool(b["on"]))
        return web.json_response({"ok": True})

    async def _tracks_from(self, b: dict) -> list:
        if b.get("tracks"):
            return b["tracks"]
        if b.get("kind") and b.get("id"):
            return await self.library.resolve_tracks(b["kind"], str(b["id"]))
        return []

    async def h_play(self, request):
        b = await request.json()
        tracks = await self._tracks_from(b)
        await self.player.play_tracks(tracks, int(b.get("index") or 0), b.get("context"),
                                      b.get("shuffle") if "shuffle" in b else None)
        return web.json_response({"ok": True})

    async def h_radio(self, request):
        b = await request.json()
        if b["kind"] == "artist":
            tracks = await self.library.artist_radio(str(b["id"]))
            ctx = {"type": "artist", "id": str(b["id"]), "title": b.get("title") or "Radio"}
        else:
            r = await self.library.track_radio(str(b["id"]))
            tracks = ([r["track"]] if r["track"] else []) + r["tracks"]
            ctx = {"type": "radio", "id": str(b["id"]), "title": f"Radio: {r['track']['title']}" if r["track"] else "Radio"}
        await self.player.play_tracks(tracks, 0, ctx, None)
        return web.json_response({"ok": True})

    async def h_queue(self, request):
        action = request.match_info["action"]
        b = await request.json() if request.can_read_body else {}
        p = self.player
        if action == "add":
            await p.add(await self._tracks_from(b), bool(b.get("next")))
        elif action == "remove":
            await p.remove(str(b["uid"]))
        elif action == "move":
            await p.move(str(b["uid"]), int(b["to"]))
        elif action == "clear":
            await p.clear_upcoming()
        elif action == "jump":
            await p.jump(str(b["uid"]))
        else:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    async def h_player(self, request):
        action = request.match_info["action"]
        b = await request.json() if request.can_read_body else {}
        p = self.player
        handlers = {
            "toggle": lambda: p.toggle(),
            "next": lambda: p.next(),
            "previous": lambda: p.previous(),
            "seek": lambda: p.seek(int(b["ms"])),
            "volume": lambda: p.set_volume(float(b["level"])),
            "mute": lambda: p.set_mute(bool(b["mute"])),
            "shuffle": lambda: p.set_shuffle(bool(b["on"])),
            "repeat": lambda: p.set_repeat(str(b["mode"])),
            "connect": lambda: p.ensure_connected(),
            "disconnect": lambda: p.disconnect(stop=bool(b.get("stop"))),
        }
        if action not in handlers:
            raise web.HTTPNotFound()
        await handlers[action]()
        return web.json_response({"ok": True})

    async def h_device(self, request):
        b = await request.json()
        dev = next((d for d in self.devices() if d["id"] == b["id"]), None)
        if not dev:
            return web.json_response({"error": "altavoz no encontrado"}, status=404)
        dev = dict(LOCAL_DEVICE) if dev["id"] == LOCAL_ID else {k: dev[k] for k in ("id", "name", "model", "host", "port") if k in dev}
        await self.player.set_device(dev)
        self.broadcast({"type": "devices", "devices": self.devices()})
        return web.json_response({"ok": True})

    # --------------------------------------------------------- app TIDAL DJ
    async def dj_index(self, request):
        resp = web.FileResponse(config.WEB_DIR / "dj" / "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def _pause_main(self) -> None:
        """La app DJ va a sonar: pausa el reproductor principal (TIDAL, un sitio a la vez)."""
        if self.player.remote_state in ("PLAYING", "BUFFERING"):
            try:
                await self.player.toggle()
            except Exception:
                log.exception("No se pudo pausar el reproductor principal")

    async def h_djapp_state(self, request):
        return web.json_response(self.djapp.snapshot())

    async def h_djapp_load(self, request):
        """Carga un set en la app DJ: desde una sesion guardada, una estructura o una fuente."""
        b = await request.json()
        if b.get("free"):
            # sesion libre: sin lista, se elige cada siguiente cancion a mano
            await self.djapp.load_set([], "Sesión libre", autoplay=False)
            return web.json_response({"ok": True, "tracks": 0})
        if b.get("tracks"):
            await self.djapp.load_set(b["tracks"], b.get("title") or "Mezcla")
            return web.json_response({"ok": True, "tracks": len(b["tracks"])})
        recipe = self.dj.recipe(b["recipe_id"]) if b.get("recipe_id") else None
        cfg = recipe["config"] if recipe else self._clean_config(b)
        r = await self._dj_build(cfg, recipe, play_here=False)
        await self.djapp.load_set(r["seq"], r["name"])
        return web.json_response({"ok": True, "tracks": len(r["seq"]),
                                  "minutes": round(sum(t["duration"] for t in r["seq"]) / 60)})

    async def h_djapp_cmd(self, request):
        action = request.match_info["action"]
        b = await request.json() if request.can_read_body else {}
        allowed = {"play", "pause", "toggle", "mix_now", "skip", "stop", "crossfader", "filter", "volume",
                   "auto", "mix_bars", "tempo_mode", "seek", "fx", "fx_param", "sfx", "cue", "cue_param"}
        if action not in allowed:
            raise web.HTTPNotFound()
        await self.djapp.command(action, **b)
        return web.json_response({"ok": True})

    async def h_djapp_queue(self, request):
        action = request.match_info["action"]
        b = await request.json() if request.can_read_body else {}
        if action == "add":
            tracks = await self._tracks_from(b)
            self.djapp.add(tracks, bool(b.get("next")), int(b["at"]) if b.get("at") is not None else None)
        elif action == "remove":
            self.djapp.remove(int(b["index"]))
        elif action == "move":
            self.djapp.move(int(b["from"]), int(b["to"]))
        else:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    # ----- elegir la siguiente: busqueda en todo TIDAL, listas y sugerencias que encajan
    def _dj_rows(self, tracks: list) -> list:
        """Canciones con tono Camelot y como encajan con la que suena."""
        rows = dj.annotate([dict(t) for t in tracks if t and t.get("id")])
        cur = self.djapp.now_playing()
        if cur and "camelot" not in cur:
            cur = dj.annotate([dict(cur)])[0]
        for t in rows:
            t["fit"] = dj.transition(cur, t) if cur else None
        return rows

    async def h_djapp_search(self, request):
        q = request.query.get("q", "").strip()
        if not q:
            return web.json_response({"tracks": [], "albums": [], "artists": [], "playlists": []})
        r = await self.library.search(q)
        return web.json_response({"tracks": self._dj_rows(r["tracks"]), "albums": r["albums"][:12],
                                  "artists": r["artists"][:12], "playlists": r["playlists"][:12]})

    async def h_djapp_list(self, request):
        kind, iid = request.query.get("kind"), request.query.get("id")
        if not kind or not iid:
            raise ValueError("Falta la lista")
        if kind == "artist_radio":
            tracks = await self.library.artist_radio(iid)
        elif kind == "track_radio":
            tracks = (await self.library.track_radio(iid))["tracks"]
        else:
            tracks = await self.library.resolve_tracks(kind, iid)
        return web.json_response({"tracks": self._dj_rows(tracks)})

    async def h_djapp_suggest(self, request):
        """Canciones que encajan detras de la que suena (radio de la cancion, por tono y tempo)."""
        cur = self.djapp.now_playing()
        if not cur:
            return web.json_response({"base": None, "tracks": []})
        cur = dj.annotate([dict(cur)])[0]
        pool = self._dj_rows((await self.library.track_radio(cur["id"]))["tracks"])
        a = self.djapp
        exclude = {t["id"] for t in a.queue + a.history + [d for d in a.decks.values() if d]} | {cur["id"]}
        seen = {dj.song_key(cur)}
        scored = []
        for i, t in enumerate(pool):
            k = dj.song_key(t)
            if t["id"] in exclude or k in seen or not t.get("available", True):
                continue
            seen.add(k)
            cost = dj.key_cost(dj._cam(cur), dj._cam(t))[0] + dj.tempo_cost(dj.tempo_jump(cur.get("bpm"), t.get("bpm")))
            if dj.main_artist(t) and dj.main_artist(t) == dj.main_artist(cur):
                cost += 1.5
            scored.append((cost, i, t))
        scored.sort(key=lambda x: (x[0], x[1]))
        return web.json_response({"base": {"id": cur["id"], "title": cur.get("title")},
                                  "tracks": [t for _, _, t in scored[:25]]})

    async def h_djapp_next(self, request):
        b = await request.json()
        t = b.get("track")
        if not isinstance(t, dict) or not t.get("id"):
            raise ValueError("Falta la canción")
        if not t.get("available", True):
            raise ValueError("Esa canción no está disponible en TIDAL")
        fi = b.get("from_index")
        where = await self.djapp.set_next(t, int(fi) if fi is not None else None)
        return web.json_response({"ok": True, "where": where})

    async def h_djapp_drop_next(self, request):
        await self.djapp.drop_next()
        return web.json_response({"ok": True})

    async def h_djapp_output(self, request):
        b = await request.json()
        await self.djapp.set_output(b.get("id") or None)
        return web.json_response({"ok": True})

    async def h_djapp_cue_output(self, request):
        b = await request.json()
        await self.djapp.set_cue_output(b.get("id") or None)
        return web.json_response({"ok": True})

    # ------------------------------------------------------ este ordenador
    async def h_local_stream(self, request):
        """Audio de una cancion (FLAC) para mpv o el motor DJ. Solo en 127.0.0.1."""
        if request.remote not in ("127.0.0.1", "::1"):
            raise web.HTTPForbidden()
        track_id = request.match_info["id"]
        if not track_id.isdigit():
            raise web.HTTPBadRequest()
        tf = self.streams.file(track_id)
        rng = (request.headers.get("Range") or "").replace(" ", "")
        if not tf.done.is_set() and rng and not rng.startswith("bytes=0-"):
            # Salto a una parte aun no descargada: se espera a tener la cancion entera.
            await asyncio.wait_for(tf.done.wait(), 90)
        if tf.done.is_set():
            if tf.error or not tf.path.exists():
                raise web.HTTPBadGateway(text=tf.error or "descarga fallida")
            # Archivo completo: admite saltos (Range) y tiene tamano conocido.
            return web.FileResponse(tf.path, headers={"Content-Type": "audio/mp4", "Cache-Control": "no-store"})
        # Mientras se descarga: emision continua para empezar al instante.
        resp = web.StreamResponse(headers={"Content-Type": "audio/mp4", "Cache-Control": "no-store"})
        await resp.prepare(request)
        pos = 0
        try:
            with open(tf.part if tf.part.exists() else tf.path, "rb") as fh:
                while True:
                    if pos < tf.size:
                        fh.seek(pos)
                        data = fh.read(min(tf.size - pos, 512 * 1024))
                        if data:
                            await resp.write(data)
                            pos += len(data)
                            continue
                    if tf.done.is_set() and pos >= tf.size:
                        break
                    await tf.grown(pos)
        except (ConnectionResetError, asyncio.CancelledError, asyncio.TimeoutError):
            pass  # el reproductor cerro la conexion (salto de cancion, etc.)
        return resp

    async def h_local_outputs(self, request):
        outs = await list_outputs()
        return web.json_response({"outputs": outs, "selected": config.load_settings().get("local_output")})

    async def h_local_output(self, request):
        b = await request.json()
        await self.player.set_local_output(str(b.get("id") or ""))
        return web.json_response({"ok": True})

    # -------------------------------------------------------------------- DJ
    async def _dj_pool(self, source: dict) -> list:
        """Canciones candidatas para el set, segun la fuente elegida."""
        kind, sid = source.get("kind"), str(source.get("id") or "")
        lib = self.library
        if kind == "favorites":
            return await lib.collection("tracks")
        if kind == "playlist":
            return (await lib.playlist(sid))["tracks"]
        if kind == "mix":
            return (await lib.mix(sid))["tracks"]
        if kind == "artist":
            d = await lib.artist(sid)
            return d["top_tracks"] + await lib.artist_radio(sid)
        if kind == "album":
            d = await lib.album(sid)
            extra = []
            if d["album"]["artists"]:
                extra = await lib.artist_radio(d["album"]["artists"][0]["id"])
            return d["tracks"] + extra
        raise ValueError("Fuente no valida")

    def _dj_targets(self) -> list:
        n = len(self.player.queue)
        shape = (self.dj.active or {}).get("shape", "constante")
        return [round(dj.target(shape, i / max(1, n - 1)), 3) for i in range(n)]

    def _dj_snapshot(self) -> dict:
        p = self.player
        active = self.dj.active if (p.context or {}).get("type") == "dj" else None
        items = []
        prev = None
        for e in p.queue:
            t = e["track"]
            items.append({"uid": e["uid"], "bpm": t.get("bpm"), "camelot": t.get("camelot"),
                          "key_name": t.get("key_name"), "energy": t.get("energy"),
                          "seg": t.get("seg"), "style": t.get("style"),
                          "transition": dj.transition(prev, t) if prev else None})
            prev = t
        return {"active": active, "index": p.index, "items": items,
                "targets": self._dj_targets() if active else [], "shapes": dj.SHAPES}

    async def h_dj_state(self, request):
        return web.json_response(self._dj_snapshot())

    @staticmethod
    def _clean_config(b: dict) -> dict:
        """Estructura de un set (lo que se guarda en una sesion): sin canciones."""
        shape = b.get("shape") if b.get("shape") in dj.SHAPES else "subida"
        cfg = {"shape": shape, "bpm_min": b.get("bpm_min") or None, "bpm_max": b.get("bpm_max") or None,
               "include_unknown": bool(b.get("include_unknown")), "expand": b.get("expand", True) is not False,
               "prefer_favorites": bool(b.get("prefer_favorites"))}
        segs = b.get("segments") or []
        if segs:
            clean = []
            for s in segs:
                styles = [{"label": str(x.get("label") or x.get("query")), "query": str(x.get("query") or x.get("label"))}
                          for x in s.get("styles") or [] if (x.get("label") or x.get("query"))]
                if styles:
                    clean.append({"label": s.get("label") or " y ".join(x["label"] for x in styles),
                                  "styles": styles, "minutes": max(10, min(240, int(s.get("minutes") or 30)))})
            if not clean:
                raise ValueError("Añade al menos un estilo")
            cfg.update(mode="styles", segments=clean)
        else:
            cfg.update(mode="source", source=b.get("source") or {"kind": "favorites", "title": "Mis canciones"},
                       minutes=max(15, min(480, int(b.get("minutes") or 120))))
        return cfg

    async def h_dj_create(self, request):
        b = await request.json()
        recipe = self.dj.recipe(b["recipe_id"]) if b.get("recipe_id") else None
        cfg = recipe["config"] if recipe else self._clean_config(b)
        return web.json_response(await self._dj_build(cfg, recipe))

    async def _dj_build(self, cfg: dict, recipe: Optional[dict] = None, play_here: bool = True) -> dict:
        """Crea un set. play_here=True lo pone en el reproductor principal (cabina);
        False solo lo devuelve (lo usa la app TIDAL DJ para mezclarlo ella)."""
        shape = cfg["shape"]
        opts = dict(bpm_min=cfg.get("bpm_min"), bpm_max=cfg.get("bpm_max"), include_unknown=cfg.get("include_unknown"))
        avoid = set(recipe.get("recent") or []) if recipe else None
        favs = None
        if cfg.get("prefer_favorites"):
            favs = set((await self.library.favorite_ids())["track"])

        def progress(done, total):
            self.broadcast({"type": "dj_progress", "step": "style", "done": done, "total": total})

        async with self._dj_lock:
            added = 0
            if cfg["mode"] == "styles":
                segments = cfg["segments"]
                pools = []
                for i, s in enumerate(segments):
                    p = []
                    for st in s["styles"]:
                        self.broadcast({"type": "dj_progress", "step": "styles", "label": st["label"]})
                        p += await self.library.style_pool(st["query"], st["label"])
                    pools.append(dj.dedupe(p))
                if not any(pools):
                    raise ValueError("No he encontrado música de esos estilos en TIDAL")
                allp = [{**t, "seg": i} for i, p in enumerate(pools) for t in p]
                dj.annotate(allp)  # energia comparable entre todos los estilos
                pools = [[t for t in allp if t["seg"] == i] for i in range(len(segments))]
                graph = await self.library.artist_graph({dj.main_artist(t) for t in allp if t.get("bpm")}, progress)
                self.broadcast({"type": "dj_progress", "step": "build"})
                seq = await asyncio.to_thread(dj.make_journey, segments, pools, shape, graph=graph, favs=favs,
                                              avoid=avoid, **opts)
                pool = allp
                minutes = sum(s["minutes"] for s in segments)
                what = " → ".join(s["label"] for s in segments)
            else:
                source, minutes = cfg["source"], cfg["minutes"]
                self.broadcast({"type": "dj_progress", "step": "pool"})
                pool = await self._dj_pool(source)
                for t in pool:
                    t["src"] = True  # canciones de la fuente elegida: tienen preferencia
                usable = sum(1 for t in pool if t.get("bpm") and t.get("key"))
                if cfg.get("expand", True) and source.get("kind") != "favorites" and usable < minutes * 60 / 210 * 1.5:
                    self.broadcast({"type": "dj_progress", "step": "expand"})
                    seen = {t["id"] for t in pool}
                    for t in await self._dj_expand(pool):
                        if t["id"] not in seen:
                            seen.add(t["id"])
                            pool.append(t)
                            added += 1
                pool = dj.annotate(dj.dedupe(pool))
                if avoid:
                    fresh = [t for t in pool if dj.song_key(t) not in avoid]
                    if len(fresh) > 0.6 * len(pool):
                        pool = fresh
                graph = await self.library.artist_graph({dj.main_artist(t) for t in pool if t.get("bpm")}, progress)
                self.broadcast({"type": "dj_progress", "step": "build"})
                seq = await asyncio.to_thread(dj.make_set, pool, minutes, shape, graph=graph, **opts)
                segments = []
                what = source.get("title") or "Mis canciones"
            if not seq:
                raise ValueError("No hay canciones con tempo y tonalidad que encajen con lo pedido")
            name = recipe["name"] if recipe else f"{dj.SHAPES[shape]} · {what}"
            title = f"Set DJ · {name}"
            result = {"seq": seq, "title": title, "name": name, "pool": pool, "graph": graph, "added": added,
                      "active": {"title": title, "shape": shape, "minutes": minutes, "mode": cfg["mode"],
                                 "segments": [{"label": s["label"], "minutes": s["minutes"]} for s in segments],
                                 "config": cfg, "recipe_id": recipe["id"] if recipe else None, "options": opts}}
            if recipe:
                await self._remember(recipe, seq)
            if not play_here:
                return result
            self.dj.active = result["active"]
            self.dj.pool = pool
            await asyncio.to_thread(self.dj.save)
            self._dj_graph = graph
            await self.player.play_tracks(seq, 0, {"type": "dj", "id": "set", "title": title}, shuffle=False)
        self.broadcast({"type": "dj", "dj": self._dj_snapshot()})
        return {"ok": True, "tracks": len(seq), "added": added,
                "from_source": sum(1 for t in seq if t.get("src")),
                "minutes": round(sum(t["duration"] for t in seq) / 60)}

    async def _remember(self, recipe: dict, seq: list) -> None:
        """Recuerda lo que ha sonado con una sesion para no repetirlo la proxima vez."""
        recent = [dj.song_key(t) for t in seq] + (recipe.get("recent") or [])
        recipe["recent"] = recent[:400]
        recipe["last_played"] = time.time()
        recipe["plays"] = recipe.get("plays", 0) + 1
        await asyncio.to_thread(self.dj.save_recipes)

    # ------------------------------------------------- sesiones guardadas
    def _recipes_out(self) -> list:
        return [{"id": r["id"], "name": r["name"], "summary": dj.describe(r["config"]), "config": r["config"],
                 "plays": r.get("plays", 0), "last_played": r.get("last_played")} for r in self.dj.recipes]

    async def h_dj_recipes(self, request):
        return web.json_response(self._recipes_out())

    async def h_dj_recipe_save(self, request):
        b = await request.json()
        name = (b.get("name") or "").strip()[:80]
        if not name:
            return web.json_response({"error": "Ponle un nombre a la sesión"}, status=400)
        cfg = self._clean_config(b.get("config") or {}) if b.get("config") else (self.dj.active or {}).get("config")
        if not cfg:
            return web.json_response({"error": "No hay ninguna estructura que guardar"}, status=400)
        existing = self.dj.recipe(b["id"]) if b.get("id") else None
        if existing:
            existing.update(name=name, config=cfg)
        else:
            existing = {"id": uuid.uuid4().hex[:12], "name": name, "config": cfg, "created": time.time(),
                        "recent": [], "plays": 0}
            self.dj.recipes.insert(0, existing)
        await asyncio.to_thread(self.dj.save_recipes)
        return web.json_response({"ok": True, "id": existing["id"], "sessions": self._recipes_out()})

    async def h_dj_recipe_rename(self, request):
        b = await request.json()
        r = self.dj.recipe(b.get("id") or "")
        name = (b.get("name") or "").strip()[:80]
        if not r:
            return web.json_response({"error": "Sesión no encontrada"}, status=404)
        if not name:
            return web.json_response({"error": "Ponle un nombre a la sesión"}, status=400)
        r["name"] = name
        await asyncio.to_thread(self.dj.save_recipes)
        if self.dj.active and self.dj.active.get("recipe_id") == r["id"]:
            self.dj.active["title"] = f"Set DJ · {name}"
        return web.json_response({"ok": True, "sessions": self._recipes_out()})

    async def h_dj_recipe_delete(self, request):
        b = await request.json()
        self.dj.recipes = [r for r in self.dj.recipes if r["id"] != b.get("id")]
        await asyncio.to_thread(self.dj.save_recipes)
        return web.json_response({"ok": True, "sessions": self._recipes_out()})

    async def _dj_expand(self, pool: list) -> list:
        """Musica parecida para completar una fuente corta: radios de sus artistas principales."""
        counts: dict = {}
        for t in pool:
            a = dj.main_artist(t)
            if a:
                counts[a] = counts.get(a, 0) + 1
        artists = sorted(counts, key=lambda a: -counts[a])[:6]
        sem = asyncio.Semaphore(3)

        async def radio(aid):
            async with sem:
                try:
                    return await self.library.artist_radio(aid)
                except Exception:
                    return []
        out = []
        for lst in await asyncio.gather(*(radio(a) for a in artists)):
            out.extend(lst)
        return out

    async def _graph(self) -> dict:
        g = getattr(self, "_dj_graph", None)
        if g is None:
            g = await self.library.artist_graph({dj.main_artist(t) for t in self.dj.pool if t.get("bpm")})
            self._dj_graph = g
        return g

    def _dj_slot(self, b: dict) -> tuple[int, Optional[dict], Optional[dict], Optional[int]]:
        """Hueco del set que se va a editar: (posicion, anterior, posterior, tramo).
        mode "replace": cambiar la cancion uid (por defecto, la siguiente).
        mode "insert": meter una nueva despues de la cancion uid."""
        p = self.player
        q = p.queue
        uid = b.get("uid")
        i = next((k for k, e in enumerate(q) if e["uid"] == uid), -1) if uid else p.index + 1
        if not (0 <= i < len(q)):
            raise ValueError("Esa canción ya no está en el set")
        if b.get("mode") == "insert":
            if i < p.index:
                raise ValueError("Esa parte del set ya ha sonado")
            return i + 1, q[i]["track"], (q[i + 1]["track"] if i + 1 < len(q) else None), q[i]["track"].get("seg")
        if i <= p.index:
            raise ValueError("Esa canción ya ha sonado o está sonando")
        return i, q[i - 1]["track"] if i > 0 else None, (q[i + 1]["track"] if i + 1 < len(q) else None), q[i]["track"].get("seg")

    def _dj_like_pool(self, t: dict, seg: Optional[int]) -> dict:
        """Una cancion de fuera del set con tono y energia comparables a las del set."""
        t = dj.annotate([dict(t)])[0]
        e = dj.raw_energy(t)
        vals = sorted(x for x in (dj.raw_energy(c) for c in self.dj.pool) if x is not None)
        if e is not None and vals:
            t["energy"] = round(sum(v <= e for v in vals) / len(vals), 3)
        if seg is not None:
            t["seg"] = seg
        return t

    async def h_dj_alternatives(self, request):
        p = self.player
        b = await request.json() if request.can_read_body else {}
        if not self.dj.active:
            return web.json_response([])
        try:
            pos, prev, after, seg = self._dj_slot(b)
        except ValueError:
            return web.json_response([])
        if prev is None:
            return web.json_response([])
        n = len(p.queue) + (1 if b.get("mode") == "insert" else 0)
        tgt = dj.target(self.dj.active["shape"], pos / max(1, n - 1))
        exclude = {e["track"]["id"] for e in p.queue}
        in_set = {dj.song_key(e["track"]) for e in p.queue}
        pool = [t for t in self.dj.pool if dj.song_key(t) not in in_set]
        if seg is not None:
            pool = [t for t in pool if t.get("seg") == seg]  # recorrido: del mismo tramo
        alts = dj.alternatives(pool, prev, after, tgt, exclude, n=18, graph=await self._graph())
        rank = {"good": 0, "ok": 1, "unknown": 2, "bad": 3}
        out = [{**t, "transition": dj.transition(prev, t)} for t in alts]
        out.sort(key=lambda t: rank.get((t["transition"] or {}).get("level"), 2))  # estable: mantiene el resto del orden
        return web.json_response(out[:6])

    async def h_dj_search(self, request):
        """Busqueda en todo TIDAL para cambiar o meter una cancion en el set: como encaja ahi."""
        b = await request.json()
        q = (b.get("q") or "").strip()
        if not q:
            return web.json_response([])
        pos, prev, after, seg = self._dj_slot(b)
        r = await self.library.search(q)
        out = []
        for t in r["tracks"][:30]:
            t = self._dj_like_pool(t, seg)
            t["transition"] = dj.transition(prev, t) if prev else None
            out.append(t)
        return web.json_response(out)

    async def h_dj_replace(self, request):
        b = await request.json()
        t = b.get("track")
        if not isinstance(t, dict) or not t.get("id"):
            raise ValueError("Falta la canción")
        pos, prev, after, seg = self._dj_slot({"uid": b.get("uid"), "mode": "replace"})
        await self.player.replace_item(self.player.queue[pos]["uid"], self._dj_like_pool(t, seg))
        self.broadcast({"type": "dj", "dj": self._dj_snapshot()})
        return web.json_response({"ok": True})

    async def h_dj_insert(self, request):
        b = await request.json()
        t = b.get("track")
        if not isinstance(t, dict) or not t.get("id"):
            raise ValueError("Falta la canción")
        if not t.get("available", True):
            raise ValueError("Esa canción no está disponible en TIDAL")
        pos, prev, after, seg = self._dj_slot({"uid": b.get("uid"), "mode": "insert"})
        await self.player.insert_at(pos, [self._dj_like_pool(t, seg)])
        self.broadcast({"type": "dj", "dj": self._dj_snapshot()})
        return web.json_response({"ok": True})

    async def _save_playlist(self, name: str, tracks: list) -> dict:
        seen, ids = set(), []
        for t in tracks:
            if t and t.get("id") and t["id"] not in seen and t.get("available", True):
                seen.add(t["id"])
                ids.append(t["id"])
        desc = f"Sesión DJ guardada desde TIDAL Connect para Linux el {time.strftime('%d/%m/%Y')}"
        pl = await self.library.create_playlist(name, desc, ids)
        return {"ok": True, "playlist": pl, "tracks": len(ids)}

    @staticmethod
    def _playlist_name(b: dict, title: str) -> str:
        name = (b.get("name") or "").strip()
        return name or f"{title} · {time.strftime('%d/%m/%Y')}"

    async def h_dj_save_playlist(self, request):
        """Guarda el set DJ que suena (todo: lo que ha sonado y lo que viene) como playlist."""
        b = await request.json() if request.can_read_body else {}
        p = self.player
        if not self.dj.active or (p.context or {}).get("type") != "dj" or not p.queue:
            raise ValueError("No hay un set DJ sonando")
        title = self.dj.active.get("title", "Set DJ").replace("Set DJ · ", "")
        return web.json_response(await self._save_playlist(self._playlist_name(b, title), [e["track"] for e in p.queue]))

    async def h_djapp_save_playlist(self, request):
        """Guarda la sesion de TIDAL DJ: lo que ha sonado, lo que esta en los platos y lo que viene."""
        b = await request.json() if request.can_read_body else {}
        a = self.djapp
        nxt = [d for d in a.decks.values() if d]
        tracks = a.history + nxt + a.queue[a.pos:]
        if not tracks:
            raise ValueError("Todavía no hay canciones en la sesión")
        return web.json_response(await self._save_playlist(self._playlist_name(b, a.title or "Sesión DJ"), tracks))

    async def h_dj_replace_next(self, request):
        b = await request.json()
        p = self.player
        if not (0 <= p.index < len(p.queue) - 1):
            return web.json_response({"error": "No hay siguiente canción"}, status=400)
        await p.replace_item(p.queue[p.index + 1]["uid"], b["track"])
        self.broadcast({"type": "dj", "dj": self._dj_snapshot()})
        return web.json_response({"ok": True})

    async def h_dj_regenerate(self, request):
        p = self.player
        if not self.dj.active or p.index < 0:
            return web.json_response({"error": "No hay un set DJ activo"}, status=400)
        a = self.dj.active
        played = [e["track"] for e in p.queue[: p.index + 1]]
        done_sec = sum(t.get("duration") or 0 for t in played)
        start_p = min(0.95, done_sec / max(60, a["minutes"] * 60))
        opts = a.get("options") or {}
        played_keys = {dj.song_key(t) for t in played}
        pool = [t for t in self.dj.pool if dj.song_key(t) not in played_keys]
        if a.get("mode") == "styles":
            # Mantiene el reparto de tramos de lo que queda; cambia las canciones.
            segs = (a.get("config") or {}).get("segments") or a.get("segments") or []
            last = played[-1].get("seg", 0) if played else 0
            plan = [e["track"].get("seg", last) for e in p.queue[p.index + 1:]]
            pools = [[t for t in pool if t.get("seg") == i] for i in range(len(segs))]
            seq = await asyncio.to_thread(
                dj.make_journey, segs, pools, a["shape"], plan=plan, start=played, graph=await self._graph(),
                bpm_min=opts.get("bpm_min") or None, bpm_max=opts.get("bpm_max") or None,
                include_unknown=bool(opts.get("include_unknown")))
        else:
            seq = await asyncio.to_thread(
                dj.build_set, pool, a["minutes"], a["shape"],
                bpm_min=opts.get("bpm_min") or None, bpm_max=opts.get("bpm_max") or None,
                include_unknown=bool(opts.get("include_unknown")), start=played, start_p=start_p,
                seed=None, graph=await self._graph())
        if not seq:
            return web.json_response({"error": "No quedan canciones que encajen"}, status=400)
        await p.replace_upcoming(seq)
        self.broadcast({"type": "dj", "dj": self._dj_snapshot()})
        return web.json_response({"ok": True, "tracks": len(seq)})

    async def h_settings(self, request):
        b = await request.json()
        allowed = {k: v for k, v in b.items() if k in ("quality",)}
        return web.json_response(config.save_settings(**allowed))

    async def ws(self, request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.sockets.add(ws)
        await ws.send_str(json.dumps({"type": "hello", "status": self.status()}))
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
        self.sockets.discard(ws)
        return ws


async def _send(ws, data: str) -> None:
    try:
        await ws.send_str(data)
    except Exception:
        pass


def run() -> None:
    import os
    level = logging.DEBUG if os.environ.get("TIDAL_CONNECT_DEBUG") else logging.INFO
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("tidalconnect").setLevel(level)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    web.run_app(App().build(), host=config.HOST, port=config.PORT, print=None, shutdown_timeout=3)
