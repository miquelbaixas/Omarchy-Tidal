"""Conexion WebSocket (TLS) con un receptor Tidal Connect.

Mensajes JSON con un campo "command" en el nivel superior. Formato tomado del
receptor libre tidal-connect-ng y comprobado contra un Eversolo DMP-A6.
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import aiohttp

log = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[None]]


class ConnectClient:
    def __init__(self, host: str, port: int, on_message: Handler, on_closed: Callable[[], Awaitable[None]]):
        self.host, self.port = host, port
        self._on_message = on_message
        self._on_closed = on_closed
        self._http: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._reader: Optional[asyncio.Task] = None
        self._keepalive: Optional[asyncio.Task] = None
        self._req = 0

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def next_request_id(self) -> int:
        self._req += 1
        return self._req

    async def connect(self) -> None:
        # El receptor presenta un certificado de la CA privada de TIDAL:
        # no se puede verificar con el almacen del sistema.
        self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=8))
        self._ws = await self._http.ws_connect(
            f"wss://{self.host}:{self.port}/", ssl=False, heartbeat=None, autoping=True, max_msg_size=0
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._keepalive = asyncio.create_task(self._keepalive_loop())
        log.info("Conectado a %s:%s", self.host, self.port)

    async def _keepalive_loop(self) -> None:
        # El receptor cierra la conexion si el mando no da senales de vida
        # (~37 s en un DMP-A6), aunque el reciba mensajes nuestros de estado.
        try:
            while self.connected:
                await asyncio.sleep(10)
                if self.connected:
                    await self._ws.ping()
        except (asyncio.CancelledError, ConnectionError, RuntimeError):
            pass

    async def _read_loop(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if log.isEnabledFor(logging.DEBUG) and '"notifyPlayerStatusChanged"' not in msg.data:
                        log.debug("<< %s", msg.data[:600])
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        log.warning("Mensaje no JSON: %r", msg.data[:200])
                        continue
                    try:
                        await self._on_message(data)
                    except Exception:
                        log.exception("Error procesando %s", data.get("command"))
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                    log.info("Cierre del receptor: tipo=%s datos=%r extra=%r", msg.type, msg.data, msg.extra)
                    break
        except Exception:
            log.exception("Lectura del receptor interrumpida")
        finally:
            code = self._ws.close_code if self._ws is not None else None
            exc = self._ws.exception() if self._ws is not None else None
            log.info("Conexion con el receptor cerrada (codigo=%s, error=%r)", code, exc)
            await self._cleanup()
            await self._on_closed()

    async def send(self, msg: dict) -> None:
        if not self.connected:
            raise ConnectionError("no hay conexion con el receptor")
        cmd = msg.get("command")
        if log.isEnabledFor(logging.DEBUG):
            safe = {k: v for k, v in msg.items() if k not in ("contentServerInfo", "mediaInfos", "currentMediaInfo")}
            log.debug(">> %s", json.dumps(safe)[:400])
        await self._ws.send_str(json.dumps(msg))

    async def request(self, command: str, **fields) -> int:
        rid = self.next_request_id()
        await self.send({"command": command, "requestId": rid, **fields})
        return rid

    async def _cleanup(self) -> None:
        if self._keepalive is not None and not self._keepalive.done():
            self._keepalive.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._http is not None:
            await self._http.close()
        self._ws, self._http = None, None

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        await self._cleanup()
