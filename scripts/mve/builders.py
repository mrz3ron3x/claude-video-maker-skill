"""Scene builders: turn high-level scene specs (kinetic, logo_reveal, ring, motion, and title/text/quote/outro with an
After-Effects preset) into layer lists, and wrap a layer list as a MotionElement the renderer can draw.
"""
from __future__ import annotations

import os

from . import presets as P
from .fonts import script_of
from .layers import Composition, parse_color, validate_layers
from .textkit import TextBlock

MOTION_TYPES = {"motion", "kinetic", "logo_reveal", "ring"}
CHAR_PRESETS = {"tracking", "scatter", "glitch", "drop", "elastic", "bounce", "spin_in", "flip", "rise", "blur_in", "pop", "stretch", "zoom_in"}
# classic scene types that switch to the layer engine when given one of these (new) animation names
NEW_ANIMS = set(P.PRESET_NAMES) - {"fade", "slide_up", "slide_left", "slide_right", "pop"}
PRESET_TYPES = {"title", "text", "quote", "outro"}


class MotionElement:
    """Renderer-facing wrapper (same duck type as render.Element for the motion path)."""
    is_motion = True

    def __init__(self, comp: Composition):
        self.comp = comp

    def params(self, tl: float, dur: float, hold: bool = False):
        return (round(tl, 4), hold)

    def draw(self, canvas, p, dur: float, tick: int):
        self.comp.draw(canvas, p[0], dur, tick, p[1])


# ---------------------------------------------------------------- layout helpers
def _measure(ctx, sp: dict) -> float:
    if sp.get("type") == "text":
        tb = TextBlock(ctx.fk, ctx.S, sp["text"], sp.get("size", 60), by="all", max_w=ctx.W * float(sp.get("width", 0.84)),
                       tracking=float(sp.get("tracking", 0)), line_spacing=float(sp.get("line_spacing", 1.0)))
        return tb.h / ctx.S
    return float(sp.get("h", 10))


def vstack(ctx, parts: list[dict], anchor_y: float = 0.5, gap: float = 12.0) -> list[dict]:
    """Centre `parts` as a vertical stack around anchor_y (parts may carry '_gap' = extra space before them)."""
    hs = [_measure(ctx, p) for p in parts]
    gaps = [float(p.pop("_gap", gap)) for p in parts]
    total = sum(hs) + sum(gaps[1:])
    y = -total / 2
    for i, (p, h) in enumerate(zip(parts, hs)):
        if i:
            y += gaps[i]
        p["y"] = anchor_y; p["dy"] = y + h / 2; y += h
    return parts


def _dim_layer(sc: dict) -> dict:
    return dict(type="solid", id="_dim", color=[0, 0, 0], opacity=float(sc.get("dim_alpha", 0.55)), fade_in=0.4, fade_out=0.4)


def _bar(ctx, start: float) -> dict:
    return dict(type="shape", shape="rounded", w=120, h=6, radius=3, fill="accent",
                scale_x={"from": 0, "to": 1, "start": start, "dur": 0.5, "ease": "out_expo"}, opacity={"keys": [[0, 1]]},
                exit="fade", exit_dur=0.4)


def _pick_by(text: str, preset: str, by: str | None) -> str:
    if by:
        return by
    return "char" if (preset in CHAR_PRESETS and script_of(text) == "latin" and len(text) <= 24) else "word"


def _anim(preset: str, by: str, stagger, dur, delay, loop) -> dict:
    st = float(stagger) if stagger is not None else (0.045 if by == "char" else 0.12 if by == "word" else 0.18)
    return dict(preset=preset, stagger=st, dur=float(dur), delay=delay, loop=loop)


def _main_text(sc: dict, text: str, size: float, preset: str, delay: float = 0.0, color=None) -> dict:
    by = _pick_by(text, preset, sc.get("by"))
    return dict(type="text", text=text, size=sc.get("size", size), by=by, color=color or sc.get("color"), gradient=sc.get("gradient"),
                stroke=sc.get("stroke", 0), tracking=sc.get("tracking", 0), highlight=sc.get("highlight"), align=sc.get("align", "center"),
                line_spacing=sc.get("line_spacing", 1.0), animate=_anim(preset, by, sc.get("stagger"), sc.get("anim_dur", 0.6), delay, sc.get("loop")),
                exit="fade", exit_dur=0.4, shine=sc.get("shine"))


def _units(text: str, by: str) -> int:
    return len(text) if by == "char" else max(1, len(text.split())) if by == "word" else 1


