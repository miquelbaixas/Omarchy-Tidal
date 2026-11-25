"""Reproductor: cola + dos salidas posibles.

- Un receptor TIDAL Connect (p. ej. DMP-A6): la app solo manda ordenes; el receptor
  pide la cola (requestQueueItems) y descarga la musica de TIDAL por su cuenta.
- Este ordenador (LOCAL_ID): la musica se reproduce aqui con mpv, por la salida de
  audio elegida (altavoces, cascos, Bluetooth...).
"""
import asyncio
import json
import logging
import random
import re
import time
import uuid
from typing import Any, Callable, Optional

from . import config
from .connect_client import ConnectClient
from .local_output import LocalOutput
from .tidal_service import TidalService

log = logging.getLogger(__name__)

QUEUE_FILE = config.CONFIG_DIR / "queue.json"
LOCAL_ID = "local"
LOCAL_DEVICE = {"id": LOCAL_ID, "name": "Este ordenador", "model": "Altavoces, cascos o Bluetooth"}


class Player:
    def __init__(self, tidal: TidalService):
        self.tidal = tidal
        self.listeners: list[Callable[[dict], Any]] = []
        self.device: Optional[dict] = config.load_settings().get("device")
        self.client: Optional[ConnectClient] = None
        self.session_id: Optional[str] = None
        self._session_waiter: Optional[asyncio.Future] = None
        self._connect_lock = asyncio.Lock()
        self._closing = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._loaded = False   # el receptor tiene cargada nuestra cola en esta sesion
        self._joined = False
        self.taken_over = False  # otro dispositivo ha tomado el control del altavoz

        self.queue: list[dict] = []        # [{"uid", "track"}] en orden de reproduccion
        self.original: list[str] = []      # uids en orden original (para quitar aleatorio)
        self.index = -1
        self.queue_id: Optional[str] = None
        self.context: Optional[dict] = None
        self.shuffle = False
        self.repeat = "OFF"
        self._uid = 0

        self.remote_state = "IDLE"
        self.progress = 0
        self.duration = 0
        self._progress_at = time.monotonic()
        self.volume: Optional[float] = None
        self.muted = False
        self.remote_media: Optional[dict] = None
        self.audio_info: Optional[dict] = None
        self.error: Optional[str] = None
        self._saved_session: Optional[str] = None
        self._saved_loaded = False
        self._pending: dict[int, str] = {}  # requestId -> orden, para detectar fallos
        # Reproduccion en este ordenador (mpv)
        self.local = LocalOutput(self._local_status, self._local_advance, self._local_end)
        self._local_active = False
        self._load_queue()
        if self.is_local:
            self.volume = config.load_settings().get("local_volume") or 0.6

    # ------------------------------------------------------- este ordenador
    @property
    def is_local(self) -> bool:
        return (self.device or {}).get("id") == LOCAL_ID

    @staticmethod
    def _url(entry: Optional[dict]) -> Optional[str]:
        if not entry:
            return None
        return f"http://{config.HOST}:{config.PORT}/local/stream/{entry['track']['id']}"

    def _next_entry(self) -> Optional[dict]:
        if self.repeat == "ONE" and 0 <= self.index < len(self.queue):
            return self.queue[self.index]
        if self.index + 1 < len(self.queue):
            return self.queue[self.index + 1]
        if self.repeat == "ALL" and self.queue:
            return self.queue[0]
        return None

    def _local_status(self, st: dict) -> None:
        if not self.is_local:
            return
        if st["idle"]:
            state = "IDLE"
        elif st["buffering"]:
            state = "BUFFERING"
        else:
            state = "PAUSED" if st["paused"] else "PLAYING"
        self.remote_state = state
        self.progress = int(st["pos"] * 1000)
        # Duracion real de TIDAL (mpv solo la estima con lo que lleva descargado)
        cur = self.queue[self.index]["track"] if 0 <= self.index < len(self.queue) else None
        if cur and cur.get("duration"):
            self.duration = int(cur["duration"]) * 1000
        elif st["duration"]:
            self.duration = int(st["duration"] * 1000)
        self._progress_at = time.monotonic()
        a = st.get("audio")
        if a:
            fmt = (a.get("format") or "")
            bits = 24 if ("s32" in fmt or "s24" in fmt or "float" in fmt) else 16
            info = self._local_format or {}
            self.audio_info = {"quality": info.get("quality"), "bit_depth": info.get("bit_depth") or bits,
                               "sample_rate": info.get("sample_rate") or a.get("sample_rate"),
                               "codec": "flac", "mode": "STEREO"}
        self._emit_state()

    async def _local_advance(self) -> None:
        """mpv ha pasado a la siguiente cancion de su lista."""
        nxt = self._next_entry()
        if nxt is not None:
            self.index = self.queue.index(nxt)
        self.progress, self._progress_at = 0, time.monotonic()
        await self._load_local_format()
        self._emit_queue()
        self._emit_state()
        await self.local.set_next(self._url(self._next_entry()))

    async def _local_end(self) -> None:
        """mpv se ha quedado sin nada que reproducir."""
        if self.is_local and self._local_active:
            self.remote_state = "IDLE"
            self._emit_state()

    _local_format: Optional[dict] = None

    async def _load_local_format(self) -> None:
        self._local_format = None
        cur = self.queue[self.index]["track"] if 0 <= self.index < len(self.queue) else None
        if cur and self.streams:
            try:
                info = await self.streams.resolve(cur["id"])
                self._local_format = {"quality": info.get("quality"), "bit_depth": info.get("bit_depth"),
                                      "sample_rate": info.get("sample_rate")}
            except Exception:
                pass

    streams = None  # StreamResolver (lo asigna el servidor)

    # -------------------------------------------------------------- eventos
    def _emit(self, kind: str, data: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn({"type": kind, **data})
            except Exception:
                log.exception("listener")

    def snapshot(self) -> dict:
        cur = self.queue[self.index]["track"] if 0 <= self.index < len(self.queue) else None
        if cur is None and self.remote_media:
            cur = self.remote_media
        return {
            "device": self.device,
            "connected": self._local_active if self.is_local else bool(self.client and self.client.connected and self.session_id),
            "state": self.remote_state,
            "progress": self.progress,
            "progress_at": time.time() - (time.monotonic() - self._progress_at),
            "duration": self.duration,
            "volume": self.volume,
            "muted": self.muted,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "index": self.index,
            "current": cur,
            "context": self.context,
            "audio": self.audio_info,
            "error": self.error,
            "taken_over": self.taken_over,
        }

    def queue_snapshot(self) -> dict:
        return {"index": self.index,
                "items": [{"uid": e["uid"], **e["track"]} for e in self.queue],
                "context": self.context}

    def _emit_state(self) -> None:
        self._emit("state", {"state": self.snapshot()})

    def _emit_queue(self) -> None:
        self._save_queue()
        self._emit("queue", {"queue": self.queue_snapshot()})

    # ---------------------------------------------------------- persistencia
    def _save_queue(self) -> None:
        try:
            config.write_private_json(QUEUE_FILE, {
                "queue": self.queue, "original": self.original, "index": self.index,
                "queue_id": self.queue_id, "context": self.context, "shuffle": self.shuffle,
                "repeat": self.repeat, "uid": self._uid, "session_id": self._saved_session,
                "session_loaded": self._loaded,
            })
        except OSError:
            log.exception("No se pudo guardar la cola")

    def _load_queue(self) -> None:
        try:
            d = json.loads(QUEUE_FILE.read_text())
        except (OSError, ValueError):
            return
        self.queue = d.get("queue") or []
        self.original = d.get("original") or [e["uid"] for e in self.queue]
        self.index = d.get("index", -1)
        self.queue_id = d.get("queue_id")
        self.context = d.get("context")
        self.shuffle = bool(d.get("shuffle"))
        self.repeat = d.get("repeat") or "OFF"
        self._uid = d.get("uid", len(self.queue))
        self._saved_session = d.get("session_id")
        self._saved_loaded = bool(d.get("session_loaded"))

    # -------------------------------------------------------------- receptor
    async def set_device(self, device: dict) -> None:
        """Cambia de salida. Si estaba sonando, sigue en la nueva desde el mismo punto
        (TIDAL solo deja reproducir en un sitio a la vez por cuenta)."""
        if self.device and self.device.get("id") == device.get("id"):
            self.device = device
            config.save_settings(device=device)
            self._emit_state()
            return
        was_playing = self.remote_state in ("PLAYING", "BUFFERING")
        pos = self.current_progress()
        if self.is_local:
            await self.local.stop()
            self._local_active = False
        elif self.device:
            await self.disconnect(stop=was_playing)
        self.device = device
        config.save_settings(device=device)
        self.remote_state, self._loaded, self.audio_info = "IDLE", False, None
        if self.is_local:
            self.volume = config.load_settings().get("local_volume") or 0.6
        self._emit_state()
        if was_playing and 0 <= self.index < len(self.queue):
            self.progress = pos
            await self._load_remote(position=pos, autoplay=True)

    async def try_resume(self) -> None:
        """Al arrancar: recupera el control de nuestra sesion si sigue activa en el altavoz."""
        if not (self.device and self._saved_session and self.tidal.logged_in):
            return
        try:
            await self.ensure_connected(resume_only=True)
        except Exception as e:
            log.info("No se pudo retomar la sesion: %s", e)

    async def ensure_connected(self, resume_only: bool = False) -> None:
        if self.is_local:
            if not resume_only:
                await self.local.ensure()
            return
        async with self._connect_lock:
            if self.client and self.client.connected and self.session_id:
                return
            if not self.device:
                raise RuntimeError("Elige primero un altavoz")
            if not self.tidal.logged_in:
                raise RuntimeError("Inicia sesion en Tidal")
            if self.client:
                await self.client.close()
            self.client = ConnectClient(self.device["host"], int(self.device["port"]), self._on_message, self._on_closed)
            await self.client.connect()
            loop = asyncio.get_running_loop()
            # 1) Intenta retomar nuestra propia sesion (no le quita el control a nadie).
            if self._saved_session:
                self._session_waiter = loop.create_future()
                await self.client.send({"command": "resumeSession", "sessionId": self._saved_session})
                try:
                    sid = await asyncio.wait_for(self._session_waiter, timeout=4)
                except asyncio.TimeoutError:
                    sid = None
                if sid:
                    self.session_id = sid
                    self._loaded = self._saved_loaded  # la sesion retomada conserva lo que tuviera
                    self.error = None
                    log.info("Sesion %s retomada", sid)
                    self._emit_state()
                    return
                self._saved_session = None
                self._save_queue()
                # El receptor cierra la conexion tras rechazar resumeSession.
                self._closing = True
                await self.client.close()
                self._closing = False
                if resume_only:
                    self.client = None
                    return
                self.client = ConnectClient(self.device["host"], int(self.device["port"]), self._on_message, self._on_closed)
                await self.client.connect()
            elif resume_only:
                return
            # 2) Sesion nueva.
            self._joined = False
            self._session_waiter = loop.create_future()
            user = self.tidal.session.user
            await self.client.send({
                "command": "startSession",
                "appId": "tidal",
                "appName": "TIDAL",
                "sessionCredential": f"tidal-connect-linux-{user.id}",
            })
            try:
                self.session_id = await asyncio.wait_for(self._session_waiter, timeout=8)
            except asyncio.TimeoutError:
                await self.client.close()
                raise RuntimeError("El altavoz no ha aceptado la sesion")
            if not self.session_id:
                await self.client.close()
                raise RuntimeError("El altavoz ha rechazado la sesion")
            # Una sesion nueva no tiene nuestra cola (salvo si nos hemos unido a la nuestra).
            self._loaded = self._joined
            self._saved_session = self.session_id
            self._save_queue()
            self.error = None
            self._emit_state()

    async def disconnect(self, stop: bool = False) -> None:
        if self.is_local and stop:
            await self.local.stop()
            self._local_active = False
            self.remote_state = "IDLE"
            self._emit_state()
        if self.client and self.client.connected and self.session_id:
            try:
                await self.client.send({"command": "endSession", "sessionId": self.session_id, "stopCasting": stop})
                await asyncio.sleep(0.3)
            except Exception:
                pass
        if self.client:
            self._closing = True
            await self.client.close()
            self._closing = False
        self.client, self.session_id = None, None
        self._emit_state()

    async def _on_closed(self) -> None:
        had_session = self.session_id is not None
        self.session_id = None
        self._emit_state()
        if not self._closing and had_session and self._saved_session:
            # Caida inesperada: recupera nuestra sesion en segundo plano.
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        for delay in (1, 2, 5, 10, 20, 30):
            await asyncio.sleep(delay)
            if self.session_id or self._closing or not self._saved_session:
                return
            try:
                await self.ensure_connected(resume_only=True)
            except Exception as e:
                log.info("Reconexion fallida: %s", e)
            if self.session_id:
                log.info("Reconectado con el altavoz")
                return

    async def _content_server_info(self) -> dict:
        access, refresh = await self.tidal.fresh_tokens()
        quality = config.load_settings().get("quality") or "HI_RES_LOSSLESS"
        return {
            "serverUrl": "https://api.tidal.com/v1",
            "authInfo": {
                "headerAuth": f"Bearer {access}",
                "oauthServerInfo": {
                    "serverUrl": "https://auth.tidal.com/v1/oauth2/token",
                    "authInfo": {"headerAuth": "", "oauthParameters": {"accessToken": access, "refreshToken": refresh}},
                    "httpHeaderFields": [],
                    "formParameters": {"grant_type": "switch_client", "scope": "r_usr w_usr"},
                },
            },
            "queryParameters": {"audioquality": quality, "countryCode": self.tidal.session.country_code},
            "httpHeaderFields": [],
        }

    async def update_server_info(self) -> None:
        """Manda al receptor un permiso renovado."""
        if self.client and self.client.connected and self.session_id:
            await self.client.request("updateServerInfo", contentServerInfo=await self._content_server_info())

    @staticmethod
    def _media(entry: dict) -> dict:
        """Describe una pista para el receptor (formato de cola local de TIDAL Connect).

        artists: lista de nombres; images: {"low"|"medium"|"high": {url, width, height}}.
        Con otro formato el receptor ignora la portada y no la muestra en su pantalla.
        """
        t = entry["track"]
        album = t.get("album") or {}
        meta = {
            "title": t["title"],
            "artists": [a["name"] for a in t.get("artists", []) if a.get("name")],
            "albumTitle": album.get("title", ""),
            "duration": int(t.get("duration", 0)) * 1000,
        }
        images = _cover_sizes(album.get("cover_large") or album.get("cover"))
        if images:
            meta["images"] = images
        return {"itemId": entry["uid"], "mediaId": t["id"], "mediaType": 0, "metadata": meta}

    async def _on_message(self, m: dict) -> None:
        cmd = m.get("command")
        if cmd == "notifySessionStarted":
            self._joined = bool(m.get("joined"))
            if self._session_waiter and not self._session_waiter.done():
                self._session_waiter.set_result(m.get("sessionId"))
            else:
                self.session_id = m.get("sessionId")
        elif cmd == "notifySessionResumed":
            if self._session_waiter and not self._session_waiter.done():
                self._session_waiter.set_result(m.get("sessionId"))
            else:
                self.session_id = m.get("sessionId")
        elif cmd == "notifySessionEnded":
            if m.get("sessionId") == self.session_id:
                # Otra app ha tomado el control (o el altavoz cerro la sesion).
                self.session_id = None
                self._loaded = False
                if self.remote_state in ("PLAYING", "BUFFERING"):
                    self.progress = self.current_progress()
                self.remote_state = "IDLE"
                self._progress_at = time.monotonic()
                self.taken_over = not m.get("suspended")
                log.info("El altavoz ha cerrado nuestra sesion (suspended=%s)", m.get("suspended"))
                self._emit_state()
        elif cmd == "notifyPlayerStatusChanged":
            self.remote_state = m.get("playerState") or self.remote_state
            self.progress = int(m.get("progress") or 0)
            self.duration = int(m.get("duration") or self.duration or 0)
            self._progress_at = time.monotonic()
            self._emit_state()
        elif cmd == "notifyMediaChanged":
            info = m.get("mediaInfo") or {}
            uid = info.get("itemId")
            idx = next((i for i, e in enumerate(self.queue) if e["uid"] == uid), -1)
            self.audio_info = _parse_custom(info.get("customData"))
            if idx >= 0:
                changed = idx != self.index
                self.index, self.remote_media = idx, None
                if changed:
                    self.progress, self._progress_at = 0, time.monotonic()
                    self._emit_queue()
            else:
                self.remote_media = _media_to_track(info)
            self._emit_state()
        elif cmd == "notifyDeviceStatusChanged":
            vol = m.get("volume") or {}
            if "level" in vol:
                self.volume = float(vol["level"])
            if "mute" in vol:
                self.muted = bool(vol["mute"])
            self._emit_state()
        elif cmd == "requestQueueItems":
            await self._respond_items(m)
        elif cmd in ("notifyContentServerError", "notifyPlaybackError"):
            log.warning("Error del receptor: %s", m)
            if cmd == "notifyContentServerError":
                try:
                    await self.tidal.force_refresh()
                    await self.update_server_info()
                except Exception:
                    log.exception("No se pudo renovar el permiso")
            self.error = f"{cmd}: {m.get('errorCode')}"
            self._emit_state()
        elif cmd == "notifySessionError":
            log.warning("Error de sesion: %s", m)
            if self._session_waiter and not self._session_waiter.done():
                self._session_waiter.set_result(None)
            elif m.get("errorCode") in (1101, 1102):
                self.session_id = None
        elif cmd == "notifyRequestResult":
            sent = self._pending.pop(m.get("requestId"), None)
            code = m.get("resultCode")
            if code not in (0, None):
                log.info("Peticion %s (%s) -> codigo %s", m.get("requestId"), sent, code)
            if code == 1002 and sent in ("play", "next", "previous", "seek", "selectQueueItem") and self.queue:
                # El altavoz no tiene nuestra cola: se vuelve a cargar donde iba.
                self._loaded = False
                pos = self.progress if sent in ("play", "seek") else 0
                asyncio.ensure_future(self._load_remote(position=pos, autoplay=True))
        else:
            log.debug("<< %s", cmd)

    async def _respond_items(self, m: dict) -> None:
        uid = m.get("itemId")
        idx = next((i for i, e in enumerate(self.queue) if e["uid"] == uid), -1)
        if idx < 0:
            items, has_begin, has_end = [], True, True
        else:
            before, after = int(m.get("beforeSize") or 20), int(m.get("afterSize") or 20)
            start, end = max(0, idx - before), min(len(self.queue), idx + after + 1)
            items = [self._media(e) for e in self.queue[start:end]]
            has_begin, has_end = start == 0, end == len(self.queue)
        await self.client.request("respondQueueItems", queueRequestId=m.get("queueRequestId"),
                                  mediaInfos=items, hasBegin=has_begin, hasEnd=has_end)

    # ------------------------------------------------------------ cola local
    def _entry(self, track: dict) -> dict:
        self._uid += 1
        return {"uid": str(self._uid), "track": track}

    async def play_tracks(self, tracks: list, index: int = 0, context: Optional[dict] = None,
                          shuffle: Optional[bool] = None) -> None:
        tracks = [t for t in tracks if t and t.get("available", True)]
        if not tracks:
            raise RuntimeError("No hay canciones que reproducir")
        index = max(0, min(index, len(tracks) - 1))
        entries = [self._entry(t) for t in tracks]
        self.original = [e["uid"] for e in entries]
        if shuffle is not None:
            self.shuffle = shuffle
        if self.shuffle:
            if shuffle is True and index == 0:
                # Boton "Aleatorio": empieza por una cancion al azar.
                index = random.randrange(len(entries))
            first = entries.pop(index)
            random.shuffle(entries)
            entries.insert(0, first)
            index = 0
        self.queue, self.index, self.context = entries, index, context
        self.queue_id = f"linux-{uuid.uuid4().hex[:12]}"
        self.remote_media, self.error = None, None
        self.progress, self.duration = 0, int(tracks[0].get("duration", 0)) * 1000
        self._emit_queue()
        await self._load_remote(position=0, autoplay=True)

    async def _load_remote(self, position: int, autoplay: bool) -> None:
        await self.ensure_connected()
        if not (0 <= self.index < len(self.queue)):
            return
        if self.is_local:
            await self._load_local_format()
            await self.local.play(self._url(self.queue[self.index]), self._url(self._next_entry()),
                                  position / 1000, paused=not autoplay)
            self._local_active, self._loaded, self.taken_over, self.error = True, True, False, None
            self.remote_state = "BUFFERING"
            self._emit_state()
            return
        await self.client.request(
            "loadLocalQueue",
            queueInfo={"queueId": self.queue_id, "repeatMode": self.repeat, "shuffled": False},
            currentMediaInfo=self._media(self.queue[self.index]),
            contentServerInfo=await self._content_server_info(),
            position=int(position),
            autoplay=autoplay,
        )
        self._loaded = True
        self.taken_over = False
        self._save_queue()
        self.remote_state = "BUFFERING"
        self._emit_state()

    async def _refresh_remote(self) -> None:
        """Pide al receptor que vuelva a leer la cola sin cortar la cancion."""
        if self.is_local:
            if self._local_active and self.local.proc:
                await self.local.set_next(self._url(self._next_entry()))
            return
        if self.client and self.client.connected and self.session_id:
            await self.client.request("refreshQueue")

    async def add(self, tracks: list, play_next: bool) -> None:
        tracks = [t for t in tracks if t and t.get("available", True)]
        if not tracks:
            return
        entries = [self._entry(t) for t in tracks]
        if not self.queue or self.index < 0:
            await self.play_tracks(tracks, 0, context=None)
            return
        pos = self.index + 1 if play_next else len(self.queue)
        self.queue[pos:pos] = entries
        self.original.extend(e["uid"] for e in entries)
        self._emit_queue()
        await self._refresh_remote()

    async def insert_at(self, pos: int, tracks: list) -> None:
        """Mete canciones en una posicion de la cola (siempre despues de la que suena)."""
        entries = [self._entry(t) for t in tracks if t and t.get("available", True)]
        if not entries:
            return
        pos = max(self.index + 1, min(pos, len(self.queue)))
        self.queue[pos:pos] = entries
        self.original.extend(e["uid"] for e in entries)
        self._emit_queue()
        await self._refresh_remote()

    async def remove(self, uid: str) -> None:
        idx = next((i for i, e in enumerate(self.queue) if e["uid"] == uid), -1)
        if idx < 0 or idx == self.index:
            return
        del self.queue[idx]
        if idx < self.index:
            self.index -= 1
        self.original = [u for u in self.original if u != uid]
        self._emit_queue()
        await self._refresh_remote()

    async def move(self, uid: str, to: int) -> None:
        idx = next((i for i, e in enumerate(self.queue) if e["uid"] == uid), -1)
        if idx < 0:
            return
        cur_uid = self.queue[self.index]["uid"] if 0 <= self.index < len(self.queue) else None
        entry = self.queue.pop(idx)
        to = max(0, min(to, len(self.queue)))
        self.queue.insert(to, entry)
        if cur_uid:
            self.index = next(i for i, e in enumerate(self.queue) if e["uid"] == cur_uid)
        self._emit_queue()
        await self._refresh_remote()

    async def replace_item(self, uid: str, track: dict) -> None:
        """Cambia una cancion de la cola por otra (p. ej. "otra siguiente" del DJ)."""
        idx = next((i for i, e in enumerate(self.queue) if e["uid"] == uid), -1)
        if idx < 0 or idx == self.index:
            return
        entry = self._entry(track)
        self.queue[idx] = entry
        self.original = [entry["uid"] if u == uid else u for u in self.original]
        self._emit_queue()
        await self._refresh_remote()

    async def replace_upcoming(self, tracks: list) -> None:
        """Sustituye todo lo que viene despues de la cancion actual."""
        entries = [self._entry(t) for t in tracks if t]
        self.queue = self.queue[: self.index + 1] + entries
        self.original = [e["uid"] for e in self.queue]
        self._emit_queue()
        await self._refresh_remote()

    async def clear_upcoming(self) -> None:
        if 0 <= self.index < len(self.queue):
            keep = self.queue[: self.index + 1]
            self.queue = keep
            self.original = [e["uid"] for e in keep]
            self._emit_queue()
            await self._refresh_remote()

    async def jump(self, uid: str) -> None:
        idx = next((i for i, e in enumerate(self.queue) if e["uid"] == uid), -1)
        if idx < 0:
            return
        if self.is_local or not await self._ready():
            self.index, self.progress = idx, 0
            self._emit_queue()
            await self._load_remote(position=0, autoplay=True)
            return
        await self.client.request("selectQueueItem", mediaInfo=self._media(self.queue[idx]))

    # -------------------------------------------------------------- mandos
    async def _cmd(self, command: str, **fields) -> None:
        try:
            await self.ensure_connected()
        except Exception as e:
            self.error = str(e)
            self._emit_state()
            raise
        rid = await self.client.request(command, **fields)
        self._pending[rid] = command
        if len(self._pending) > 100:
            self._pending.pop(next(iter(self._pending)))

    async def _ready(self) -> bool:
        """Conecta con el altavoz y dice si tiene cargada nuestra cola."""
        if self.is_local:
            return self._local_active and self.local.proc is not None and not self.local.state["idle"]
        try:
            await self.ensure_connected()
        except Exception as e:
            self.error = str(e)
            self._emit_state()
            raise
        return self._loaded

    async def toggle(self) -> None:
        if self.is_local:
            if await self._ready():
                await self.local.pause(self.remote_state in ("PLAYING", "BUFFERING"))
            elif self.queue:
                await self._load_remote(position=self.progress, autoplay=True)
            return
        if self.remote_state == "PLAYING" and self.session_id and self._loaded:
            await self._cmd("pause")
            return
        if not await self._ready():
            # El altavoz no tiene nuestra cola (app reiniciada u otra app tomo el control):
            # se vuelve a cargar donde iba.
            if self.queue:
                await self._load_remote(position=self.progress, autoplay=True)
            return
        await self._cmd("play")

    async def next(self) -> None:
        if self.is_local:
            nxt = self.queue[self.index + 1] if self.index + 1 < len(self.queue) else (
                self.queue[0] if self.repeat == "ALL" and self.queue else None)
            if nxt is not None:
                self.index, self.progress = self.queue.index(nxt), 0
                self._emit_queue()
                await self._load_remote(position=0, autoplay=True)
            return
        if not await self._ready():
            if self.index + 1 < len(self.queue):
                self.index += 1
                self.progress = 0
                self._emit_queue()
                await self._load_remote(position=0, autoplay=True)
            return
        await self._cmd("next")

    async def previous(self) -> None:
        if self.current_progress() > 3000:
            await self.seek(0)
            return
        if self.is_local or not await self._ready():
            if self.index > 0:
                self.index -= 1
                self._emit_queue()
            self.progress = 0
            await self._load_remote(position=0, autoplay=True)
            return
        await self._cmd("previous")

    async def seek(self, ms: int) -> None:
        if not await self._ready():
            self.progress = int(ms)
            await self._load_remote(position=int(ms), autoplay=True)
            return
        if self.is_local:
            await self.local.seek(ms / 1000)
        else:
            await self._cmd("seek", position=int(ms))
        self.progress, self._progress_at = int(ms), time.monotonic()
        self._emit_state()

    async def set_volume(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level)))
        if self.is_local:
            await self.local.ensure()
            await self.local.set_volume(level)
            config.save_settings(local_volume=level)
        else:
            await self._cmd("setVolume", level=level)
        self.volume = level
        self._emit_state()

    async def set_mute(self, mute: bool) -> None:
        if self.is_local:
            await self.local.ensure()
            await self.local.set_mute(bool(mute))
        else:
            await self._cmd("setMute", mute=bool(mute))
        self.muted = bool(mute)
        self._emit_state()

    async def set_repeat(self, mode: str) -> None:
        mode = mode if mode in ("OFF", "ALL", "ONE") else "OFF"
        self.repeat = mode
        self._emit_state()
        self._save_queue()
        if self.is_local:
            await self._refresh_remote()
        elif self.client and self.client.connected and self.session_id:
            await self.client.request("setRepeatMode", repeatMode=mode)

    async def set_local_output(self, output: str) -> None:
        config.save_settings(local_output=output or None)
        await self.local.set_output(output)
        self._emit_state()

    async def set_shuffle(self, on: bool) -> None:
        if on == self.shuffle:
            return
        self.shuffle = on
        if 0 <= self.index < len(self.queue):
            cur = self.queue[self.index]
            if on:
                upcoming = self.queue[self.index + 1:]
                random.shuffle(upcoming)
                self.queue = self.queue[: self.index + 1] + upcoming
            else:
                by_uid = {e["uid"]: e for e in self.queue}
                ordered = [by_uid[u] for u in self.original if u in by_uid]
                ordered += [e for e in self.queue if e["uid"] not in set(self.original)]
                self.queue = ordered
                self.index = next(i for i, e in enumerate(self.queue) if e["uid"] == cur["uid"])
            self._emit_queue()
            await self._refresh_remote()
        self._emit_state()

    def current_progress(self) -> int:
        if self.remote_state == "PLAYING":
            return int(self.progress + (time.monotonic() - self._progress_at) * 1000)
        return self.progress


