"""Motor de audio de TIDAL DJ: dos platos, crossfader, filtros y mezcla automatica.

Proceso aparte (python -m tidalconnect.djengine.engine). Recibe ordenes JSON por la
entrada estandar (una por linea) y emite eventos JSON por la salida estandar.
El audio se manda a PipeWire con pw-cat (float32, 48 kHz, estereo).

Mezcla automatica: cuando el plato principal llega a su punto de salida (un comienzo
de compas cerca del final), el otro plato entra en su primer compas, ajustado al mismo
tempo (varispeed) y en fase, y el crossfader pasa de uno a otro durante N compases con
intercambio de graves. Despues el tempo vuelve poco a poco al original de la cancion.

Efectos (fx.py): eco, reverberacion, flanger, radio, lo-fi y corte ritmico sobre la
mezcla o sobre un plato; repetir (bucle corto al compas, sin perder el sitio) y freno
de disco; y sonidos sintetizados (bocina, sirena...) que entran a compas.

Pre-escucha: una segunda salida (auriculares) con los platos marcados con el boton de
auriculares. Un plato que no suena en la mezcla se puede escuchar solo por los
auriculares (sin que lo oiga nadie) y moverse por su forma de onda.
"""
import array
import fcntl
import json
import math
import os
import subprocess
import termios
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi

from . import fx
from .analysis import SR, analyze, mix_points

BLOCK = 1024
OTHER = {"A": "B", "B": "A"}

_out_lock = threading.Lock()


def emit(ev: str, **data) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps({"ev": ev, **data}) + "\n")
        sys.stdout.flush()


def log(msg: str) -> None:
    sys.stderr.write(f"[djengine] {msg}\n")
    sys.stderr.flush()


class Deck:
    def __init__(self, name: str):
        self.name = name
        self.audio: Optional[np.ndarray] = None
        self.meta: dict = {}
        self.an: dict = {}
        self.points: dict = {}
        self.pos = 0.0          # posicion en muestras de la cancion
        self.rate = 1.0         # velocidad de reproduccion (varispeed)
        self.rate_target = 1.0
        self.rate_step = 0.0    # cambio de velocidad por muestra
        self.playing = False
        self.filter = 0.0       # -1 paso bajo ... 0 nada ... +1 paso alto
        self._filt_key = None
        self._ba = None
        self._zi = None
        self.loading = False
        self.roll: Optional[dict] = None   # bucle corto mientras se mantiene "Repetir"
        self.cue = False        # boton de auriculares: este plato va a la pre-escucha
        self.preview = False    # suena solo por los auriculares (no esta en la mezcla)

    @property
    def ready(self) -> bool:
        return self.audio is not None and not self.loading

    @property
    def secs(self) -> float:
        return self.pos / SR

    @property
    def bpm_now(self) -> float:
        return (self.an.get("bpm") or 120.0) * self.rate

    def clear(self) -> None:
        cue = self.cue          # el boton de auriculares sigue puesto al cambiar de cancion
        self.__init__(self.name)
        self.cue = cue

    # -------------------------------------------------------------- audio
    def _coeffs(self):
        v = round(self.filter, 3)
        if abs(v) < 0.02:
            return None
        if self._filt_key != v:
            if v < 0:   # paso bajo: de 20 kHz a 250 Hz
                fc = 20000 * (250 / 20000) ** (-v)
                b, a = butter(2, fc, btype="low", fs=SR)
            else:       # paso alto: de 20 Hz a 4 kHz
                fc = 20 * (4000 / 20) ** v
                b, a = butter(2, fc, btype="high", fs=SR)
            self._ba = (b, a)
            if self._zi is None:
                zi = lfilter_zi(b, a)
                self._zi = [zi * 0.0, zi * 0.0]
            self._filt_key = v
        return self._ba

    def render(self, n: int, preview: bool = False) -> np.ndarray:
        if not (self.playing or preview) or self.audio is None:
            return np.zeros((n, 2), np.float32)
        audio = self.audio
        if self.rate_step:
            rates = self.rate + self.rate_step * np.arange(n)
            if (self.rate_step > 0 and rates[-1] >= self.rate_target) or (self.rate_step < 0 and rates[-1] <= self.rate_target):
                rates = np.clip(rates, min(self.rate, self.rate_target), max(self.rate, self.rate_target))
                self.rate_step = 0.0
            idx = self.pos + np.concatenate([[0.0], np.cumsum(rates[:-1])])
            self.rate = float(rates[-1])
        else:
            idx = self.pos + self.rate * np.arange(n)
        i0 = idx.astype(np.int64)
        f = (idx - i0).astype(np.float32)[:, None]
        last = len(audio) - 2
        valid = i0 <= last
        i0c = np.minimum(i0, last)
        out = audio[i0c] * (1 - f) + audio[i0c + 1] * f
        if not valid.all():
            out[~valid] = 0
        self.pos = float(idx[-1] + self.rate)
        if self.pos >= len(audio) - 2:
            if self.playing:
                self.playing = False
            else:
                self.preview = False
            self.pos = float(len(audio) - 2)
        if self.roll:
            # suena el bucle, pero la cancion sigue avanzando por debajo: al soltar, nada se ha movido
            r = self.roll
            steps = idx - idx[0]
            inl = (r["t"] + steps) % r["len"]
            rr = r["start"] + inl
            j0 = np.clip(rr.astype(np.int64), 0, last)
            jf = (rr - j0).astype(np.float32)[:, None]
            fade = np.minimum(1.0, np.minimum(inl, r["len"] - inl) / 96.0).astype(np.float32)[:, None]
            out = (audio[j0] * (1 - jf) + audio[j0 + 1] * jf) * fade
            r["t"] = float((r["t"] + steps[-1] + self.rate) % r["len"])
        ba = self._coeffs()
        if ba is not None:
            b, a = ba
            for ch in range(2):
                out[:, ch], self._zi[ch] = lfilter(b, a, out[:, ch], zi=self._zi[ch])
        else:
            self._zi = None
            self._filt_key = None
        return out.astype(np.float32)


