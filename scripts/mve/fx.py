"""Frame effects and the camera rig.

Scene spec:
  "fx": [ {"type": "glow", "amount": 0.7}, {"type": "rgb_split", "start": 1.0, "dur": 0.3, "amount": 8}, ... ]
  "camera": "push_in" | {"zoom": ..., "pan_x": ..., "pan_y": ..., "rotate": ...}      (any property = Prop syntax)

Common fx keys: start (s, default 0), dur (s, default = until the scene ends), attack / release (s, soft edges),
pulse (true = one-shot impulse at `start` that decays over `decay` s), amount (a Prop: keyframes etc.).
Works in CREATE mode on the whole frame and in EDIT mode on the overlay graphics (vignette / grain / flash / tint /
scanlines also darken or tint the video underneath, because they are drawn as translucent overlays).
"""
from __future__ import annotations

import difflib
import math

import numpy as np
from PIL import Image, ImageFilter

from .easing import clamp01
from .props import Prop, check as check_prop, noise

CAMERA_FX = {"shake", "zoom_punch", "pulse"}
PRE_FX = {"glow", "bloom", "blur", "pixelate"}
POST_FX = {"rgb_split", "glitch", "vignette", "grain", "scanlines", "flash", "tint", "light_leak"}
FX_TYPES = sorted(CAMERA_FX | PRE_FX | POST_FX)
CAMERA_PRESETS = {
    "push_in": dict(zoom="1+0.08*smooth(0,d,t)"),
    "pull_out": dict(zoom="1.08-0.08*smooth(0,d,t)"),
    "pan_left": dict(zoom=1.08, pan_x="0.03-0.06*smooth(0,d,t)"),
    "pan_right": dict(zoom=1.08, pan_x="-0.03+0.06*smooth(0,d,t)"),
    "drift": dict(zoom=1.07, pan_x="wiggle(0.35,0.012,1)", pan_y="wiggle(0.3,0.008,2)"),
    "handheld": dict(zoom=1.05, pan_x="wiggle(1.7,0.006,3)", pan_y="wiggle(1.5,0.006,4)", rotate="wiggle(1.2,0.35,5)"),
    "dutch": dict(zoom=1.12, rotate=4),
    "tilt_up": dict(zoom=1.08, pan_y="0.03-0.06*smooth(0,d,t)"),
}


# ---------------------------------------------------------------- helpers (work for RGB canvases and RGBA overlays)
def _rgb(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), np.float32)


def _from_rgb(src: Image.Image, arr: np.ndarray, extra_alpha: np.ndarray | None = None) -> Image.Image:
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    if src.mode != "RGBA":
        return out
    out = out.convert("RGBA")
    a = np.asarray(src.getchannel("A"), np.float32)
    if extra_alpha is not None:
        a = np.maximum(a, extra_alpha)
    out.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L"))
    return out


def _overlay_color(img: Image.Image, color, alpha) -> Image.Image:
    """Draw a translucent colour (scalar or per-pixel alpha 0..1) over the frame - done with PIL's C routines."""
    col = tuple(int(c) for c in color[:3])
    if np.isscalar(alpha):
        a = float(alpha)
        if a <= 0.002:
            return img
        if img.mode == "RGB":
            return Image.blend(img, Image.new("RGB", img.size, col), min(1.0, a))
        mask = Image.new("L", img.size, int(min(1.0, a) * 255))
    else:
        mask = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), "L")
    if img.mode == "RGB":
        out = img.copy(); out.paste(col, (0, 0), mask); return out
    ov = Image.new("RGBA", img.size, (*col, 255)); ov.putalpha(mask)
    out = img.copy(); out.alpha_composite(ov); return out


def _overlay_mask(img: Image.Image, color, mask: Image.Image) -> Image.Image:
    col = tuple(int(c) for c in color[:3])
    if img.mode == "RGB":
        out = img.copy(); out.paste(col, (0, 0), mask); return out
    ov = Image.new("RGBA", img.size, (*col, 255)); ov.putalpha(mask)
    out = img.copy(); out.alpha_composite(ov); return out


