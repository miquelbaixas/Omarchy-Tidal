"""Controlador de la app TIDAL DJ: lanza el motor de mezcla y le da las canciones.

El motor (djengine.engine) corre en un proceso aparte. Este controlador:
- descarga cada cancion en la cache local (streams) antes de mandarla a un plato,
- mantiene la cola del set y prepara la siguiente cuando un plato queda libre,
- reenvia el estado del motor a la interfaz (eventos "djapp").
"""
import asyncio
import json
import logging
import sys
from typing import Callable, Optional

from . import config
from .streams import StreamResolver

log = logging.getLogger(__name__)

OTHER = {"A": "B", "B": "A"}


class DJApp:
    def __init__(self, streams: StreamResolver, broadcast: Callable[[dict], None], on_play: Callable[[], object]):
        self.streams = streams
        self.broadcast = broadcast
        self._on_play = on_play           # pausa la otra reproduccion (TIDAL: un sitio a la vez)
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.title = ""
        self.queue: list[dict] = []
        self.pos = 0                      # siguiente cancion de la cola por cargar
        self.decks: dict[str, Optional[dict]] = {"A": None, "B": None}
        self.waveforms: dict[str, list] = {"A": [], "B": []}
        self.points: dict[str, dict] = {"A": {}, "B": {}}
        self.engine: dict = {}
        self.current: Optional[str] = None  # plato que suena principalmente
        self.history: list[dict] = []
        self._autoplay = False
        self._preparing: dict[str, Optional[str]] = {"A": None, "B": None}
        self._failures = 0

    # ---------------------------------------------------------------- proceso
    async def ensure(self) -> None:
        async with self._lock:
            if self.proc and self.proc.returncode is None:
                return
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "tidalconnect.djengine.engine",
                cwd=str(config.WEB_DIR.parent), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=None, limit=8 * 1024 * 1024)
            self._reader = asyncio.create_task(self._read())
            st = config.load_settings()
            await self._send({"cmd": "output", "target": st.get("dj_output")})
            if st.get("dj_cue_output"):
                await self._send({"cmd": "cue_output", "target": st["dj_cue_output"]})
            log.info("Motor DJ en marcha")

    async def shutdown(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                await self._send({"cmd": "quit"})
                await asyncio.wait_for(self.proc.wait(), 3)
            except Exception:
                self.proc.kill()
        self.proc = None

    async def _send(self, msg: dict) -> None:
        if not self.proc or self.proc.returncode is not None:
            return
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def command(self, cmd: str, **fields) -> None:
        await self.ensure()
        if cmd in ("play", "toggle"):
            await self._maybe_pause_other(cmd)
        await self._send({"cmd": cmd, **fields})

    async def _maybe_pause_other(self, cmd: str) -> None:
        if cmd == "play" or (cmd == "toggle" and self.engine.get("paused", True)):
            r = self._on_play()
            if asyncio.iscoroutine(r):
                await r

    # ---------------------------------------------------------------- eventos
    async def _read(self) -> None:
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                await self._event(m)
        except Exception:
            log.exception("Lectura del motor DJ interrumpida")
        log.info("Motor DJ detenido")

    async def _event(self, m: dict) -> None:
        ev = m.pop("ev", None)
        if ev == "state":
            self.engine = m
            self.broadcast({"type": "djapp_state", "engine": m})
            return
        if ev == "ready":
            d = m["deck"]
            self.waveforms[d] = m.get("waveform") or []
            self.points[d] = {**(m.get("points") or {}), **(m.get("analysis") or {})}
            self._preparing[d] = None
            if self._autoplay and d == "A":
                self._autoplay = False
                await self.command("play")
        elif ev == "track_started":
            d = m["deck"]
            self.current = d
            if self.decks.get(d):
                self.history.append(self.decks[d])
        elif ev == "need_next":
            d = m["deck"]
            self.decks[d] = None
            self.waveforms[d] = []
            self.points[d] = {}
            await self._load_next(d)
        elif ev == "error":
            log.warning("Motor DJ: %s", m.get("msg"))
            self.broadcast({"type": "djapp_error", "msg": m.get("msg") or "Error en el motor de mezcla"})
        elif ev == "ended":
            self.current = None
        self.broadcast({"type": "djapp", "ev": ev, "app": self.snapshot()})

    # ---------------------------------------------------------------- cola
    def snapshot(self) -> dict:
        return {"title": self.title, "queue": self.queue, "pos": self.pos, "decks": self.decks,
                "waveforms": self.waveforms, "points": self.points, "engine": self.engine,
                "current": self.current, "history": self.history[-30:],
                "output": config.load_settings().get("dj_output"),
                "cue_output": config.load_settings().get("dj_cue_output")}

    async def _prepare(self, deck: str, track: dict) -> None:
        self.decks[deck] = track
        self._preparing[deck] = track["id"]
        self.waveforms[deck] = []
        self.points[deck] = {}
        # el plato suelta la cancion anterior ya (si no suena), para que no entre mientras se descarga la nueva
        await self._send({"cmd": "unload", "deck": deck})
        self.broadcast({"type": "djapp", "ev": "loading", "app": self.snapshot()})
        tf = self.streams.file(track["id"])
        try:
            await asyncio.wait_for(tf.done.wait(), 120)
        except asyncio.TimeoutError:
            log.warning("Descarga lenta de %s", track["id"])
            return
        if tf.error:
            log.warning("No se pudo descargar %s: %s", track["id"], tf.error)
            self._failures += 1
            self.decks[deck] = None
            if self._failures >= 2:
                # Algo va mal con TIDAL (permiso, red...): se para y se avisa, sin quemar la cola.
                self._autoplay = False
                self.broadcast({"type": "djapp_error",
                                "msg": "No se puede descargar la música de TIDAL ahora mismo. Revisa la conexión o vuelve a cargar la sesión."})
                self.broadcast({"type": "djapp", "ev": "error", "app": self.snapshot()})
                return
            self.broadcast({"type": "djapp_error", "msg": f"«{track.get('title')}» no se pudo descargar: paso a la siguiente"})
            await self._load_next(deck)
            return
        self._failures = 0
        if self._preparing.get(deck) != track["id"]:
            return
        meta = {k: track.get(k) for k in ("id", "title", "bpm", "key", "key_scale", "camelot", "duration")}
        await self._send({"cmd": "prepare", "deck": deck, "path": str(tf.path), "meta": meta})

    async def _load_next(self, deck: str) -> None:
        while self.pos < len(self.queue):
            t = self.queue[self.pos]
            self.pos += 1
            if t.get("available", True):
                await self._prepare(deck, t)
                # descarga de antemano la que vendra despues
                if self.pos < len(self.queue):
                    self.streams.file(self.queue[self.pos]["id"])
                return

    async def load_set(self, tracks: list, title: str, autoplay: bool = True) -> None:
        await self.ensure()
        await self._send({"cmd": "stop"})
        self.queue = [t for t in tracks if t]
        self.title = title
        self.pos = 0
        self.history = []
        self.current = None
        self.decks = {"A": None, "B": None}
        self.waveforms = {"A": [], "B": []}
        self.points = {"A": {}, "B": {}}
        self._failures = 0
        if autoplay:
            await self._maybe_pause_other("play")
        self._autoplay = autoplay
        await self._load_next("A")
        await self._load_next("B")
        self.broadcast({"type": "djapp", "ev": "loaded", "app": self.snapshot()})

    def add(self, tracks: list, play_next: bool = False, at: Optional[int] = None) -> None:
        tracks = [t for t in tracks if t]
        if play_next:
            at = self.pos
        if at is not None:
            at = min(len(self.queue), max(self.pos, at))
            self.queue[at:at] = tracks
        else:
            self.queue.extend(tracks)
        self.broadcast({"type": "djapp", "ev": "queue", "app": self.snapshot()})

    def move(self, frm: int, to: int) -> None:
        """Cambia de sitio una cancion de la lista (solo las que aun no han sonado)."""
        n = len(self.queue)
        if not (self.pos <= frm < n):
            return
        to = min(n - 1, max(self.pos, to))
        t = self.queue.pop(frm)
        self.queue.insert(to, t)
        self.broadcast({"type": "djapp", "ev": "queue", "app": self.snapshot()})

    async def replace_loaded(self, deck: str, track: dict) -> None:
        """Cambia la cancion de un plato que no suena (p. ej. elegir otra siguiente)."""
        d = (self.engine.get("decks") or {}).get(deck) or {}
        if d.get("playing"):
            raise ValueError("Ese plato está sonando")
        await self._prepare(deck, track)

    # ------------------------------------------------------- elegir la siguiente
    def now_playing(self) -> Optional[dict]:
        """La cancion que suena (o la ultima que sono) en el plato principal."""
        m = self.engine.get("master") or "A"
        if self.decks.get(m):   # la que suena, o la que esta preparada para sonar
            return self.decks[m]
        return self.decks.get(self.current) if self.current else None

    def _deck_playing(self, d: str) -> bool:
        return bool(((self.engine.get("decks") or {}).get(d) or {}).get("playing"))

    async def set_next(self, track: dict, from_index: Optional[int] = None) -> str:
        """Pone una cancion como la siguiente. Devuelve donde ha ido:
        "first" (no sonaba nada: queda lista en el plato principal), "deck" (sustituye a la
        que iba a entrar) o "queue" (hay una mezcla en marcha: entra justo despues)."""
        if from_index is not None and self.pos <= from_index < len(self.queue):
            del self.queue[from_index]
        await self.ensure()
        master = self.engine.get("master") or "A"
        other = OTHER[master]
        if not self.decks.get(master) and not self._deck_playing(master) and not self._deck_playing(other):
            deck, where = master, "first"
        elif not self._deck_playing(other) and not self.engine.get("mix"):
            deck, where = other, "deck"
        else:
            self.queue.insert(self.pos, track)
            self.broadcast({"type": "djapp", "ev": "queue", "app": self.snapshot()})
            return "queue"
        self._failures = 0
        asyncio.create_task(self._prepare(deck, track))
        return where

    async def drop_next(self) -> None:
        """Quita la que iba a entrar y carga la siguiente de la lista (si hay)."""
        other = OTHER[self.engine.get("master") or "A"]
        if self._deck_playing(other):
            raise ValueError("Esa canción ya está sonando")
        self.decks[other] = None
        self.waveforms[other] = []
        self.points[other] = {}
        self._preparing[other] = None
        await self._send({"cmd": "unload", "deck": other})
        if self.pos < len(self.queue):
            asyncio.create_task(self._load_next(other))
        self.broadcast({"type": "djapp", "ev": "queue", "app": self.snapshot()})

    def remove(self, index: int) -> None:
        if self.pos <= index < len(self.queue):
            del self.queue[index]
            self.broadcast({"type": "djapp", "ev": "queue", "app": self.snapshot()})

    async def set_output(self, target: Optional[str]) -> None:
        config.save_settings(dj_output=target or None)
        await self.command("output", target=target or None)

    async def set_cue_output(self, target: Optional[str]) -> None:
        """Auriculares para la pre-escucha (None: sin pre-escucha)."""
        config.save_settings(dj_cue_output=target or None)
        await self.command("cue_output", target=target or None)
