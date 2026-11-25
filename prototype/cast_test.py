#!/usr/bin/env python3
"""Prueba: manda una pista de Tidal a un receptor Tidal Connect y muestra lo que responde.

Uso:
  cast_test.py HOST [--track ID | --search "texto"] [--quality HI_RES_LOSSLESS|LOSSLESS|HIGH]
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import uuid

import tidalapi

from tconnect import WebSocket, content_server_info, media_info

SESSION_FILE = pathlib.Path.home() / ".config" / "tidal-connect-linux" / "session.json"


def load_session() -> tidalapi.Session:
    data = json.loads(SESSION_FILE.read_text())
    s = tidalapi.Session()
    expiry = dt.datetime.fromisoformat(data["expiry_time"]) if data.get("expiry_time") else None
    if not s.load_oauth_session(data["token_type"], data["access_token"], data["refresh_token"], expiry):
        sys.exit("No se pudo recuperar la sesion de Tidal; vuelve a ejecutar login.py")
    if s.access_token != data["access_token"]:
        data.update(access_token=s.access_token, refresh_token=s.refresh_token or data["refresh_token"],
                    expiry_time=s.expiry_time.isoformat() if s.expiry_time else None)
        fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=2019)
    ap.add_argument("--track", type=int)
    ap.add_argument("--search")
    ap.add_argument("--quality", default="HI_RES_LOSSLESS")
    ap.add_argument("--seconds", type=int, default=25)
    args = ap.parse_args()

    s = load_session()
    if args.track:
        track = s.track(args.track)
    else:
        res = s.search(args.search or "Judeline", models=[tidalapi.Track], limit=5)
        track = res["tracks"][0]
    artists = ", ".join(a.name for a in track.artists)
    print(f"Pista: {artists} - {track.name} (id {track.id})")

    secrets = {s.access_token, s.refresh_token}

    def show(direction, text):
        for sec in secrets:
            if sec:
                text = text.replace(sec, "<oculto>")
        print(f"{direction} {text[:1200]}", flush=True)

    ws = WebSocket(args.host, args.port)
    start = {"command": "startSession", "appId": "tidal", "appName": "TIDAL",
             "sessionCredential": f"tidal-connect-linux-{s.user.id}"}
    ws.send(json.dumps(start))
    show(">>", json.dumps(start))

    session_id = None
    deadline = time.time() + 6
    while time.time() < deadline and not session_id:
        msg = ws.recv(timeout=max(0.1, deadline - time.time()))
        if msg is None:
            break
        show("<<", msg)
        j = json.loads(msg)
        if j.get("command") == "notifySessionStarted":
            session_id = j.get("sessionId")
    if not session_id:
        print("El receptor no inicio la sesion")
        return 1

    current = media_info("1", track)
    load = {
        "command": "loadLocalQueue",
        "requestId": 1,
        "queueInfo": {"queueId": f"linux-{uuid.uuid4().hex[:12]}", "repeatMode": "OFF", "shuffled": False},
        "currentMediaInfo": current,
        "contentServerInfo": content_server_info(s.access_token, s.refresh_token, s.country_code, args.quality),
        "position": 0,
        "autoplay": True,
    }
    ws.send(json.dumps(load))
    show(">>", json.dumps(load))

    next_req = 2
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        msg = ws.recv(timeout=max(0.1, deadline - time.time()))
        if msg is None:
            break
        show("<<", msg)
        j = json.loads(msg)
        if j.get("command") == "requestQueueItems":
            resp = {"command": "respondQueueItems", "requestId": next_req,
                    "queueRequestId": j.get("queueRequestId"), "mediaInfos": [current],
                    "hasBegin": True, "hasEnd": True}
            next_req += 1
            ws.send(json.dumps(resp))
            show(">>", json.dumps(resp))

    # Salir de la sesion sin parar la musica.
    end = {"command": "endSession", "sessionId": session_id, "stopCasting": False}
    ws.send(json.dumps(end))
    show(">>", json.dumps(end))
    try:
        msg = ws.recv(timeout=3)
        if msg:
            show("<<", msg)
    except ConnectionError:
        pass
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
