"""Text layout: wrapping, per-unit (char / word / line) boxes, RTL (Urdu/Arabic) awareness and glyph sprites.

A TextBlock lays out once; `render(state_of)` then composes the units at any moment, each with its own transform,
which is what makes kinetic typography (stagger, masks, tracking, scatter...) possible.

Arabic-script and Devanagari text is never split per character (that would break letter joining / conjuncts):
it falls back to per-word units automatically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from .compositing import blur as _blur, place, vgradient
from .fonts import FontKit, script_of

_WORD = re.compile(r"\S+")


@dataclass
class Unit:
    text: str
    x: float                 # left edge inside the block (px)
    w: float                 # advance width (px)
    line: int
    color: tuple
    sprite: Image.Image | None = None
    cx: float = 0.0          # sprite centre inside the block
    cy: float = 0.0
    index: int = 0
    off: float = 0.0         # centre offset from the block centre (px) - used by "tracking"


@dataclass
class Line:
    text: str
    y: float                 # top of the line box inside the block
    w: float
    x0: float                # left edge of the line (alignment applied)


class TextBlock:
    def __init__(self, fk: FontKit, S: float, text: str, size: float, color=(255, 255, 255), *, align: str = "center",
                 max_w: float | None = None, tracking: float = 0.0, stroke: float = 0.0, shadow: bool = True,
                 gradient: tuple | None = None, by: str = "word", highlight: dict | None = None,
                 line_spacing: float = 1.0):
        self.fk, self.S = fk, S
        self.text = str(text)
        self.px = max(6, int(size * S))
        self.color, self.align, self.tracking, self.stroke = tuple(color), align, tracking * S, int(stroke * S)
        self.shadow, self.gradient = shadow, gradient
        self.script = script_of(self.text)
        self.font = fk.get(self.text, self.px)
        self.by = by if (self.script == "latin" or by in ("line", "all")) else "word"
        if by == "char" and self.script != "latin":
            self.by = "word"
        self.highlight = {k.lower().strip(".,!?;:\"'"): tuple(v) for k, v in (highlight or {}).items()}
        asc, desc = self.font.getmetrics()
        self.asc, self.desc = asc, desc
        self.line_h = int((asc + desc) * 1.06 * line_spacing)
        self.padx = int(self.px * 0.18) + self.stroke + int(3 * S) + 2
        self.pady = int(self.px * 0.12) + self.stroke + int(3 * S) + 2
        self.lines: list[Line] = []
        self.units: list[Unit] = []
        self._layout(max_w)

    # ---------------------------------------------------------------- layout
    def _wrap(self, max_w: float | None) -> list[str]:
        out = []
        for para in self.text.split("\n"):
            cur = ""
            for w in para.split(" "):
                t = (cur + " " + w).strip() if cur else w
                if max_w is None or self.font.getlength(t) <= max_w or not cur:
                    cur = t
                else:
                    out.append(cur); cur = w
            out.append(cur)
        return out

    def _color_for(self, word: str) -> tuple:
        return self.highlight.get(word.lower().strip(".,!?;:\"'"), self.color)

    def _layout(self, max_w):
        texts = self._wrap(max_w)
        widths = [self.font.getlength(t) + (self.tracking * max(0, len(t) - 1) if self.by == "char" else 0) for t in texts]
        bw = max(widths + [1.0])
        self.w = int(bw + 2 * self.padx); self.h = int(self.line_h * len(texts) + 2 * self.pady)
        n = 0
        for li, (t, lw) in enumerate(zip(texts, widths)):
            x0 = self.padx + (0 if self.align == "left" else (bw - lw) if self.align == "right" else (bw - lw) / 2)
            y = self.pady + li * self.line_h
            self.lines.append(Line(t, y, lw, x0))
            if self.by == "line" or self.by == "all":
                self.units.append(Unit(t, x0, lw, li, self.color, index=n)); n += 1
            elif self.by == "word":
                words = [(m.group(), m.start()) for m in _WORD.finditer(t)]
                rtl = self.script == "arabic"
                for wi, (wd, st) in enumerate(words):
                    if rtl:
                        x = x0 + lw - self.font.getlength(t[:st + len(wd)])
                    else:
                        x = x0 + self.font.getlength(t[:st])
                    self.units.append(Unit(wd, x, self.font.getlength(wd), li, self._color_for(wd), index=n)); n += 1
            else:  # char
                spans = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(t)]
                for i, ch in enumerate(t):
                    if ch.isspace():
                        continue
                    wd = next((s[2] for s in spans if s[0] <= i < s[1]), ch)
                    x = x0 + self.font.getlength(t[:i]) + self.tracking * i
                    adv = self.font.getlength(t[:i + 1]) - self.font.getlength(t[:i])
                    self.units.append(Unit(ch, x, adv, li, self._color_for(wd), index=n)); n += 1
        if self.by == "all":
            self.units = [Unit(self.text, 0, self.w, 0, self.color, index=0)]
        mid = self.w / 2
        for u in self.units:
            if self.by == "all":
                u.cx, u.cy = self.w / 2, self.h / 2; u.off = 0
            else:
                u.cx = u.x + u.w / 2; u.cy = self.lines[u.line].y + self.line_h / 2; u.off = u.cx - mid

    # ---------------------------------------------------------------- sprites
    def _line(self, d, txt, x, y, color, shadow):
        """Draw one line of text at baseline (x, y). Shadow pass draws a soft offset copy in black."""
        S = self.S
        off = 3 * S if shadow else 0
        fill = (0, 0, 0, 90) if shadow else (*color[:3], 255)
        sw = 0 if shadow else self.stroke
        sf = (0, 0, 0, 200) if (self.stroke and not shadow) else None
        if self.highlight and not shadow and self.by in ("line", "all"):
            rtl = self.script == "arabic"; total = self.font.getlength(txt)
            for m in _WORD.finditer(txt):
                wx = x + (total - self.font.getlength(txt[:m.end()]) if rtl else self.font.getlength(txt[:m.start()]))
                col = self._color_for(m.group())
                d.text((wx, y), m.group(), font=self.font, fill=(*col[:3], 255), anchor="ls", stroke_width=sw, stroke_fill=sf)
        else:
            d.text((x + off, y + off), txt, font=self.font, fill=fill, anchor="ls", stroke_width=sw, stroke_fill=sf)

    def _paint(self, u: Unit, shadow: bool) -> Image.Image:
        if self.by == "all":
            w, h = self.w, self.h
        else:
            w, h = int(u.w + 2 * self.padx), int(self.line_h + 2 * self.pady)
        img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
        slack = (self.line_h - self.asc - self.desc) / 2
        if self.by == "all":
            for ln in self.lines:
                self._line(d, ln.text, ln.x0, ln.y + self.asc + slack, u.color, shadow)
        else:
            txt = self.lines[u.line].text if self.by == "line" else u.text
            self._line(d, txt, self.padx, self.pady + self.asc + slack, u.color, shadow)
        return img

    def _sprite(self, u: Unit) -> Image.Image:
        if u.sprite is not None:
            return u.sprite
        txt = self._paint(u, shadow=False)
        if self.gradient:
            g = vgradient(txt.width, txt.height, self.gradient[0], self.gradient[1])
            g.putalpha(txt.getchannel("A")); txt = g
        if self.shadow:
            out = self._paint(u, shadow=True); out.alpha_composite(txt)
        else:
            out = txt
        u.sprite = out
        return out

    # ---------------------------------------------------------------- composition
    def render(self, state_of) -> Image.Image:
        """state_of(unit) -> presets.State | None (None = unit not visible yet). Returns an RGBA block image."""
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        for u in self.units:
            st = state_of(u)
            if st is None or st.alpha <= 0.003:
                continue
            sp = self._sprite(u)
            if st.blur > 0.3:
                sp = _blur(sp, st.blur)
            clip = None
            if st.clip and self.by != "all":
                ln = self.lines[u.line]
                clip = (0, int(ln.y), self.w, int(ln.y + self.line_h))
            place(canvas, sp, u.cx + st.dx, u.cy + st.dy, st.sx, st.sy, st.rot, st.alpha, clip=clip)
        return canvas

    def static(self) -> Image.Image:
        from .presets import State
        return self.render(lambda u: State())
