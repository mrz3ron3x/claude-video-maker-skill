"""The layer system - After Effects style compositions.

A `motion` scene holds a list of layers (first = bottom, last = top). Every layer has the same transform stack:
  x, y            position as a fraction of the frame (0.5 = centre)        dx, dy  extra offset in px (at 720p)
  scale, scale_x, scale_y, rotation (deg, clockwise), opacity (0..1), blur (px), glow (0..2), anchor [ax, ay] (0..1)
  start, end|duration   when the layer exists (scene seconds)               parent   id of another layer or null
  enter / exit    preset name (see presets.py) + enter_dur / exit_dur       fade_in / fade_out  seconds
  blend           normal | add | screen | multiply                          shadow   true | {dx,dy,blur,alpha}
  shine           {"start": 0.8, "dur": 0.9}  light sweep                   loop     wave|float|pulse|jitter|sway|breathe
Any numeric property accepts the full Prop syntax (keyframes, tween, expression, wiggle) - see props.py.

Types: text, counter, shape, image, particles, solid, null (invisible; use as a parent).
With `parent`, x/y are ignored and dx/dy are relative to the parent, scaled + rotated by its transform.
"""
from __future__ import annotations

import difflib
import math
import os
from dataclasses import dataclass

from PIL import Image, ImageDraw

from . import presets as P
from .compositing import BLENDS, blur as _blur, glow as _glow, pad as _pad, place, shadow as _shadow, shine as _shine, vgradient
from .easing import ease
from .props import Prop, PropError, check as check_prop
from .textkit import TextBlock

NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (239, 68, 68), "green": (34, 197, 94), "blue": (59, 130, 246),
         "yellow": (250, 204, 21), "orange": (249, 115, 22), "pink": (236, 72, 153), "purple": (168, 85, 247),
         "cyan": (6, 182, 212), "gold": (245, 197, 66), "gray": (156, 163, 175)}


def parse_color(v, theme: dict, default=(255, 255, 255)) -> tuple:
    if v is None:
        return tuple(default)
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return tuple(int(max(0, min(255, c))) for c in v[:3])
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("accent", "text", "muted"):
            return tuple(theme[s])
        if s in NAMED:
            return NAMED[s]
        if s.startswith("#") and len(s) in (4, 7):
            s = s[1:]
            if len(s) == 3:
                s = "".join(c * 2 for c in s)
            try:
                return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
    return tuple(default)


def color_ok(v) -> bool:
    if v is None or (isinstance(v, (list, tuple)) and len(v) >= 3):
        return True
    return isinstance(v, str) and (v.lower() in ("accent", "text", "muted", *NAMED) or v.startswith("#"))


@dataclass
class World:
    x: float = 0.0
    y: float = 0.0
    sx: float = 1.0
    sy: float = 1.0
    rot: float = 0.0
    alpha: float = 1.0
    blur: float = 0.0


