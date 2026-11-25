"""Sesion de Tidal: inicio de sesion por enlace, persistencia y renovacion del permiso.

tidalapi es sincrono y no es seguro entre hilos: todas las llamadas pasan por
`call()`, que las ejecuta de una en una en un hilo aparte.
"""
import asyncio
import datetime as dt
import json
import logging
import threading
import time
from typing import Any, Callable, Optional

import requests
import tidalapi

from . import config

log = logging.getLogger(__name__)


class TidalService:
    def __init__(self, locale: str = "es_ES"):
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(6)
        self._locale = locale
        self.session = self._new_session()
        self._login_future = None
        self.on_tokens_changed: Optional[Callable[[], None]] = None
        self._saved_access: Optional[str] = None
        self._load()

    # ---------------------------------------------------------------- sesion
    def _new_session(self) -> tidalapi.Session:
        s = tidalapi.Session()
        s.locale = self._locale
        return s

    def _load(self) -> None:
        try:
            d = json.loads(config.SESSION_FILE.read_text())
        except (OSError, ValueError):
            return
        expiry = dt.datetime.fromisoformat(d["expiry_time"]) if d.get("expiry_time") else None
        try:
            ok = self.session.load_oauth_session(d["token_type"], d["access_token"], d["refresh_token"], expiry)
        except Exception:  # red caida, token revocado...
            log.exception("No se pudo recuperar la sesion de Tidal")
            ok = False
        self.session.locale = self._locale
        if ok:
            self._saved_access = d["access_token"]
            self._persist_if_changed()

    def _persist_if_changed(self) -> None:
        s = self.session
        if not s.access_token or s.access_token == self._saved_access:
            return
        config.write_private_json(config.SESSION_FILE, {
            "token_type": s.token_type,
            "access_token": s.access_token,
            "refresh_token": s.refresh_token,
            "expiry_time": s.expiry_time.isoformat() if s.expiry_time else None,
            "client_id": s.config.client_id,
            "country_code": s.country_code,
            "user_id": s.user.id if s.user else None,
        })
        first = self._saved_access is None
        self._saved_access = s.access_token
        if not first and self.on_tokens_changed:
            self.on_tokens_changed()

    @property
    def logged_in(self) -> bool:
        return bool(self.session.access_token and self.session.user)

    def user_info(self) -> Optional[dict]:
        if not self.logged_in:
            return None
        u = self.session.user
        name = " ".join(x for x in (getattr(u, "first_name", None), getattr(u, "last_name", None)) if x)
        return {
            "id": u.id,
            "name": name or getattr(u, "username", None) or getattr(u, "email", None) or "Tidal",
            "country": self.session.country_code,
        }

    # ------------------------------------------------------------ llamadas
    async def call(self, fn: Callable[[tidalapi.Session], Any]) -> Any:
        # Varias consultas a la vez (requests.Session admite uso concurrente);
        # el candado solo protege la renovacion y el guardado del permiso.
        def run():
            with self._sem:
                try:
                    try:
                        return fn(self.session)
                    except requests.exceptions.ConnectionError:
                        # Cortes puntuales de la conexion con TIDAL: un reintento.
                        time.sleep(0.5)
                        return fn(self.session)
                    except requests.exceptions.HTTPError as e:
                        if e.response is None or e.response.status_code != 401:
                            raise
                        # Permiso rechazado (caducado o renovado por otro proceso): se recupera y se reintenta.
                        self._recover_auth()
                        return fn(self.session)
                finally:
                    with self._lock:
                        self._persist_if_changed()
        return await asyncio.to_thread(run)

    def _recover_auth(self) -> None:
        """Tras un 401: si otro proceso guardo un permiso nuevo, se usa ese; si no, se renueva."""
        with self._lock:
            try:
                d = json.loads(config.SESSION_FILE.read_text())
            except (OSError, ValueError):
                d = {}
            if d.get("access_token") and d["access_token"] != self.session.access_token:
                expiry = dt.datetime.fromisoformat(d["expiry_time"]) if d.get("expiry_time") else None
                log.info("Permiso de TIDAL actualizado desde el archivo (lo renovo otro proceso)")
                self.session.load_oauth_session(d["token_type"], d["access_token"], d["refresh_token"], expiry)
                self.session.locale = self._locale
                self._saved_access = d["access_token"]
                if self.on_tokens_changed:
                    self.on_tokens_changed()
            elif self.session.refresh_token:
                log.info("Permiso de TIDAL rechazado: se renueva")
                self.session.token_refresh(self.session.refresh_token)
                self._persist_if_changed()

    async def fresh_tokens(self) -> tuple[str, str]:
        """Devuelve (access, refresh), renovando antes si caduca en menos de 15 min."""
        def run():
            with self._lock:
                s = self.session
                exp = s.expiry_time
                if exp is not None:
                    now = dt.datetime.now(exp.tzinfo) if exp.tzinfo else dt.datetime.now()
                    if exp - now < dt.timedelta(minutes=15) and s.refresh_token:
                        try:
                            s.token_refresh(s.refresh_token)
                        except Exception:
                            log.exception("Fallo al renovar el token")
                self._persist_if_changed()
                return s.access_token, s.refresh_token
        return await asyncio.to_thread(run)

    async def force_refresh(self) -> None:
        def run():
            with self._lock:
                if self.session.refresh_token:
                    self.session.token_refresh(self.session.refresh_token)
                self._persist_if_changed()
        await asyncio.to_thread(run)

    # ------------------------------------------------- inicio / cierre sesion
    async def start_login(self, on_done: Callable[[bool], Any]) -> dict:
        """Empieza el inicio de sesion por enlace. Llama on_done(ok) al terminar."""
        def begin():
            with self._lock:
                s = self._new_session()
                login, future = s.login_oauth()
                return s, login, future
        s, login, future = await asyncio.to_thread(begin)
        self._login_future = future
        loop = asyncio.get_running_loop()

        def finished(fut):
            ok = False
            try:
                fut.result()
                ok = s.check_login()
            except Exception as e:  # caducado o cancelado
                log.info("Inicio de sesion no completado: %s", e)
            if ok:
                with self._lock:
                    s.locale = self._locale
                    self.session = s
                    self._saved_access = None
                    self._persist_if_changed()
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_maybe_await(on_done(ok))))

        future.add_done_callback(finished)
        url = login.verification_uri_complete
        if not url.startswith("http"):
            url = "https://" + url
        return {"url": url, "code": login.user_code, "expires_in": int(login.expires_in)}

    async def logout(self) -> None:
        with self._lock:
            self.session = self._new_session()
            self._saved_access = None
        try:
            config.SESSION_FILE.unlink()
        except OSError:
            pass


async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        await x
