"""Efectos y sonidos de TIDAL DJ.

Todo se procesa por bloques (unos 21 ms) con numpy/scipy, sin bucles por muestra:
- Eco y reverberacion usan lineas de retardo mas largas que el bloque, asi la
  realimentacion se calcula de golpe para todo el bloque.
- Los efectos ritmicos (eco, flanger, corte) siguen el compas de la cancion que suena.
- Los sonidos (bocina, sirena...) se sintetizan aqui mismo: no hay muestras de terceros.
"""
import math
from typing import Optional

import numpy as np
from scipy.signal import butter, lfilter, sosfilt, sosfilt_zi

from .analysis import SR

# efectos de la mezcla (en este orden) y efectos de plato
BUS_FX = ("radio", "lofi", "flanger", "gate", "echo", "reverb")
DECK_FX = ("roll", "brake")
SOUNDS = ("horn", "siren", "laser", "impact", "riser", "snare", "scratch")


# ------------------------------------------------------------------ utilidades
def _ring_idx(p: int, n: int, size: int) -> np.ndarray:
    return (p + np.arange(n)) % size


class _Tail:
    """Lleva la cuenta de cuando se apaga la cola de un efecto con memoria (eco, reverb)."""

    def __init__(self):
        self.quiet = 0

    def done(self, on: bool, wet: np.ndarray, needed: int) -> bool:
        if on:
            self.quiet = 0
            return False
        self.quiet = self.quiet + len(wet) if float(np.max(np.abs(wet))) < 1e-4 else 0
        return self.quiet > needed


# ------------------------------------------------------------------ eco
class Echo:
    """Eco al compas, con realimentacion y un paso bajo en la repeticion (tipo cinta)."""
    MAX = 4 * SR

    def __init__(self):
        self.buf = np.zeros((self.MAX, 2), np.float32)
        self.w = 0
        self.b, self.a = butter(1, 4000, fs=SR)
        self.zi = np.zeros((1, 2))
        self.tail = _Tail()
        self.active = False
        self.delay = SR // 2

    def process(self, x: np.ndarray, on: bool, delay_s: float, amount: float) -> np.ndarray:
        if not on and not self.active:
            return x
        n = len(x)
        out = np.empty_like(x)
        d = int(min(self.MAX - 2, max(256, delay_s * SR)))
        self.delay = d
        fb = 0.4 + 0.3 * amount
        i = 0
        while i < n:   # por trozos no mas largos que el retardo
            m = min(d, n - i)
            wet = self.buf[_ring_idx(self.w - d, m, self.MAX)]
            filt, self.zi = lfilter(self.b, self.a, wet, axis=0, zi=self.zi)
            feed = x[i:i + m] if on else 0.0
            self.buf[_ring_idx(self.w, m, self.MAX)] = feed + fb * filt
            out[i:i + m] = x[i:i + m] + wet * (0.35 + 0.65 * amount)
            self.w = (self.w + m) % self.MAX
            i += m
        self.active = True
        if self.tail.done(on, out - x, d + n):
            self.active = False
            self.buf[:] = 0
            self.zi[:] = 0
        return out


# ------------------------------------------------------------------ reverberacion
class _Comb:
    def __init__(self, d: int, fb: float, damp: float):
        self.buf = np.zeros(d, np.float32)
        self.d, self.p, self.fb = d, 0, fb
        self.b, self.a = [1 - damp], [1, -damp]
        self.zi = np.zeros(1)

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        i, n = 0, len(x)
        while i < n:
            m = min(self.d, n - i)
            idx = _ring_idx(self.p, m, self.d)
            o = self.buf[idx]
            filt, self.zi = lfilter(self.b, self.a, o, zi=self.zi)
            self.buf[idx] = x[i:i + m] + filt * self.fb
            out[i:i + m] = o
            self.p = (self.p + m) % self.d
            i += m
        return out


class _Allpass:
    def __init__(self, d: int, g: float = 0.5):
        self.buf = np.zeros(d, np.float32)
        self.d, self.p, self.g = d, 0, g

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        i, n = 0, len(x)
        while i < n:
            m = min(self.d, n - i)
            idx = _ring_idx(self.p, m, self.d)
            bo = self.buf[idx]
            xi = x[i:i + m]
            out[i:i + m] = -xi + bo
            self.buf[idx] = xi + bo * self.g
            self.p = (self.p + m) % self.d
            i += m
        return out