# ======================================================================= base layer
class Layer:
    kind = "base"
    renders_sprite = True

    def __init__(self, spec: dict, ctx, index: int):
        self.spec, self.ctx, self.S = spec, ctx, ctx.S
        self.id = str(spec.get("id") or f"{self.kind}{index}")
        self.parent_id = spec.get("parent")
        self.start = float(spec.get("start", 0))
        end = spec.get("end")
        if end is None and spec.get("duration") is not None:
            end = self.start + float(spec["duration"])
        self.end = None if end is None else float(end)
        pr = lambda key, default: Prop(spec.get(key), default, key)  # noqa: E731
        self.px, self.py = pr("x", 0.5), pr("y", 0.5)
        self.pdx, self.pdy = pr("dx", 0), pr("dy", 0)
        self.pscale, self.psx, self.psy = pr("scale", 1), pr("scale_x", 1), pr("scale_y", 1)
        self.prot, self.popacity, self.pblur, self.pglow = pr("rotation", 0), pr("opacity", 1), pr("blur", 0), pr("glow", 0)
        self.anchor = tuple(spec["anchor"]) if spec.get("anchor") else None
        self.blend = spec.get("blend", "normal") if spec.get("blend", "normal") in BLENDS else "normal"
        self.enter, self.exit = spec.get("enter"), spec.get("exit")
        self.enter_dur, self.exit_dur = float(spec.get("enter_dur", 0.6)), float(spec.get("exit_dur", 0.4))
        self.fade_in, self.fade_out = float(spec.get("fade_in", 0)), float(spec.get("fade_out", 0))
        self.loop = spec.get("loop") if spec.get("loop") in P.LOOPS else None
        sh = spec.get("shadow")
        self.shadow = None if not sh else ({} if sh is True else dict(sh))
        self.shine_spec = spec.get("shine") if isinstance(spec.get("shine"), dict) else None
        self.glow_color = parse_color(spec["glow_color"], ctx.theme) if spec.get("glow_color") else None
        self._deco: dict = {}
        self._static = None

    # ---- time
    def visible(self, t: float) -> bool:
        return t >= self.start and (self.end is None or t < self.end)

    # ---- transform
    def local(self, t: float, d: float, hold: bool, tick: int) -> World:
        lt = t - self.start
        W, H, S = self.ctx.W, self.ctx.H, self.S
        sc = self.pscale(lt, d)
        w = World(self.px(lt, d) * W + self.pdx(lt, d) * S, self.py(lt, d) * H + self.pdy(lt, d) * S,
                  sc * self.psx(lt, d), sc * self.psy(lt, d), self.prot(lt, d), max(0.0, min(1.0, self.popacity(lt, d))),
                  self.pblur(lt, d) * S)
        # enter / exit presets, fades, loops
        end = None if hold else (self.end if self.end is not None else d)
        mods = []
        if self.enter and lt < self.enter_dur:
            mods.append(P.apply(self.enter, lt / max(1e-6, self.enter_dur), S, tick=tick))
        if self.exit and end is not None and t > end - self.exit_dur:
            mods.append(P.apply(self.exit, (end - t) / max(1e-6, self.exit_dur), S, tick=tick))
        if self.loop:
            mods.append(P.loop(self.loop, lt, S))
        for m in mods:
            w.x += m.dx; w.y += m.dy; w.sx *= m.sx; w.sy *= m.sy; w.rot += m.rot; w.alpha *= m.alpha; w.blur += m.blur
        if self.fade_in > 0:
            w.alpha *= ease("out_cubic", lt / self.fade_in)
        if self.fade_out > 0 and end is not None:
            w.alpha *= min(1.0, max(0.0, (end - t) / self.fade_out))
        return w

    # ---- content (override)
    def sprite(self, lt: float, d: float):
        """-> RGBA image of the layer content at local time lt (or None)."""
        return None

    def is_static(self) -> bool:
        return True

    # ---- decorations (blur / shine / glow / shadow) and drawing
    def stable(self, base) -> bool:
        """True when `base` is a long-lived cached sprite (so its decorated version may be cached too)."""
        return self.is_static()

    def _decorated(self, lt: float, d: float, wblur: float):
        base = self._static if (self._static is not None and self.is_static()) else None
        if base is None:
            base = self.sprite(lt, d)
            if base is not None and self.is_static():
                self._static = base
        if base is None:
            return None, None
        w0, h0 = base.size
        ax, ay = (self.anchor[0] * w0, self.anchor[1] * h0) if self.anchor else (w0 / 2, h0 / 2)
        shine_k = None
        if self.shine_spec:
            s0, sd = float(self.shine_spec.get("start", 0.6)), max(0.05, float(self.shine_spec.get("dur", 0.9)))
            k = (lt - s0) / sd
            shine_k = round(k, 2) if 0 < k < 1 else None
        gl = round(self.pglow(lt, d), 2)
        if wblur < 0.3 and shine_k is None and gl <= 0.01 and self.shadow is None:
            return base, (ax, ay)                                     # nothing to decorate
        key = (round(wblur, 1), shine_k, gl)
        cacheable = self.stable(base)
        hit = self._deco.get(key) if cacheable else None
        if hit is not None and hit[2] is base:
            return hit[0], hit[1]
        img = base
        if shine_k is not None:
            img = _shine(img, shine_k, float(self.shine_spec.get("width", 0.22)), float(self.shine_spec.get("strength", 0.85)))
        if wblur >= 0.3:
            p = int(wblur * 2.5) + 2; img = _blur(_pad(img, p), wblur); ax += p; ay += p
        if gl > 0.01:
            img, p = _glow(img, max(4, 14 * self.S) * (0.6 + gl * 0.6), min(2.0, gl), self.glow_color); ax += p; ay += p
        if self.shadow is not None:
            sd = self.shadow
            img, p = _shadow(img, sd.get("dx", 0) * self.S, sd.get("dy", 8) * self.S, max(2, sd.get("blur", 14) * self.S),
                             float(sd.get("alpha", 0.5))); ax += p; ay += p
        if cacheable:
            if len(self._deco) > 60:
                self._deco.clear()
            self._deco[key] = (img, (ax, ay), base)
        return img, (ax, ay)

    def draw(self, canvas: Image.Image, t: float, w: World, d: float, tick: int) -> None:
        img, anchor = self._decorated(t - self.start, d, w.blur)
        if img is not None:
            place(canvas, img, w.x, w.y, w.sx, w.sy, w.rot, w.alpha, anchor, self.blend)