class Engine:
    def __init__(self):
        self.decks = {"A": Deck("A"), "B": Deck("B")}
        self.master = "A"
        self.xfader = 0.0          # 0 = A, 1 = B
        self.volume = 0.8
        self.auto = True
        self.mix_bars = 8
        self.tempo_mode = "glide"  # tras la mezcla: volver al tempo original o mantenerlo
        self.paused = True
        self.mix: Optional[dict] = None
        self.mix_at: Optional[dict] = None   # "Mezclar ya": {"deck", "at"} (segundos de ese plato)
        self.manual_xfader = False
        self.lock = threading.RLock()
        self.target: Optional[str] = None
        self.out: Optional[subprocess.Popen] = None
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.running = True
        self.prepare_req = {"A": 0, "B": 0}
        # efectos
        self.fx_on: set = set()
        self.fx_target = "all"     # "all" (la mezcla), "A" o "B"
        self.fx_beats = 0.5        # golpes: eco, corte, repetir, freno
        self.fx_amount = 0.6
        self.sfx_volume = 0.8
        self.echo = fx.Echo()
        self.reverb = fx.Reverb()
        self.flanger = fx.Flanger()
        self.radio = fx.Radio()
        self.lofi = fx.LoFi()
        self.brake: Optional[dict] = None
        self.voices: list = []
        self._clock = 0.0
        self._rng = np.random.default_rng()
        self._sounds = {"horn": fx.horn(), "siren": fx.siren(), "laser": fx.laser(), "impact": fx.impact(self._rng)}
        # pre-escucha (auriculares)
        self.cue_target: Optional[str] = None
        self.cue_out: Optional[subprocess.Popen] = None
        self.cue_mix = 0.0         # 0 = solo lo pre-escuchado, 1 = solo la mezcla
        self.cue_volume = 0.8
        self._cue_block: Optional[np.ndarray] = None
        self._cue_cap = 0
        self._cue_fail = 0.0

    # ------------------------------------------------------------ salida
    def open_output(self) -> None:
        if self.out:
            try:
                self.out.stdin.close()
            except Exception:
                pass
            self.out.terminate()
        args = ["pw-cat", "--playback", "--raw", "--format", "f32", "--rate", str(SR), "--channels", "2",
                "--latency", "60ms", "--media-role", "Music", "-P",
                '{ "media.name": "TIDAL DJ", "application.name": "TIDAL DJ", "node.description": "TIDAL DJ" }']
        if self.target:
            args += ["--target", self.target.removeprefix("pipewire/")]
        args.append("-")
        self.out = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def open_cue(self) -> None:
        """Salida de auriculares. No bloquea nunca a la mezcla: si los auriculares van
        lentos (Bluetooth...) se descarta un trozo en los auriculares, no en los altavoces."""
        self.close_cue()
        if not self.cue_target:
            return
        args = ["pw-cat", "--playback", "--raw", "--format", "f32", "--rate", str(SR), "--channels", "2",
                "--latency", "60ms", "--media-role", "Music", "-P",
                '{ "media.name": "TIDAL DJ (auriculares)", "application.name": "TIDAL DJ", "node.description": "TIDAL DJ: pre-escucha" }',
                "--target", self.cue_target.removeprefix("pipewire/"), "-"]
        self.cue_out = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        fd = self.cue_out.stdin.fileno()
        try:
            fcntl.fcntl(fd, 1031, 256 * 1024)        # F_SETPIPE_SZ: margen de ~0,7 s
        except OSError:
            pass
        self._cue_cap = fcntl.fcntl(fd, 1032)        # F_GETPIPE_SZ
        os.set_blocking(fd, False)

    def close_cue(self) -> None:
        if self.cue_out:
            try:
                self.cue_out.stdin.close()
            except Exception:
                pass
            self.cue_out.terminate()
        self.cue_out = None

    def write_cue(self, block: np.ndarray) -> None:
        out = self.cue_out
        if out is None:
            return
        if out.poll() is not None:   # se cerro (auriculares desconectados...): se reintenta cada 3 s
            if time.monotonic() - self._cue_fail > 3:
                self._cue_fail = time.monotonic()
                log("salida de auriculares cerrada: se reabre")
                with self.lock:
                    self.open_cue()
            return
        data = block.tobytes()
        fd = out.stdin.fileno()
        try:
            used = array.array("i", [0])
            fcntl.ioctl(fd, termios.FIONREAD, used)
            if self._cue_cap - used[0] >= len(data):   # solo bloques enteros: nunca se descuadran los canales
                os.write(fd, data)
        except (BlockingIOError, BrokenPipeError, OSError, ValueError):
            pass

    # ------------------------------------------------------------ bucle de audio
    def audio_loop(self) -> None:
        last_state = 0.0
        while self.running:
            if not self.live() and not self.voices and not self.echo.active and not self.reverb.active \
                    and not any(d.preview for d in self.decks.values()):
                time.sleep(0.02)
                if time.monotonic() - last_state > 0.25:
                    last_state = time.monotonic()
                    self.emit_state()
                continue
            with self.lock:
                block = self.render(BLOCK)
            try:
                self.out.stdin.write(block.tobytes())
            except (BrokenPipeError, AttributeError, ValueError):
                log("salida cerrada: se reabre")
                with self.lock:
                    self.open_output()
            if self._cue_block is not None:
                self.write_cue(self._cue_block)
            if time.monotonic() - last_state > 0.2:
                last_state = time.monotonic()
                self.emit_state()

    def gains(self) -> tuple[float, float]:
        x = min(1.0, max(0.0, self.xfader))
        return math.cos(x * math.pi / 2), math.sin(x * math.pi / 2)

    def live(self) -> bool:
        return not self.paused and any(d.playing for d in self.decks.values())

    def render(self, n: int) -> np.ndarray:
        live = self.live()
        if live:
            self.automix_tick()
            self.brake_tick()
        beat0, dbeat, bpm = self.beat_clock(n, live)
        ga, gb = self.gains()
        raw = {}
        if live:
            raw = {k: self.decks[k].render(n) for k in ("A", "B")}
            a, b = raw["A"] * ga, raw["B"] * gb
        else:   # en pausa solo suenan los sonidos y las colas de eco/reverb
            a = b = np.zeros((n, 2), np.float32)
        # pre-escucha: los platos con auriculares (el que no esta en la mezcla avanza solo aqui)
        cue = None
        if self.cue_out is not None:
            for k, d in self.decks.items():
                if d.playing:
                    d.preview = False
                if not d.cue:
                    continue
                sig = raw.get(k) if d.playing and live else d.render(n, preview=True) if d.preview else None
                if sig is not None:
                    cue = sig if cue is None else cue + sig
        snd = self.render_voices(n)
        beats = beat0 + dbeat * np.arange(n)
        if self.fx_target == "A":
            mixed = self.fx_bus(a, beats, bpm) + b + snd
        elif self.fx_target == "B":
            mixed = a + self.fx_bus(b, beats, bpm) + snd
        else:
            mixed = self.fx_bus(a + b + snd, beats, bpm)
        mixed = mixed * self.volume
        peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
        if peak > 0.95:  # limitador suave
            mixed = np.tanh(mixed)
        if self.cue_out is not None:
            hp = mixed if cue is None else cue * (1 - self.cue_mix) + mixed * self.cue_mix
            hp = hp * self.cue_volume
            if float(np.max(np.abs(hp))) > 0.95:
                hp = np.tanh(hp)
            self._cue_block = hp.astype(np.float32)
        else:
            self._cue_block = None
        if live:
            self.after_render()
        return mixed.astype(np.float32)

    # ------------------------------------------------------------ efectos
    def beat_clock(self, n: int, live: bool) -> tuple[float, float, float]:
        """Golpe (contado desde el primero de la cancion) al empezar el bloque, golpes por
        muestra y tempo actual. Sin cancion sonando, un reloj propio a 120 BPM."""
        m = self.decks[self.master]
        if live and m.playing and m.an:
            bl = 60.0 / (m.an.get("bpm") or 120.0)
            return (m.secs - m.an.get("beat0", 0.0)) / bl, m.rate / (SR * bl), m.bpm_now
        b0 = self._clock
        self._clock += n / (SR * 0.5)
        return b0, 1.0 / (SR * 0.5), 120.0

    def fx_bus(self, x: np.ndarray, beats: np.ndarray, bpm: float) -> np.ndarray:
        on, amt, div = self.fx_on, self.fx_amount, self.fx_beats
        if not on and not self.echo.active and not self.reverb.active:
            self.flanger.hist = np.concatenate([self.flanger.hist, x])[-fx.Flanger.HIST:]
            return x
        if "radio" in on:
            x = self.radio.process(x, amt)
        if "lofi" in on:
            x = self.lofi.process(x, amt)
        if "flanger" in on:
            x = self.flanger.process(x, beats / (8 * div), amt)
        else:
            self.flanger.hist = np.concatenate([self.flanger.hist, x])[-fx.Flanger.HIST:]
        if "gate" in on:
            x = fx.gate(x, beats / div, amt)
        x = self.echo.process(x, "echo" in on, div * 60.0 / bpm, amt)
        x = self.reverb.process(x, "reverb" in on, amt)
        return x

    def fx_deck(self) -> "Deck":
        return self.decks[self.fx_target] if self.fx_target in ("A", "B") else self.decks[self.master]

    def roll_on(self) -> None:
        d = self.fx_deck()
        if not d.playing or not d.an:
            return
        bl = 60.0 / (d.an.get("bpm") or 120.0)
        grid = self.fx_beats * bl
        b0 = d.an.get("beat0", 0.0)
        start = b0 + math.floor((d.secs - b0) / grid) * grid
        for o in self.decks.values():
            o.roll = None
        d.roll = {"start": start * SR, "len": grid * SR, "t": (d.secs - start) * SR}

    def roll_off(self) -> None:
        for d in self.decks.values():
            d.roll = None

    def brake_start(self) -> None:
        """Freno de disco: la cancion que suena se para en unos golpes bajando de tono."""
        m = self.decks[self.master]
        if not m.playing or self.brake or self.paused:
            return
        dur = max(0.5, 4 * self.fx_beats * 60.0 / m.bpm_now)
        self.brake = {"deck": m.name, "rate": m.rate_target if m.rate_step else m.rate}
        m.rate_target = 0.0
        m.rate_step = -m.rate / (dur * SR)

    def brake_tick(self) -> None:
        br = self.brake
        if not br:
            return
        d = self.decks[br["deck"]]
        if not d.playing:
            self.brake = None
            return
        if d.rate > 0.003 and d.rate_step < 0:
            return
        self.brake = None
        o = self.decks[OTHER[d.name]]
        if self.mix and self.mix["from"] == d.name:
            d.playing = False          # se acaba la mezcla y sigue la que entraba
        elif o.ready and not o.playing and not self.mix:
            d.playing = False          # entra la siguiente de golpe (after_render)
        else:                          # no hay siguiente: se queda en pausa donde se paro
            d.rate = d.rate_target = br["rate"] or 1.0
            d.rate_step = 0.0
            self.paused = True

    def until_next(self, beats: int) -> int:
        """Muestras hasta el proximo golpe (1) o comienzo de compas (4) de la que suena."""
        m = self.decks[self.master]
        if not self.live() or not m.playing or not m.an:
            return 0
        bl = 60.0 / (m.an.get("bpm") or 120.0)
        b0 = m.an.get("downbeat0" if beats >= 4 else "beat0", 0.0)
        unit = beats * bl
        k = math.ceil((m.secs - b0) / unit - 1e-6)
        return int(max(0.0, b0 + k * unit - m.secs) / max(m.rate, 1e-6) * SR)

    def make_sound(self, name: str) -> tuple[Optional[np.ndarray], int]:
        """Crea el sonido (fuera del candado: alguno tarda unos milisegundos) y a que golpe entra."""
        m = self.decks[self.master]
        sounding = self.live() and m.playing and m.an
        bpm = m.bpm_now if sounding else 120.0
        if name in ("horn", "siren", "laser"):
            return self._sounds[name], 0
        if name == "impact":
            return self._sounds["impact"], 1
        if name == "riser":
            return fx.riser(bpm, self._rng), 4
        if name == "snare":
            return fx.snare_roll(bpm, self._rng), 4
        if name == "scratch":
            if not sounding:
                return None, 0
            return fx.scratch(m.audio, m.pos, m.an.get("bpm") or 120.0), 1
        return None, 0

    def render_voices(self, n: int) -> np.ndarray:
        out = np.zeros((n, 2), np.float32)
        if not self.voices:
            return out
        keep = []
        for v in self.voices:
            p, buf = v["pos"], v["buf"]
            start, src = max(0, -p), max(0, p)
            m = min(n - start, len(buf) - src)
            if m > 0:
                out[start:start + m] += buf[src:src + m]
            v["pos"] = p + n
            if v["pos"] < len(buf):
                keep.append(v)
        self.voices = keep
        return out * self.sfx_volume

    # ------------------------------------------------------------ mezcla
    def automix_tick(self) -> None:
        m = self.decks[self.master]
        o = self.decks[OTHER[self.master]]
        if self.mix:
            self.mix_step()
            return
        if not m.playing:
            return
        if self.mix_at:
            # mezcla pedida a mano: funciona con el modo automatico encendido o apagado
            if self.mix_at["deck"] != self.master or not o.ready:
                self.mix_at = None
            elif m.secs >= self.mix_at["at"]:
                self.start_mix(self.mix_bars, at_downbeat=self.mix_at["at"])
            return
        if self.auto and o.ready and m.points and m.secs >= m.points["out"]:
            self.start_mix(self.mix_bars)

    def next_downbeat(self, d: "Deck") -> float:
        """Segundos (del plato) del proximo comienzo de compas."""
        bar = 4 * 60.0 / (d.an.get("bpm") or 120)
        d0 = d.an.get("downbeat0", 0)
        return d0 + math.ceil((d.secs - d0) / bar - 1e-6) * bar

    def start_mix(self, bars: int, at_downbeat: Optional[float] = None) -> None:
        m = self.decks[self.master]
        oname = OTHER[self.master]
        o = self.decks[oname]
        if not o.ready or self.mix:
            return
        self.mix_at = None
        bpm_m = m.bpm_now
        bpm_o = o.an.get("bpm") or bpm_m
        ratio = bpm_m / bpm_o
        # medio o doble tiempo si cuadra mejor
        for r in (ratio, ratio * 2, ratio / 2):
            if 0.9 <= r <= 1.1:
                ratio = r
                break
        else:
            ratio = 1.0  # tempos muy distintos: fundido sin cuadrar el ritmo
        o.rate, o.rate_target, o.rate_step = ratio, ratio, 0.0
        ref = at_downbeat if at_downbeat is not None else m.points.get("out", m.secs)
        # tiempo desde el ultimo comienzo de compas (si se salto mas alla del punto, sigue cuadrando)
        bar_m = 4 * 60.0 / (m.an.get("bpm") or 120)
        elapsed = (max(0.0, m.secs - ref) % bar_m) / max(m.rate, 1e-6)   # segundos reales
        o.pos = (o.points.get("in", 0.0) + elapsed * o.rate) * SR
        o.playing = True
        o.filter = 0.55   # intercambio de graves: el que entra, sin graves al principio
        length = bars * 4 * 60.0 / bpm_m   # segundos reales
        self.mix = {"from": self.master, "to": oname, "t0": time.monotonic() - elapsed, "len": length,
                    "x0": self.xfader, "x1": 1.0 if oname == "B" else 0.0, "swapped": False}
        self.manual_xfader = False
        emit("track_started", deck=oname, id=o.meta.get("id"))

    def mix_step(self) -> None:
        mx = self.mix
        f = self.decks[mx["from"]]
        t = self.decks[mx["to"]]
        p = (time.monotonic() - mx["t0"]) / max(0.1, mx["len"])
        if not self.manual_xfader:
            self.xfader = mx["x0"] + (mx["x1"] - mx["x0"]) * min(1.0, max(0.0, p))
        if p >= 0.5 and not mx["swapped"]:
            mx["swapped"] = True
            t.filter = 0.0
            f.filter = 0.55
        if p >= 1.0 or not f.playing:
            self.finish_mix()

    def finish_mix(self) -> None:
        mx = self.mix
        self.mix = None
        f = self.decks[mx["from"]]
        t = self.decks[mx["to"]]
        if not self.manual_xfader:
            self.xfader = mx["x1"]
        f.playing = False
        f.filter = 0.0
        self.master = mx["to"]
        if self.tempo_mode == "glide" and abs(t.rate - 1.0) > 0.001:
            # vuelve al tempo original en unos 16 golpes
            dur = 16 * 60.0 / t.bpm_now
            t.rate_target = 1.0
            t.rate_step = (1.0 - t.rate) / (dur * SR)
        freed = mx["from"]
        f_meta = f.meta
        f.clear()
        emit("need_next", deck=freed, finished=f_meta.get("id"))

    def after_render(self) -> None:
        m = self.decks[self.master]
        o = self.decks[OTHER[self.master]]
        if self.mix is None and not m.playing and m.audio is not None and not self.paused:
            # termino sin mezcla: entra la siguiente directamente si esta lista
            if o.ready:
                o.pos = o.points.get("in", 0.0) * SR
                o.playing = True
                self.xfader = 1.0 if o.name == "B" else 0.0
                self.master = o.name
                emit("track_started", deck=o.name, id=o.meta.get("id"))
                freed = m.name
                m.clear()
                emit("need_next", deck=freed)
            else:
                m.clear()
                self.paused = True
                emit("ended")

    # ------------------------------------------------------------ estado
    def emit_state(self) -> None:
        with self.lock:
            decks = {}
            for k, d in self.decks.items():
                decks[k] = {
                    "id": d.meta.get("id"), "loaded": d.audio is not None, "loading": d.loading,
                    "pos": round(d.secs, 2), "duration": round(d.an.get("duration", 0), 2),
                    "rate": round(d.rate, 4), "bpm": round(d.bpm_now, 1) if d.an else None,
                    "bpm_orig": d.an.get("bpm"), "playing": d.playing, "filter": round(d.filter, 2),
                    "mix_in": d.points.get("in"), "mix_out": d.points.get("out"),
                    "cue": d.cue, "preview": d.preview,
                }
            mix = None
            if self.mix:
                mix = {"from": self.mix["from"], "to": self.mix["to"],
                       "progress": round(min(1.0, (time.monotonic() - self.mix["t0"]) / self.mix["len"]), 3)}
            ga, gb = self.gains()
            st = {"decks": decks, "master": self.master, "xfader": round(self.xfader, 3), "gains": [round(ga, 3), round(gb, 3)],
                  "volume": self.volume, "auto": self.auto, "mix_bars": self.mix_bars, "paused": self.paused,
                  "mix": mix, "mix_at": self.mix_at["at"] if self.mix_at else None,
                  "tempo_mode": self.tempo_mode, "target": self.target,
                  "fx": {"on": sorted(self.fx_on), "target": self.fx_target, "beats": self.fx_beats,
                         "amount": round(self.fx_amount, 2), "sfx_volume": round(self.sfx_volume, 2),
                         "roll": any(d.roll for d in self.decks.values()), "brake": bool(self.brake),
                         "sounds": sorted({v["name"] for v in self.voices if v["pos"] >= 0})},
                  "cue": {"target": self.cue_target, "mix": round(self.cue_mix, 2), "volume": round(self.cue_volume, 2),
                          "decks": {k: d.cue for k, d in self.decks.items()},
                          "preview": {k: d.preview for k, d in self.decks.items()}}}
        emit("state", **st)

    # ------------------------------------------------------------ preparar plato
    def prepare(self, deck: str, path: str, meta: dict) -> None:
        d = self.decks[deck]
        with self.lock:
            if d.playing:
                emit("error", msg=f"El plato {deck} esta sonando")
                return
            d.clear()
            d.loading = True
            d.meta = meta
            self.prepare_req[deck] += 1
            req = self.prepare_req[deck]

        def job():
            try:
                raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                                     capture_output=True, check=True).stdout
                audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()
                an = analyze(audio, meta.get("bpm"))
                pts = mix_points(an, self.mix_bars)
                with self.lock:
                    if self.prepare_req[deck] != req:
                        return  # se pidio otra cancion mientras tanto
                    d.audio, d.an, d.points, d.loading = audio, an, pts, False
                    d.pos = pts["in"] * SR
                    if d.cue and not d.playing and self.cue_out is not None:
                        d.preview = True   # con los auriculares puestos, la nueva se oye al momento
                emit("ready", deck=deck, id=meta.get("id"),
                     analysis={k: an[k] for k in ("bpm", "beat0", "downbeat0", "start", "end", "duration")},
                     points=pts, waveform=an["waveform"])
            except Exception as e:
                log(traceback.format_exc())
                with self.lock:
                    d.loading = False
                emit("error", msg=f"No se pudo preparar la cancion: {e}", deck=deck, id=meta.get("id"))

        self.worker.submit(job)

    # ------------------------------------------------------------ ordenes
    def handle(self, c: dict) -> None:
        cmd = c.get("cmd")
        if cmd == "sfx":
            buf, q = self.make_sound(str(c.get("name")))
            with self.lock:
                if buf is None:
                    emit("error", msg="Para rascar tiene que estar sonando una canción")
                else:
                    self.voices.append({"buf": buf, "pos": -self.until_next(q) if q else 0, "name": c.get("name")})
            self.emit_state()
            return
        with self.lock:
            m = self.decks[self.master]
            o = self.decks[OTHER[self.master]]
            if cmd == "output":
                self.target = c.get("target") or None
                self.open_output()
            elif cmd == "prepare":
                pass
            elif cmd == "play":
                if not m.playing and m.ready:
                    if m.secs <= 0.01 and m.points:
                        m.pos = m.points["in"] * SR
                    m.playing = True
                    self.xfader = 0.0 if self.master == "A" else 1.0
                    emit("track_started", deck=self.master, id=m.meta.get("id"))
                elif not m.ready and o.ready and not o.playing:
                    self.master = o.name
                    o.playing = True
                    self.xfader = 0.0 if o.name == "A" else 1.0
                    emit("track_started", deck=o.name, id=o.meta.get("id"))
                self.paused = False
            elif cmd == "pause":
                self.paused = True
            elif cmd == "toggle":
                self.handle({"cmd": "pause" if not self.paused else "play"})
                return
            elif cmd == "crossfader":
                self.xfader = float(c.get("value", 0))
                self.manual_xfader = True
                # al mover el crossfader a mano se puede "rescatar" el otro plato
                if self.xfader > 0.5 and not self.decks["B"].playing and self.decks["B"].ready:
                    self.decks["B"].playing = True
                if self.xfader < 0.5 and not self.decks["A"].playing and self.decks["A"].ready:
                    self.decks["A"].playing = True
            elif cmd == "filter":
                self.decks[c["deck"]].filter = max(-1.0, min(1.0, float(c.get("value", 0))))
            elif cmd == "volume":
                self.volume = max(0.0, min(1.0, float(c.get("value", 0.8))))
            elif cmd == "auto":
                self.auto = bool(c.get("on"))
            elif cmd == "mix_bars":
                self.mix_bars = int(c.get("value") or 8)
                for d in self.decks.values():
                    if d.an:
                        d.points = mix_points(d.an, self.mix_bars)
            elif cmd == "tempo_mode":
                self.tempo_mode = "hold" if c.get("value") == "hold" else "glide"
            elif cmd == "mix_now":
                if self.mix:
                    pass
                elif not m.playing:
                    emit("error", msg="No hay ninguna canción sonando")
                elif not o.ready:
                    emit("error", msg="La siguiente canción aún se está preparando")
                else:
                    # en el siguiente comienzo de compas
                    self.mix_at = {"deck": self.master, "at": self.next_downbeat(m)}
            elif cmd == "skip":
                if self.mix:
                    self.mix["len"] = max(0.5, (time.monotonic() - self.mix["t0"]) + 1.0)
                elif not m.playing:
                    emit("error", msg="No hay ninguna canción sonando")
                elif not o.ready:
                    emit("error", msg="La siguiente canción aún se está preparando")
                else:
                    self.start_mix(2, at_downbeat=m.secs)
            elif cmd == "seek":
                d = self.decks[c["deck"]]
                if d.audio is not None:
                    d.pos = max(0.0, min(float(c["pos"]), d.an.get("duration", 0) - 1)) * SR
                    if self.mix_at and self.mix_at["deck"] == d.name:
                        self.mix_at["at"] = self.next_downbeat(d)
            elif cmd == "fx":
                name, on = c.get("name"), bool(c.get("on"))
                if name == "roll":
                    self.roll_on() if on else self.roll_off()
                elif name == "brake":
                    if on:
                        self.brake_start()
                elif name in fx.BUS_FX:
                    if on:
                        self.fx_on.add(name)
                    else:
                        self.fx_on.discard(name)
            elif cmd == "fx_param":
                if "target" in c:
                    self.fx_target = c["target"] if c["target"] in ("A", "B") else "all"
                if "beats" in c:
                    self.fx_beats = min(4.0, max(0.125, float(c["beats"])))
                    if any(d.roll for d in self.decks.values()):
                        self.roll_on()
                if "amount" in c:
                    self.fx_amount = min(1.0, max(0.0, float(c["amount"])))
                if "sfx_volume" in c:
                    self.sfx_volume = min(1.0, max(0.0, float(c["sfx_volume"])))
            elif cmd == "cue_output":
                self.cue_target = c.get("target") or None
                self.open_cue()
                if not self.cue_target:
                    for d in self.decks.values():
                        d.preview = False
            elif cmd == "cue":
                d = self.decks[c["deck"]]
                d.cue = bool(c.get("on"))
                if not d.cue:
                    d.preview = False
                elif not d.playing and d.ready and self.cue_out is not None:
                    d.preview = True
            elif cmd == "cue_param":
                if "mix" in c:
                    self.cue_mix = min(1.0, max(0.0, float(c["mix"])))
                if "volume" in c:
                    self.cue_volume = min(1.0, max(0.0, float(c["volume"])))
            elif cmd == "unload":
                d = self.decks[c["deck"]]
                if not d.playing:
                    self.prepare_req[d.name] += 1   # cancela una preparacion pendiente
                    d.clear()
            elif cmd == "stop":
                self.paused = True
                self.mix = None
                self.mix_at = None
                self.brake = None
                self.voices = []
                self.fx_on.clear()
                self.echo, self.reverb = fx.Echo(), fx.Reverb()
                for d in self.decks.values():
                    d.clear()
                self.master = "A"
                self.xfader = 0.0
            elif cmd == "quit":
                self.running = False
                self.close_cue()
        if cmd == "prepare":
            self.prepare(c["deck"], c["path"], c.get("meta") or {})
        self.emit_state()


def main() -> None:
    eng = Engine()
    eng.open_output()
    t = threading.Thread(target=eng.audio_loop, daemon=True)
    t.start()
    emit("hello")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            eng.handle(json.loads(line))
        except Exception as e:
            log(traceback.format_exc())
            emit("error", msg=str(e))
        if not eng.running:
            break
    eng.running = False
    if eng.out:
        try:
            eng.out.stdin.close()
        except Exception:
            pass
        eng.out.terminate()
    eng.close_cue()


if __name__ == "__main__":
    main()