# ---------------------------------------------------------------- scene -> layers
def kinetic_layers(ctx, sc: dict, kind: str) -> list[dict]:
    th = ctx.theme
    preset = sc.get("preset") or (sc.get("animation") if sc.get("animation") in P.PRESETS else None) or "rise"
    text, sub = str(sc.get("text", "")), str(sc.get("subtext", ""))
    parts: list[dict] = []
    if kind == "kinetic" and sc.get("lines"):
        t0 = 0.0
        for i, ln in enumerate(sc["lines"]):
            d = ln if isinstance(ln, dict) else {"text": str(ln)}
            sub_sc = {**sc, **d}
            pr = d.get("preset", preset)
            part = _main_text(sub_sc, str(d.get("text", "")), d.get("size", sc.get("size", 70)), pr, delay=0.0)
            part["start"] = t0; parts.append(part)
            t0 += float(sc.get("line_delay", 0.4))
    elif kind == "quote":
        parts.append(_main_text(sc, "\u201C" + text + "\u201D", 54, preset))
    else:
        parts.append(_main_text(sc, text, 76 if kind in ("title", "outro", "kinetic") else 58, preset))
    main_end = max((p["start"] if "start" in p else 0.0) + p["animate"]["stagger"] * _units(p["text"], p["by"]) + 0.5 for p in parts)
    if kind in ("title", "outro", "kinetic") and not sc.get("lines"):
        b = _bar(ctx, main_end * 0.7); b["_gap"] = 16; parts.append(b)
    if sub:
        col = th["accent"] if kind == "quote" else th["muted"]
        parts.append(dict(type="text", text=("\u2014 " + sub) if kind == "quote" else sub, size=34 if kind != "text" else 30, color=col,
                          enter="rise", enter_dur=0.6, start=main_end * 0.75, exit="fade", exit_dur=0.4, _gap=14))
    if kind == "outro" and sc.get("cta"):
        parts.append(dict(type="text", text=str(sc["cta"]), size=30, color=th["accent"], enter="pop", enter_dur=0.6, start=main_end + 0.2,
                          exit="fade", exit_dur=0.4, _gap=24))
    vstack(ctx, parts, anchor_y=float(sc.get("y", 0.5)))
    layers = parts
    if ctx.video_mode and sc.get("dim", True):
        layers = [_dim_layer(sc)] + layers
    return layers


def logo_layers(ctx, sc: dict) -> list[dict]:
    th = ctx.theme
    img, brand, tag = sc.get("path"), str(sc.get("text", "")), str(sc.get("subtext", ""))
    has_img = bool(img)
    cy = 0.42 if has_img else 0.46
    L: list[dict] = []
    if ctx.video_mode and sc.get("dim", True):
        L.append(_dim_layer(sc))
    L.append(dict(type="shape", shape="ring", w=70, h=70, stroke="accent", stroke_w=5, x=0.5, y=cy, start=0.15,
                  scale={"from": 0.4, "to": 7, "start": 0, "dur": 1.1, "ease": "out_expo"},
                  opacity={"keys": [[0, 0.9], [1.1, 0]], "ease": "linear"}, glow=0.6))
    L.append(dict(type="particles", kind="sparkle", x=0.5, y=cy, count=38, start=0.5, life=1.5, size=11, speed=0.9, seed=7, exit="fade"))
    if has_img:
        L.append(dict(type="image", path=img, w=sc.get("logo_size", 240), x=0.5, y=cy,
                      scale={"from": 0.15, "to": 1, "start": 0.35, "dur": 0.9, "ease": "out_back"},
                      opacity={"from": 0, "to": 1, "start": 0.35, "dur": 0.3, "ease": "linear"}, shine={"start": 1.25, "dur": 0.9},
                      glow={"keys": [[0.35, 0], [0.8, 1.2], [2.0, 0.4]]}, loop="breathe", exit="fade"))
    text_y = 0.66 if has_img else 0.5
    if brand:
        L.append(dict(type="text", text=brand, size=sc.get("size", 64 if has_img else 92), x=0.5, y=text_y, by="char" if len(brand) <= 24 else "word",
                      animate=dict(preset="tracking", stagger=0.035, dur=0.8, delay=0.85), shine={"start": 1.9, "dur": 0.8}, exit="fade",
                      glow={"keys": [[0.85, 0], [1.4, 0.8], [2.5, 0.25]]}, highlight=sc.get("highlight")))
    if tag:
        L.append(dict(type="text", text=tag, size=32, color="muted", x=0.5, y=text_y + (0.1 if has_img else 0.12), enter="rise", enter_dur=0.7,
                      start=1.7, exit="fade"))
    L.append(dict(type="solid", color=[255, 255, 255], opacity={"keys": [[0.3, 0], [0.37, 0.75], [0.95, 0]], "ease": "out_cubic"}, blend="add"))
    return L