def _parse_custom(raw) -> Optional[dict]:
    if not raw:
        return None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return {
        "quality": d.get("audioQuality"),
        "bit_depth": d.get("bitDepth"),
        "sample_rate": d.get("sampleRate"),
        "codec": d.get("codecs"),
        "mode": d.get("audioMode"),
    }


_SIZE_RE = re.compile(r"/\d+x\d+\.jpg$")


def _cover_sizes(url: Optional[str]) -> Optional[dict]:
    """Portada de TIDAL en tres tamanos, como la mandan las apps oficiales."""
    if not url:
        return None
    if not _SIZE_RE.search(url):
        return {"high": {"url": url}}
    out = {}
    for name, px in (("low", 160), ("medium", 640), ("high", 1280)):
        out[name] = {"url": _SIZE_RE.sub(f"/{px}x{px}.jpg", url), "width": px, "height": px}
    return out


def _media_to_track(info: dict) -> dict:
    meta = info.get("metadata") or {}
    images = meta.get("images") or {}
    if isinstance(images, dict):
        best = images.get("high") or images.get("medium") or images.get("low") or {}
        cover = best.get("url")
    else:
        cover = images[0].get("url") if images else None
    artists = []
    for a in meta.get("artists", []):
        if isinstance(a, str):
            artists.append({"id": "", "name": a})
        elif isinstance(a, dict):
            artists.append({"id": str(a.get("id", "")), "name": a.get("name", "")})
    return {
        "type": "track",
        "id": str(info.get("mediaId") or ""),
        "title": meta.get("title", ""),
        "duration": int((meta.get("duration") or 0) / 1000),
        "artists": artists,
        "album": {"id": meta.get("albumId"), "title": meta.get("albumTitle", ""), "cover": cover, "cover_large": cover},
        "explicit": False, "quality": None, "available": True,
    }
