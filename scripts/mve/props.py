"""Animatable properties - the After Effects "stopwatch".

A property value in a spec may be written as:

  0.5                                         constant
  [[0, 0], [0.6, 1, "out_back"], [2, 1]]      keyframes  [time, value, ease-to-next]  (or {"t","v","ease"} dicts)
  {"from": 0, "to": 1, "start": .2, "dur": .6, "ease": "out_cubic"}   tween
  {"keys": [...], "ease": "ease", "loop": "pingpong"}                 keyframes with options
  {"value": 0.5, "wiggle": {"freq": 3, "amp": 0.02, "seed": 1}}      constant + wiggle (smooth noise)
  "sin(t*4)*0.1 + 0.5"                        expression (safe subset; variables t, d; helpers below)

Expression helpers: sin cos tan abs min max sqrt floor ceil pow exp log clamp(x,a,b) lerp(a,b,k)
smooth(a,b,x) ease(name,x) noise(x[,seed]) wiggle(freq,amp[,seed]); constants pi e tau.
Any dict form may also carry "wiggle" and "loop" ("repeat" | "pingpong").
"""
from __future__ import annotations

import ast
import math
from typing import Any

from .easing import ease as _ease, is_valid as _ease_ok, smoothstep


class PropError(ValueError):
    """Raised for malformed property specs; message is user-facing."""


# ---------------------------------------------------------------- noise
def _hash01(n: int, seed: int) -> float:
    n = (n * 374761393 + seed * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / 0xFFFFFF


def noise(x: float, seed: float = 0) -> float:
    """Smooth 1-D value noise in [-1, 1]."""
    i = math.floor(x); f = x - i; s = int(seed)
    a, b = _hash01(i, s), _hash01(i + 1, s)
    k = f * f * (3 - 2 * f)
    return (a + (b - a) * k) * 2 - 1


def wiggle(t: float, freq: float, amp: float, seed: float = 0) -> float:
    """Two-octave smooth noise, like AE's wiggle()."""
    return amp * (noise(t * freq, seed) * 0.75 + noise(t * freq * 2.3 + 17, seed + 1) * 0.25)


# ---------------------------------------------------------------- safe expressions
_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt,
    "floor": math.floor, "ceil": math.ceil, "pow": pow, "exp": math.exp, "log": math.log,
    "clamp": lambda x, a, b: max(a, min(b, x)), "lerp": lambda a, b, k: a + (b - a) * k,
    "smooth": smoothstep, "ease": _ease, "noise": noise,
}
_CONST = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


class Expr:
    """A whitelisted expression compiled from a string. No attribute access, no imports, no comprehension."""

    def __init__(self, src: str):
        self.src = src
        try:
            self.tree = ast.parse(src.strip(), mode="eval").body
        except SyntaxError as e:
            raise PropError(f"bad expression '{src}': {e.msg}") from None
        self._check(self.tree)

    def _check(self, n: ast.AST) -> None:
        ok = (ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Constant, ast.IfExp, ast.Compare, ast.BoolOp,
              ast.Load, ast.operator, ast.unaryop, ast.cmpop, ast.boolop)
        if not isinstance(n, ok):
            raise PropError(f"expression '{self.src}': '{type(n).__name__}' is not allowed")
        if isinstance(n, ast.BinOp) and not isinstance(n.op, (*_BIN, ast.Pow)):
            raise PropError(f"expression '{self.src}': operator not allowed")
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in (*_FUNCS, "wiggle") or n.keywords:
                raise PropError(f"expression '{self.src}': unknown function (allowed: {', '.join([*_FUNCS, 'wiggle'])})")
        if isinstance(n, ast.Name) and n.id not in ("t", "d", *_CONST, *_FUNCS, "wiggle"):
            raise PropError(f"expression '{self.src}': unknown name '{n.id}' (use t, d)")
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float, str, bool)):
            raise PropError(f"expression '{self.src}': constant type not allowed")
        for c in ast.iter_child_nodes(n):
            self._check(c)

    def _ev(self, n: ast.AST, env: dict) -> Any:
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return env[n.id] if n.id in env else _CONST[n.id]
        if isinstance(n, ast.UnaryOp):
            v = self._ev(n.operand, env)
            return -v if isinstance(n.op, ast.USub) else v if isinstance(n.op, ast.UAdd) else (not v)
        if isinstance(n, ast.BinOp):
            a, b = self._ev(n.left, env), self._ev(n.right, env)
            if isinstance(n.op, ast.Pow):
                if abs(b) > 12:
                    raise OverflowError
                return a ** b
            return _BIN[type(n.op)](a, b)
        if isinstance(n, ast.IfExp):
            return self._ev(n.body if self._ev(n.test, env) else n.orelse, env)
        if isinstance(n, ast.Compare):
            left = self._ev(n.left, env)
            for op, comp in zip(n.ops, n.comparators):
                right = self._ev(comp, env)
                if not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.BoolOp):
            vals = (self._ev(v, env) for v in n.values)
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.Call):
            args = [self._ev(a, env) for a in n.args]
            if n.func.id == "wiggle":
                return wiggle(env["t"], *args)
            return _FUNCS[n.func.id](*args)
        raise PropError("unsupported node")

    def __call__(self, t: float, d: float = 1.0) -> float:
        try:
            return float(self._ev(self.tree, {"t": t, "d": d}))
        except (ZeroDivisionError, ValueError, OverflowError, TypeError):
            return 0.0


