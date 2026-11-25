"""Reproduccion en este ordenador con mpv (controlado por su socket JSON IPC).

mpv mantiene una lista de dos entradas: la cancion actual y la siguiente, para
que el paso entre canciones sea sin cortes. Cuando mpv pasa a la siguiente se
avisa al reproductor (on_advance) y este le da la nueva "siguiente".
"""
import asyncio
import json
import logging
import os
import pathlib
import time
from typing import Awaitable, Callable, Optional

from . import config

log = logging.getLogger(__name__)

SOCKET = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tidal-connect-mpv.sock"


async def list_outputs() -> list[dict]:
    """Salidas de audio del sistema (altavoces, cascos, Bluetooth...) segun PipeWire."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "-f", "json", "list", "sinks",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        sinks = json.loads(out or b"[]")
    except Exception:
        sinks = []
    res = []
    for s in sinks:
        name = s.get("name", "")
        desc = s.get("description") or name
        kind = "bluetooth" if name.startswith("bluez") else "hdmi" if "hdmi" in name.lower() else "usb" if "usb" in name.lower() else "builtin"
        res.append({"id": f"pipewire/{name}", "name": desc, "kind": kind})
    return res


class LocalOutput:
    def __init__(self, on_status: Callable[[dict], None], on_advance: Callable[[], Awaitable[None]],
                 on_end: Callable[[], Awaitable[None]], socket_path: pathlib.Path = SOCKET,
                 output: Optional[str] = None):
        self.socket_path = socket_path
        self._fixed_output = output
        self._on_status = on_status
        self._on_advance = on_advance
        self._on_end = on_end
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._req = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self.state = {"pos": 0.0, "duration": 0.0, "paused": True, "idle": True, "playlist_pos": 0,
                      "audio": None, "buffering": False}
        self._last_status = 0.0
        self._expect_advance = False

    # ---------------------------------------------------------------- proceso
    async def ensure(self) -> None:
        async with self._lock:
            if self.proc and self.proc.returncode is None and self._writer:
                return
            try:
                self.socket_path.unlink()
            except OSError:
                pass
            settings = config.load_settings()
            args = ["mpv", "--idle=yes", "--no-video", "--no-terminal", "--audio-display=no",
                    f"--input-ipc-server={self.socket_path}", "--prefetch-playlist=yes", "--gapless-audio=weak",
                    "--cache=yes", "--demuxer-seekable-cache=yes", "--demuxer-max-bytes=300MiB",
                    "--demuxer-readahead-secs=600",
                    "--keep-open=no", f"--volume={int(round((settings.get('local_volume') or 0.6) * 100))}",
                    "--volume-max=100", "--replaygain=no", "--msg-level=all=warn"]
            output = self._fixed_output or settings.get("local_output")
            if output:
                args.append(f"--audio-device={output}")
            self.proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                              stderr=asyncio.subprocess.DEVNULL)
            for _ in range(50):
                if self.socket_path.exists():
                    break
                await asyncio.sleep(0.05)
            self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path))
            self._read_task = asyncio.create_task(self._read_loop())
            for i, prop in enumerate(("time-pos", "duration", "pause", "idle-active", "playlist-pos",
                                      "audio-params", "paused-for-cache", "volume", "mute")):
                await self._send({"command": ["observe_property", i + 1, prop]})
            log.info("mpv listo para reproducir en este ordenador")

    async def shutdown(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                await self.command("quit")
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self._read_task:
            self._read_task.cancel()
        self.proc = None
        self._writer = None

    # ------------------------------------------------------------- mensajes
    async def _send(self, msg: dict) -> None:
        self._writer.write((json.dumps(msg) + "\n").encode())
        await self._writer.drain()

    async def command(self, *cmd, wait: bool = True):
        await self.ensure()
        self._req += 1
        rid = self._req
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"command": list(cmd), "request_id": rid})
        if not wait:
            return None
        try:
            r = await asyncio.wait_for(fut, 5)
        finally:
            self._pending.pop(rid, None)
        if r.get("error") not in (None, "success"):
            raise RuntimeError(f"mpv: {r.get('error')} ({cmd[0]})")
        return r.get("data")

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if "request_id" in m and m["request_id"] in self._pending:
                    fut = self._pending.pop(m["request_id"])
                    if not fut.done():
                        fut.set_result(m)
                    continue
                ev = m.get("event")
                if ev == "property-change":
                    await self._prop(m.get("name"), m.get("data"))
                elif ev == "end-file" and m.get("reason") == "eof":
                    pass
                elif ev == "idle":
                    await self._on_end()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Lectura de mpv interrumpida")
        finally:
            self._writer = None

    async def _prop(self, name: str, data) -> None:
        st = self.state
        if name == "time-pos" and data is not None:
            st["pos"] = float(data)
        elif name == "duration" and data is not None:
            st["duration"] = float(data)
        elif name == "pause":
            st["paused"] = bool(data)
        elif name == "idle-active":
            st["idle"] = bool(data)
        elif name == "paused-for-cache":
            st["buffering"] = bool(data)
        elif name == "audio-params" and data:
            st["audio"] = {"sample_rate": data.get("samplerate"), "format": data.get("format"),
                           "channels": data.get("channel-count")}
        elif name == "playlist-pos" and data is not None:
            old = st["playlist_pos"]
            st["playlist_pos"] = int(data)
            if int(data) == 1 and old == 0:
                # mpv ha pasado a la siguiente cancion de la lista
                self.current_url = self.next_url
                await self._on_advance()
        # time-pos llega muy a menudo: se avisa como mucho 4 veces por segundo
        now = time.monotonic()
        if name != "time-pos" or now - self._last_status > 0.25:
            self._last_status = now
            self._on_status(dict(st))

    # ----------------------------------------------------------- acciones
    async def play(self, url: str, next_url: Optional[str], position: float = 0.0, paused: bool = False) -> None:
        await self.ensure()
        self.current_url, self.next_url = url, next_url
        await self.command("playlist-clear")
        opts = f"start={max(0.0, position):.2f},pause={'yes' if paused else 'no'}"
        await self.command("loadfile", url, "replace", -1, opts)
        self.state["playlist_pos"] = 0
        if next_url:
            await self.command("loadfile", next_url, "append")

    current_url: Optional[str] = None
    next_url: Optional[str] = None

    async def set_next(self, next_url: Optional[str]) -> None:
        """Deja en la lista solo [actual, siguiente]."""
        if not self.proc:
            return
        count = await self.command("get_property", "playlist-count")
        pos = await self.command("get_property", "playlist-pos")
        pos = pos if isinstance(pos, int) and pos >= 0 else 0
        # quita todo lo que haya despues de la actual y lo anterior a ella
        for i in range(int(count or 0) - 1, pos, -1):
            await self.command("playlist-remove", i)
        for _ in range(pos):
            await self.command("playlist-remove", 0)
        self.state["playlist_pos"] = 0
        self.next_url = next_url
        if next_url:
            await self.command("loadfile", next_url, "append")

    async def pause(self, paused: bool) -> None:
        await self.command("set_property", "pause", paused)

    async def seek(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        cache = await self.command("get_property", "demuxer-cache-state") or {}
        end = cache.get("cache-end")
        if end is not None and seconds <= float(end) - 1:
            await self.command("seek", seconds, "absolute+exact")
        elif self.current_url:
            # Aun no se ha descargado hasta ahi: se recarga la cancion empezando en ese punto.
            await self.play(self.current_url, self.next_url, seconds, paused=self.state["paused"])

    async def set_volume(self, level: float) -> None:
        await self.command("set_property", "volume", max(0.0, min(100.0, level * 100)))

    async def set_mute(self, mute: bool) -> None:
        await self.command("set_property", "mute", bool(mute))

    async def set_output(self, device: str) -> None:
        if self.proc:
            await self.command("set_property", "audio-device", device or "auto")

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            await self.command("stop")
