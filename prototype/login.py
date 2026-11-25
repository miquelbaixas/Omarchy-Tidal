#!/usr/bin/env python3
"""Inicia sesion en Tidal (flujo de dispositivo) y guarda el permiso localmente.

El usuario autoriza en la web oficial de Tidal; aqui solo se recibe el token.
Los tokens se guardan en ~/.config/tidal-connect-linux/session.json (permisos 600)
y nunca se imprimen.
"""
import json
import os
import pathlib
import sys

import tidalapi

CFG_DIR = pathlib.Path.home() / ".config" / "tidal-connect-linux"
SESSION_FILE = CFG_DIR / "session.json"


def save(session: tidalapi.Session) -> None:
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CFG_DIR, 0o700)
    data = {
        "token_type": session.token_type,
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
        "client_id": session.config.client_id,
        "country_code": session.country_code,
        "user_id": session.user.id if session.user else None,
    }
    fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def main() -> int:
    session = tidalapi.Session()
    login, future = session.login_oauth()
    url = login.verification_uri_complete
    if not url.startswith("http"):
        url = "https://" + url
    print(f"ENLACE: {url}", flush=True)
    print(f"Caduca en {login.expires_in} segundos", flush=True)
    future.result()
    if not session.check_login():
        print("LOGIN FALLIDO", flush=True)
        return 1
    save(session)
    print(f"LOGIN OK | pais: {session.country_code} | usuario: {session.user.id}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
