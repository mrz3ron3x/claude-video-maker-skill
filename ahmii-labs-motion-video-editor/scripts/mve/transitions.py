"""Full-frame scene transitions: blend(prev, cur, k) for k in 0..1, both frames are complete RGB renders.

push, push_up, whip (push + motion blur), zoom (punch-through), spin, circle (iris), barn (doors open),
flash, dip (to black), glitch, blur, pixelate, rgb (chromatic cross-fade).
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .easing import ease

SOUND = {  # sfx name that fits each transition (used by the auto-sfx track)
    "push": "swish", "push_up": "swish", "whip": "whoosh", "zoom": "whoosh", "spin": "whoosh", "circle": "swish",
    "barn": "swish", "flash": "impact", "dip": "swish", "glitch": "glitch", "blur": "swish", "pixelate": "glitch", "rgb": "glitch",
}
FULL_TRANSITIONS = tuple(sorted(SOUND))


def _zoom(img: Image.Image, z: float, rot: float = 0.0) -> Image.Image:
    W, H = img.size
    th = math.radians(rot); c, s = math.cos(th), math.sin(th)
    m00, m01, m10, m11 = c / z, s / z, -s / z, c / z
    return img.transform((W, H), Image.AFFINE, (m00, m01, W / 2 - (m00 * W / 2 + m01 * H / 2), m10, m11, H / 2 - (m10 * W / 2 + m11 * H / 2)),
                         Image.BILINEAR)


def _dir_blur(img: Image.Image, px: float, horizontal: bool = True) -> Image.Image:
    """Cheap directional (motion) blur: average of shifted copies."""
    n = int(min(14, max(2, px / 4)))
    if px < 2:
        return img
    arr = np.asarray(img, np.float32); acc = np.zeros_like(arr)
    for i in range(n):
        sh = int((i / (n - 1) - 0.5) * px)
        acc += np.roll(arr, sh, axis=1 if horizontal else 0)
    return Image.fromarray((acc / n).astype(np.uint8), "RGB")


def _mask_paste(prev: Image.Image, cur: Image.Image, mask: Image.Image) -> Image.Image:
    out = prev.copy(); out.paste(cur, (0, 0), mask); return out


def push(prev, cur, k, **_):
    W, H = prev.size; e = ease("in_out_cubic", k)
    c = Image.new("RGB", (W, H)); c.paste(prev, (int(-e * W), 0)); c.paste(cur, (int((1 - e) * W), 0)); return c


def push_up(prev, cur, k, **_):
    W, H = prev.size; e = ease("in_out_cubic", k)
    c = Image.new("RGB", (W, H)); c.paste(prev, (0, int(-e * H))); c.paste(cur, (0, int((1 - e) * H))); return c


def whip(prev, cur, k, S=1.0, **_):
    W, H = prev.size; e = ease("in_out_expo", k)
    c = Image.new("RGB", (W, H)); c.paste(prev, (int(-e * W), 0)); c.paste(cur, (int((1 - e) * W), 0))
    return _dir_blur(c, math.sin(math.pi * k) * W * 0.09)


def zoom(prev, cur, k, **_):
    e = ease("in_out_cubic", k)
    a = _zoom(prev, 1 + 0.45 * e); b = _zoom(cur, 1 + 0.35 * (1 - e))
    return Image.blend(a, b, ease("in_out_sine", k))


def spin(prev, cur, k, **_):
    e = ease("in_out_cubic", k)
    a = _zoom(prev, 1 + 0.85 * e, -22 * e); b = _zoom(cur, 1 + 0.85 * (1 - e), 22 * (1 - e))
    return Image.blend(a, b, ease("in_out_sine", k))


def circle(prev, cur, k, **_):
    W, H = prev.size; e = ease("in_out_cubic", k); r = e * math.hypot(W, H) / 2 * 1.02
    ss = 2; m = Image.new("L", (W * ss // 2, H * ss // 2), 0)
    ImageDraw.Draw(m).ellipse([W * ss / 4 - r * ss / 2, H * ss / 4 - r * ss / 2, W * ss / 4 + r * ss / 2, H * ss / 4 + r * ss / 2], fill=255)
    return _mask_paste(prev, cur, m.resize((W, H), Image.LANCZOS))


def barn(prev, cur, k, **_):
    W, H = prev.size; e = ease("in_out_cubic", k); half = int(e * W / 2)
    m = Image.new("L", (W, H), 0); ImageDraw.Draw(m).rectangle([W // 2 - half, 0, W // 2 + half, H], fill=255)
    return _mask_paste(prev, cur, m)


def flash(prev, cur, k, **_):
    white = Image.new("RGB", prev.size, (255, 255, 255))
    return Image.blend(prev, white, ease("in_cubic", k * 2)) if k < 0.5 else Image.blend(white, cur, ease("out_cubic", (k - 0.5) * 2))


def dip(prev, cur, k, **_):
    black = Image.new("RGB", prev.size, (0, 0, 0))
    return Image.blend(prev, black, ease("in_out_sine", k * 2)) if k < 0.5 else Image.blend(black, cur, ease("in_out_sine", (k - 0.5) * 2))


def glitch(prev, cur, k, tick=0, **_):
    W, H = prev.size; rng = np.random.default_rng(int(tick) * 31 + 5)
    a, b = np.asarray(prev), np.asarray(cur); out = a.copy(); amp = math.sin(math.pi * k)
    n = 14; edges = np.sort(rng.integers(0, H, n)); edges = np.concatenate([[0], edges, [H]])
    for y0, y1 in zip(edges, edges[1:]):
        if y1 <= y0:
            continue
        src = b if rng.random() < ease("in_out_sine", k) else a
        sh = int(rng.uniform(-0.12, 0.12) * W * amp)
        out[y0:y1] = np.roll(src[y0:y1], sh, axis=1)
    img = Image.fromarray(out, "RGB")
    arr = np.asarray(img).copy(); d = max(1, int(amp * W * 0.012))
    arr[..., 0] = np.roll(arr[..., 0], d, axis=1); arr[..., 2] = np.roll(arr[..., 2], -d, axis=1)
    return Image.fromarray(arr, "RGB")


def blur(prev, cur, k, S=1.0, **_):
    W, H = prev.size; r = math.sin(math.pi * k) * 26 * S; sm = (max(8, W // 4), max(8, H // 4))
    f = lambda im: im.resize(sm, Image.BILINEAR).filter(ImageFilter.GaussianBlur(r / 4)).resize((W, H), Image.BILINEAR)  # noqa: E731
    return Image.blend(f(prev) if r > 0.5 else prev, f(cur) if r > 0.5 else cur, ease("in_out_sine", k))


def pixelate(prev, cur, k, **_):
    W, H = prev.size; amp = math.sin(math.pi * k); size = max(1, int(amp * W / 18))
    mix = Image.blend(prev, cur, 1.0 if k > 0.5 else 0.0)
    return mix if size <= 1 else mix.resize((max(1, W // size), max(1, H // size)), Image.BILINEAR).resize((W, H), Image.NEAREST)


def rgb(prev, cur, k, S=1.0, **_):
    mix = np.asarray(Image.blend(prev, cur, ease("in_out_sine", k)), np.float32); d = int(math.sin(math.pi * k) * 42 * S)
    if d:
        mix[..., 0] = np.roll(mix[..., 0], d, axis=1); mix[..., 2] = np.roll(mix[..., 2], -d, axis=1)
    return Image.fromarray(mix.astype(np.uint8), "RGB")


_FN = {"push": push, "push_up": push_up, "whip": whip, "zoom": zoom, "spin": spin, "circle": circle, "barn": barn, "flash": flash,
       "dip": dip, "glitch": glitch, "blur": blur, "pixelate": pixelate, "rgb": rgb}


def blend(kind: str, prev: Image.Image, cur: Image.Image, k: float, S: float = 1.0, tick: int = 0) -> Image.Image:
    return _FN[kind](prev, cur, max(0.0, min(1.0, k)), S=S, tick=tick)
