"""Procedural sound effects - synthesised with numpy, no audio assets needed.

build_track(events, duration, path): events = [(time_s, name, gain)] -> mono 44.1 kHz WAV (events summed + soft-limited).
Sounds: whoosh swish pop tick impact riser shimmer glitch click.
"""
from __future__ import annotations

import wave

import numpy as np

SR = 44100
NAMES = ("whoosh", "swish", "pop", "tick", "impact", "riser", "shimmer", "glitch", "click")


def _t(d): return np.arange(int(SR * d)) / SR
def _env(n, a=0.01, r=0.2):
    e = np.ones(n); na, nr = max(1, int(SR * a)), max(1, int(SR * r)); e[:na] = np.linspace(0, 1, na); e[-nr:] *= np.linspace(1, 0, nr); return e


def _lowpass(x, k):
    """One-pole low-pass with a per-sample coefficient array k in (0,1]."""
    y = np.empty_like(x); acc = 0.0
    for i in range(len(x)):
        acc += k[i] * (x[i] - acc); y[i] = acc
    return y


def _noise(n, seed=1): return np.random.default_rng(seed).standard_normal(n)


def whoosh(d=0.55):
    t = _t(d); n = len(t); k = 0.02 + 0.55 * np.sin(np.pi * t / d) ** 2
    x = _lowpass(_noise(n, 3), k); x -= _lowpass(x, np.full(n, 0.01))          # band-ish sweep
    return x / (np.abs(x).max() + 1e-9) * np.sin(np.pi * t / d) ** 1.5


def swish(d=0.28):
    t = _t(d); n = len(t); x = _lowpass(_noise(n, 5), 0.05 + 0.4 * t / d); return x / (np.abs(x).max() + 1e-9) * np.sin(np.pi * t / d)


def pop(d=0.16):
    t = _t(d); f = 520 * np.exp(-t * 14) + 180; return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 26)


def tick(d=0.05):
    t = _t(d); return np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 120) * 0.8


def click(d=0.03):
    t = _t(d); return _noise(len(t), 9) * np.exp(-t * 300) * 0.6


def impact(d=0.7):
    t = _t(d); body = np.sin(2 * np.pi * (52 * np.exp(-t * 2.2) + 38) * t) * np.exp(-t * 5.5)
    return body + _lowpass(_noise(len(t), 7), np.full(len(t), 0.3)) * np.exp(-t * 18) * 0.7


def riser(d=1.1):
    t = _t(d); n = len(t); x = _lowpass(_noise(n, 11), 0.03 + 0.5 * (t / d) ** 2) * (t / d) ** 1.5
    tone = np.sin(2 * np.pi * np.cumsum(220 + 900 * (t / d) ** 2) / SR) * 0.25 * (t / d)
    return (x / (np.abs(x).max() + 1e-9) * 0.8 + tone) * _env(n, 0.05, 0.03)


def shimmer(d=1.0):
    t = _t(d); s = sum(np.sin(2 * np.pi * f * t) for f in (1320, 1760, 2093, 2637)) / 4
    return s * (0.6 + 0.4 * np.sin(2 * np.pi * 9 * t)) * np.exp(-t * 3.2) * _env(len(t), 0.005, 0.05)


def glitch(d=0.26):
    rng = np.random.default_rng(13); n = int(SR * d); out = np.zeros(n); pos = 0
    while pos < n:
        ln = int(rng.integers(SR // 200, SR // 40)); seg = np.sign(np.sin(2 * np.pi * rng.uniform(150, 2500) * np.arange(ln) / SR)) * rng.uniform(.2, .6)
        m = min(ln, n - pos); out[pos:pos + m] = seg[:m]; pos += ln + int(rng.integers(0, SR // 60))
    return out * np.linspace(1, 0.2, n)


_GEN = {"whoosh": whoosh, "swish": swish, "pop": pop, "tick": tick, "click": click, "impact": impact, "riser": riser, "shimmer": shimmer, "glitch": glitch}
_CACHE: dict = {}


def render(name: str) -> np.ndarray:
    if name not in _CACHE:
        _CACHE[name] = _GEN[name]().astype(np.float32)
    return _CACHE[name]


def build_track(events, duration: float, path: str) -> str | None:
    """Mix events into a WAV file. Returns the path, or None when there is nothing to write."""
    evs = [(float(t), n, float(g)) for t, n, g in events if n in _GEN and 0 <= t < duration]
    if not evs:
        return None
    n_total = int(SR * (duration + 1.5)); buf = np.zeros(n_total, np.float32)
    for t, name, gain in evs:
        s = render(name); i = int(t * SR); j = min(n_total, i + len(s))
        if j > i:
            buf[i:j] += s[:j - i] * gain
    buf = np.tanh(buf * 0.9) * 0.9                                   # soft limiter
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((buf * 32767).astype("<i2").tobytes())
    return path