def ring_layers(ctx, sc: dict) -> list[dict]:
    th = ctx.theme
    mx = float(sc.get("max", 100)); val = float(sc.get("value", sc.get("percent", 75))); frac = max(0.0, min(1.0, val / mx if mx else 0))
    suffix = sc.get("suffix", "%" if mx == 100 else "")
    size = float(sc.get("ring_size", 330)); cy = 0.44 if sc.get("text") else 0.5
    L: list[dict] = []
    if ctx.video_mode and sc.get("dim", True):
        L.append(_dim_layer(sc))
    L.append(dict(type="shape", shape="ring", w=size, h=size, stroke="muted", stroke_w=sc.get("ring_width", 22), x=0.5, y=cy, opacity=0.22,
                  enter="fade", exit="fade"))
    L.append(dict(type="shape", shape="ring", w=size, h=size, stroke="accent", stroke_w=sc.get("ring_width", 22), x=0.5, y=cy, glow=0.9,
                  trim={"from": 0, "to": frac, "start": 0.25, "dur": 1.7, "ease": "out_cubic"}, exit="fade"))
    L.append(dict(type="counter", **{"from": 0}, to=val, suffix=suffix, size=sc.get("size", 110), x=0.5, y=cy, start=0.25, count_dur=1.7, exit="fade",
                  decimals=int(sc.get("decimals", 0)), color="text"))
    if sc.get("text"):
        L.append(dict(type="text", text=str(sc["text"]), size=40, x=0.5, y=0.76, enter="rise", enter_dur=0.6, start=0.9, exit="fade"))
    if sc.get("subtext"):
        L.append(dict(type="text", text=str(sc["subtext"]), size=28, color="muted", x=0.5, y=0.83, enter="fade", start=1.2, exit="fade"))
    return L


def build_motion_scene(ctx, sc: dict) -> list:
    """-> [MotionElement] for a motion-engine scene (type in MOTION_TYPES, or a classic type with a new animation)."""
    t = sc.get("type", "text")
    if t == "motion":
        layers = list(sc.get("layers", []))
        if ctx.video_mode and sc.get("dim", False):
            layers = [_dim_layer(sc)] + layers
    elif t == "kinetic":
        layers = kinetic_layers(ctx, sc, "kinetic")
    elif t == "logo_reveal":
        layers = logo_layers(ctx, sc)
    elif t == "ring":
        layers = ring_layers(ctx, sc)
    else:
        layers = kinetic_layers(ctx, sc, t)
    return [MotionElement(Composition(layers, ctx))]


def uses_motion_engine(sc: dict) -> bool:
    t = sc.get("type", "text")
    return t in MOTION_TYPES or (t in PRESET_TYPES and sc.get("animation") in NEW_ANIMS)


# ---------------------------------------------------------------- validation
def validate_motion_scene(sc: dict, tag: str, errs: list, warns: list) -> None:
    t = sc.get("type")
    if t == "motion":
        validate_layers(sc.get("layers"), tag, errs, warns)
    elif t == "kinetic":
        if not sc.get("text") and not sc.get("lines"):
            errs.append(f"{tag} (kinetic): 'text' or 'lines' is required")
        if sc.get("preset") and sc["preset"] not in P.PRESETS:
            errs.append(f"{tag}: unknown preset '{sc['preset']}' (valid: {', '.join(P.PRESET_NAMES)})")
    elif t == "logo_reveal":
        if not (sc.get("text") or sc.get("path")):
            errs.append(f"{tag} (logo_reveal): give 'text' (brand) and/or 'path' (logo image)")
        if sc.get("path") and not os.path.exists(sc["path"]):
            errs.append(f"{tag} (logo_reveal): file not found: {sc['path']}")
    elif t == "ring":
        try:
            float(sc.get("value", sc.get("percent", 75)))
        except (TypeError, ValueError):
            errs.append(f"{tag} (ring): 'value' must be a number")
    if t in ("kinetic", "title", "text", "quote", "outro") and sc.get("text") and script_of(str(sc["text"])) != "latin" \
            and sc.get("by") == "char":
        warns.append(f"{tag}: Urdu/Arabic/Hindi text is animated per word (per-letter would break joining)")
