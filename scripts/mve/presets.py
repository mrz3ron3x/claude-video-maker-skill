"""Entry / exit / loop animation presets, shared by kinetic text units and by layer `enter` / `exit`.

apply(name, p, S, ...) -> State(dx, dy, sx, sy, rot, alpha, blur, clip)
  p  = linear progress 0..1 of the entrance (for an exit the caller passes 1 - progress)
  S  = frame scale factor (short side / 720) so distances stay proportional
  u  = unit centre offset from the block centre in px (used by "tracking")
  i  = unit index (seeds "scatter" / "glitch"), tick = frame number (seeds "glitch")
  lh = line height in px (used by the masked presets)
"""
from __future__ import annotations

from dataclasses import dataclass

from .easing import ease
from .props import _hash01, noise


@dataclass
class State:
    dx: float = 0.0
    dy: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    alpha: float = 1.0
    blur: float = 0.0
    clip: bool = False


def _a(x: float) -> float:
    return max(0.0, min(1.0, x))


def _rand(i: int, k: int) -> float:
    return _hash01(i * 7 + k, 91) * 2 - 1


def _fade(p, S, **_):
    return State(alpha=ease("in_out_sine", p))


def _rise(p, S, **_):
    e = ease("out_cubic", p); return State(dy=(1 - e) * 70 * S, alpha=_a(e * 1.6))


def _drop(p, S, **_):
    e = ease("out_bounce", p); return State(dy=-(1 - e) * 90 * S, alpha=_a(p * 6))


def _slide_down(p, S, **_):
    e = ease("out_cubic", p); return State(dy=-(1 - e) * 70 * S, alpha=_a(e * 1.6))


def _slide_left(p, S, **_):
    e = ease("out_cubic", p); return State(dx=-(1 - e) * 140 * S, alpha=_a(e * 1.5))


def _slide_right(p, S, **_):
    e = ease("out_cubic", p); return State(dx=(1 - e) * 140 * S, alpha=_a(e * 1.5))


def _pop(p, S, **_):
    e = ease("out_back", p); s = 0.55 + 0.45 * e; return State(sx=s, sy=s, alpha=_a(p * 4))


def _zoom_out(p, S, **_):
    e = ease("out_cubic", p); s = 1 + 0.8 * (1 - e); return State(sx=s, sy=s, alpha=_a(e * 1.4))


def _zoom_in(p, S, **_):
    e = ease("out_cubic", p); s = 0.3 + 0.7 * e; return State(sx=s, sy=s, alpha=_a(p * 3))


def _blur_in(p, S, **_):
    e = ease("out_cubic", p); s = 1 + 0.08 * (1 - e); return State(sx=s, sy=s, blur=(1 - e) * 18 * S, alpha=_a(e * 1.5))


def _tracking(p, S, u=0.0, **_):
    e = ease("out_expo", p); return State(dx=u * (1 - e) * 0.9, alpha=_a(e * 2))


def _spin_in(p, S, **_):
    e = ease("out_cubic", p); s = 0.4 + 0.6 * e; return State(sx=s, sy=s, rot=-(1 - e) * 120, alpha=e)


def _flip(p, S, **_):
    return State(sy=max(0.001, ease("out_back", p)), alpha=_a(p * 5))


def _bounce(p, S, **_):
    s = 0.3 + 0.7 * ease("out_bounce", p); return State(sx=s, sy=s, alpha=_a(p * 6))


def _elastic(p, S, **_):
    s = max(0.001, ease("out_elastic", p)); return State(sx=s, sy=s, alpha=_a(p * 6))


def _scatter(p, S, i=0, **_):
    e = ease("out_cubic", p)
    return State(dx=_rand(i, 1) * (1 - e) * 220 * S, dy=_rand(i, 2) * (1 - e) * 220 * S, rot=_rand(i, 3) * (1 - e) * 180, alpha=e)


def _mask_up(p, S, lh=60.0, **_):
    return State(dy=(1 - ease("out_expo", p)) * lh * 1.05, clip=True)


def _mask_down(p, S, lh=60.0, **_):
    return State(dy=-(1 - ease("out_expo", p)) * lh * 1.05, clip=True)


def _stretch(p, S, **_):
    e = ease("out_cubic", p); return State(sx=1 + 1.2 * (1 - e), alpha=e)


def _glitch(p, S, i=0, tick=0, **_):
    if p >= 1:
        return State()
    r1, r2, r3 = _rand(tick, i + 1), _rand(tick + 3, i + 5), _hash01(tick * 13 + i, 5)
    k = 1 - p
    return State(dx=r1 * 46 * S * k, sx=1 + r2 * 0.35 * k, alpha=0.0 if r3 < 0.45 * k else 1.0)


PRESETS = {
    "fade": _fade, "rise": _rise, "slide_up": _rise, "drop": _drop, "slide_down": _slide_down,
    "slide_left": _slide_left, "slide_right": _slide_right, "pop": _pop, "zoom_out": _zoom_out, "zoom_in": _zoom_in,
    "blur_in": _blur_in, "tracking": _tracking, "spin_in": _spin_in, "flip": _flip, "bounce": _bounce,
    "elastic": _elastic, "scatter": _scatter, "mask_up": _mask_up, "mask_down": _mask_down, "stretch": _stretch,
    "glitch": _glitch,
}
PRESET_NAMES = sorted(PRESETS)
# presets whose effect needs per-unit stagger to look right
MASKED = {"mask_up", "mask_down"}


def apply(name: str, p: float, S: float, **kw) -> State:
    fn = PRESETS.get(name)
    if fn is None or p >= 1.0 and name not in ("glitch",):
        return State()
    return fn(max(0.0, p), S, **kw)


# ---------------------------------------------------------------- continuous loops (added on top of the entrance)
LOOPS = ("wave", "float", "pulse", "jitter", "sway", "breathe")


def loop(name: str | None, t: float, S: float, i: int = 0) -> State:
    import math
    st = State()
    if name == "wave":
        st.dy = math.sin(t * 4 + i * 0.6) * 9 * S
    elif name == "float":
        st.dy = math.sin(t * 2.0 + i * 0.3) * 8 * S
    elif name == "pulse":
        s = 1 + 0.045 * math.sin(t * 5.5); st.sx = st.sy = s
    elif name == "jitter":
        st.dx, st.dy = noise(t * 14 + i * 3, 1) * 3 * S, noise(t * 14 + i * 3, 2) * 3 * S
    elif name == "sway":
        st.rot = math.sin(t * 2.4 + i * 0.4) * 3.0
    elif name == "breathe":
        s = 1 + 0.025 * math.sin(t * 1.8); st.sx = st.sy = s
    return st
