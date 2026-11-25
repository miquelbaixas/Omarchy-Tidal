"""Cliente minimo de Tidal Connect (controlador) solo con la libreria estandar.

Habla con un receptor Tidal Connect (p. ej. Eversolo DMP-A6) por WebSocket
seguro (puerto 2019). Formato de mensajes basado en el receptor libre
tidal-connect-ng (https://github.com/resurekto/tidal-connect-ng).
"""
import base64
import json
import os
import socket
import ssl
import struct
import threading
import time


class WebSocket:
    def __init__(self, host: str, port: int, path: str = "/"):
        ctx = ssl.create_default_context()
        # El receptor presenta un certificado firmado por la CA privada de TIDAL,
        # que no esta en el almacen del sistema.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((host, port), timeout=10)
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("el receptor cerro la conexion en el handshake")
            buf += chunk
        head, _, self._rest = buf.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise ConnectionError(f"handshake rechazado: {status!r}")
        self._lock = threading.Lock()

    def _recv_exact(self, n: int) -> bytes:
        while len(self._rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("conexion cerrada por el receptor")
            self._rest += chunk
        out, self._rest = self._rest[:n], self._rest[n:]
        return out

    def send(self, data, opcode: int = 0x1) -> None:
        payload = data.encode() if isinstance(data, str) else data
        n = len(payload)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(hdr + mask + masked)

    def recv(self, timeout=None):
        """Devuelve el texto del siguiente mensaje, o None si vence el tiempo."""
        while True:
            self.sock.settimeout(timeout)
            try:
                b1, b2 = self._recv_exact(2)
            except (socket.timeout, TimeoutError):
                return None
            self.sock.settimeout(None)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b2 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:
                self.send(data, opcode=0xA)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise ConnectionError("el receptor cerro la conexion")
            return data.decode(errors="replace")

    def close(self) -> None:
        try:
            self.send(struct.pack(">H", 1000), opcode=0x8)
        except OSError:
            pass
        self.sock.close()


def content_server_info(access_token: str, refresh_token: str, country: str,
                        quality: str = "HI_RES_LOSSLESS") -> dict:
    """Datos con los que el receptor obtiene su propio permiso y las URLs de audio."""
    return {
        "serverUrl": "https://api.tidal.com/v1",
        "authInfo": {
            "headerAuth": f"Bearer {access_token}",
            "oauthServerInfo": {
                "serverUrl": "https://auth.tidal.com/v1/oauth2/token",
                "authInfo": {
                    "headerAuth": "",
                    "oauthParameters": {"accessToken": access_token, "refreshToken": refresh_token},
                },
                "httpHeaderFields": [],
                "formParameters": {"grant_type": "switch_client", "scope": "r_usr w_usr"},
            },
        },
        "queryParameters": {"audioquality": quality, "countryCode": country},
        "httpHeaderFields": [],
    }


def media_info(item_id: str, track) -> dict:
    """Descripcion de una pista de Tidal (objeto tidalapi.Track) para el receptor."""
    album = getattr(track, "album", None)
    image = None
    try:
        image = album.image(640) if album else None
    except Exception:
        pass
    meta = {
        "title": track.name,
        "artists": [{"name": a.name, "id": str(a.id)} for a in (track.artists or [])],
        "albumTitle": album.name if album else "",
        "albumId": str(album.id) if album else "",
        "duration": int((track.duration or 0) * 1000),
    }
    if image:
        meta["images"] = [{"url": image}]
    return {"itemId": item_id, "mediaId": str(track.id), "mediaType": 0, "metadata": meta}
