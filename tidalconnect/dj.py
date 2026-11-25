"""DJ automatico: ordena canciones como un DJ (tempo, tonalidad y curva de energia).

No mezcla audio: el receptor reproduce una cancion cada vez. Lo que hace es
elegir el orden para que el tempo cambie poco a poco, las tonalidades encajen
(rueda Camelot) y la energia siga la forma pedida.
"""
import json
import logging
import math
import random
from typing import Optional

from . import config

log = logging.getLogger(__name__)

DJ_FILE = config.CONFIG_DIR / "dj.json"

SHAPES = {
    "subida": "Subida",
    "montana": "Montaña",
    "constante": "Constante",
    "bajada": "Bajada",
}

# ------------------------------------------------------------- tonalidades
_PITCH = {"C": 0, "CSharp": 1, "Db": 1, "D": 2, "DSharp": 3, "Eb": 3, "E": 4, "F": 5, "FSharp": 6,
          "Gb": 6, "G": 7, "GSharp": 8, "Ab": 8, "A": 9, "ASharp": 10, "Bb": 10, "B": 11}
# Numero Camelot por tono (mayor "B", menor "A")
_MAJOR = {0: 8, 7: 9, 2: 10, 9: 11, 4: 12, 11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7}
_MINOR = {9: 8, 4: 9, 11: 10, 6: 11, 1: 12, 8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7}
_NAMES_ES = {0: "Do", 1: "Do#", 2: "Re", 3: "Mi♭", 4: "Mi", 5: "Fa", 6: "Fa#", 7: "Sol", 8: "La♭", 9: "La",
             10: "Si♭", 11: "Si"}


def camelot(key: Optional[str], scale: Optional[str]) -> Optional[tuple[int, str]]:
    if not key or key not in _PITCH or scale not in ("MAJOR", "MINOR"):
        return None
    p = _PITCH[key]
    return (_MAJOR[p], "B") if scale == "MAJOR" else (_MINOR[p], "A")


def key_name(key: Optional[str], scale: Optional[str]) -> Optional[str]:
    if not key or key not in _PITCH:
        return None
    return f"{_NAMES_ES[_PITCH[key]]} {'mayor' if scale == 'MAJOR' else 'menor'}"


def key_cost(a: Optional[tuple], b: Optional[tuple]) -> tuple[float, str]:
    """Coste de pasar de la tonalidad a a la b, y explicacion corta."""
    if not a or not b:
        return 2.5, "tono desconocido"
    (na, la), (nb, lb) = a, b
    diff = min((na - nb) % 12, (nb - na) % 12)
    if diff == 0 and la == lb:
        return 0.0, "mismo tono"
    if diff == 0:
        return 1.0, "tono relativo"
    if diff == 1 and la == lb:
        return 1.0, "tono vecino"
    if diff == 1:
        return 3.0, "tono diagonal"
    if diff == 2 and la == lb:
        return 3.0, "tono cercano"
    if (nb - na) % 12 == 7 and la == lb:
        return 3.5, "subida de semitono"
    return 6.0, "los tonos chocan"