# ======================================================================= text
class TextLayer(Layer):
    kind = "text"

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        th = ctx.theme
        self.text = str(spec.get("text", ""))
        color = parse_color(spec.get("color"), th, th["text"])
        grad = spec.get("gradient")
        self.block = TextBlock(
            ctx.fk, ctx.S, self.text, float(spec.get("size", 60)), color, align=spec.get("align", "center"),
            max_w=ctx.W * float(spec.get("width", 0.84)), tracking=float(spec.get("tracking", 0)),
            stroke=float(spec.get("stroke", 0)), shadow=spec.get("text_shadow", True),
            gradient=(parse_color(grad[0], th), parse_color(grad[1], th)) if isinstance(grad, (list, tuple)) and len(grad) == 2 else None,
            by=spec.get("by", "char" if spec.get("reveal") is not None else "word" if spec.get("animate") else "all"),
            highlight={k: parse_color(v, th) for k, v in (spec.get("highlight") or {}).items()} if isinstance(spec.get("highlight"), dict)
            else {w: th["accent"] for w in (spec.get("highlight") or [])},
            line_spacing=float(spec.get("line_spacing", 1.0)))
        an = spec.get("animate") or {}
        self.an_preset = an.get("preset") if an.get("preset") in P.PRESETS else None
        self.an_stagger = float(an.get("stagger", 0.05))
        self.an_dur = max(0.05, float(an.get("dur", 0.55)))
        self.an_delay = float(an.get("delay", 0.0))
        self.an_loop = an.get("loop") if an.get("loop") in P.LOOPS else None
        self.preveal = Prop(spec.get("reveal"), 1.0, "reveal")
        self._blk_static = None

    def is_static(self) -> bool:
        return False

    def stable(self, base) -> bool:
        return base is self._blk_static

    def sprite(self, lt, d):
        blk, S = self.block, self.S
        n = len(blk.units)
        rv = self.preveal(lt, d)
        done = lt >= self.an_delay + self.an_stagger * max(0, n - 1) + self.an_dur and not self.an_loop
        if (done or not self.an_preset) and rv >= 0.999 and not self.an_loop:
            if self._blk_static is None:
                self._blk_static = blk.static()
            return self._blk_static
        tick = int(lt * 30)
        shown = rv * n

        def state_of(u):
            if u.index + 1 > shown + 1e-6 and rv < 0.999:
                return None
            st = P.State()
            if self.an_preset:
                te = lt - self.an_delay - u.index * self.an_stagger
                if te < 0:
                    return None
                st = P.apply(self.an_preset, te / self.an_dur, S, u=u.off, i=u.index, tick=tick, lh=blk.line_h)
            if self.an_loop:
                m = P.loop(self.an_loop, lt, S, u.index)
                st.dx += m.dx; st.dy += m.dy; st.sx *= m.sx; st.sy *= m.sy; st.rot += m.rot
            return st
        return blk.render(state_of)