def _mask_gain(mask: Image.Image, gain: float) -> Image.Image:
    return mask.point([min(255, int(i * gain)) for i in range(256)])


def _overlay_signed(img: Image.Image, delta: np.ndarray) -> Image.Image:
    """delta in [-1, 1]: positive lightens (white), negative darkens (black)."""
    out = _overlay_color(img, (255, 255, 255), np.clip(delta, 0, 1))
    return _overlay_color(out, (0, 0, 0), np.clip(-delta, 0, 1))


def _screen_img(img: Image.Image, light: Image.Image) -> Image.Image:
    """Screen-blend an RGB light image (any size) onto the frame - PIL C routines, fast at 4K."""
    from PIL import ImageChops
    if light.size != img.size:
        light = light.resize(img.size, Image.BILINEAR)
    if img.mode == "RGB":
        return ImageChops.screen(img, light)
    ov = light.convert("RGBA"); ov.putalpha(light.convert("L"))
    out = img.copy(); out.alpha_composite(ov); return out


def _scale_rgb(img: Image.Image, gain: float) -> Image.Image:
    lut = [min(255, int(i * gain)) for i in range(256)]
    return img.point(lut * len(img.getbands()))


# ---------------------------------------------------------------- pixel effects
def fx_glow(img, k, p, S, **_):
    thr = float(p.get("threshold", 0.55)); rad = max(2.0, float(p.get("radius", 22)) * S)
    W, H = img.size; f = 4 if W * H <= 2_100_000 else 8                    # blur at 1/4 (1/8 at 4K): glow is soft anyway
    small = img.convert("RGB").resize((max(8, W // f), max(8, H // f)), Image.BILINEAR)
    lut = [min(255, int(max(0.0, i / 255.0 - thr) / max(1e-3, 1 - thr) * i)) for i in range(256)]
    bright = small.point(lut * 3).filter(ImageFilter.GaussianBlur(rad / f))
    return _screen_img(img, _scale_rgb(bright, 1.6 * k * float(p.get("intensity", 1.0))))


def fx_blur(img, k, p, S, **_):
    r = float(p.get("radius", 14)) * S * k
    if r < 0.4:
        return img
    if img.mode == "RGBA":
        return img.convert("RGBa").filter(ImageFilter.GaussianBlur(r)).convert("RGBA")
    return img.filter(ImageFilter.GaussianBlur(r))


def fx_pixelate(img, k, p, S, **_):
    size = max(1, int(float(p.get("size", 28)) * S * k))
    if size <= 1:
        return img
    W, H = img.size
    return img.resize((max(1, W // size), max(1, H // size)), Image.BILINEAR).resize((W, H), Image.NEAREST)


def fx_rgb_split(img, k, p, S, **_):
    amt = float(p.get("distance", 7)) * S * k
    if amt < 0.5:
        return img
    ang = math.radians(float(p.get("angle", 0))); dx, dy = int(round(math.cos(ang) * amt)), int(round(math.sin(ang) * amt))
    arr = _rgb(img); out = arr.copy()
    out[..., 0] = np.roll(arr[..., 0], (dy, dx), (0, 1)); out[..., 2] = np.roll(arr[..., 2], (-dy, -dx), (0, 1))
    extra = None
    if img.mode == "RGBA":
        a = np.asarray(img.getchannel("A"), np.float32)
        extra = np.maximum(np.roll(a, (dy, dx), (0, 1)), np.roll(a, (-dy, -dx), (0, 1)))
    return _from_rgb(img, out, extra)


def fx_glitch(img, k, p, S, tick=0, **_):
    W, H = img.size; rng = np.random.default_rng(int(tick // 2) * 104729 + 7)
    arr = np.asarray(img.convert("RGBA") if img.mode == "RGBA" else img.convert("RGB")).copy()
    n = int(4 + 10 * min(1.0, k))
    for _ in range(n):
        y = int(rng.integers(0, H)); h = int(rng.integers(max(2, H // 90), max(4, H // 14)))
        sh = int(rng.integers(-1, 2) * rng.uniform(0.02, 0.13) * W * k)
        arr[y:y + h] = np.roll(arr[y:y + h], sh, axis=1)
    out = Image.fromarray(arr, img.mode if img.mode in ("RGB", "RGBA") else "RGB")
    return fx_rgb_split(out, 1.0, dict(distance=float(p.get("distance", 9)) * k, angle=0), S)


_VIG: dict = {}


def fx_vignette(img, k, p, S, **_):
    W, H = img.size; key = (W, H, float(p.get("softness", 0.65)))
    if key not in _VIG:
        w, h = max(64, W // 4), max(64, H // 4)                       # smooth falloff: compute small, scale up once
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / 1.4142
        m = np.clip((r - (1 - key[2]) * 0.9) / key[2], 0, 1) ** 1.7
        _VIG[key] = Image.fromarray((m * 255).astype(np.uint8), "L").resize((W, H), Image.BILINEAR)
    return _overlay_mask(img, (0, 0, 0), _mask_gain(_VIG[key], float(p.get("strength", 0.6)) * k))


def fx_grain(img, k, p, S, tick=0, **_):
    W, H = img.size; rng = np.random.default_rng(tick * 7919 + 3); g = max(2, int(round(2 * S)))   # grain size follows resolution
    res = Image.NEAREST if g <= 2 else Image.BILINEAR               # bigger grain cells are smoothed (no mosaic at 1080p/4K)
    gain = 1.0 if g <= 2 else 1.5                                     # bilinear softening lowers contrast: compensate
    n = np.clip(rng.standard_normal((max(2, H // g), max(2, W // g))).astype(np.float32) * (float(p.get("strength", 0.045)) * k * gain), -1, 1)
    pos = Image.fromarray((np.clip(n, 0, 1) * 255).astype(np.uint8), "L").resize((W, H), res)
    neg = Image.fromarray((np.clip(-n, 0, 1) * 255).astype(np.uint8), "L").resize((W, H), res)
    return _overlay_mask(_overlay_mask(img, (255, 255, 255), pos), (0, 0, 0), neg)


_SCAN: dict = {}


def fx_scanlines(img, k, p, S, **_):
    W, H = img.size; gap = max(2, int(3 * S)); key = (W, H, gap)
    if key not in _SCAN:
        m = np.zeros((H, W), np.uint8); m[::gap] = 255; _SCAN[key] = Image.fromarray(m, "L")
    return _overlay_mask(img, (0, 0, 0), _mask_gain(_SCAN[key], float(p.get("strength", 0.35)) * k))


def fx_flash(img, k, p, S, **_):
    from .layers import parse_color
    col = parse_color(p.get("color"), {"accent": (0, 229, 255), "text": (255, 255, 255), "muted": (200, 200, 200)}, (255, 255, 255))
    return _overlay_color(img, col, clamp01(k * float(p.get("strength", 1.0))))


def fx_tint(img, k, p, S, **_):
    from .layers import parse_color
    col = parse_color(p.get("color"), {"accent": (0, 229, 255), "text": (255, 255, 255), "muted": (200, 200, 200)}, (255, 120, 40))
    return _overlay_color(img, col, clamp01(float(p.get("strength", 0.25)) * k))


def fx_light_leak(img, k, p, S, tl=0.0, **_):
    W, H = img.size; w, h = max(16, W // 8), max(16, H // 8)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32); field = np.zeros((h, w), np.float32)
    for i in range(3):
        cx = w * (0.5 + 0.55 * math.sin(tl * 0.35 + i * 2.1)); cy = h * (0.5 + 0.5 * math.cos(tl * 0.28 + i * 1.3)); r = w * (0.28 + 0.07 * i)
        field += np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * r * r)))
    light = np.clip(field, 0, 1)[..., None] * np.array([255, 140, 70], np.float32) * float(p.get("strength", 0.55)) * k
    return _screen_img(img, Image.fromarray(np.clip(light, 0, 255).astype(np.uint8), "RGB"))


PIXEL_FX = {"glow": fx_glow, "bloom": fx_glow, "blur": fx_blur, "pixelate": fx_pixelate, "rgb_split": fx_rgb_split,
            "glitch": fx_glitch, "vignette": fx_vignette, "grain": fx_grain, "scanlines": fx_scanlines, "flash": fx_flash,
            "tint": fx_tint, "light_leak": fx_light_leak}


# ---------------------------------------------------------------- envelope
class Envelope:
    def __init__(self, spec: dict):
        self.start = float(spec.get("start", 0)); self.dur = spec.get("dur")
        self.attack, self.release = float(spec.get("attack", 0)), float(spec.get("release", 0))
        self.pulse, self.decay = bool(spec.get("pulse", False)), max(0.05, float(spec.get("decay", 0.45)))

    def __call__(self, tl: float, d: float) -> float:
        if tl < self.start:
            return 0.0
        if self.pulse:
            return math.exp(-(tl - self.start) / self.decay * 3.0)
        end = self.start + float(self.dur) if self.dur is not None else float("inf")
        if tl > end:
            return 0.0
        v = 1.0
        if self.attack > 0:
            v *= clamp01((tl - self.start) / self.attack)
        if self.release > 0 and end != float("inf"):
            v *= clamp01((end - tl) / self.release)
        return v


class FxItem:
    def __init__(self, spec: dict):
        self.type = spec["type"]; self.spec = spec; self.env = Envelope(spec)
        self.amount = Prop(spec.get("amount"), 1.0, "amount")

    def k(self, tl, d):
        return self.env(tl, d) * self.amount(tl - self.env.start, d)


# ---------------------------------------------------------------- camera
class Camera:
    def __init__(self, spec):
        if isinstance(spec, str):
            spec = CAMERA_PRESETS.get(spec, {})
        spec = spec or {}
        self.zoom, self.px, self.py, self.rot = (Prop(spec.get("zoom"), 1.0, "camera.zoom"), Prop(spec.get("pan_x"), 0.0, "camera.pan_x"),
                                                 Prop(spec.get("pan_y"), 0.0, "camera.pan_y"), Prop(spec.get("rotate"), 0.0, "camera.rotate"))
        self.active = bool(spec)

    def at(self, tl, d):
        return self.zoom(tl, d), self.px(tl, d), self.py(tl, d), self.rot(tl, d)


def camera_transform(img: Image.Image, zoom: float, panx: float, pany: float, rot: float, cover: bool) -> Image.Image:
    W, H = img.size
    if abs(zoom - 1) < 1e-4 and abs(panx) < 1e-5 and abs(pany) < 1e-5 and abs(rot) < 1e-3:
        return img
    if cover:  # RGB canvases: never reveal the edge
        th = math.radians(abs(rot)); need = math.cos(th) + max(W / H, H / W) * math.sin(th)
        zoom = max(zoom, need * (1 + 2 * max(abs(panx), abs(pany))), 1.0)
    cx, cy = W / 2 + panx * W, H / 2 + pany * H
    if abs(rot) < 1e-3:                                   # pure zoom/pan: one crop+resize is ~5x faster than an affine warp
        box = (W / 2 - cx / zoom, H / 2 - cy / zoom, (W - cx) / zoom + W / 2, (H - cy) / zoom + H / 2)
        if box[0] >= -1e-6 and box[1] >= -1e-6 and box[2] <= W + 1e-6 and box[3] <= H + 1e-6:
            src = img.convert("RGBa") if img.mode == "RGBA" else img
            out = src.resize((W, H), Image.BILINEAR if W * H > 2_100_000 else Image.BICUBIC, box=box)
            return out.convert("RGBA") if img.mode == "RGBA" else out
    th = math.radians(rot); c, s = math.cos(th), math.sin(th)
    m00, m01, m10, m11 = c / zoom, s / zoom, -s / zoom, c / zoom
    coeffs = (m00, m01, W / 2 - (m00 * cx + m01 * cy), m10, m11, H / 2 - (m10 * cx + m11 * cy))
    if img.mode == "RGBA":
        return img.convert("RGBa").transform((W, H), Image.AFFINE, coeffs, Image.BILINEAR).convert("RGBA")
    return img.transform((W, H), Image.AFFINE, coeffs, Image.BILINEAR)


class FxStack:
    def __init__(self, fx_specs, camera_spec, ctx):
        self.S = ctx.S
        self.items = [FxItem(s) for s in (fx_specs or []) if isinstance(s, dict) and s.get("type") in FX_TYPES]
        self.camera = Camera(camera_spec)
        self.active = bool(self.items) or self.camera.active

    def apply(self, img: Image.Image, tl: float, d: float, tick: int = 0) -> Image.Image:
        S = self.S
        zoom, panx, pany, rot = self.camera.at(tl, d) if self.camera.active else (1.0, 0.0, 0.0, 0.0)
        post = []
        for it in self.items:
            k = it.k(tl, d)
            if k <= 0.002:
                continue
            t, sp = it.type, it.spec
            if t == "shake":
                amt = float(sp.get("distance", 14)) * S * k; f = float(sp.get("freq", 18))
                panx += noise(tl * f, 11) * amt / img.width; pany += noise(tl * f, 23) * amt / img.height
                rot += noise(tl * f, 37) * float(sp.get("roll", 0.6)) * k
            elif t == "zoom_punch":
                zoom *= 1 + float(sp.get("zoom", 0.12)) * k
            elif t == "pulse":
                bpm = float(sp.get("bpm", 120)); ph = (tl * bpm / 60.0) % 1.0
                zoom *= 1 + float(sp.get("zoom", 0.035)) * k * math.exp(-ph * 5)
            elif t in PRE_FX:
                img = PIXEL_FX[t](img, k, sp, S, tick=tick, tl=tl)
            else:
                post.append((t, k, sp))
        img = camera_transform(img, zoom, panx, pany, rot, cover=(img.mode == "RGB"))
        for t, k, sp in post:
            img = PIXEL_FX[t](img, k, sp, S, tick=tick, tl=tl)
        return img


# ---------------------------------------------------------------- validation
def validate_fx(fx, camera, tag: str, errs: list, warns: list) -> None:
    if fx is not None and not isinstance(fx, list):
        errs.append(f"{tag}: 'fx' must be a list"); return
    for i, sp in enumerate(fx or []):
        t = sp.get("type") if isinstance(sp, dict) else None
        if t not in FX_TYPES:
            m = difflib.get_close_matches(str(t), FX_TYPES, 1)
            errs.append(f"{tag} fx {i + 1}: unknown type '{t}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(FX_TYPES)})")); continue
        if (m := check_prop(sp.get("amount"), f"{tag} fx {i + 1}.amount")):
            errs.append(m)
    if isinstance(camera, str) and camera not in CAMERA_PRESETS:
        m = difflib.get_close_matches(camera, CAMERA_PRESETS, 1)
        errs.append(f"{tag}: unknown camera preset '{camera}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(CAMERA_PRESETS)})"))
    elif isinstance(camera, dict):
        for key in ("zoom", "pan_x", "pan_y", "rotate"):
            if key in camera and (m := check_prop(camera[key], f"{tag} camera.{key}")):
                errs.append(m)