class Reverb:
    """Reverberacion de sala grande (estructura clasica de peines y pasatodos)."""
    COMBS = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
    ALLPASS = (556, 441, 341, 225)

    def __init__(self):
        sc = SR / 44100
        self.ch = []
        for spread in (0, 23):
            combs = [_Comb(int((c + spread) * sc), 0.9, 0.25) for c in self.COMBS]
            aps = [_Allpass(int((a + spread) * sc)) for a in self.ALLPASS]
            self.ch.append((combs, aps))
        self.tail = _Tail()
        self.active = False

    def process(self, x: np.ndarray, on: bool, amount: float) -> np.ndarray:
        if not on and not self.active:
            return x
        inp = (x[:, 0] + x[:, 1]) * (0.5 if on else 0.0)
        wet = np.empty_like(x)
        for c, (combs, aps) in enumerate(self.ch):
            y = sum(cb.process(inp) for cb in combs)
            for ap in aps:
                y = ap.process(y)
            wet[:, c] = y
        wet *= 0.05 * (0.4 + 0.6 * amount)
        self.active = True
        if self.tail.done(on, wet, SR // 4):
            self.active = False
            self.__init__()
        return x + wet


# ------------------------------------------------------------------ flanger
class Flanger:
    """Retardo muy corto que oscila al compas: el sonido de "avion"."""
    HIST = 1024

    def __init__(self):
        self.hist = np.zeros((self.HIST, 2), np.float32)

    def process(self, x: np.ndarray, phase: np.ndarray, amount: float) -> np.ndarray:
        n = len(x)
        full = np.concatenate([self.hist, x])
        self.hist = full[-self.HIST:]
        out = np.empty_like(x)
        for c, off in ((0, 0.0), (1, 0.18)):   # el canal derecho un poco desfasado: mas amplitud
            lfo = 0.5 - 0.5 * np.cos(2 * np.pi * (phase + off))
            d = (0.0006 + 0.0048 * lfo) * SR
            t = self.HIST + np.arange(n) - d
            i0 = np.floor(t).astype(np.int64)
            f = (t - i0).astype(np.float32)
            delayed = full[i0, c] * (1 - f) + full[i0 + 1, c] * f
            out[:, c] = x[:, c] * (1 - 0.45 * amount) + delayed * (0.45 + 0.25 * amount)
        return out


# ------------------------------------------------------------------ radio
class Radio:
    """Sonido de radio vieja o telefono: banda estrecha y algo saturado."""

    def __init__(self):
        self.sos = butter(2, [450, 3200], btype="band", fs=SR, output="sos")
        self.zi = np.repeat(sosfilt_zi(self.sos)[:, :, None], 2, axis=2) * 0.0

    def process(self, x: np.ndarray, amount: float) -> np.ndarray:
        y, self.zi = sosfilt(self.sos, x, axis=0, zi=self.zi)
        y = np.tanh(y * 3.0) * 0.55
        mix = 0.5 + 0.5 * amount
        return x * (1 - mix) + y * mix


# ------------------------------------------------------------------ lo-fi
class LoFi:
    """Menos bits y menos muestras por segundo: sonido de videojuego antiguo."""

    def process(self, x: np.ndarray, amount: float) -> np.ndarray:
        n = len(x)
        k = 2 + int(amount * 14)
        bits = 10 - amount * 6
        q = 2 ** bits
        y = np.repeat(x[::k], k, axis=0)[:n]
        y = np.round(y * q) / q
        mix = 0.6 + 0.4 * amount
        return x * (1 - mix) + y * mix


# ------------------------------------------------------------------ corte ritmico
def gate(x: np.ndarray, steps: np.ndarray, amount: float) -> np.ndarray:
    """Corta el sonido a golpes (mitad abierto, mitad cerrado en cada paso)."""
    ph = steps % 1.0
    opened = np.clip(ph / 0.02, 0, 1) * np.clip((0.5 - ph) / 0.04, 0, 1)
    g = 1 - amount * 0.95 * (1 - opened)
    return x * g[:, None].astype(np.float32)


# ------------------------------------------------------------------ sonidos
def _phase(freq: np.ndarray) -> np.ndarray:
    return np.cumsum(freq) / SR


def _saw(ph: np.ndarray) -> np.ndarray:
    return 2 * (ph % 1.0) - 1


def _env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n, np.float32)
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] *= np.linspace(1, 0, r)
    return e


def _stereo(y: np.ndarray, peak: float = 0.8) -> np.ndarray:
    y = y / (np.max(np.abs(y)) + 1e-9) * peak
    return np.stack([y, y], axis=1).astype(np.float32)