# ======================================================================= counter
class CounterLayer(Layer):
    kind = "counter"

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        th = ctx.theme
        self.v0, self.v1 = float(spec.get("from", 0)), float(spec.get("to", spec.get("value", 100)))
        self.dec = int(spec.get("decimals", 0)); self.pre, self.suf = str(spec.get("prefix", "")), str(spec.get("suffix", ""))
        self.commas = bool(spec.get("commas", abs(self.v1) >= 10000))
        self.cdur = max(0.1, float(spec.get("count_dur", 1.6))); self.cease = spec.get("ease", "out_cubic")
        self.size = float(spec.get("size", 120)); self.color = parse_color(spec.get("color"), th, th["accent"])
        self.fk, self.fpx = ctx.fk, max(6, int(self.size * ctx.S))
        probe = self.pre + self._fmt(self.v1) + self.suf
        self.font = self.fk.get(probe + "0123456789", self.fpx)
        self.cell = max(self.font.getlength(c) for c in "0123456789")
        self.cache: dict[str, Image.Image] = {}

    def _fmt(self, v):
        return f"{v:,.{self.dec}f}" if self.commas else f"{v:.{self.dec}f}"

    def is_static(self):
        return False

    def sprite(self, lt, d):
        v = self.v0 + (self.v1 - self.v0) * ease(self.cease, lt / self.cdur)
        s = self.pre + self._fmt(v) + self.suf
        if s in self.cache:
            return self.cache[s]
        f, S = self.font, self.S
        adv = [self.cell if c.isdigit() else f.getlength(c) for c in s]
        asc, desc = f.getmetrics(); pad = int(self.fpx * 0.15) + 4
        img = Image.new("RGBA", (int(sum(adv) + 2 * pad), int(asc + desc + 2 * pad)), (0, 0, 0, 0)); dr = ImageDraw.Draw(img)
        x = float(pad)
        for c, a in zip(s, adv):
            cx = x + a / 2
            dr.text((cx + 3 * S, pad + asc + 3 * S), c, font=f, fill=(0, 0, 0, 90), anchor="ms")
            dr.text((cx, pad + asc), c, font=f, fill=(*self.color, 255), anchor="ms"); x += a
        if len(self.cache) > 400:
            self.cache.clear()
        self.cache[s] = img
        return img


# ======================================================================= shapes
SHAPES = ("rect", "rounded", "circle", "ellipse", "line", "ring", "polygon", "triangle", "star")


def _trim_path(points: list[tuple], frac: float) -> list[tuple]:
    segs = [(points[i], points[i + 1], math.dist(points[i], points[i + 1])) for i in range(len(points) - 1)]
    total = sum(s[2] for s in segs); goal = total * max(0.0, min(1.0, frac)); out = [points[0]]
    for a, b, ln in segs:
        if goal <= 0:
            break
        k = min(1.0, goal / ln) if ln else 1.0
        out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)); goal -= ln
    return out