# ---------------------------------------------------------------- property classes
class Prop:
    """Callable: prop(t, d) -> float. `t` = seconds since the layer/scene started, `d` = its duration."""
    constant = False

    def __init__(self, spec: Any = None, default: float = 0.0, name: str = "property"):
        self.name, self.default = name, float(default)
        self._wig = None
        self._loop = None
        self._f = self._build(spec)

    # -- construction
    def _build(self, spec):
        if spec is None:
            self.constant = True; return lambda t, d: self.default
        if isinstance(spec, bool):
            v = float(spec); self.constant = True; return lambda t, d: v
        if isinstance(spec, (int, float)):
            v = float(spec); self.constant = True; return lambda t, d: v
        if isinstance(spec, str):
            ex = Expr(spec); return lambda t, d: ex(t, d)
        if isinstance(spec, (list, tuple)):
            return self._keys(spec, None)
        if isinstance(spec, dict):
            if spec.get("wiggle"):
                w = spec["wiggle"]
                if not isinstance(w, dict):
                    raise PropError(f"{self.name}: 'wiggle' must be an object like {{freq, amp, seed}}")
                self._wig = (float(w.get("freq", 3)), float(w.get("amp", 1)), float(w.get("seed", 0)))
            self._loop = spec.get("loop")
            if self._loop not in (None, "repeat", "pingpong"):
                raise PropError(f"{self.name}: loop must be 'repeat' or 'pingpong'")
            if "keys" in spec:
                return self._keys(spec["keys"], spec.get("ease"))
            if "to" in spec:
                return self._tween(spec)
            if "expr" in spec:
                ex = Expr(str(spec["expr"])); return lambda t, d: ex(t, d)
            v = float(spec.get("value", spec.get("base", self.default)))
            if not self._wig and not self._loop:
                self.constant = True
            return lambda t, d: v
        raise PropError(f"{self.name}: unsupported value {spec!r}")

    def _keys(self, items, default_ease):
        keys = []
        for it in items:
            if isinstance(it, dict):
                if "t" not in it or "v" not in it:
                    raise PropError(f"{self.name}: keyframe dict needs 't' and 'v'")
                keys.append((float(it["t"]), float(it["v"]), it.get("ease", default_ease)))
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                keys.append((float(it[0]), float(it[1]), it[2] if len(it) > 2 else default_ease))
            else:
                raise PropError(f"{self.name}: keyframe must be [time, value] or [time, value, ease]; got {it!r}")
        if not keys:
            raise PropError(f"{self.name}: empty keyframe list")
        keys.sort(key=lambda k: k[0])
        for k in keys:
            if not _ease_ok(k[2]):
                raise PropError(f"{self.name}: unknown ease '{k[2]}'")
        if len(keys) == 1:
            self.constant = not (self._wig or self._loop)
            v = keys[0][1]; return lambda t, d: v
        T = keys[-1][0]

        def f(t, d):
            if self._loop and T > 0:
                if self._loop == "repeat":
                    t = t % T
                else:
                    m = t % (2 * T); t = m if m <= T else 2 * T - m
            if t <= keys[0][0]:
                return keys[0][1]
            if t >= T:
                return keys[-1][1]
            for a, b in zip(keys, keys[1:]):
                if a[0] <= t < b[0]:
                    k = (t - a[0]) / (b[0] - a[0])
                    return a[1] + (b[1] - a[1]) * _ease(a[2] if a[2] is not None else "in_out_cubic", k)
            return keys[-1][1]
        return f

    def _tween(self, s):
        a, b = float(s.get("from", self.default)), float(s["to"])
        start, dur = float(s.get("start", s.get("delay", 0))), max(1e-6, float(s.get("dur", 0.6)))
        e = s.get("ease", "out_cubic")
        if not _ease_ok(e):
            raise PropError(f"{self.name}: unknown ease '{e}'")

        def f(t, d):
            if self._loop:
                T = start + dur
                t = t % T if self._loop == "repeat" else (lambda m: m if m <= T else 2 * T - m)(t % (2 * T))
            return a + (b - a) * _ease(e, (t - start) / dur)
        return f

    # -- evaluation
    def __call__(self, t: float, d: float = 1.0) -> float:
        v = self._f(t, d)
        if self._wig:
            v += wiggle(t, *self._wig)
        return v


def check(spec: Any, name: str = "property") -> str | None:
    """Return an error message when `spec` is malformed, else None (used by --validate)."""
    try:
        Prop(spec, 0.0, name)
        return None
    except PropError as e:
        return str(e)