# ------------------------------------------------------------------- tempo
def tempo_jump(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Cambio de tempo en %, teniendo en cuenta medio y doble tiempo (70 ~ 140)."""
    if not a or not b:
        return None
    best = min((b / a, 2 * b / a, b / (2 * a)), key=lambda r: abs(math.log(r)))
    return (best - 1) * 100


def tempo_cost(pct: Optional[float]) -> float:
    if pct is None:
        return 2.5
    x = abs(pct)
    if x <= 3:
        return 0.0
    if x <= 6:
        return 1.0
    if x <= 10:
        return 2.5
    return 4.0 + (x - 10) / 4


# ----------------------------------------------------------------- energia
def raw_energy(t: dict) -> Optional[float]:
    """Energia aproximada 0..1 a partir del tempo y del volumen (TIDAL no da energia)."""
    bpm = t.get("bpm")
    if not bpm:
        return None
    tempo = min(1.0, max(0.0, (bpm - 70) / 100))
    gain = t.get("gain")
    if gain is None:
        return tempo
    loud = min(1.0, max(0.0, (-gain - 2) / 12))  # replayGain mas negativo = mas fuerte
    return 0.75 * tempo + 0.25 * loud


def target(shape: str, p: float) -> float:
    """Energia objetivo (percentil 0..1) en la posicion p (0..1) del set."""
    p = min(1.0, max(0.0, p))
    if shape == "subida":
        return 0.15 + 0.8 * (p ** 1.3)
    if shape == "montana":
        return 0.2 + 0.75 * (math.sin(math.pi * min(1.0, p / 0.62) / 2) if p <= 0.62 else 1 - ((p - 0.62) / 0.38) ** 1.4 * 0.75)
    if shape == "bajada":
        return 0.9 - 0.75 * (p ** 0.9)
    return 0.55


# ------------------------------------------------------------------ puntos
def annotate(pool: list) -> list:
    """Anade camelot, nombre del tono y energia (percentil dentro del conjunto)."""
    known = [(raw_energy(t), i) for i, t in enumerate(pool)]
    vals = sorted(e for e, _ in known if e is not None)
    n = len(vals)
    for t in pool:
        c = camelot(t.get("key"), t.get("key_scale"))
        t["camelot"] = f"{c[0]}{c[1]}" if c else None
        t["key_name"] = key_name(t.get("key"), t.get("key_scale"))
        e = raw_energy(t)
        if e is None or not n:
            t["energy"] = None
        else:
            # percentil: posicion relativa dentro de las canciones del conjunto
            lo = _bisect(vals, e)
            t["energy"] = round(lo / max(1, n - 1), 3)
    return pool


def _bisect(vals, x):
    lo, hi = 0, len(vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if vals[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return min(lo, len(vals) - 1)


def _cam(t):
    c = t.get("camelot")
    return (int(c[:-1]), c[-1]) if c else None


def _artists(t):
    return {a.get("id") or a.get("name") for a in t.get("artists", [])}


def main_artist(t: dict) -> Optional[str]:
    arts = t.get("artists") or []
    return str(arts[0].get("id")) if arts and arts[0].get("id") else None


def style_cost(a: dict, b: dict, graph: Optional[dict]) -> float:
    """Cercania de estilo entre dos canciones segun los artistas similares de TIDAL."""
    if not graph:
        return 0.0
    x, y = main_artist(a), main_artist(b)
    if not x or not y:
        return 4.0
    if x == y:
        return 0.0
    nx, ny = graph.get(x), graph.get(y)
    if nx is None or ny is None:
        return 4.0
    if y in nx or x in ny:
        return 1.0
    shared = len(nx & ny)
    if shared >= 3:
        return 2.0
    if shared >= 1:
        return 4.0
    return 11.0


def _double_time(a: Optional[float], b: Optional[float]) -> bool:
    """True si para encajar hace falta pasar a doble o medio tiempo."""
    if not a or not b:
        return False
    return abs(math.log(b / a)) > abs(math.log(2 * b / a)) or abs(math.log(b / a)) > abs(math.log(b / (2 * a)))


def step_cost(prev: Optional[dict], cand: dict, tgt: float, recent: list, rng: random.Random,
              graph: Optional[dict] = None) -> float:
    e = cand.get("energy")
    cost = (abs(e - tgt) * 12) if e is not None else 3.0
    if prev is not None:
        cost += tempo_cost(tempo_jump(prev.get("bpm"), cand.get("bpm"))) * 1.2
        if _double_time(prev.get("bpm"), cand.get("bpm")):
            cost += 2.0
        cost += key_cost(_cam(prev), _cam(cand))[0]
        cost += style_cost(prev, cand, graph)
        pe = prev.get("energy")
        if e is not None and pe is not None:
            # La energia cambia poco a poco: nada de saltos bruscos entre dos canciones.
            cost += max(0.0, abs(e - pe) - 0.12) * 18
    if not cand.get("src"):
        cost += 4.5  # musica anadida para completar la fuente: las tuyas van primero
    arts = _artists(cand)
    for back, t in enumerate(reversed(recent[-5:])):
        if arts & _artists(t):
            cost += 20 if back == 0 else 8 - back
    return cost + rng.random() * 0.6


def transition(prev: Optional[dict], cur: dict) -> Optional[dict]:
    """Explicacion de como encaja cur despues de prev (para la cabina)."""
    if prev is None:
        return None
    kc, klabel = key_cost(_cam(prev), _cam(cur))
    pct = tempo_jump(prev.get("bpm"), cur.get("bpm"))
    tc = tempo_cost(pct)
    total = kc + tc
    level = "unknown" if pct is None or not _cam(prev) or not _cam(cur) else (
        "good" if total <= 2 else "ok" if total <= 4.5 else "bad")
    key_txt = klabel
    if _cam(prev) and _cam(cur):
        key_txt = f"{klabel} {prev['camelot']}→{cur['camelot']}"
    tempo_txt = None
    if pct is not None:
        diff = round(cur["bpm"] - prev["bpm"]) if abs(pct) < 40 else None
        tempo_txt = "mismo tempo" if abs(pct) < 1 else (f"{diff:+d} BPM" if diff is not None else f"{pct:+.0f}% de tempo")
    return {"level": level, "key": key_txt, "tempo": tempo_txt}


# ------------------------------------------------------------- generacion
def build_set(pool: list, minutes: int, shape: str, *, bpm_min=None, bpm_max=None, include_unknown=False,
              start: Optional[list] = None, start_p: float = 0.0, seed: Optional[int] = None,
              beam: int = 6, branch: int = 10, graph: Optional[dict] = None) -> list:
    """Ordena canciones del conjunto para llenar `minutes` siguiendo la curva `shape`.

    start: canciones ya fijadas (para "regenerar desde aqui"); start_p: posicion
    relativa (0..1) del set en la que se continua.
    """
    rng = random.Random(seed)
    used = {t["id"] for t in (start or [])}
    cands = [t for t in pool if t["id"] not in used and _usable(t, include_unknown, bpm_min, bpm_max)]
    # quita duplicados por id
    seen, uniq = set(), []
    for t in cands:
        if t["id"] not in seen:
            seen.add(t["id"])
            uniq.append(t)
    cands = uniq
    if not cands:
        return []

    total = max(60, minutes * 60)
    start = list(start or [])
    avg = sum(t.get("duration") or 210 for t in cands) / len(cands)
    remaining = total * (1 - start_p)
    n_target = max(1, min(len(cands), round(remaining / max(60, avg))))

    # Busqueda en haz: varias secuencias candidatas, se queda con la de menor coste.
    beams = [(0.0, start, set(used))]
    for step in range(n_target):
        p = start_p + (1 - start_p) * (step / max(1, n_target - 1))
        tgt = target(shape, p)
        nxt = []
        for cost, seq, taken in beams:
            prev = seq[-1] if seq else None
            scored = []
            for c in cands:
                if c["id"] in taken:
                    continue
                scored.append((step_cost(prev, c, tgt, seq, rng, graph), c))
            scored.sort(key=lambda x: x[0])
            for sc, c in scored[:branch]:
                nxt.append((cost + sc, seq + [c], taken | {c["id"]}))
        if not nxt:
            break
        nxt.sort(key=lambda x: x[0])
        beams = nxt[:beam]
    best = beams[0][1]
    return best[len(start):]


def song_key(t: dict) -> str:
    """Identifica la misma cancion aunque venga de otro album o version (single, recopilatorio...)."""
    title = (t.get("title") or "").lower()
    title = title.split(" (")[0].split(" - ")[0].strip()
    return f"{title}|{main_artist(t)}"


def dedupe(pool: list) -> list:
    """Quita canciones repetidas, quedandose con la de tu fuente si la hay."""
    best: dict = {}
    order = []
    for t in pool:
        k = song_key(t)
        if k not in best:
            best[k] = t
            order.append(k)
        elif t.get("src") and not best[k].get("src"):
            best[k] = t
    return [best[k] for k in order]


def _usable(t: dict, include_unknown: bool, bpm_min, bpm_max) -> bool:
    if not t.get("available", True):
        return False
    if t.get("bpm") is None:
        return include_unknown
    if t.get("camelot") is None and not (include_unknown or t.get("src")):
        return False  # sin tonalidad: solo si es de tu fuente (o lo pides)
    return not ((bpm_min and t["bpm"] < bpm_min) or (bpm_max and t["bpm"] > bpm_max))


def _bridge(a: dict, b: Optional[dict], k: int, fillers: list, used: set, shape: str, pos0: int, total_n: int,
            rng: random.Random, graph: Optional[dict], beam: int = 6, branch: int = 8) -> list:
    """k canciones que lleven de a hasta b de forma natural (tempo, tono, estilo y energia)."""
    beams = [(0.0, [a], set(used))]
    for j in range(k):
        tgt = target(shape, (pos0 + j) / max(1, total_n - 1))
        nxt = []
        for cost, seq, taken in beams:
            scored = []
            for c in fillers:
                if c["id"] in taken:
                    continue
                sc = step_cost(seq[-1], c, tgt, seq, rng, graph)
                if j == k - 1 and b is not None:
                    # El ultimo puente tiene que llevar bien hasta la siguiente cancion tuya.
                    sc += step_cost(c, b, tgt, seq + [c], random.Random(0), graph)
                scored.append((sc, c))
            scored.sort(key=lambda x: x[0])
            for sc, c in scored[:branch]:
                nxt.append((cost + sc, seq + [c], taken | {c["id"]}))
        if not nxt:
            break
        nxt.sort(key=lambda x: x[0])
        beams = nxt[:beam]
    return beams[0][1][1:]


def make_set(pool: list, minutes: int, shape: str, *, bpm_min=None, bpm_max=None, include_unknown=False,
             seed: Optional[int] = None, graph: Optional[dict] = None) -> list:
    """Crea el set. Si la fuente se completo con musica parecida, garantiza que entren
    todas las canciones de la fuente y tiende puentes entre ellas."""
    cands = [t for t in pool if _usable(t, include_unknown, bpm_min, bpm_max)]
    src = [t for t in cands if t.get("src")]
    extra = [t for t in cands if not t.get("src")]
    kw = dict(bpm_min=bpm_min, bpm_max=bpm_max, include_unknown=include_unknown, graph=graph)
    if not src or not extra:
        return build_set(pool, minutes, shape, seed=seed, **kw)
    total = minutes * 60
    src_dur = sum(t.get("duration") or 210 for t in src)
    # 1) Orden de las canciones de la fuente segun la forma pedida.
    order = build_set(src, int(src_dur / 60) * 3 + 10, shape, seed=seed, **kw)
    if src_dur >= total * 0.9:
        return build_set(src, minutes, shape, seed=seed, **kw)
    # 2) Puentes con musica parecida hasta completar la duracion.
    avg = sum(t.get("duration") or 210 for t in extra) / len(extra)
    n_fill = max(0, round((total - src_dur) / avg))
    gaps = len(order)
    fills = [n_fill // gaps] * gaps
    # El resto va a los saltos mas dificiles entre canciones de la fuente.
    hard = sorted(range(gaps - 1), key=lambda i: -(tempo_cost(tempo_jump(order[i].get("bpm"), order[i + 1].get("bpm")))
                                                   + key_cost(_cam(order[i]), _cam(order[i + 1]))[0]
                                                   + style_cost(order[i], order[i + 1], graph)))
    for i in (hard + [gaps - 1])[: n_fill % gaps]:
        fills[i] += 1
    rng = random.Random(seed)
    total_n = len(order) + n_fill
    seq, used = [], {t["id"] for t in order}
    for i, a in enumerate(order):
        seq.append(a)
        b = order[i + 1] if i + 1 < len(order) else None
        if fills[i]:
            br = _bridge(a, b, fills[i], extra, used, shape, len(seq), total_n, rng, graph)
            seq.extend(br)
            used |= {t["id"] for t in br}
    return seq


def journey_plan(segments: list, pools: list) -> list:
    """Tramo de cada posicion del set, segun los minutos de cada tramo."""
    plan = []
    for i, seg in enumerate(segments):
        durs = [t.get("duration") or 210 for t in pools[i]] or [210]
        avg = sum(durs) / len(durs)
        plan += [i] * max(1, round(seg["minutes"] * 60 / avg))
    return plan


def make_journey(segments: list, pools: list, shape: str, *, plan: Optional[list] = None,
                 start: Optional[list] = None, graph: Optional[dict] = None, bpm_min=None, bpm_max=None,
                 include_unknown=False, favs: Optional[set] = None, avoid: Optional[set] = None,
                 seed: Optional[int] = None, beam: int = 6, branch: int = 10) -> list:
    """Set por tramos de estilos (p. ej. Blues -> Disco y Funk -> Rock).

    Cada posicion toma canciones de su tramo. En las dos ultimas de cada tramo se admiten
    canciones del siguiente y se premia a las que se parecen a el: asi se hace el puente.
    La energia sigue la forma `shape` a lo largo de todo el set.
    """
    rng = random.Random(seed)
    usable = []
    for i, pool in enumerate(pools):
        c = [{**t, "seg": i} for t in pool if _usable(t, include_unknown, bpm_min, bpm_max)]
        if avoid:
            fresh = [t for t in c if song_key(t) not in avoid]
            if len(fresh) >= 0.5 * len(c):
                c = fresh  # evita repetir lo de las ultimas sesiones si hay donde elegir
        usable.append(dedupe(c))
    start = list(start or [])
    plan = plan if plan is not None else journey_plan(segments, usable)
    total_n = len(start) + len(plan)
    samples = [rng.sample(p, min(25, len(p))) if p else [] for p in usable]
    multi = [len(seg.get("styles") or []) > 1 for seg in segments]
    # posiciones que quedan hasta el final de cada tramo
    to_end = []
    for j, i in enumerate(plan):
        k = j
        while k + 1 < len(plan) and plan[k + 1] == i:
            k += 1
        to_end.append(k - j)

    beams = [(0.0, start, {song_key(t) for t in start})]
    for j, i in enumerate(plan):
        pos = len(start) + j
        tgt = target(shape, pos / max(1, total_n - 1))
        bridge = i + 1 < len(usable) and to_end[j] <= 1
        cands = usable[i] + (usable[i + 1] if bridge else [])
        nxt = []
        for cost, seq, taken in beams:
            prev = seq[-1] if seq else None
            scored = []
            for c in cands:
                k = song_key(c)
                if k in taken:
                    continue
                sc = step_cost(prev, c, tgt, seq, rng, graph)
                if c["seg"] != i:
                    sc += 1.5  # cancion del siguiente tramo: solo si hace de puente
                if bridge and samples[i + 1]:
                    sc += 0.4 * min(style_cost(c, x, graph) for x in samples[i + 1])
                if favs and c["id"] in favs:
                    sc -= 1.0
                if multi[i] and len(seq) >= 3 and all(t.get("style") == c.get("style") for t in seq[-3:]):
                    sc += 1.0  # tramo con varios estilos: que se vayan alternando
                scored.append((sc, c))
            scored.sort(key=lambda x: x[0])
            for sc, c in scored[:branch]:
                nxt.append((cost + sc, seq + [c], taken | {song_key(c)}))
        if not nxt:
            break
        nxt.sort(key=lambda x: x[0])
        beams = nxt[:beam]
    return beams[0][1][len(start):]


def alternatives(pool: list, prev: dict, after: Optional[dict], tgt: float, exclude: set, n: int = 6,
                 graph: Optional[dict] = None) -> list:
    rng = random.Random()
    scored = []
    for c in pool:
        if c["id"] in exclude or c.get("bpm") is None or c.get("camelot") is None:
            continue
        sc = step_cost(prev, c, tgt, [prev], rng, graph)
        if after is not None:
            sc += 0.5 * (tempo_cost(tempo_jump(c.get("bpm"), after.get("bpm"))) + key_cost(_cam(c), _cam(after))[0]
                         + style_cost(c, after, graph))
        scored.append((sc, c))
    scored.sort(key=lambda x: x[0])
    return [c for _, c in scored[:n]]


# ------------------------------------------------------------------ estado
RECIPES_FILE = config.CONFIG_DIR / "dj_sessions.json"


def describe(cfg: dict) -> str:
    """Resumen legible de una estructura de set ("Blues → Disco y Funk → Rock · Montaña · 2 h")."""
    if cfg.get("mode") == "styles":
        what = " → ".join(s.get("label") or "?" for s in cfg.get("segments") or [])
        minutes = sum(int(s.get("minutes") or 0) for s in cfg.get("segments") or [])
    else:
        what = (cfg.get("source") or {}).get("title") or "Mis canciones"
        minutes = int(cfg.get("minutes") or 0)
    h, m = divmod(minutes, 60)
    dur = f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"
    return f"{what} · {SHAPES.get(cfg.get('shape'), '')} · {dur}"


class DJState:
    """Set activo, conjunto de canciones disponibles y sesiones guardadas (estructuras)."""

    def __init__(self):
        self.active: Optional[dict] = None  # {title, shape, minutes, mode, segments, config, recipe_id}
        self.pool: list = []
        self.recipes: list = []
        self.load()

    def load(self) -> None:
        try:
            d = json.loads(DJ_FILE.read_text())
            self.active, self.pool = d.get("active"), d.get("pool") or []
        except (OSError, ValueError):
            pass
        try:
            self.recipes = json.loads(RECIPES_FILE.read_text()).get("sessions") or []
        except (OSError, ValueError):
            self.recipes = []

    def save(self) -> None:
        try:
            config.write_private_json(DJ_FILE, {"active": self.active, "pool": self.pool})
        except OSError:
            log.exception("No se pudo guardar el estado del DJ")

    def save_recipes(self) -> None:
        try:
            config.write_private_json(RECIPES_FILE, {"sessions": self.recipes})
        except OSError:
            log.exception("No se pudieron guardar las sesiones del DJ")

    def recipe(self, rid: str) -> Optional[dict]:
        return next((r for r in self.recipes if r["id"] == rid), None)