class ShapeLayer(Layer):
    kind = "shape"
    SS = 2

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        th = ctx.theme
        self.shape = spec.get("shape", "rect")
        self.pw, self.ph = Prop(spec.get("w", 200), 200, "w"), Prop(spec.get("h", spec.get("w", 200)), 200, "h")
        self.ptrim = Prop(spec.get("trim"), 1.0, "trim")
        f, st_ = spec.get("fill"), spec.get("stroke")
        if f in (False, "none"):
            self.fill = None
        elif f is not None:
            self.fill = parse_color(f, th, th["accent"])
        else:
            self.fill = None if st_ else th["accent"]
        self.stroke = parse_color(spec["stroke"], th, th["accent"]) if spec.get("stroke") else None
        self.sw = float(spec.get("stroke_w", 6)); self.radius = float(spec.get("radius", 24))
        self.sides = int(spec.get("sides", 5)); self.inner = float(spec.get("inner", 0.45))
        g = spec.get("gradient")
        self.grad = (parse_color(g[0], th), parse_color(g[1], th), float(spec.get("gradient_angle", 90))) if isinstance(g, (list, tuple)) and len(g) == 2 else None
        self._cache: dict = {}

    def is_static(self):
        return all(p.constant for p in (self.pw, self.ph, self.ptrim))

    def sprite(self, lt, d):
        S = self.S
        w, h, tr = self.pw(lt, d) * S, self.ph(lt, d) * S, max(0.0, min(1.0, self.ptrim(lt, d)))
        key = (round(w), round(h), round(tr, 3))
        if key in self._cache:
            return self._cache[key]
        img = self._draw(w, h, tr)
        if len(self._cache) > 300:
            self._cache.clear()
        self._cache[key] = img
        return img

    def _draw(self, w, h, tr):
        S, SS = self.S, self.SS
        if w < 1 or h < 1:
            return None
        sw = max(1.0, self.sw * S) if self.stroke else 0.0
        pad = int(sw / 2) + 3
        Wc, Hc = int(w) + 2 * pad, int(h) + 2 * pad
        x0, y0, x1, y1 = pad, pad, pad + w, pad + h
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        k = SS

        def layer():
            im = Image.new("RGBA", (Wc * k, Hc * k), (0, 0, 0, 0)); return im, ImageDraw.Draw(im)

        def box(): return [x0 * k, y0 * k, x1 * k, y1 * k]
        fill_im, fd = layer(); line_im, ld = layer()
        sh, st = self.shape, self.stroke
        r = min(self.radius * S, h / 2, w / 2)
        if sh in ("rect", "rounded"):
            xe = x0 + (x1 - x0) * tr if self.fill and not st else x1       # trim on a filled rect = progress bar
            bx = [x0 * k, y0 * k, xe * k, y1 * k]
            if self.fill and xe - x0 > 0.5:
                (fd.rounded_rectangle(bx, radius=r * k, fill=(*self.fill, 255)) if sh == "rounded" else fd.rectangle(bx, fill=(*self.fill, 255)))
            if st:
                if tr >= 0.999:
                    (ld.rounded_rectangle(box(), radius=r * k, outline=(*st, 255), width=int(sw * k)) if sh == "rounded"
                     else ld.rectangle(box(), outline=(*st, 255), width=int(sw * k)))
                else:
                    hw = sw / 2; pts = [(x0 + hw, y0 + hw), (x1 - hw, y0 + hw), (x1 - hw, y1 - hw), (x0 + hw, y1 - hw), (x0 + hw, y0 + hw)]
                    ld.line([(px * k, py * k) for px, py in _trim_path(pts, tr)], fill=(*st, 255), width=int(sw * k), joint="curve")
        elif sh in ("circle", "ellipse"):
            if sh == "circle":
                dm = min(w, h); bx = [(cx - dm / 2) * k, (cy - dm / 2) * k, (cx + dm / 2) * k, (cy + dm / 2) * k]
            else:
                bx = box()
            if self.fill:
                fd.ellipse(bx, fill=(*self.fill, 255))
            if st:
                ld.ellipse(bx, outline=(*st, 255), width=int(sw * k))
        elif sh == "ring":
            dm = min(w, h); rad = (dm - sw) / 2; col = (*(st or self.fill or (255, 255, 255)), 255)
            bx = [(cx - rad) * k, (cy - rad) * k, (cx + rad) * k, (cy + rad) * k]
            if tr >= 0.999:
                ld.ellipse([(cx - dm / 2) * k, (cy - dm / 2) * k, (cx + dm / 2) * k, (cy + dm / 2) * k], outline=col, width=int(sw * k))
            elif tr > 0:
                ld.arc(bx, -90, -90 + 360 * tr, fill=col, width=int(sw * k))
                for ang in (-90, -90 + 360 * tr):                          # round caps
                    ex, ey = cx + rad * math.cos(math.radians(ang)), cy + rad * math.sin(math.radians(ang))
                    ld.ellipse([(ex - sw / 2) * k, (ey - sw / 2) * k, (ex + sw / 2) * k, (ey + sw / 2) * k], fill=col)
        elif sh == "line":
            col = (*(st or self.fill or (255, 255, 255)), 255); sw2 = max(sw, 2 * S) if sw else 6 * S
            xa, xb = x0 + sw2 / 2, x0 + sw2 / 2 + (w - sw2) * tr
            if tr > 0:
                ld.line([(xa * k, cy * k), (xb * k, cy * k)], fill=col, width=int(sw2 * k))
                for ex in (xa, xb):
                    ld.ellipse([(ex - sw2 / 2) * k, (cy - sw2 / 2) * k, (ex + sw2 / 2) * k, (cy + sw2 / 2) * k], fill=col)
        elif sh in ("polygon", "triangle", "star"):
            n = 3 if sh == "triangle" else max(3, self.sides); rad = min(w, h) / 2
            pts = []
            for i in range(n * (2 if sh == "star" else 1)):
                rr = rad * (self.inner if (sh == "star" and i % 2) else 1.0)
                a = -math.pi / 2 + i * math.pi / (n if sh == "star" else n / 2)
                pts.append(((cx + rr * math.cos(a)) * k, (cy + rr * math.sin(a)) * k))
            if self.fill:
                fd.polygon(pts, fill=(*self.fill, 255))
            if st:
                ld.line(pts + [pts[0]], fill=(*st, 255), width=int(sw * k), joint="curve")
        if self.grad and self.fill:
            g = vgradient(fill_im.width, fill_im.height, self.grad[0], self.grad[1], self.grad[2]); g.putalpha(fill_im.getchannel("A")); fill_im = g
        out = fill_im; out.alpha_composite(line_im)
        return out.resize((Wc, Hc), Image.LANCZOS)


