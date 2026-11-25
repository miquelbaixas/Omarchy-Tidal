"""Analisis de una cancion para mezclarla: golpes, compases, inicio/fin de la musica y forma de onda.

Se usa el tempo que da TIDAL como pista y se busca en la senal la fase exacta de los
golpes (donde caen) y el primer tiempo de cada compas (donde suena el bombo con mas fuerza).
"""
import math
from typing import Optional

import numpy as np

SR = 48000
HOP = 512
FRAME = 1024


def _frames(x: np.ndarray) -> np.ndarray:
    n = 1 + (len(x) - FRAME) // HOP
    if n <= 0:
        return np.zeros((0, FRAME), dtype=np.float32)
    shape = (n, FRAME)
    strides = (x.strides[0] * HOP, x.strides[0])
    return np.lib.stride_tricks.as_strided(x, shape=shape, strides=strides)


def onset_envelope(mono: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fuerza de los ataques (flujo espectral) y energia de graves por trama."""
    win = np.hanning(FRAME).astype(np.float32)
    env, low = [], []
    # por bloques para no gastar demasiada memoria
    fr = _frames(mono)
    prev = None
    lo_bins = int(150 / (SR / FRAME)) + 1
    for i in range(0, len(fr), 4096):
        spec = np.abs(np.fft.rfft(fr[i:i + 4096] * win, axis=1)).astype(np.float32)
        logs = np.log1p(spec * 10)
        if prev is not None:
            d = np.diff(np.vstack([prev[-1:], logs]), axis=0)
        else:
            d = np.vstack([np.zeros((1, logs.shape[1]), np.float32), np.diff(logs, axis=0)])
        env.append(np.maximum(d, 0).sum(axis=1))
        low.append(spec[:, 1:lo_bins].sum(axis=1))
        prev = logs
    if not env:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    e = np.concatenate(env)
    e = e - np.convolve(e, np.ones(16) / 16, mode="same")  # quita la tendencia lenta
    return np.maximum(e, 0), np.concatenate(low)


def _comb_score(env: np.ndarray, period: float, phase: float) -> float:
    idx = np.arange(phase, len(env) - 1, period)
    i0 = idx.astype(int)
    f = idx - i0
    return float(np.sum(env[i0] * (1 - f) + env[np.minimum(i0 + 1, len(env) - 1)] * f)) / max(1, len(idx))


def estimate_bpm(env: np.ndarray) -> float:
    fps = SR / HOP
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    bpms = 60 * fps / np.maximum(lags, 1)
    mask = (bpms >= 70) & (bpms <= 180)
    best = lags[mask][np.argmax(ac[mask])]
    return 60 * fps / best


def analyze(audio: np.ndarray, bpm_hint: Optional[float] = None) -> dict:
    """audio: (n, 2) float32 a 48 kHz. Devuelve tiempos en segundos."""
    mono = audio.mean(axis=1).astype(np.float32)
    duration = len(mono) / SR
    env, low = onset_envelope(mono)
    fps = SR / HOP
    if len(env) < 100:
        return {"bpm": bpm_hint or 120.0, "beat0": 0.0, "downbeat0": 0.0, "beats": [], "start": 0.0,
                "end": duration, "duration": duration, "waveform": []}

    bpm = float(bpm_hint) if bpm_hint else estimate_bpm(env[: int(fps * 90)])
    # Afina el tempo (+-2 %) y la fase con un peine de golpes sobre la envolvente.
    best = (-1.0, bpm, 0.0)
    for b in np.linspace(bpm * 0.98, bpm * 1.02, 41):
        period = 60 * fps / b
        phases = np.linspace(0, period, 24, endpoint=False)
        for ph in phases:
            sc = _comb_score(env, period, ph)
            if sc > best[0]:
                best = (sc, b, ph)
    _, bpm, phase = best
    period = 60 * fps / bpm
    # Fase fina alrededor de la mejor
    for ph in np.linspace(phase - period / 24, phase + period / 24, 13):
        if ph >= 0:
            sc = _comb_score(env, period, ph)
            if sc > best[0]:
                best = (sc, bpm, ph)
    phase = best[2]
    beat_dt = 60.0 / bpm
    beat0 = phase * HOP / SR

    # Primer tiempo del compas: el desplazamiento (0..3) con mas graves en sus golpes.
    beats_f = np.arange(phase, len(low) - 1, period)
    lowb = low[beats_f.astype(int)]
    scores = [float(lowb[k::4].mean()) if len(lowb[k::4]) else 0 for k in range(4)]
    k = int(np.argmax(scores))
    downbeat0 = beat0 + k * beat_dt

    # Donde empieza y acaba la musica (energia por encima de -35 dB del maximo)
    blk = int(SR * 0.05)
    nb = len(mono) // blk
    rms = np.sqrt(np.mean(mono[: nb * blk].reshape(nb, blk) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / (rms.max() + 1e-12))
    loud = np.where(db > -35)[0]
    start = float(loud[0] * 0.05) if len(loud) else 0.0
    end = float((loud[-1] + 1) * 0.05) if len(loud) else duration

    # Forma de onda resumida (picos) para pintar
    n = 1200
    step = max(1, len(mono) // n)
    m = np.abs(mono[: step * (len(mono) // step)]).reshape(-1, step)
    peaks = m.max(axis=1)
    peaks = peaks / (peaks.max() + 1e-9)
    wave = [round(float(v), 3) for v in peaks[:n]]

    # Energia relativa por tramos (para elegir buenos puntos de mezcla)
    return {
        "bpm": round(bpm, 3),
        "beat0": round(beat0, 4),
        "downbeat0": round(downbeat0, 4),
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "waveform": wave,
    }


def mix_points(a: dict, bars: int) -> dict:
    """Punto de entrada (primer compas tras el comienzo de la musica) y de salida
    (compas desde el que empezar a mezclar para acabar justo antes del final)."""
    bar = 4 * 60.0 / a["bpm"]
    d0 = a["downbeat0"]
    k_in = max(0, math.ceil((a["start"] - d0) / bar - 1e-6))
    mix_in = d0 + k_in * bar
    if mix_in > a["duration"] * 0.3:  # intro rara: se empieza en el primer compas
        mix_in = d0
    latest = a["end"] - bars * bar - 0.5
    k_out = math.floor((latest - d0) / bar)
    mix_out = d0 + k_out * bar
    if mix_out < a["duration"] * 0.5:
        mix_out = max(mix_in + bar, a["duration"] - bars * bar - 1)
    return {"in": round(max(0.0, mix_in), 4), "out": round(max(0.0, mix_out), 4), "bar": bar}
