"""Audio de TIDAL para reproducir en este ordenador.

Se pide siempre la calidad "Hi-Res": con este acceso llega como DASH con FLAC
(24 bits si existe; si no, 16 bits). "Lossless" llegaria en AAC, con perdida.
Los trozos DASH (cabecera + audio) encadenados forman un MP4 con FLAC valido,
que el motor sirve en http://127.0.0.1:PUERTO/local/stream/<id> para mpv y ffmpeg.
"""
import asyncio
import logging
import os
import pathlib
import time
from typing import Optional

import aiohttp
import tidalapi

from .tidal_service import TidalService

log = logging.getLogger(__name__)

CACHE_DIR = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "tidal-connect-linux" / "audio"
CACHE_MAX = 12  # canciones guardadas como mucho (se borran las mas antiguas)


class TrackFile:
    """Cancion que se esta descargando (o ya descargada) en la cache temporal."""

    def __init__(self, track_id: str):
        self.track_id = track_id
        self.path = CACHE_DIR / f"{track_id}.mp4"
        self.part = CACHE_DIR / f"{track_id}.mp4.part"
        self.size = 0
        self.done = asyncio.Event()
        self.error: Optional[str] = None
        self._cond = asyncio.Condition()
        if self.path.exists():
            self.size = self.path.stat().st_size
            self.done.set()

    async def grown(self, size: int, timeout: float = 30) -> None:
        """Espera a que el archivo pase de `size` bytes o termine."""
        async with self._cond:
            await asyncio.wait_for(self._cond.wait_for(lambda: self.size > size or self.done.is_set()), timeout)

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()


class StreamResolver:
    def __init__(self, tidal: TidalService):
        self.tidal = tidal
        self._cache: dict[str, tuple[float, dict]] = {}
        self._http: Optional[aiohttp.ClientSession] = None
        self._files: dict[str, TrackFile] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for p in CACHE_DIR.glob("*.part"):  # descargas a medias de otra ejecucion
            p.unlink(missing_ok=True)

    # ------------------------------------------------------- cache en disco
    def file(self, track_id: str) -> TrackFile:
        """Devuelve la cancion en cache, empezando a descargarla si hace falta."""
        tf = self._files.get(track_id)
        if tf and (tf.done.is_set() and tf.path.exists() or not tf.done.is_set()) and not tf.error:
            if tf.done.is_set():
                os.utime(tf.path)  # para el borrado de las mas antiguas
            return tf
        tf = TrackFile(track_id)
        self._files[track_id] = tf
        if not tf.done.is_set():
            asyncio.ensure_future(self._download(tf))
        else:
            os.utime(tf.path)
        return tf

    async def _download(self, tf: TrackFile) -> None:
        try:
            with open(tf.part, "wb") as fh:
                async for chunk in self.iter_bytes(tf.track_id):
                    fh.write(chunk)
                    fh.flush()
                    tf.size += len(chunk)
                    await tf._notify()
            os.replace(tf.part, tf.path)
        except Exception as e:
            log.exception("Fallo al descargar %s", tf.track_id)
            tf.error = str(e)
            tf.part.unlink(missing_ok=True)
        finally:
            tf.done.set()
            await tf._notify()
            self._prune()

    def _prune(self) -> None:
        files = sorted(CACHE_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[CACHE_MAX:]:
            p.unlink(missing_ok=True)
            self._files.pop(p.stem, None)

    async def resolve(self, track_id: str) -> dict:
        hit = self._cache.get(track_id)
        if hit and time.time() - hit[0] < 600:  # las URL caducan: se renuevan cada 10 min
            return hit[1]

        def run(s):
            prev = s.audio_quality
            s.audio_quality = tidalapi.Quality.hi_res_lossless
            try:
                st = s.track(track_id).get_stream()
                m = st.get_stream_manifest()
                return {
                    "urls": list(m.urls),
                    "mpd": bool(st.is_mpd),
                    "codec": str(m.codecs),
                    "bit_depth": st.bit_depth,
                    "sample_rate": st.sample_rate,
                    "quality": st.audio_quality,
                }
            finally:
                s.audio_quality = prev
        info = await self.tidal.call(run)
        self._cache[track_id] = (time.time(), info)
        return info

    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=30))
        return self._http

    async def iter_bytes(self, track_id: str, chunk: int = 256 * 1024):
        """Bytes de la cancion completa, en orden (trozos DASH encadenados)."""
        info = await self.resolve(track_id)
        http = self._session()
        # Se descargan por adelantado unos pocos trozos para no esperar entre ellos.
        sem = asyncio.Semaphore(4)

        async def fetch(url):
            async with sem:
                for attempt in range(3):
                    try:
                        async with http.get(url) as r:
                            r.raise_for_status()
                            return await r.read()
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.5)

        tasks: list[asyncio.Task] = []
        urls = info["urls"]
        ahead = 6
        try:
            for i in range(min(ahead, len(urls))):
                tasks.append(asyncio.create_task(fetch(urls[i])))
            for i in range(len(urls)):
                data = await tasks[i]
                if i + ahead < len(urls):
                    tasks.append(asyncio.create_task(fetch(urls[i + ahead])))
                for k in range(0, len(data), chunk):
                    yield data[k:k + chunk]
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

    async def download(self, track_id: str) -> bytes:
        parts = []
        async for b in self.iter_bytes(track_id):
            parts.append(b)
        return b"".join(parts)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
