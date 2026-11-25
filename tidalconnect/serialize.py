"""Convierte objetos de tidalapi en diccionarios simples para la interfaz."""
import datetime as dt
from typing import Any, Iterable, Optional

import tidalapi
from tidalapi.mix import Mix, MixV2

QUALITY_TAGS = {"HIRES_LOSSLESS": "MAX", "LOSSLESS": "HIGH", "DOLBY_ATMOS": "ATMOS", "MQA": "MAX"}


def _img(obj: Any, *sizes) -> Optional[str]:
    for size in sizes:
        try:
            url = obj.image(size)
            if url:
                return url
        except Exception:
            continue
    return None


def _date(value) -> Optional[str]:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value if isinstance(value, str) else None


def _quality(obj: Any) -> Optional[str]:
    tags = getattr(obj, "media_metadata_tags", None) or []
    if "HIRES_LOSSLESS" in tags or "MQA" in tags:
        return "MAX"
    if "DOLBY_ATMOS" in tags:
        return "ATMOS"
    if "LOSSLESS" in tags:
        return "HIGH"
    q = getattr(obj, "audio_quality", None)
    return {"HI_RES_LOSSLESS": "MAX", "HI_RES": "MAX", "LOSSLESS": "HIGH"}.get(q, None)


def artist_ref(a) -> dict:
    return {"id": str(a.id), "name": a.name}


def track(t) -> Optional[dict]:
    if not isinstance(t, tidalapi.Track):
        return None
    album = getattr(t, "album", None)
    title = t.name or ""
    version = getattr(t, "version", None)
    if version and version.lower() not in title.lower():
        title = f"{title} ({version})"
    return {
        "type": "track",
        "id": str(t.id),
        "title": title,
        "duration": int(t.duration or 0),
        "artists": [artist_ref(a) for a in (t.artists or [])],
        "album": {
            "id": str(album.id) if album else None,
            "title": album.name if album else "",
            "cover": _img(album, 320, 160) if album else None,
            "cover_large": _img(album, 1280, 640) if album else None,
        },
        "explicit": bool(getattr(t, "explicit", False)),
        "quality": _quality(t),
        "track_num": getattr(t, "track_num", None),
        "volume_num": getattr(t, "volume_num", None),
        "available": getattr(t, "available", True) is not False,
        # Datos para el DJ (TIDAL los tiene en ~2/3 del catalogo)
        "bpm": getattr(t, "bpm", None) or None,
        "key": getattr(t, "key", None) or None,
        "key_scale": getattr(t, "key_scale", None) or None,
        "gain": getattr(t, "replay_gain", None),
    }


def album(a) -> dict:
    rd = getattr(a, "release_date", None) or getattr(a, "tidal_release_date", None)
    return {
        "type": "album",
        "id": str(a.id),
        "title": a.name,
        "artists": [artist_ref(x) for x in (getattr(a, "artists", None) or ([a.artist] if a.artist else []))],
        "cover": _img(a, 320, 640, 160),
        "cover_large": _img(a, 1280, 640),
        "year": getattr(a, "year", None) or (rd.year if isinstance(rd, (dt.date, dt.datetime)) else None),
        "release_date": _date(rd),
        "num_tracks": getattr(a, "num_tracks", None),
        "duration": getattr(a, "duration", None),
        "album_type": (getattr(a, "type", None) or "ALBUM"),
        "explicit": bool(getattr(a, "explicit", False)),
        "quality": _quality(a),
        "copyright": getattr(a, "copyright", None),
    }


def artist(a) -> dict:
    return {
        "type": "artist",
        "id": str(a.id),
        "name": a.name,
        "picture": _img(a, 320, 480, 160),
        "picture_large": _img(a, 750, 480),
    }


def playlist(p) -> dict:
    creator = getattr(p, "creator", None)
    creator_name = getattr(creator, "name", None) if creator else None
    if not creator_name and getattr(p, "type", None) == "EDITORIAL":
        creator_name = "TIDAL"
    return {
        "type": "playlist",
        "id": str(p.id),
        "title": p.name,
        "description": getattr(p, "description", None),
        "cover": _img(p, 320, 480, 160),
        "cover_large": _img(p, 750, 640, 480),
        "num_tracks": getattr(p, "num_tracks", None),
        "duration": getattr(p, "duration", None),
        "creator": creator_name,
        "updated": _date(getattr(p, "last_updated", None)),
        "playlist_type": getattr(p, "type", None),
    }


def mix(m) -> dict:
    sub = getattr(m, "sub_title", None) or getattr(m, "short_subtitle", None) or ""
    return {
        "type": "mix",
        "id": str(m.id),
        "title": getattr(m, "title", "") or "",
        "subtitle": sub,
        "cover": _img(m, 320, 640, 1500),
        "cover_large": _img(m, 1500, 640, 320),
    }


def item(x) -> Optional[dict]:
    """Serializa cualquier elemento de pagina conocido; None si no se reconoce."""
    if isinstance(x, tidalapi.Track):
        return track(x)
    if isinstance(x, tidalapi.Album):
        return album(x)
    if isinstance(x, tidalapi.Artist):
        return artist(x)
    if isinstance(x, tidalapi.Playlist):
        return playlist(x)
    if isinstance(x, (Mix, MixV2)):
        return mix(x)
    return None


def items(xs: Iterable) -> list:
    out = []
    for x in xs or []:
        try:
            d = item(x)
        except Exception:
            d = None
        if d:
            out.append(d)
    return out


def tracks(xs: Iterable) -> list:
    return [d for d in (track(x) for x in (xs or [])) if d]
