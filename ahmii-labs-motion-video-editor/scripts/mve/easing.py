"""Easing curves. `ease(spec, x)` maps x in [0,1] -> eased value (may overshoot for back/elastic).

spec may be: a name ("out_cubic", "in_out_back", "bounce"...), a 4-list [x1,y1,x2,y2] (CSS/AE cubic-bezier),
or None (linear).
"""
from __future__ import annotations

import math
from functools import lru_cache


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _poly(n):
    return (lambda x: x ** n), (lambda x: 1 - (1 - x) ** n), \
           (lambda x: 2 ** (n - 1) * x ** n if x < .5 else 1 - (-2 * x + 2) ** n / 2)


def _back(x, c=1.70158):
    return (c + 1) * x ** 3 - c * x ** 2


def _out_back(x, c=1.70158):
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def _in_out_back(x, c=1.70158 * 1.525):
    return ((2 * x) ** 2 * ((c + 1) * 2 * x - c)) / 2 if x < .5 else ((2 * x - 2) ** 2 * ((c + 1) * (2 * x - 2) + c) + 2) / 2


def _out_bounce(x):
    n, d = 7.5625, 2.75
    if x < 1 / d:
        return n * x * x
    if x < 2 / d:
        x -= 1.5 / d; return n * x * x + .75
    if x < 2.5 / d:
        x -= 2.25 / d; return n * x * x + .9375
    x -= 2.625 / d; return n * x * x + .984375


def _out_elastic(x):
    if x in (0, 1):
        return x
    return 2 ** (-10 * x) * math.sin((x * 10 - .75) * (2 * math.pi / 3)) + 1


def _in_elastic(x):
    return 1 - _out_elastic(1 - x)


_SPRING_END = 1 - math.exp(-6) * math.cos(9)


def _spring(x):
    return (1 - math.exp(-6 * x) * math.cos(9 * x)) / _SPRING_END


EASES = {"linear": lambda x: x, "hold": lambda x: 0.0 if x < 1 else 1.0}
for _n, _name in ((2, "quad"), (3, "cubic"), (4, "quart"), (5, "quint")):
    _i, _o, _io = _poly(_n)
    EASES[f"in_{_name}"], EASES[f"out_{_name}"], EASES[f"in_out_{_name}"] = _i, _o, _io
EASES.update({
    "in_sine": lambda x: 1 - math.cos(x * math.pi / 2), "out_sine": lambda x: math.sin(x * math.pi / 2),
    "in_out_sine": lambda x: -(math.cos(math.pi * x) - 1) / 2,
    "in_expo": lambda x: 0.0 if x == 0 else 2 ** (10 * x - 10), "out_expo": lambda x: 1.0 if x == 1 else 1 - 2 ** (-10 * x),
    "in_out_expo": lambda x: x if x in (0, 1) else (2 ** (20 * x - 10) / 2 if x < .5 else (2 - 2 ** (-20 * x + 10)) / 2),
    "in_back": _back, "out_back": _out_back, "in_out_back": _in_out_back,
    "out_bounce": _out_bounce, "in_bounce": lambda x: 1 - _out_bounce(1 - x),
    "in_out_bounce": lambda x: (1 - _out_bounce(1 - 2 * x)) / 2 if x < .5 else (1 + _out_bounce(2 * x - 1)) / 2,
    "out_elastic": _out_elastic, "in_elastic": _in_elastic, "spring": _spring,
})
ALIASES = {
    "ease": "in_out_cubic", "ease_in": "in_cubic", "ease_out": "out_cubic", "ease_in_out": "in_out_cubic",
    "smooth": "in_out_sine", "snap": "out_expo", "overshoot": "out_back", "bounce": "out_bounce", "elastic": "out_elastic",
    "step": "hold", "in_out": "in_out_cubic", "out": "out_cubic", "in": "in_cubic",
}
EASE_NAMES = sorted(set(EASES) | set(ALIASES))


@lru_cache(maxsize=256)
def _bezier_solver(x1: float, y1: float, x2: float, y2: float):
    """CSS-style cubic-bezier timing function."""
    def bx(t): return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3
    def by(t): return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3
    def dbx(t): return 3 * (1 - t) ** 2 * x1 + 6 * (1 - t) * t * (x2 - x1) + 3 * t ** 2 * (1 - x2)

    def solve(x):
        t = x
        for _ in range(8):                       # Newton
            err = bx(t) - x
            if abs(err) < 1e-6:
                return by(t)
            d = dbx(t)
            if abs(d) < 1e-6:
                break
            t -= err / d
        lo, hi = 0.0, 1.0                        # bisection fallback
        for _ in range(24):
            t = (lo + hi) / 2
            if bx(t) < x: lo = t
            else: hi = t
        return by(t)
    return solve


def is_valid(spec) -> bool:
    if spec is None:
        return True
    if isinstance(spec, str):
        return spec in EASES or spec in ALIASES
    return isinstance(spec, (list, tuple)) and len(spec) == 4 and all(isinstance(v, (int, float)) for v in spec)


def ease(spec, x: float) -> float:
    x = clamp01(x)
    if spec is None:
        return x
    if isinstance(spec, str):
        fn = EASES.get(ALIASES.get(spec, spec))
        return fn(x) if fn else x
    if isinstance(spec, (list, tuple)) and len(spec) == 4:
        return _bezier_solver(*[float(v) for v in spec])(x)
    return x


def smoothstep(a: float, b: float, x: float) -> float:
    k = clamp01((x - a) / (b - a)) if b != a else (1.0 if x >= b else 0.0)
    return k * k * (3 - 2 * k)