def _noise(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.uniform(-1, 1, n)


def horn() -> np.ndarray:
    """Bocina: tres toques cortos y uno largo."""
    parts = []
    bp = butter(2, [300, 4500], btype="band", fs=SR, output="sos")
    for dur, gap in ((0.12, 0.06), (0.12, 0.06), (0.12, 0.06), (0.75, 0.0)):
        n = int(dur * SR)
        t = np.arange(n) / SR
        f = 440 * (1 - 0.08 * np.exp(-t * 35))
        y = sum(_saw(_phase(f * r)) for r in (1.0, 1.006, 0.994, 1.498, 2.004))
        y = np.tanh(sosfilt(bp, y) * 1.5) * _env(n, 0.004, 0.03)
        parts += [y, np.zeros(int(gap * SR))]
    return _stereo(np.concatenate(parts), 0.75)


def siren() -> np.ndarray:
    """Sirena de dub: el tono sube y baja cada vez mas deprisa."""
    n = int(2.6 * SR)
    t = np.arange(n) / SR
    rate = 2.0 + 4.0 * (t / t[-1])
    lfo = np.abs(((np.cumsum(rate) / SR) % 1.0) * 2 - 1)   # triangular
    f = 520 + 700 * lfo
    ph = _phase(f)
    y = 0.7 * np.sin(2 * np.pi * ph) + 0.3 * np.sign(np.sin(2 * np.pi * ph))
    return _stereo(y * _env(n, 0.02, 0.4), 0.6)


def laser() -> np.ndarray:
    """Tres disparos de laser."""
    parts = []
    for i in range(3):
        n = int(0.2 * SR)
        t = np.arange(n) / SR
        f = 150 + 2600 * np.exp(-t * 22)
        ph = _phase(f)
        y = (np.sin(2 * np.pi * ph) + 0.25 * _saw(ph)) * np.exp(-t * 9) * _env(n, 0.002, 0.02)
        parts += [y * (1 - 0.18 * i), np.zeros(int(0.03 * SR))]
    return _stereo(np.concatenate(parts), 0.7)


def impact(rng: np.random.Generator) -> np.ndarray:
    """Golpe grave con cola."""
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    f = 38 + 80 * np.exp(-t * 7)
    body = np.sin(2 * np.pi * _phase(f)) * np.exp(-t * 2.6)
    lp = butter(2, 1800, fs=SR, output="sos")
    hit = sosfilt(lp, _noise(n, rng)) * np.exp(-t * 28)
    y = np.tanh((body + 0.6 * hit) * 1.4)
    return _stereo(y * _env(n, 0.001, 0.2), 0.95)


def riser(bpm: float, rng: np.random.Generator, beats: int = 8) -> np.ndarray:
    """Subidon: ruido y tono que suben durante 2 compases hasta el comienzo del siguiente."""
    n = int(beats * 60.0 / bpm * SR)
    t = np.arange(n) / SR
    p = t / t[-1]
    noise = _noise(n, rng)
    out = np.zeros(n)
    chunk = 1024
    zi = None
    for i in range(0, n, chunk):
        fc = 250 * (9000 / 250) ** (p[i] ** 1.5)
        sos = butter(2, fc, btype="high", fs=SR, output="sos")
        if zi is None:
            zi = sosfilt_zi(sos) * 0.0
        out[i:i + chunk], zi = sosfilt(sos, noise[i:i + chunk], zi=zi)
    tone = np.sin(2 * np.pi * _phase(220 * 2 ** (3 * p ** 1.3))) * 0.25
    y = (out + tone) * (0.08 + 0.92 * p ** 2.2)
    y *= _env(n, 0.01, 0.01)
    return _stereo(y, 0.75)


def _snare_hit(rng: np.random.Generator) -> np.ndarray:
    n = int(0.16 * SR)
    t = np.arange(n) / SR
    bp = butter(2, [900, 7000], btype="band", fs=SR, output="sos")
    noise = sosfilt(bp, _noise(n, rng)) * np.exp(-t * 32)
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t * 45)
    return noise * 1.4 + tone * 0.6


def snare_roll(bpm: float, rng: np.random.Generator) -> np.ndarray:
    """Redoble de 2 compases que se acelera y crece (corcheas, semicorcheas, fusas)."""
    beat = 60.0 / bpm
    n = int(8 * beat * SR)
    y = np.zeros(n + int(0.2 * SR))
    hits = [i * 0.5 for i in range(8)] + [4 + i * 0.25 for i in range(8)] + [6 + i * 0.125 for i in range(16)]
    for b in hits:
        h = _snare_hit(rng)
        s = int(b * beat * SR)
        y[s:s + len(h)] += h * (0.25 + 0.75 * (b / 8) ** 1.4)
    return _stereo(y[:n + int(0.15 * SR)], 0.8)


def scratch(audio: Optional[np.ndarray], pos: float, bpm: float) -> Optional[np.ndarray]:
    """Rascado hecho con la propia cancion que suena: el disco va adelante y atras."""
    if audio is None:
        return None
    beat = 60.0 / bpm
    n = int(beat * SR)
    t = np.arange(n) / SR
    rate = 2.4 * np.sin(2 * np.pi * t * 2 / beat)   # dos idas y vueltas por golpe
    p0 = max(0.0, pos - 0.15 * SR)
    idx = np.clip(p0 + np.cumsum(rate), 0, len(audio) - 2)
    i0 = idx.astype(np.int64)
    f = (idx - i0)[:, None]
    y = audio[i0] * (1 - f) + audio[i0 + 1] * f
    y *= (np.minimum(1.0, np.abs(rate) / 0.6))[:, None]   # el disco parado no suena
    y *= _env(n, 0.003, 0.02)[:, None]
    peak = float(np.max(np.abs(y))) + 1e-9
    return (y / peak * 0.85).astype(np.float32)