# ======================================================================= image / solid / null
class ImageLayer(Layer):
    kind = "image"

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        self.path = spec.get("path"); self.wpx = float(spec.get("w", 400)); self.radius = float(spec.get("radius", 0))
        self._img = None

    def sprite(self, lt, d):
        if self._img is None:
            im = Image.open(self.path).convert("RGBA"); S = self.S
            k = self.wpx * S / im.width
            if spec_h := self.spec.get("h"):
                k = min(k, float(spec_h) * S / im.height)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
            if self.radius:
                m = Image.new("L", (im.width * 2, im.height * 2), 0)
                ImageDraw.Draw(m).rounded_rectangle([0, 0, im.width * 2, im.height * 2], radius=self.radius * S * 2, fill=255)
                m = m.resize(im.size, Image.LANCZOS); a = im.getchannel("A"); from PIL import ImageChops
                im.putalpha(ImageChops.multiply(a, m))
            self._img = im
        return self._img


class SolidLayer(Layer):
    kind = "solid"

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        th = ctx.theme
        self.color = parse_color(spec.get("color"), th, (255, 255, 255))
        g = spec.get("gradient")
        self.grad = (parse_color(g[0], th), parse_color(g[1], th), float(spec.get("gradient_angle", 90))) if isinstance(g, (list, tuple)) and len(g) == 2 else None

    def sprite(self, lt, d):
        W, H = self.ctx.W, self.ctx.H
        return vgradient(W, H, *self.grad) if self.grad else Image.new("RGBA", (W, H), (*self.color, 255))


class NullLayer(Layer):
    kind = "null"
    renders_sprite = False

    def draw(self, *a, **k):
        return


