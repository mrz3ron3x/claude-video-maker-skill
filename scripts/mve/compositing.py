"""Compositing primitives: sub-pixel affine placement, blend modes, blur / glow / shadow / shine.

All sprites are RGBA PIL images. Canvases are RGB (create mode, opaque) or RGBA (edit mode, transparent overlay).
Transforms run on premultiplied alpha ("RGBa") so edges never get dark fringes.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

BLENDS = ("normal", "add", "screen", "multiply")


def with_opacity(img: Image.Image, k: float) -> Image.Image:
    if k >= 0.999:
        return img
    lut = [int(i * max(0.0, k)) for i in range(256)]
    out = img.copy(); out.putalpha(img.getchannel("A").point(lut)); return out


def blur(img: Image.Image, radius: float) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius)) if radius >= 0.3 else img


def pad(img: Image.Image, p: int) -> Image.Image:
    if p <= 0:
        return img
    out = Image.new("RGBA", (img.width + 2 * p, img.height + 2 * p), (0, 0, 0, 0)); out.paste(img, (p, p)); return out


# ---------------------------------------------------------------- blending
def _blend_region(canvas: Image.Image, patch: Image.Image, x: int, y: int, mode: str) -> None:
    box = (x, y, x + patch.width, y + patch.height)
    base = canvas.crop(box)
    b = np.asarray(base.convert("RGB"), np.float32)
    s = np.asarray(patch.convert("RGB"), np.float32)
    a = np.asarray(patch.getchannel("A"), np.float32)[..., None] / 255.0
    if mode == "add":
        out = np.minimum(255.0, b + s * a)
    elif mode == "screen":
        out = 255.0 - (255.0 - b) * (255.0 - s * a) / 255.0
    else:  # multiply
        out = b * (1 - a) + (b * s / 255.0) * a
    res = Image.fromarray(out.astype(np.uint8), "RGB")
    if canvas.mode == "RGBA":
        al = np.maximum(np.asarray(base.getchannel("A"), np.float32), a[..., 0] * 255.0)
        res = res.convert("RGBA"); res.putalpha(Image.fromarray(al.astype(np.uint8), "L"))
    canvas.paste(res, (x, y))


def composite(canvas: Image.Image, patch: Image.Image, x: int, y: int, mode: str = "normal") -> None:
    """Draw `patch` (RGBA) with its top-left at (x, y), clipped to the canvas."""
    W, H = canvas.size; w, h = patch.size
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    if (x0, y0, x1, y1) != (x, y, x + w, y + h):
        patch = patch.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    if mode in ("add", "screen", "multiply"):
        _blend_region(canvas, patch, x0, y0, mode)
    elif canvas.mode == "RGB":
        canvas.paste(patch, (x0, y0), patch)
    else:
        canvas.alpha_composite(patch, (x0, y0))


# ---------------------------------------------------------------- placement
def place(canvas: Image.Image, sprite: Image.Image | None, cx: float, cy: float, sx: float = 1.0, sy: float = 1.0,
          rot: float = 0.0, opacity: float = 1.0, anchor: tuple[float, float] | None = None, blend: str = "normal",
          clip: tuple[int, int, int, int] | None = None) -> None:
    """Place `sprite` so that its anchor point (px inside the sprite; default centre) lands on canvas (cx, cy),
    scaled by (sx, sy) and rotated `rot` degrees clockwise about the anchor."""
    if sprite is None or opacity <= 0.003 or abs(sx) < 1e-3 or abs(sy) < 1e-3:
        return
    w, h = sprite.size
    ax, ay = anchor if anchor is not None else (w / 2, h / 2)
    W, H = canvas.size
    th = math.radians(rot); c, s = math.cos(th), math.sin(th)
    # forward corners -> bounding box on the canvas
    xs, ys = [], []
    for px, py in ((0, 0), (w, 0), (0, h), (w, h)):
        vx, vy = (px - ax) * sx, (py - ay) * sy
        xs.append(cx + c * vx - s * vy); ys.append(cy + s * vx + c * vy)
    bx0, by0, bx1, by1 = math.floor(min(xs)) - 1, math.floor(min(ys)) - 1, math.ceil(max(xs)) + 1, math.ceil(max(ys)) + 1
    lim = (0, 0, W, H) if clip is None else (max(0, clip[0]), max(0, clip[1]), min(W, clip[2]), min(H, clip[3]))
    rx0, ry0, rx1, ry1 = max(bx0, lim[0]), max(by0, lim[1]), min(bx1, lim[2]), min(by1, lim[3])
    if rx1 <= rx0 or ry1 <= ry0:
        return
    identity = abs(sx - 1) < 1e-4 and abs(sy - 1) < 1e-4 and abs(rot) < 1e-3
    if identity and abs(cx - ax - round(cx - ax)) < 0.02 and abs(cy - ay - round(cy - ay)) < 0.02:
        patch = with_opacity(sprite, opacity)
        px0, py0 = round(cx - ax), round(cy - ay)
        if clip is not None:
            ix0, iy0, ix1, iy1 = max(px0, lim[0]), max(py0, lim[1]), min(px0 + w, lim[2]), min(py0 + h, lim[3])
            if ix1 <= ix0 or iy1 <= iy0:
                return
            patch = patch.crop((ix0 - px0, iy0 - py0, ix1 - px0, iy1 - py0)); px0, py0 = ix0, iy0
        composite(canvas, patch, px0, py0, blend); return
    # inverse affine: q = anchor + M^-1 (P - pos),  M = R(rot) S(sx, sy)
    m00, m01 = c / sx, s / sx          # M^-1 = S^-1 R(-rot)
    m10, m11 = -s / sy, c / sy
    ox, oy = rx0 - cx, ry0 - cy
    coeffs = (m00, m01, ax + m00 * ox + m01 * oy, m10, m11, ay + m10 * ox + m11 * oy)
    res = Image.BICUBIC if (sx > 0.5 and sy > 0.5) else Image.BILINEAR
    patch = sprite.convert("RGBa").transform((rx1 - rx0, ry1 - ry0), Image.AFFINE, coeffs, res).convert("RGBA")
    composite(canvas, with_opacity(patch, opacity), rx0, ry0, blend)


# ---------------------------------------------------------------- sprite effects (return NEW sprite, padded equally)
def glow(sprite: Image.Image, radius: float, intensity: float = 1.0, color: tuple | None = None) -> tuple[Image.Image, int]:
    """Soft halo behind the sprite. Returns (sprite, pad) - pad is the extra px added on every side."""
    p = int(radius * 2.5) + 2
    base = pad(sprite, p)
    halo = blur(base, radius)
    if color is not None:
        solid = Image.new("RGBA", halo.size, (*color[:3], 255)); solid.putalpha(halo.getchannel("A")); halo = solid
    a = halo.getchannel("A").point(lambda v: min(255, int(v * (1.0 + 1.5 * intensity))))
    halo.putalpha(a)
    out = Image.new("RGBA", base.size, (0, 0, 0, 0)); out.alpha_composite(halo); out.alpha_composite(base)
    return out, p


def shadow(sprite: Image.Image, dx: float, dy: float, radius: float, alpha: float = 0.5,
           color: tuple = (0, 0, 0)) -> tuple[Image.Image, int]:
    p = int(radius * 2.5 + max(abs(dx), abs(dy))) + 2
    base = pad(sprite, p)
    sh = Image.new("RGBA", base.size, (*color[:3], 255)); sh.putalpha(base.getchannel("A").point(lambda v: int(v * alpha)))
    sh = blur(sh, radius)
    out = Image.new("RGBA", base.size, (0, 0, 0, 0)); out.alpha_composite(sh, (int(dx), int(dy)))
    out.alpha_composite(base)
    return out, p


def shine(sprite: Image.Image, k: float, width: float = 0.22, strength: float = 0.85, angle: float = 20.0) -> Image.Image:
    """Light sweep across the sprite (k 0..1 moves the highlight band), masked by the sprite alpha."""
    if k <= 0 or k >= 1:
        return sprite
    w, h = sprite.size
    th = math.radians(angle)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    span = w * math.cos(th) + h * math.sin(th)
    u = (xx * math.cos(th) + yy * math.sin(th)) / max(span, 1.0)
    center = -width + k * (1 + 2 * width)
    band = np.clip(1 - np.abs(u - center) / width, 0, 1) ** 2 * strength
    arr = np.asarray(sprite, np.float32).copy()
    a = arr[..., 3:4] / 255.0
    arr[..., :3] = np.minimum(255.0, arr[..., :3] + 255.0 * band[..., None] * a)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def vgradient(w: int, h: int, c1: tuple, c2: tuple, angle: float = 90.0) -> Image.Image:
    th = math.radians(angle)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = (xx * math.cos(th) + yy * math.sin(th))
    u = (u - u.min()) / max(1e-6, u.max() - u.min())
    arr = np.array(c1[:3], np.float32) * (1 - u[..., None]) + np.array(c2[:3], np.float32) * u[..., None]
    return Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
