"""Rutas y ajustes persistentes."""
import json
import os
import pathlib
import threading

CONFIG_DIR = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "tidal-connect-linux"
SESSION_FILE = CONFIG_DIR / "session.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

HOST = "127.0.0.1"
PORT = int(os.environ.get("TIDAL_CONNECT_PORT", "47900"))

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

DEFAULT_SETTINGS = {
    "device": None,          # {"id", "name", "host", "port"} del ultimo receptor usado
    "quality": "HI_RES_LOSSLESS",
    "locale": "es_ES",
}

_lock = threading.Lock()


def ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def write_private_json(path: pathlib.Path, data: dict) -> None:
    """Escribe JSON con permisos 600 de forma atomica."""
    ensure_dir()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load_settings() -> dict:
    with _lock:
        data = dict(DEFAULT_SETTINGS)
        try:
            data.update(json.loads(SETTINGS_FILE.read_text()))
        except (OSError, ValueError):
            pass
        return data


def save_settings(**changes) -> dict:
    with _lock:
        data = dict(DEFAULT_SETTINGS)
        try:
            data.update(json.loads(SETTINGS_FILE.read_text()))
        except (OSError, ValueError):
            pass
        data.update(changes)
        write_private_json(SETTINGS_FILE, data)
        return data