# ======================================================================= particles
class ParticleLayer(Layer):
    kind = "particles"
    KINDS = ("confetti", "sparkle", "burst", "bubbles", "snow", "embers", "stars")

    def __init__(self, spec, ctx, index):
        super().__init__(spec, ctx, index)
        th = ctx.theme
        self.pkind = spec.get("kind", "confetti")
        self.count = max(1, min(400, int(spec.get("count", 60)))); self.seed = int(spec.get("seed", 1))
        cols = spec.get("colors") or [th["accent"], th["text"], (255, 196, 0), (255, 80, 140), (110, 90, 255)]
        self.colors = [parse_color(c, th) for c in cols]
        self.psize = float(spec.get("size", 10)); self.speed = float(spec.get("speed", 1.0)); self.life = float(spec.get("life", 3.0))
        self.spread = float(spec.get("spread", 1.0))
        self._sprites: dict = {}
        from .props import _hash01
        self._h = _hash01

    def _r(self, i, k):
        return self._h(i * 13 + k, self.seed)

    def _spr(self, kind, size, color):
        key = (kind, int(size), color)
        if key in self._sprites:
            return self._sprites[key]
        n = int(size) * 2 + 8; k = 3
        im = Image.new("RGBA", (n * k, n * k), (0, 0, 0, 0)); d = ImageDraw.Draw(im); c = n * k / 2; r = size * k / 2
        if kind in ("dot", "ember"):
            d.ellipse([c - r, c - r, c + r, c + r], fill=(*color, 255))
        elif kind == "ring":
            d.ellipse([c - r, c - r, c + r, c + r], outline=(*color, 255), width=max(2, int(k * 1.5)))
        elif kind == "rect":
            d.rectangle([c - r, c - r * 0.5, c + r, c + r * 0.5], fill=(*color, 255))
        else:  # spark: 4-point star
            pts = []
            for j in range(8):
                a = j * math.pi / 4; rr = r * 1.6 if j % 2 == 0 else r * 0.35
                pts.append((c + rr * math.cos(a), c + rr * math.sin(a)))
            d.polygon(pts, fill=(*color, 255))
        out = im.resize((n, n), Image.LANCZOS)
        if kind == "ember":
            out, _ = _glow(out, max(3, size * 0.8), 0.8)
        self._sprites[key] = out
        return out

    def is_static(self):
        return False

    def draw(self, canvas, t, w, d, tick):
        lt = t - self.start; W, H, S = self.ctx.W, self.ctx.H, self.S
        ox, oy = w.x, w.y
        k = self.pkind
        for i in range(self.count):
            r = lambda j: self._r(i, j)  # noqa: E731
            col = self.colors[int(r(1) * len(self.colors)) % len(self.colors)]
            sz = self.psize * S * (0.6 + r(2) * 0.9)
            a = 1.0; rot = 0.0; blend = "normal"
            if k == "confetti":
                life = self.life / self.speed; tt = (lt + r(3) * life) % life; p = tt / life
                x = r(4) * W + math.sin(tt * 3 + r(5) * 6) * 40 * S
                y = -20 * S + p * (H + 60 * S) * (0.8 + r(6) * 0.4)
                rot = (tt * (120 + r(7) * 360)) % 360; spr = self._spr("rect", sz, col); a = min(1, p * 8) * min(1, (1 - p) * 8)
                sy_ = abs(math.cos(tt * 5 + r(8) * 6)) * 0.9 + 0.1
                place(canvas, spr, x, y, 1, sy_, rot, a * w.alpha, blend=blend); continue
            if k in ("burst", "sparkle"):
                if lt < 0:
                    continue
                ang = r(4) * math.tau; v = (160 + r(5) * 420) * S * self.speed * self.spread; drag = 3.2
                tt = lt * self.speed; dist = v * (1 - math.exp(-drag * tt)) / drag
                p = lt / (self.life * (0.5 + r(6) * 0.6))
                if p >= 1:
                    continue
                x, y = ox + math.cos(ang) * dist, oy + math.sin(ang) * dist + 70 * S * tt * tt * 0.5
                a = (1 - p) ** 1.4; sizep = sz * (1 - 0.6 * p)
                spr = self._spr("spark" if k == "sparkle" else "dot", sizep, col); rot = tt * 90 * (r(7) - .5)
                place(canvas, spr, x, y, 1, 1, rot, a * w.alpha, blend="add"); continue
            if k == "bubbles":
                life = (self.life * 2.2) / self.speed; tt = (lt + r(3) * life) % life; p = tt / life
                x = r(4) * W + math.sin(tt * 1.7 + r(5) * 6) * 26 * S; y = H * (1.08 - p * 1.2)
                spr = self._spr("ring", sz * 1.6, col); a = 0.55 * min(1, p * 6) * min(1, (1 - p) * 4)
            elif k == "snow":
                life = (self.life * 3) / self.speed; tt = (lt + r(3) * life) % life; p = tt / life
                x = r(4) * W + math.sin(tt * 1.3 + r(5) * 6) * 30 * S; y = -10 * S + p * (H + 20 * S)
                spr = self._spr("dot", sz * 0.6, (255, 255, 255)); a = 0.85 * min(1, p * 10) * min(1, (1 - p) * 10)
            elif k == "embers":
                life = (self.life * 1.6) / self.speed; tt = (lt + r(3) * life) % life; p = tt / life
                x = r(4) * W + math.sin(tt * 3.1 + r(5) * 6) * 30 * S; y = H * (1.05 - p * 1.15)
                spr = self._spr("ember", sz * 0.8, col if r(9) > .35 else (255, 160, 40))
                a = (0.5 + 0.5 * math.sin(tt * 12 + r(5) * 9)) * min(1, p * 6) * min(1, (1 - p) * 3); blend = "add"
            else:  # stars
                x, y = r(4) * W, r(5) * H; spr = self._spr("dot", sz * 0.35, (255, 255, 255))
                a = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(lt * (1.5 + r(6) * 3) + r(7) * 9)); x += lt * 6 * S * (0.3 + r(8))
                x %= W
            place(canvas, spr, x, y, 1, 1, rot, a * w.alpha, blend=blend)


TYPES = {"text": TextLayer, "counter": CounterLayer, "shape": ShapeLayer, "image": ImageLayer, "particles": ParticleLayer,
         "solid": SolidLayer, "null": NullLayer}


# ======================================================================= composition
class Composition:
    def __init__(self, specs: list[dict], ctx):
        self.ctx = ctx
        self.layers: list[Layer] = []
        for i, sp in enumerate(specs):
            cls = TYPES.get(sp.get("type", "text"))
            if cls is None:
                raise SystemExit(f"layer {i + 1}: unknown type '{sp.get('type')}' (valid: {', '.join(TYPES)})")
            self.layers.append(cls(sp, ctx, i))
        self.by_id = {L.id: L for L in self.layers}

    def _world(self, L: Layer, t: float, d: float, memo: dict, hold: bool, tick: int, depth: int = 0) -> World:
        if L.id in memo:
            return memo[L.id]
        loc = L.local(t, d, hold, tick)
        par = self.by_id.get(L.parent_id) if L.parent_id else None
        if par is not None and par is not L and depth < 8:
            pw = self._world(par, t, d, memo, hold, tick, depth + 1); lt = t - L.start; S = self.ctx.S
            ox, oy = L.pdx(lt, d) * S * pw.sx, L.pdy(lt, d) * S * pw.sy
            th = math.radians(pw.rot); c, s = math.cos(th), math.sin(th)
            loc = World(pw.x + c * ox - s * oy, pw.y + s * ox + c * oy, pw.sx * (loc.sx), pw.sy * (loc.sy),
                        pw.rot + loc.rot, pw.alpha * loc.alpha, loc.blur + pw.blur)
        memo[L.id] = loc
        return loc

    def draw(self, canvas: Image.Image, t: float, d: float, tick: int = 0, hold: bool = False) -> None:
        memo: dict = {}
        for L in self.layers:
            if not L.visible(t) or isinstance(L, NullLayer):
                continue
            w = self._world(L, t, d, memo, hold, tick)
            if w.alpha > 0.003:
                L.draw(canvas, t, w, d, tick)


# ======================================================================= validation
def validate_layers(specs, tag: str, errs: list, warns: list) -> None:
    if not isinstance(specs, list) or not specs:
        errs.append(f"{tag}: 'layers' must be a non-empty list"); return
    ids = set()
    for i, sp in enumerate(specs):
        lt = f"{tag} layer {i + 1}"
        if not isinstance(sp, dict):
            errs.append(f"{lt}: must be an object"); continue
        ty = sp.get("type", "text")
        if ty not in TYPES:
            m = difflib.get_close_matches(str(ty), TYPES, 1)
            errs.append(f"{lt}: unknown type '{ty}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(TYPES)})")); continue
        lid = sp.get("id")
        if lid:
            if lid in ids:
                warns.append(f"{lt}: duplicate id '{lid}'")
            ids.add(lid)
        for key in ("x", "y", "dx", "dy", "scale", "scale_x", "scale_y", "rotation", "opacity", "blur", "glow", "trim", "w", "h", "reveal"):
            if key in sp and (m := check_prop(sp[key], f"{lt}.{key}")):
                errs.append(m)
        for key in ("enter", "exit"):
            if sp.get(key) and sp[key] not in P.PRESETS:
                m = difflib.get_close_matches(str(sp[key]), P.PRESET_NAMES, 1)
                errs.append(f"{lt}: unknown {key} preset '{sp[key]}'" + (f" - did you mean '{m[0]}'?" if m else f" (valid: {', '.join(P.PRESET_NAMES)})"))
        if sp.get("loop") and sp["loop"] not in P.LOOPS:
            warns.append(f"{lt}: unknown loop '{sp['loop']}' (valid: {', '.join(P.LOOPS)})")
        if sp.get("blend") and sp["blend"] not in BLENDS:
            warns.append(f"{lt}: unknown blend '{sp['blend']}' (valid: {', '.join(BLENDS)})")
        for key in ("color", "fill", "stroke", "glow_color"):
            if key in sp and not color_ok(sp[key]):
                warns.append(f"{lt}: unreadable {key} {sp[key]!r} (use [r,g,b], '#rrggbb', or accent/text/muted/white/...)")
        if ty == "text" and not sp.get("text"):
            errs.append(f"{lt} (text): 'text' is required")
        if ty == "text" and isinstance(sp.get("animate"), dict) and sp["animate"].get("preset") not in P.PRESETS:
            errs.append(f"{lt}: animate.preset must be one of: {', '.join(P.PRESET_NAMES)}")
        if ty == "shape" and sp.get("shape", "rect") not in SHAPES:
            errs.append(f"{lt}: unknown shape '{sp.get('shape')}' (valid: {', '.join(SHAPES)})")
        if ty == "image" and (not sp.get("path") or not os.path.exists(sp["path"])):
            errs.append(f"{lt} (image): file not found: {sp.get('path')}")
        if ty == "particles" and sp.get("kind", "confetti") not in ParticleLayer.KINDS:
            errs.append(f"{lt}: unknown particle kind '{sp.get('kind')}' (valid: {', '.join(ParticleLayer.KINDS)})")
    for i, sp in enumerate(specs):
        if isinstance(sp, dict) and sp.get("parent") and sp["parent"] not in ids:
            warns.append(f"{tag} layer {i + 1}: parent '{sp['parent']}' does not exist (ignored)")
