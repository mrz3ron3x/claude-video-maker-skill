#!/usr/bin/env python3
"""
motion-video-editor renderer v3 (After-Effects-style motion engine in ./mve).

Two modes (decided automatically by the spec):
  EDIT   : spec has "input_video" -> animated overlays on top of the video.
  CREATE : no "input_video"       -> a motion-graphics video built from scenes.

Usage:
  python render.py spec.json                       # full render (parallel, all CPU cores)
  python render.py spec.json --quality draft       # fast low-res render to check pacing
  python render.py spec.json --preview             # draft quality, first 12 s only
  python render.py spec.json --still 3.5 -o a.png  # one frame as PNG (check layout)
  python render.py spec.json --validate            # lint the spec (JSON report), no render
  python render.py spec.json --estimate            # time a few frames, predict render time
  python render.py --doctor                        # check ffmpeg / fonts / TTS / CPU cores
Options: --workers N  --progress-file p.json  --quality draft|standard|high  -o out.mp4

New in v3: scene types motion / kinetic / logo_reveal / ring, 20 text presets, keyframes + expressions, layers with
parenting, particles, frame FX + camera, 13 full-frame transitions, animated backgrounds, procedural SFX, video_fx
for EDIT mode, bundled Urdu/Hindi fonts with glyph checking.   See references/motion.md.

Needs: ffmpeg, Pillow, numpy.
"""
import argparse, json, math, multiprocessing as mp, os, platform, re, shutil, subprocess, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw, ImageFont
from mve import backgrounds as bgfx, builders as bld, fx as fxm, layers as lyr, presets as prs, sfx as sfxm, transitions as trn
from mve.fonts import FontKit, script_of, ARABIC_RE, DEVA_RE

VERSION = "3.0"

# ----------------------------------------------------------------- constants
THEMES = {
    "neon":   dict(accent=(0, 229, 255),   text=(255, 255, 255), muted=(190, 200, 225),
                   bgs=[((15, 12, 41), (48, 43, 99)), ((8, 20, 48), (22, 78, 99)), ((30, 8, 60), (90, 24, 154))]),
    "clean":  dict(accent=(37, 99, 235),   text=(17, 24, 39),    muted=(75, 85, 99),
                   bgs=[((248, 250, 252), (226, 232, 240)), ((255, 255, 255), (219, 234, 254))]),
    "sunset": dict(accent=(255, 209, 102), text=(255, 255, 255), muted=(255, 232, 214),
                   bgs=[((255, 94, 98), (255, 153, 102)), ((131, 58, 180), (253, 29, 29)), ((255, 126, 95), (254, 180, 123))]),
    "mono":   dict(accent=(250, 250, 250), text=(250, 250, 250), muted=(160, 160, 160),
                   bgs=[((10, 10, 10), (38, 38, 38)), ((0, 0, 0), (24, 24, 27))]),
    "forest": dict(accent=(167, 243, 208), text=(255, 255, 255), muted=(209, 250, 229),
                   bgs=[((6, 78, 59), (4, 120, 87)), ((2, 44, 34), (16, 100, 80))]),
    "gold":   dict(accent=(245, 197, 66),  text=(255, 250, 235), muted=(200, 185, 150),
                   bgs=[((8, 8, 8), (40, 32, 12)), ((0, 0, 0), (28, 22, 8)), ((18, 14, 4), (60, 46, 14))]),
    "cyber":  dict(accent=(255, 56, 140),  text=(255, 255, 255), muted=(200, 190, 230),
                   bgs=[((13, 2, 33), (60, 9, 108)), ((5, 5, 30), (110, 20, 120)), ((20, 0, 40), (0, 80, 120))]),
    "ocean":  dict(accent=(94, 234, 212),  text=(255, 255, 255), muted=(186, 230, 253),
                   bgs=[((3, 37, 76), (14, 116, 144)), ((8, 47, 73), (3, 105, 161)), ((2, 20, 44), (8, 90, 110))]),
    "rose":   dict(accent=(253, 224, 71),  text=(255, 255, 255), muted=(254, 215, 226),
                   bgs=[((136, 19, 55), (225, 29, 72)), ((76, 5, 25), (159, 18, 57)), ((157, 23, 77), (244, 114, 182))]),
}
ASPECTS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1080, 1080), "4:5": (864, 1080), "21:9": (1680, 720),
           "4:3": (960, 720), "3:4": (720, 960)}
# resolution = pixels on the SHORT side (so 1080 = 1920x1080 landscape = 1080x1920 portrait); names users say -> short side
RES_NAMES = {"sd": 480, "480p": 480, "hd": 720, "720p": 720, "fhd": 1080, "fullhd": 1080, "full hd": 1080, "1080p": 1080, "2k": 1440, "qhd": 1440,
             "1440p": 1440, "4k": 2160, "uhd": 2160, "ultrahd": 2160, "ultra hd": 2160, "2160p": 2160}
DEFAULT_RESOLUTION, MAX_RESOLUTION = 1080, 2160


def parse_resolution(v):
    """720 | "1080p" | "2k" | "4k" | "uhd" | "1920x1080" -> short-side pixels (None when unreadable)."""
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return int(v) if v > 0 else None
    t = str(v).strip().lower().replace("_", " ")
    if t in RES_NAMES: return RES_NAMES[t]
    m = re.fullmatch(r"(\d{3,5})\s*[x\u00d7]\s*(\d{3,5})", t)
    if m: return min(int(m.group(1)), int(m.group(2)))
    return int(t) if t.isdigit() and int(t) > 0 else (int(t[:-1]) if t.endswith("p") and t[:-1].isdigit() else None)


def aspect_size(aspect):
    """"16:9" -> (1280, 720)-style base size (short side 720). Accepts any "W:H"."""
    if aspect in ASPECTS: return ASPECTS[aspect]
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", str(aspect or ""))
    if m and float(m.group(1)) > 0 and float(m.group(2)) > 0:
        a, b = float(m.group(1)), float(m.group(2))
        return (int(round(720 * a / b / 2) * 2), 720) if a >= b else (720, int(round(720 * b / a / 2) * 2))
    return None


def avail_mem_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable"): return int(line.split()[1]) / 1024.0
    except OSError: pass
    try:
        import psutil; return psutil.virtual_memory().available / 2 ** 20
    except Exception: return None


def safe_workers(workers, W, H):
    """Each worker holds several full frames (fx, blends): cap the pool so big resolutions never run out of RAM."""
    per = 350 + W * H * 3 * 16 / 2 ** 20
    mem = avail_mem_mb()
    cap = max(1, int(mem * 0.7 / per)) if mem else (workers if W * H < 3000000 else 4)
    return max(1, min(workers, cap))
PRESETS = {  # "preset" shortcut in a spec; explicit keys always win
    "reel": dict(aspect="9:16", fps=30), "tiktok": dict(aspect="9:16", fps=30), "short": dict(aspect="9:16", fps=30),
    "story": dict(aspect="9:16", fps=30), "youtube": dict(aspect="16:9", resolution=1080, fps=30),
    "promo": dict(aspect="16:9", resolution=1080, fps=30), "square": dict(aspect="1:1", fps=30),
    "instagram": dict(aspect="4:5", fps=30),
}
PROFILES = {  # quality -> encoder settings
    "draft":    dict(max_side=640, fps=15, preset="ultrafast", crf=30),
    "standard": dict(max_side=None, fps=None, preset="veryfast", crf=20),
    "high":     dict(max_side=None, fps=None, preset="medium", crf=17),
}
SCENE_TYPES = {"title", "text", "bullets", "quote", "stat", "outro", "lower_third", "caption", "image", "chart", "steps"} | bld.MOTION_TYPES
FULLSCREEN = {"title", "text", "bullets", "quote", "stat", "outro", "chart", "steps"} | bld.MOTION_TYPES
ANIMATIONS = {"fade", "slide_up", "slide_left", "slide_right", "pop", "typewriter", "words"} | set(prs.PRESET_NAMES)
TRANSITIONS = {"fade", "slide", "wipe", "none"} | set(trn.FULL_TRANSITIONS)
TOP_KEYS = {"input_video", "output", "aspect", "width", "height", "resolution", "fps", "theme", "font", "font_ur",
            "font_ar", "font_hi", "grade", "trim", "speed", "music", "music_volume", "voiceover", "voiceover_volume",
            "source_volume", "tts", "logo", "progress_bar", "scenes", "preset", "srt", "transition", "duck",
            "watermark", "auto_duration", "quality", "title", "notes", "sfx", "sfx_volume", "bg_style", "fx", "camera",
            "video_fx", "urdu_style", "seed", "resolution", "codec", "crf", "fps", "width", "height", "aspect"}
GRADES = {
    "warm": "eq=contrast=1.06:saturation=1.15:gamma_r=1.04:gamma_b=0.96",
    "cool": "eq=contrast=1.06:saturation=1.1:gamma_r=0.96:gamma_b=1.05",
    "punchy": "eq=contrast=1.12:saturation=1.3",
    "cinematic": "eq=contrast=1.1:saturation=0.9:gamma=0.95",
    "bw": "hue=s=0,eq=contrast=1.1",
    "teal_orange": "colorbalance=rs=-0.12:bs=0.14:rm=0.04:bm=-0.04:rh=0.16:bh=-0.14,eq=contrast=1.08:saturation=1.18",
    "vintage": "curves=r='0/0.06 0.5/0.52 1/0.94':b='0/0.1 0.5/0.46 1/0.82',eq=saturation=0.8:contrast=1.04",
    "faded": "eq=contrast=0.88:saturation=0.82:brightness=0.04",
    "dramatic": "eq=contrast=1.22:saturation=0.85:gamma=0.92",
}


def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): return 1 - (1 - clamp(x)) ** 3
def ease_back(x):
    x = clamp(x); c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
def rgba(c, a=255): return (c[0], c[1], c[2], a)
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe(path):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
            "-of", "json", path])
    j = json.loads(r.stdout or "{}")
    info = dict(duration=float(j.get("format", {}).get("duration", 0) or 0), w=0, h=0, audio=False)
    for s in j.get("streams", []):
        if s["codec_type"] == "video" and not info["w"]:
            info["w"], info["h"] = s["width"], s["height"]
        if s["codec_type"] == "audio":
            info["audio"] = True
    return info


# ----------------------------------------------------------------- context / text
class Ctx:
    def __init__(self, spec, W, H, video_mode):
        self.W, self.H, self.video_mode = W, H, video_mode
        self.S = min(W, H) / 720.0
        th = spec.get("theme", "neon")
        base = dict(THEMES.get(th, THEMES["neon"])) if isinstance(th, str) else dict(THEMES["neon"])
        if isinstance(th, dict):
            base.update({k: (tuple(v) if k in ("accent", "text", "muted") else v) for k, v in th.items()})
            if "bgs" in th:
                base["bgs"] = [(tuple(a), tuple(b)) for a, b in th["bgs"]]
        self.theme = base
        self.fk = FontKit(spec)

    def font(self, size, text=""):
        return self.fk.get(text or "Ag", size * self.S)


def wrap(font, text, maxw):
    out = []
    for para in str(text).split("\n"):
        words, cur = para.split(" "), ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.getlength(t) <= maxw or not cur:
                cur = t
            else:
                out.append(cur); cur = w
        out.append(cur)
    return out


def layout_blocks(ctx, blocks, maxw_frac, k):
    laid, total = [], 0
    for b in blocks:
        if b.get("bar"):
            h = int((b["h"] + b.get("gap", 0)) * ctx.S); laid.append((b, None, None, h)); total += h; continue
        f = ctx.font(b["size"] * k, b["text"]); lines = wrap(f, b["text"], ctx.W * maxw_frac)
        lh = int(max(b["size"] * k * ctx.S * 1.25, sum(f.getmetrics()) * 0.92))
        laid.append((b, f, lines, lh)); total += lh * len(lines) + int(b.get("gap", 0) * ctx.S)
    return laid, total


def render_stack(ctx, blocks, anchor_y=0.5, align="center", x_frac=0.5, maxw_frac=0.84, reveal=1.0, unit="chars",
                 max_h_frac=0.82):
    """Draw text blocks; auto-shrinks the font when the text would not fit the screen."""
    W, H, S = ctx.W, ctx.H, ctx.S
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    k = 1.0
    laid, total = layout_blocks(ctx, blocks, maxw_frac, k)
    while total > H * max_h_frac and k > 0.4:
        k *= 0.9; laid, total = layout_blocks(ctx, blocks, maxw_frac, k)
    if unit == "words":
        n_all = sum(len(l.split()) for _, _, ls, _ in laid if ls for l in ls)
        shown = math.ceil(n_all * reveal) if reveal < 1 else n_all
    else:
        n_all = sum(len(l) for _, _, ls, _ in laid if ls for l in ls)
        shown = int(n_all * reveal) if reveal < 1 else n_all
    y = anchor_y * H - total / 2
    for b, f, lines, lh in laid:
        if b.get("bar"):
            bw, bh = int(b["w"] * S), int(b["h"] * S); cx = W * x_frac if align == "center" else W * x_frac + bw / 2
            d.rounded_rectangle([cx - bw / 2, y, cx + bw / 2, y + bh], radius=bh // 2, fill=rgba(ctx.theme["accent"]))
            y += lh; continue
        col = rgba(b.get("color", ctx.theme["text"]))
        stroke = int(2 * S) if ctx.video_mode else 0
        for line in lines:
            if shown <= 0: break
            if unit == "words":
                ws = line.split(" "); take = min(len(ws), shown); txt = " ".join(ws[:take]); shown -= len(ws)
            else:
                txt = line[:shown]; shown -= len(line)
            w = f.getlength(line)
            x = W * x_frac - w / 2 if align == "center" else W * x_frac
            d.text((x + 3 * S, y + 3 * S), txt, font=f, fill=(0, 0, 0, 90))
            d.text((x, y), txt, font=f, fill=col, stroke_width=stroke, stroke_fill=(0, 0, 0, 200))
            y += lh
        y += int(b.get("gap", 0) * S)
    return layer


def crop_layer(layer):
    """Crop to the non-transparent bbox -> (img, ox, oy) or None. Makes compositing far cheaper."""
    bb = layer.getchannel("A").getbbox()
    if not bb: return None
    return layer.crop(bb), bb[0], bb[1]


def paste_rgba(dst, src, x, y):
    W, H = dst.size; w, h = src.size
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0: return
    if (x0, y0, x1, y1) != (x, y, x + w, y + h): src = src.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    if dst.mode == "RGB": dst.paste(src, (x0, y0), src)      # opaque canvas (create mode): fastest path
    else: dst.alpha_composite(src, (x0, y0))


def fade_img(img, k):
    lut = [int(i * k) for i in range(256)]
    out = img.copy(); out.putalpha(img.getchannel("A").point(lut)); return out


# ----------------------------------------------------------------- elements
class Element:
    """One animated piece. Static: layer=RGBA. Dynamic: fn(reveal)->RGBA layer (reveal 0..1)."""
    def __init__(self, layer=None, fn=None, anim="slide_up", delay=0.0, typewriter=False, reveal_dur=None):
        self.layer, self.fn, self.anim, self.delay = layer, fn, anim, delay
        self.typewriter, self.reveal_dur = typewriter, reveal_dur
        self._static, self._cache = None, {}

    def get(self, reveal):
        if self.fn is None:
            if self._static is None: self._static = crop_layer(self.layer) or False
            return self._static or None
        k = round(reveal, 3)
        if k not in self._cache:
            if len(self._cache) > 300: self._cache.clear()
            self._cache[k] = crop_layer(self.fn(reveal)) or False
        return self._cache[k] or None


def elem_params(el, tl, dur, ctx, in_t=0.55, out_t=0.4, hold=False):
    """Animation state at local time tl -> hashable tuple, or None when invisible."""
    te = tl - el.delay
    if te < 0: return None
    a_in = ease_out(te / in_t); a_out = 1.0 if hold else clamp((dur - tl) / out_t)
    alpha = a_out if el.typewriter else min(a_in, a_out)
    if alpha <= 0: return None
    reveal = 1.0
    if el.typewriter: reveal = clamp(te / max(0.6, min(dur * 0.55, 3.0)))
    elif el.reveal_dur: reveal = clamp(te / el.reveal_dur)
    S = ctx.S; dx = dy = 0; sc = 1.0
    if el.anim == "slide_up": dy = int((1 - a_in) * 70 * S)
    elif el.anim == "slide_left": dx = int(-(1 - a_in) * 140 * S)
    elif el.anim == "slide_right": dx = int((1 - a_in) * 140 * S)
    elif el.anim == "pop":
        sc = 0.7 + 0.3 * ease_back(te / 0.7)
        if abs(sc - 1) < 0.004: sc = 1.0
    qa = 1.0 if alpha > 0.99 else round(alpha * 50) / 50
    return (qa, round(reveal, 3), dx, dy, round(sc, 3))


def draw_element(canvas, el, p):
    qa, rv, dx, dy, sc = p
    got = el.get(rv)
    if got is None: return
    img, ox, oy = got
    if sc != 1.0:
        w, h = img.size; nw, nh = max(1, int(w * sc)), max(1, int(h * sc))
        img = img.resize((nw, nh), Image.BILINEAR); ox += (w - nw) // 2; oy += (h - nh) // 2
    if qa < 1.0: img = fade_img(img, qa)
    paste_rgba(canvas, img, ox + dx, oy + dy)


# ----------------------------------------------------------------- scene builders
def parse_value(v):
    """'10,000+' -> ('', 10000.0, '+', 0, True)  (prefix, number, suffix, decimals, has_commas)."""
    m = re.match(r"^([^\d\-]*)(-?\d[\d,]*\.?\d*)(.*)$", str(v).strip())
    if not m: return None
    num = m.group(2)
    try: val = float(num.replace(",", ""))
    except ValueError: return None
    dec = len(num.split(".")[1]) if "." in num else 0
    return m.group(1), val, m.group(3), dec, "," in num


def fmt_value(parsed, k):
    pre, val, suf, dec, commas = parsed
    x = val * k
    s = f"{x:,.{dec}f}" if commas else f"{x:.{dec}f}"
    return f"{pre}{s}{suf}"


def norm_chart_items(items):
    out = []
    for it in items:
        if isinstance(it, dict): lab, val = it.get("label", ""), it.get("value", 0)
        elif isinstance(it, (list, tuple)): lab, val = it[0], it[1]
        else:
            lab, _, val = str(it).rpartition(":"); lab = lab or str(it)
        try: fv = float(str(val).replace(",", "").rstrip("%+kKmMxX"))
        except ValueError: fv = 0.0
        out.append((str(lab), fv, str(val)))
    return out


def heading_el(ctx, text):
    return Element(layer=render_stack(ctx, [dict(text=text, size=56), dict(bar=True, w=100, h=6)],
                                      anchor_y=0.13, align="left", x_frac=0.10), anim="slide_left")


def build_scene(ctx, sc):
    if bld.uses_motion_engine(sc):
        return bld.build_motion_scene(ctx, sc)
    t = sc.get("type", "text"); S = ctx.S; th = ctx.theme
    anim = sc.get("animation")
    text, sub = str(sc.get("text", "")), str(sc.get("subtext", ""))
    els = []
    if ctx.video_mode and t in FULLSCREEN and sc.get("dim", True):
        els.append(Element(layer=Image.new("RGBA", (ctx.W, ctx.H), (0, 0, 0, int(255 * sc.get("dim_alpha", 0.55)))), anim="fade"))
    tw = anim in ("typewriter", "words"); unit = "words" if anim == "words" else "chars"

    if t in ("title", "outro"):
        blocks = [dict(text=text, size=sc.get("size", 76)), dict(bar=True, w=120, h=6, gap=18)]
        if sub: blocks.append(dict(text=sub, size=34, color=th["muted"]))
        if t == "outro" and sc.get("cta"):
            blocks.append(dict(text=sc["cta"], size=30, color=th["accent"], gap=0))
            blocks[-2]["gap"] = 26 if sub else 0
        els.append(Element(fn=lambda r, b=blocks: render_stack(ctx, b, reveal=r, unit=unit), anim=anim or "pop", typewriter=tw))
    elif t == "text":
        blocks = [dict(text=text, size=sc.get("size", 58))]
        if sub: blocks.append(dict(text=sub, size=30, color=th["muted"], gap=16))
        els.append(Element(fn=lambda r, b=blocks: render_stack(ctx, b, reveal=r, unit=unit), anim=anim or "slide_up", typewriter=tw))
    elif t == "quote":
        blocks = [dict(text="\u201C" + text + "\u201D", size=sc.get("size", 54))]
        if sub: blocks.append(dict(text="\u2014 " + sub, size=30, color=th["accent"], gap=24))
        els.append(Element(fn=lambda r, b=blocks: render_stack(ctx, b, reveal=r, unit=unit), anim=anim or "fade", typewriter=tw))
    elif t == "stat":
        val = str(sc.get("value", text))
        parsed = parse_value(val) if sc.get("count_up", True) else None
        def stat_fn(r, val=val, parsed=parsed):
            shown = fmt_value(parsed, ease_out(r)) if parsed else val
            b = [dict(text=shown, size=sc.get("size", 140), color=th["accent"])]
            if sub: b.append(dict(text=sub, size=40, gap=10))
            return render_stack(ctx, b)
        els.append(Element(fn=stat_fn, anim=anim or "pop", reveal_dur=1.4 if parsed else None))
    elif t == "bullets":
        items = sc.get("items", []); top = 0.22 if text else 0.30
        if text: els.append(heading_el(ctx, text))
        row_h = 0.12 if ctx.H >= ctx.W else 0.13
        for i, it in enumerate(items):
            ay = top + 0.14 + i * row_h
            lay = render_stack(ctx, [dict(text=str(it), size=sc.get("size", 40))], anchor_y=ay, align="left",
                               x_frac=0.155, maxw_frac=0.74)
            dot = ImageDraw.Draw(lay); r = int(9 * S); cy = int(ay * ctx.H)
            dot.ellipse([ctx.W * 0.11, cy - r, ctx.W * 0.11 + 2 * r, cy + r], fill=rgba(th["accent"]))
            els.append(Element(layer=lay, anim="slide_left", delay=0.35 * (i + 1)))
    elif t == "steps":
        items = sc.get("items", []); top = 0.22 if text else 0.30
        if text: els.append(heading_el(ctx, text))
        row_h = 0.14 if ctx.H >= ctx.W else 0.15
        for i, it in enumerate(items):
            ay = top + 0.14 + i * row_h
            lay = render_stack(ctx, [dict(text=str(it), size=sc.get("size", 38))], anchor_y=ay, align="left",
                               x_frac=0.2, maxw_frac=0.68)
            d = ImageDraw.Draw(lay); r = int(30 * S); cx, cy = int(ctx.W * 0.11 + r), int(ay * ctx.H)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgba(th["accent"]))
            f = ctx.font(34, str(i + 1)); n = str(i + 1)
            d.text((cx - f.getlength(n) / 2, cy - 21 * S), n, font=f, fill=(10, 10, 20, 255))
            els.append(Element(layer=lay, anim="slide_left", delay=0.4 * (i + 1)))
    elif t == "chart":
        items = norm_chart_items(sc.get("items", [])); n = max(1, len(items)); vmax = max([v for _, v, _ in items] + [1e-9])
        if text: els.append(heading_el(ctx, text))
        def chart_fn(r, items=items):
            lay = Image.new("RGBA", (ctx.W, ctx.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
            y0 = ctx.H * (0.27 if text else 0.18); area = ctx.H * 0.88 - y0
            row = min(ctx.H * 0.13, area / n); bh = row * 0.5
            fl = ctx.font(30, " ".join(l for l, _, _ in items)); fv = ctx.font(30)
            lw = max([fl.getlength(l) for l, _, _ in items] + [10]); x0 = ctx.W * 0.08 + lw + 24 * S
            xmax = ctx.W * 0.88 - fv.getlength("00,000%") - 20 * S
            for i, (lab, v, raw) in enumerate(items):
                k = ease_out(clamp(r * 1.5 - i * 0.12)); yc = y0 + row * i + row / 2
                d.text((ctx.W * 0.08, yc - 20 * S), lab, font=ctx.font(30, lab), fill=rgba(th["text"]))
                bw = max(2, (xmax - x0) * (v / vmax) * k)
                d.rounded_rectangle([x0, yc - bh / 2, x0 + bw, yc + bh / 2], radius=int(bh / 3), fill=rgba(th["accent"]))
                pv = parse_value(raw); shown = fmt_value(pv, k) if pv else raw
                d.text((x0 + bw + 14 * S, yc - 20 * S), shown, font=fv, fill=rgba(th["text"]))
            return lay
        els.append(Element(fn=chart_fn, anim="fade", reveal_dur=1.6))
    elif t == "lower_third":
        f1, f2 = ctx.font(38, text), ctx.font(26, sub)
        w = int(max(f1.getlength(text), f2.getlength(sub)) + 70 * S); h = int((84 if sub else 62) * S)
        x, y = int(ctx.W * 0.06), int(ctx.H * 0.80 - h / 2)
        lay = Image.new("RGBA", (ctx.W, ctx.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        d.rounded_rectangle([x, y, x + w, y + h], radius=int(12 * S), fill=(10, 10, 20, 215))
        d.rounded_rectangle([x, y, x + int(8 * S), y + h], radius=int(4 * S), fill=rgba(th["accent"]))
        d.text((x + 28 * S, y + 10 * S), text, font=f1, fill=(255, 255, 255, 255))
        if sub: d.text((x + 28 * S, y + 52 * S), sub, font=f2, fill=rgba(th["accent"]))
        els.append(Element(layer=lay, anim=anim or "slide_left"))
    elif t == "caption":
        f = ctx.font(sc.get("size", 36), text); lines = wrap(f, text or " ", ctx.W * 0.82)
        lh = int(sc.get("size", 36) * ctx.S * 1.3); h = lh * len(lines) + int(24 * S)
        w = int(max(f.getlength(l) for l in lines) + 50 * S)
        x, y = int((ctx.W - w) / 2), int(ctx.H * 0.88 - h)
        lay = Image.new("RGBA", (ctx.W, ctx.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        d.rounded_rectangle([x, y, x + w, y + h], radius=int(14 * S), fill=(0, 0, 0, 170))
        for i, l in enumerate(lines):
            d.text((ctx.W / 2 - f.getlength(l) / 2, y + 12 * S + i * lh), l, font=f, fill=(255, 255, 255, 255))
        els.append(Element(layer=lay, anim=anim or "fade"))
    elif t == "image":
        im = Image.open(sc.get("path")).convert("RGBA")
        scale = min(ctx.W * 0.8 / im.width, ctx.H * 0.7 / im.height)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        lay = Image.new("RGBA", (ctx.W, ctx.H), (0, 0, 0, 0))
        lay.paste(im, ((ctx.W - im.width) // 2, int((ctx.H - im.height) * (0.38 if text else 0.5))))
        els.append(Element(layer=lay, anim=anim or "pop"))
        if text:
            els.append(Element(layer=render_stack(ctx, [dict(text=text, size=40)], anchor_y=0.9), anim="slide_up", delay=0.3))
    else:
        raise SystemExit(f"Unknown scene type: {t}")
    return els


# ----------------------------------------------------------------- backgrounds (create mode)
def make_grad_img(W, H, c1, c2):
    xs = np.linspace(0, 1, W, dtype=np.float32)[None, :]; ys = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    s = (xs * 0.55 + ys * 0.45)[..., None]
    g = np.array(c1, np.float32) * (1 - s) + np.array(c2, np.float32) * s
    return Image.fromarray(g.astype(np.uint8), "RGB")


def load_cover(path, W, H, dim):
    im = Image.open(path).convert("RGB"); Wt, Ht = int(W * 1.25), int(H * 1.25)
    k = max(Wt / im.width, Ht / im.height)
    im = im.resize((max(Wt, int(im.width * k + .5)), max(Ht, int(im.height * k + .5))), Image.LANCZOS)
    l, t = (im.width - Wt) // 2, (im.height - Ht) // 2
    im = im.crop((l, t, l + Wt, t + Ht))
    return Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), dim)


def scene_bg_img(st, i, tl):
    b = st.bgs[i]
    if b["kind"] == "grad": return b["img"]
    s = st.scenes[i]; dur = max(0.1, s["end"] - s["start"]); p = clamp(tl / dur)
    z = 1 + 0.12 * (p if i % 2 == 0 else 1 - p)
    iw, ih = b["img"].size; vw, vh = iw / z, ih / z; l, t = (iw - vw) / 2, (ih - vh) / 2
    return b["img"].resize((st.W, st.H), Image.BILINEAR, box=(l, t, l + vw, t + vh))


def transition_img(kind, prev, cur, k, W, H):
    if kind == "slide":
        c = Image.new("RGB", (W, H)); c.paste(prev, (int(-k * W), 0)); c.paste(cur, (int((1 - k) * W), 0)); return c
    if kind == "wipe":
        m = Image.new("L", (W, H), 0); ImageDraw.Draw(m).rectangle([0, 0, int(k * W), H], fill=255)
        c = prev.copy(); c.paste(cur, (0, 0), m); return c
    return Image.blend(prev, cur, k)


def bg_base(st, i, tl, with_tr=True):
    """RGB background of scene i at local time tl (gradient / image + old-style bg transition + animated style)."""
    s = st.scenes[i]; cur = scene_bg_img(st, i, tl); t = s["start"] + tl
    if with_tr and i > 0 and s["trd"] > 0 and tl < s["trd"] and s["tr"] in ("fade", "slide", "wipe"):
        pv = st.scenes[i - 1]
        prev = scene_bg_img(st, i - 1, pv["end"] - pv["start"])
        cur = transition_img(s["tr"], prev, cur, ease_out(tl / s["trd"]), st.W, st.H)
    img = cur.copy()   # RGB
    style = s["bg_style"]
    if style == "soft":
        bw, bh = max(8, st.W // 4), max(8, st.H // 4)
        small = Image.new("L", (bw, bh), 0); d = ImageDraw.Draw(small)
        for n in range(5):
            r = (90 + 55 * n) * st.ctx.S / 4
            cx = bw * (0.5 + 0.42 * math.sin(t * 0.25 + n * 1.7)); cy = bh * (0.5 + 0.38 * math.cos(t * 0.21 + n * 2.3))
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=14 + 3 * (n % 3))
        img.paste((255, 255, 255), (0, 0), small.resize((st.W, st.H), Image.BILINEAR))
    elif style != "none":
        th = st.ctx.theme; alt = tuple(min(255, int(c * 1.3 + 40)) for c in th["bgs"][i % len(th["bgs"])][1])
        bgfx.draw(style, img, t, st.ctx.S, tuple(th["accent"]), alt)
    return img


def draw_scene_els(st, i, tl, canvas, hold, tick):
    s = st.scenes[i]; dur = s["end"] - s["start"]
    for el in s["els"]:
        if getattr(el, "is_motion", False):
            el.draw(canvas, el.params(tl, dur, hold), dur, tick)
        else:
            p = elem_params(el, tl, dur, st.ctx, hold=hold)
            if p: draw_element(canvas, el, p)


def scene_frame(st, i, tl, tick, hold=False, with_tr=True):
    """One complete frame of scene i at local time tl: background, elements, scene fx + camera."""
    s = st.scenes[i]; dur = s["end"] - s["start"]
    canvas = Image.new("RGBA", (st.W, st.H), (0, 0, 0, 0)) if st.video_mode else bg_base(st, i, tl, with_tr)
    draw_scene_els(st, i, tl, canvas, hold, tick)
    if s["fx"].active: canvas = s["fx"].apply(canvas, tl, dur, tick)
    return canvas


def scene_index(st, t):
    idx = 0
    for n, s in enumerate(st.scenes):
        if s["start"] <= t: idx = n
    return idx


# ----------------------------------------------------------------- state (built in main and in every worker)
class State: pass


def profile_for(spec, opts):
    q = opts.get("quality") or spec.get("quality") or "standard"
    if opts.get("preview"): q = "draft"
    return q, PROFILES.get(q, PROFILES["standard"])


def apply_presets(spec):
    p = PRESETS.get(str(spec.get("preset", "")).lower())
    if p:
        for k, v in p.items(): spec.setdefault(k, v)
    return spec


def build_state(spec, opts):
    st = State(); st.spec = spec
    st.video_mode = bool(spec.get("input_video"))
    q, prof = profile_for(spec, opts); st.quality, st.prof = q, prof
    st.fps = int(prof["fps"] or spec.get("fps", 30))
    trim = spec.get("trim", {}) if st.video_mode else {}
    speed = float(spec.get("speed", 1) or 1) if st.video_mode else 1.0
    st.speed = speed
    want = parse_resolution(opts.get("resolution")) or parse_resolution(spec.get("resolution"))
    want = min(want, MAX_RESOLUTION * 2) if want else None
    if st.video_mode:
        st.info = probe(spec["input_video"])
        if not st.info["w"]: raise SystemExit("Could not read input video")
        W, H = st.info["w"], st.info["h"]
        if want:                                    # explicit resolution: short side = want (up- or down-scales the footage)
            f = want / min(W, H); W, H = int(round(W * f)), int(round(H * f))
        else:                                       # default: keep the source, but never above 1080p-class (fast, sharp)
            cap = prof["max_side"] or 1920
            if max(W, H) > cap: k = cap / max(W, H); W, H = int(W * k), int(H * k)
        if prof["max_side"] and max(W, H) > prof["max_side"]:
            k = prof["max_side"] / max(W, H); W, H = int(W * k), int(H * k)
        st.t0 = float(trim.get("start", 0)); st.t1 = float(trim.get("end", st.info["duration"]))
        D = max(0.5, (st.t1 - st.t0) / speed)
    else:
        explicit = spec.get("width") and spec.get("height") and not spec.get("aspect")
        if explicit and not want: bw, bh = float(spec["width"]), float(spec["height"])
        else:
            bw, bh = aspect_size(spec.get("aspect", "16:9")) or (1280, 720)
            f = (want or DEFAULT_RESOLUTION) / min(bw, bh); bw, bh = bw * f, bh * f
        W, H = int(bw), int(bh)
        if prof["max_side"] and max(W, H) > prof["max_side"]:
            k = prof["max_side"] / max(W, H); W, H = int(W * k), int(H * k)
        D = sum(float(s.get("duration", 3)) for s in spec["scenes"]); st.t0 = 0
    st.W, st.H = int(W) // 2 * 2, int(H) // 2 * 2
    if opts.get("preview"): D = min(D, 12)
    st.D = D
    st.ctx = Ctx(spec, st.W, st.H, st.video_mode)
    ctx = st.ctx
    default_tr = spec.get("transition", "fade")
    st.scenes, cursor = [], 0.0
    for n, sc in enumerate(spec["scenes"]):
        dur = float(sc.get("duration", 3))
        explicit = st.video_mode and "start" in sc
        start = float(sc["start"]) if explicit else cursor
        if not explicit: cursor += dur
        tr = sc.get("transition", default_tr)
        bgs_ = sc.get("bg_style", spec.get("bg_style", "soft"))
        st.scenes.append(dict(sc=sc, start=start, end=start + dur, els=build_scene(ctx, sc),
                              tr=tr if tr in TRANSITIONS else "fade", trd=float(sc.get("transition_duration", 0.6)),
                              fx=fxm.FxStack(sc.get("fx", spec.get("fx")), sc.get("camera", spec.get("camera")), ctx),
                              bg_style=bgs_ if bgs_ in bgfx.STYLES else "soft", hold=False))
    for i, s_ in enumerate(st.scenes[:-1]):      # a scene followed by a full-frame transition keeps playing underneath it
        nx = st.scenes[i + 1]
        s_["hold"] = (not st.video_mode) and nx["tr"] in trn.FULL_TRANSITIONS and nx["trd"] > 0
    st.bgs = []
    if not st.video_mode:
        th = ctx.theme
        for i, s in enumerate(st.scenes):
            sc = s["sc"]
            if sc.get("bg_image") and os.path.exists(sc["bg_image"]):
                st.bgs.append(dict(kind="img", img=load_cover(sc["bg_image"], st.W, st.H, float(sc.get("bg_dim", 0.45)))))
            else:
                b = sc.get("bg", i)
                pal = (tuple(b[0]), tuple(b[1])) if isinstance(b, list) else th["bgs"][int(b) % len(th["bgs"])]
                st.bgs.append(dict(kind="grad", img=make_grad_img(st.W, st.H, *pal)))
    st.logo = None
    if spec.get("logo") and os.path.exists(spec["logo"]):
        lg = Image.open(spec["logo"]).convert("RGBA"); k = 70 * ctx.S / lg.height
        st.logo = lg.resize((max(1, int(lg.width * k)), int(lg.height * k)), Image.LANCZOS)
    st.wm = None
    if spec.get("watermark"):
        f = ctx.font(24, str(spec["watermark"])); txt = str(spec["watermark"])
        lay = Image.new("RGBA", (ctx.W, ctx.H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        d.text((ctx.W - f.getlength(txt) - 28 * ctx.S, ctx.H - 56 * ctx.S), txt, font=f, fill=(255, 255, 255, 120))
        st.wm = crop_layer(lay)
    return st


def active_params(st, t):
    """EDIT mode: hashable description of everything visible at t (used to skip identical frames)."""
    out = []
    for s in st.scenes:
        if s["start"] <= t < s["end"]:
            tl = t - s["start"]; dur = s["end"] - s["start"]
            for el in s["els"]:
                p = el.params(tl, dur, False) if getattr(el, "is_motion", False) else elem_params(el, tl, dur, st.ctx)
                if p: out.append((el, p))
            if s["fx"].active: out.append((s["fx"], round(tl, 5)))      # fx are time-varying: never dedupe
    return out


def compose(st, t, items=None):
    tick = int(round(t * st.fps))
    if st.video_mode:
        base = Image.new("RGBA", (st.W, st.H), (0, 0, 0, 0))
        for i, s in enumerate(st.scenes):
            if s["start"] <= t < s["end"]:
                tl = t - s["start"]
                if s["fx"].active: base.alpha_composite(scene_frame(st, i, tl, tick))
                else: draw_scene_els(st, i, tl, base, False, tick)
    else:
        i = scene_index(st, t); s = st.scenes[i]; tl = t - s["start"]
        full = i > 0 and s["tr"] in trn.FULL_TRANSITIONS and 0 < s["trd"] and tl < s["trd"]
        base = scene_frame(st, i, tl, tick, hold=s["hold"], with_tr=not full)
        if full:
            pv = st.scenes[i - 1]
            prev = scene_frame(st, i - 1, (pv["end"] - pv["start"]) + tl, tick, hold=True, with_tr=False)
            base = trn.blend(s["tr"], prev, base, tl / s["trd"], st.ctx.S, tick)
    if st.wm: paste_rgba(base, st.wm[0], st.wm[1], st.wm[2])
    if st.logo: paste_rgba(base, st.logo, st.W - st.logo.width - int(24 * st.ctx.S), int(20 * st.ctx.S))
    return base


_STATE = None
def _init_worker(spec, opts):
    global _STATE
    _STATE = build_state(spec, opts)


def _render_chunk(rng):
    """Render frames [n0, n1) -> list of (bytes, repeat). Identical consecutive frames are sent once."""
    st = _STATE; n0, n1 = rng; runs = []; last_sig = None
    for n in range(n0, n1):
        t = n / st.fps
        if st.video_mode:
            items = active_params(st, t); sig = tuple((id(el), p) for el, p in items)
            if runs and sig == last_sig:
                runs[-1][1] += 1; continue
            data = compose(st, t, items).tobytes(); last_sig = sig
        else:
            data = compose(st, t).tobytes()
        runs.append([data, 1])
    return runs


# ----------------------------------------------------------------- audio
def atempo_chain(s):
    parts = []
    while s > 2.0: parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5: parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.4f}"); return ",".join(parts)


def build_audio(spec, D, has_src, first_idx, speed):
    inputs, labels, filt, idx = [], [], [], first_idx
    if has_src:
        chain = f"[0:a]volume={spec.get('source_volume', 1.0)}"
        if speed != 1: chain += "," + atempo_chain(speed)
        filt.append(chain + "[a0]"); labels.append("[a0]")
    vo, mu = spec.get("voiceover"), spec.get("music")
    duck = bool(vo and mu and spec.get("duck", True))
    if vo:
        inputs += ["-i", vo]; vv = spec.get("voiceover_volume", 1.0)
        filt.append(f"[{idx}:a]volume={vv}" + (",asplit=2[a1][a1s]" if duck else "[a1]")); labels.append("[a1]"); idx += 1
    if mu:
        mv = spec.get("music_volume", 0.18 if duck else (0.12 if (vo or has_src) else 0.4))
        inputs += ["-stream_loop", "-1", "-i", mu]
        if duck:
            filt.append(f"[{idx}:a]volume={mv}[a2r]")
            filt.append("[a2r][a1s]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[a2]")
        else:
            filt.append(f"[{idx}:a]volume={mv}[a2]")
        labels.append("[a2]"); idx += 1
    if spec.get("_sfx_track"):
        inputs += ["-i", spec["_sfx_track"]]
        filt.append(f"[{idx}:a]volume={spec.get('_sfx_volume', 0.7)}[a3]"); labels.append("[a3]"); idx += 1
    if not labels: return [], None, ["-an"]
    n = len(labels)
    mix = "".join(labels) + (f"amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[am]" if n > 1 else "anull[am]")
    filt.append(mix)
    filt.append(f"[am]atrim=0:{D:.3f},afade=t=out:st={max(0, D - 1.5):.3f}:d=1.5[aout]")
    return inputs, ";".join(filt), ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]


def detect_lang(text, default):
    if ARABIC_RE.search(text): return "ur"
    if DEVA_RE.search(text): return "hi"
    return default


def fix_overlaps(spec, narrated=frozenset()):
    """EDIT mode: two full-screen scenes must not overlap. If the earlier scene carries narration the later one is
    pushed back (its voice-over must finish); otherwise the earlier scene is shortened."""
    idx = sorted((i for i, s in enumerate(spec["scenes"]) if "start" in s and s.get("type") in FULLSCREEN),
                 key=lambda i: float(spec["scenes"][i]["start"]))
    for a, b in zip(idx, idx[1:]):
        A, B = spec["scenes"][a], spec["scenes"][b]
        a_end = float(A["start"]) + float(A.get("duration", 3))
        if float(B["start"]) < a_end - 0.02:
            if a in narrated:
                print(f"  ! scene {b} moved from {float(B['start']):.1f}s to {a_end + 0.1:.1f}s (scene {a} narration still playing)")
                B["start"] = round(a_end + 0.1, 2)
            else:
                A["duration"] = round(max(0.8, float(B["start"]) - float(A["start"])), 2)
                print(f"  ! scene {a} shortened to {A['duration']}s so it does not overlap scene {b}")


def prepare_narration(spec, video_mode, tmpdir, workers=4):
    """Synthesize scene narrations (parallel, cached), stretch scene durations to fit, build spec['voiceover']."""
    import tts
    from concurrent.futures import ThreadPoolExecutor
    cfg = spec.get("tts", {}) or {}
    lang0, voice0, engine, rate = cfg.get("lang", "en"), cfg.get("voice"), cfg.get("engine", "auto"), cfg.get("rate")
    os.makedirs(tmpdir, exist_ok=True); cache = os.path.join(tmpdir, "cache")
    jobs = []
    for i, sc in enumerate(spec["scenes"]):
        if sc.get("narration"):
            lang = sc.get("lang") or (detect_lang(sc["narration"], lang0) if "lang" not in cfg else lang0)
            voice = sc.get("voice") or (voice0 if lang == lang0 else None)
            jobs.append((i, os.path.join(tmpdir, f"nar_{i:03d}.wav"), sc["narration"], lang, voice))
    if not jobs: return
    failed = []
    def run(j):
        i, wav, text, lang, voice = j
        try:
            used = tts.synth(text, wav, lang, voice, engine, rate, cache_dir=cache)
            return i, wav, used, probe(wav)["duration"]
        except Exception as e:                      # no engine / no internet: keep the timing, drop the voice
            failed.append(str(e).splitlines()[0][:160])
            return i, None, None, max(1.5, len(text.split()) / 2.6 + 0.5)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as ex: results = {r[0]: r for r in ex.map(run, jobs)}
    if failed:
        print(f"  ! voice-over unavailable for {len(failed)} scene(s): {failed[0]}")
        print("    -> rendering without that voice-over (scene lengths kept). Fix: pip install edge-tts | install espeak-ng | pip install pyttsx3")
    for i, sc in enumerate(spec["scenes"]):          # stretch scenes so every narration fits
        if i in results:
            d = results[i][3]; sc["duration"] = round(max(float(sc.get("duration", 3)), d + (0.35 if video_mode else 0.6)), 2)
    if video_mode: fix_overlaps(spec, {i for i in results})
    clips, cursor, prev_end = [], 0.0, 0.0
    for i, sc in enumerate(spec["scenes"]):
        dur = float(sc.get("duration", 3)); explicit = video_mode and "start" in sc
        start = float(sc["start"]) if explicit else cursor
        if i in results and results[i][1] is not None:
            _, wav, used, d = results[i]
            at = start + 0.12
            if at < prev_end:
                print(f"  ! narration of scene {i + 1} overlaps the previous line; pushed {prev_end - at + 0.15:.1f}s later")
                at = prev_end + 0.15
            clips.append((wav, at)); prev_end = at + d
            print(f"  narration scene {i + 1}: {d:.1f}s via {used}")
        if not explicit: cursor += dur
    if not clips: return
    out = os.path.join(tmpdir, "voiceover.wav")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for w, _ in clips: cmd += ["-i", w]
    n = len(clips)
    delays = ";".join(f"[{k}:a]adelay={int(st * 1000)}:all=1[v{k}]" for k, (_, st) in enumerate(clips))
    ins = "".join(f"[v{k}]" for k in range(n))
    r = sh(cmd + ["-filter_complex", f"{delays};{ins}amix=inputs={n}:duration=longest:normalize=0[o]", "-map", "[o]", "-ar", "44100", out])
    if r.returncode:  # old ffmpeg without normalize option
        r = sh(cmd + ["-filter_complex", f"{delays};{ins}amix=inputs={n}:duration=longest,volume={n}[o]", "-map", "[o]", "-ar", "44100", out])
    if r.returncode: raise SystemExit("voiceover build failed: " + r.stderr[-300:])
    spec["voiceover"] = out


# ----------------------------------------------------------------- sound effects
def scene_times(spec, video_mode):
    out, cursor = [], 0.0
    for sc in spec["scenes"]:
        dur = float(sc.get("duration", 3)); explicit = video_mode and "start" in sc
        start = float(sc["start"]) if explicit else cursor
        if not explicit: cursor += dur
        out.append((start, dur))
    return out


def collect_sfx(spec, video_mode):
    """Auto sound design from the scene timeline (+ custom events). Returns [(time, name, gain)]."""
    cfg = spec.get("sfx"); cfg = cfg if isinstance(cfg, dict) else {}
    ev, times = [], scene_times(spec, video_mode)
    for i, (sc, (t0, dur)) in enumerate(zip(spec["scenes"], times)):
        if sc.get("sfx") is False: continue
        ty = sc.get("type", "text")
        if i > 0 and not video_mode:
            tr = sc.get("transition", spec.get("transition", "fade"))
            if tr in trn.SOUND: ev.append((t0 - 0.05, trn.SOUND[tr], 0.75))
            elif tr in ("slide", "wipe"): ev.append((t0, "swish", 0.4))
        if ty == "title": ev.append((t0 + 0.05, "impact", 0.5))
        elif ty == "outro": ev += [(t0 + 0.1, "shimmer", 0.5)]
        elif ty in ("text", "quote", "kinetic", "chart", "image"): ev.append((t0 + 0.05, "swish", 0.35))
        elif ty == "stat": ev += [(t0 + 0.15 + j * 0.115, "tick", 0.32 - j * 0.015) for j in range(11)] + [(t0 + 1.55, "pop", 0.6)]
        elif ty == "bullets": ev += [(t0 + 0.35 * (j + 1) + 0.05, "pop", 0.45) for j in range(len(sc.get("items", [])))]
        elif ty == "steps": ev += [(t0 + 0.4 * (j + 1) + 0.05, "pop", 0.45) for j in range(len(sc.get("items", [])))]
        elif ty in ("lower_third", "caption"): ev.append((t0, "swish" if ty == "lower_third" else "click", 0.3))
        elif ty == "ring": ev += [(t0 + 0.05, "riser", 0.4), (t0 + 1.95, "pop", 0.6)]
        elif ty == "logo_reveal": ev += [(t0 - 0.05, "riser", 0.5), (t0 + 0.34, "impact", 0.9), (t0 + 0.95, "shimmer", 0.45)]
        for e in sc.get("sfx") or []:                                    # per-scene custom: [{"at": 0.5, "name": "pop"}]
            if isinstance(e, dict): ev.append((t0 + float(e.get("at", 0)), e.get("name", "pop"), float(e.get("gain", 0.7))))
    for e in cfg.get("events") or []:                                   # global custom events (absolute time)
        if isinstance(e, dict): ev.append((float(e.get("at", 0)), e.get("name", "pop"), float(e.get("gain", 0.7))))
    return [(max(0.0, t), n, g) for t, n, g in ev]


def prepare_sfx(spec, video_mode, tmpdir):
    if not spec.get("sfx"): return
    cfg = spec["sfx"] if isinstance(spec["sfx"], dict) else {}
    os.makedirs(tmpdir, exist_ok=True)
    times = scene_times(spec, video_mode); total = max([a + b for a, b in times] + [1.0])
    path = sfxm.build_track(collect_sfx(spec, video_mode), total, os.path.join(tmpdir, "sfx.wav"))
    if path:
        spec["_sfx_track"] = path; spec["_sfx_volume"] = float(cfg.get("volume", spec.get("sfx_volume", 0.7)))
        print(f"  sfx: sound design track built ({len(collect_sfx(spec, video_mode))} events)")


# ----------------------------------------------------------------- spec: srt, validation, auto-fixes
def parse_srt(path):
    txt = open(path, encoding="utf-8-sig").read().replace("\r", "")
    rx = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
    out = []
    for blk in re.split(r"\n\s*\n", txt.strip()):
        lines = blk.split("\n"); k = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if k is None: continue
        m = rx.search(lines[k])
        if not m: continue
        g = [int(x) for x in m.groups()]
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000; b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(l.strip() for l in lines[k + 1:] if l.strip())
        if text: out.append((a, b, text))
    return out


def scene_words(sc):
    n = len(str(sc.get("text", "")).split()) + len(str(sc.get("subtext", "")).split())
    n += sum(len(str(i if not isinstance(i, dict) else i.get("label", "")).split()) for i in sc.get("items", []))
    return n


MIN_DUR = {"logo_reveal": 3.4, "ring": 3.0, "kinetic": 2.2}


def scene_strings(sc):
    for k in ("text", "subtext", "cta"):
        if sc.get(k): yield str(sc[k])
    for it in sc.get("items") or []: yield str(it.get("label", "") if isinstance(it, dict) else it)
    for ln in sc.get("lines") or []: yield str(ln.get("text", "") if isinstance(ln, dict) else ln)
    for ly in sc.get("layers") or []:
        if isinstance(ly, dict) and ly.get("text"): yield str(ly["text"])


def check_glyphs(fk, sc, tag, warns, seen):
    """Warn when the chosen font lacks glyphs (they would render as empty boxes)."""
    for t in scene_strings(sc):
        p = fk.path_for(t); miss = fk.missing(p, t) if p else []
        if miss and (p, tuple(miss)) not in seen:
            seen.add((p, tuple(miss)))
            warns.append(f"{tag}: font '{os.path.basename(p)}' has no glyph for {''.join(miss)!r} - set 'font' / 'font_ur' / 'font_hi' to a font that has it")


VIDEO_FX_KEYS = {"vignette", "grain", "sharpen", "rgbshift", "blur", "letterbox", "bloom", "shake", "punch"}


def validate(spec, base_dir, fix=True):
    """Returns (errors, warnings). With fix=True applies safe auto-fixes (defaults, clamping, reading-time)."""
    errs, warns = [], []
    fk, seen_glyphs = FontKit(spec), set()
    for k in spec:
        if k not in TOP_KEYS: warns.append(f"unknown top-level key '{k}' (typo?)")
    if not isinstance(spec.get("scenes"), list) or not spec.get("scenes"):
        return ["spec needs a non-empty 'scenes' list"], warns
    video_mode = bool(spec.get("input_video"))
    fxm.validate_fx(spec.get("fx"), spec.get("camera"), "top-level", errs, warns)
    if isinstance(spec.get("video_fx"), dict):
        for k in spec["video_fx"]:
            if k not in VIDEO_FX_KEYS: warns.append(f"video_fx: unknown key '{k}' (valid: {', '.join(sorted(VIDEO_FX_KEYS))})")
        if not video_mode: warns.append("video_fx only applies in EDIT mode (spec with input_video)")
    for k in ("input_video", "music", "voiceover", "logo", "font", "font_ur", "font_hi", "srt"):
        if spec.get(k) and not os.path.exists(spec[k]): errs.append(f"file not found for '{k}': {spec[k]}")
    if spec.get("theme") and isinstance(spec["theme"], str) and spec["theme"] not in THEMES:
        warns.append(f"unknown theme '{spec['theme']}' -> using neon (themes: {', '.join(THEMES)})")
    if spec.get("grade") and spec["grade"] not in GRADES: warns.append(f"unknown grade '{spec['grade']}' (ignored)")
    if spec.get("aspect") and not aspect_size(spec["aspect"]) and not spec.get("width"):
        warns.append(f"unknown aspect '{spec['aspect']}' -> 16:9 (use e.g. 16:9, 9:16, 1:1, 4:5, 21:9 or any W:H)")
    if spec.get("resolution") is not None:
        r_ = parse_resolution(spec["resolution"])
        if not r_: warns.append(f"unreadable resolution {spec['resolution']!r} (use 720, 1080, 1440, 2160, '1080p', '2k', '4k')")
        elif r_ > MAX_RESOLUTION: warns.append(f"resolution {r_}p is above 4K: very slow and RAM-hungry; 2160 is the supported maximum")
        elif r_ < 360: warns.append(f"resolution {r_}p is very low")
        elif video_mode and r_ > 1.05 * min(probe(spec["input_video"])["w"] or 99999, probe(spec["input_video"])["h"] or 99999) \
                and os.path.exists(spec["input_video"]):
            warns.append(f"resolution {r_}p is above the source footage: it will be upscaled (graphics stay sharp, footage cannot gain detail)")
    if spec.get("codec") and str(spec["codec"]).lower() not in ("h264", "h265", "hevc"): warns.append(f"unknown codec '{spec['codec']}' (h264 or h265)")
    D_src = None
    if video_mode and os.path.exists(spec["input_video"]):
        info = probe(spec["input_video"]); trim = spec.get("trim", {}) or {}
        D_src = (float(trim.get("end", info["duration"])) - float(trim.get("start", 0))) / float(spec.get("speed", 1) or 1)
        if float(spec.get("speed", 1) or 1) != 1 and info["audio"]: warns.append("speed != 1: source audio is time-stretched with atempo (pitch kept)")
    auto = spec.get("auto_duration", not video_mode)
    for i, sc in enumerate(spec["scenes"]):
        tag = f"scene {i + 1}"
        t = sc.get("type", "text") if fix else sc.get("type")
        if fix and "type" not in sc: sc["type"] = "text"; warns.append(f"{tag}: no type -> 'text'")
        t = sc.get("type")
        if t not in SCENE_TYPES: errs.append(f"{tag}: unknown type '{t}' (valid: {', '.join(sorted(SCENE_TYPES))})"); continue
        if sc.get("animation") and sc["animation"] not in ANIMATIONS:
            warns.append(f"{tag}: unknown animation '{sc['animation']}' (valid: {', '.join(sorted(ANIMATIONS))})")
            if fix: del sc["animation"]
        if t in bld.MOTION_TYPES: bld.validate_motion_scene(sc, tag, errs, warns)
        fxm.validate_fx(sc.get("fx"), sc.get("camera"), tag, errs, warns)
        if sc.get("bg_style") and sc["bg_style"] not in bgfx.STYLES: warns.append(f"{tag}: unknown bg_style '{sc['bg_style']}' (valid: {', '.join(bgfx.STYLES)})")
        if sc.get("transition") and sc["transition"] not in TRANSITIONS:
            warns.append(f"{tag}: unknown transition '{sc['transition']}' -> fade (valid: {', '.join(sorted(TRANSITIONS))})")
        check_glyphs(fk, sc, tag, warns, seen_glyphs)
        if t in ("title", "text", "outro", "caption", "lower_third", "quote") and not sc.get("text"): errs.append(f"{tag} ({t}): 'text' is required")
        if t == "stat" and not (sc.get("value") or sc.get("text")): errs.append(f"{tag} (stat): 'value' is required")
        if t in ("bullets", "steps", "chart") and not sc.get("items"): errs.append(f"{tag} ({t}): 'items' list is required")
        if t == "image":
            p = sc.get("path")
            if not p or not os.path.exists(p): errs.append(f"{tag} (image): file not found: {p}")
        if sc.get("bg_image") and not os.path.exists(sc["bg_image"]): warns.append(f"{tag}: bg_image not found, using gradient")
        if t in ("bullets", "steps") and len(sc.get("items", [])) > 6: warns.append(f"{tag}: {len(sc['items'])} items is a lot; max ~5 reads well")
        if t == "caption" and len(str(sc.get("text", "")).split()) > 14: warns.append(f"{tag}: caption longer than 14 words; split it")
        if t in ("title", "text", "outro") and len(str(sc.get("text", "")).split()) > 14: warns.append(f"{tag}: headline longer than 14 words; shorten it")
        if "duration" not in sc and fix: sc["duration"] = 3
        dur = float(sc.get("duration", 3))
        need = (0.9 + 0.33 * scene_words(sc)) if t != "image" else 2.0
        if t in ("lower_third", "caption"): need = 1.0 + 0.3 * scene_words(sc)
        if t in bld.MOTION_TYPES: need = MIN_DUR.get(t, 0.0)
        if dur < need - 0.01 and not sc.get("narration"):
            if fix and auto and not (video_mode and "start" in sc):
                sc["duration"] = round(need, 2); warns.append(f"{tag}: duration {dur:g}s too short to read -> {need:.1f}s")
            else: warns.append(f"{tag}: {dur:g}s is short for {scene_words(sc)} words (~{need:.1f}s needed)")
        if video_mode and D_src is not None and "start" in sc:
            s0 = float(sc["start"])
            if s0 >= D_src: errs.append(f"{tag}: start {s0:g}s is after the end of the video ({D_src:.1f}s)")
            elif s0 + float(sc.get("duration", 3)) > D_src + 0.01:
                if fix: sc["duration"] = round(D_src - s0, 2)
                warns.append(f"{tag}: runs past the end of the video; shortened")
    if video_mode:
        timed = sorted((float(s["start"]), float(s.get("duration", 3)), i, s.get("type")) for i, s in enumerate(spec["scenes"]) if "start" in s)
        for a, b in zip(timed, timed[1:]):
            if a[3] in ("caption", "lower_third") and b[3] in ("caption", "lower_third") and b[0] < a[0] + a[1] - 0.05:
                warns.append(f"scenes {a[2] + 1} ({a[3]}) and {b[2] + 1} ({b[3]}) overlap in time and screen area")
    return errs, warns


# ----------------------------------------------------------------- CLI helpers
def doctor():
    print(f"motion-video-editor renderer v{VERSION}")
    print(f"python {platform.python_version()} on {platform.system()}  |  CPU cores: {os.cpu_count()}")
    ff = shutil.which("ffmpeg")
    if ff:
        v = sh(["ffmpeg", "-version"]).stdout.splitlines()[0]; print("ffmpeg  OK  ", v[:60])
        flt = sh(["ffmpeg", "-hide_banner", "-filters"]).stdout; enc = sh(["ffmpeg", "-hide_banner", "-encoders"]).stdout
        for name in ("libx264", "aac"): print(f"  encoder {name:10s}", "OK" if name in enc else "MISSING")
        for name in ("sidechaincompress", "alimiter", "atempo", "overlay"): print(f"  filter  {name:18s}", "OK" if name in flt else "MISSING (feature degraded)")
        print("  encoder libx265    ", "OK (codec: h265 gives ~40% smaller 4K files)" if "libx265" in enc else "not available (codec h265 falls back to h264)")
        mem = avail_mem_mb(); print("  RAM available      ", f"{mem / 1024:.1f} GB -> max safe workers at 4K: {safe_workers(8, 3840, 2160)}" if mem else "unknown")
    else:
        print("ffmpeg  MISSING -> install FFmpeg and add to PATH (Windows: winget install Gyan.FFmpeg)")
    try:
        from PIL import features; import PIL
        print(f"Pillow  OK   {PIL.__version__}  raqm(Urdu/Arabic shaping): {'OK' if features.check('raqm') else 'MISSING - Urdu text will look broken'}")
    except Exception as e: print("Pillow  MISSING ->  pip install pillow", e)
    print("numpy   OK" if np else "numpy MISSING")
    print("fonts   (glyph-checked, bundled OFL fonts in assets/fonts):")
    for line in FontKit({}).report(): print("   ", line)
    print("motion  engine OK: %d text presets, %d fx, %d transitions, %d bg styles, %d sfx" % (
        len(prs.PRESET_NAMES), len(fxm.FX_TYPES), len(trn.FULL_TRANSITIONS), len(bgfx.STYLES) - 2, len(sfxm.NAMES)))
    try:
        import tts; print("tts     engines:", ", ".join(f"{k}={'OK' if v else 'no'}" for k, v in tts.available().items()))
    except Exception as e: print("tts     check failed:", e)
    print("workers default:", default_workers())


def default_workers(): return max(1, min(os.cpu_count() or 1, 8))


_ENC_CACHE = {}


def have_encoder(name):
    if name not in _ENC_CACHE:
        try: _ENC_CACHE[name] = name in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        except OSError: _ENC_CACHE[name] = False
    return _ENC_CACHE[name]


class Progress:
    def __init__(self, path, total, out):
        self.path, self.total, self.out, self.t0, self.last = path, total, out, time.time(), 0

    def update(self, done, state="running"):
        now = time.time(); el = max(1e-6, now - self.t0); fps = done / el
        eta = (self.total - done) / fps if fps > 0 else 0
        pct = int(100 * done / max(1, self.total))
        if self.path and (now - self.last > 0.7 or state != "running"):
            tmp = self.path + ".tmp"
            json.dump(dict(state=state, pct=pct, frames=done, total=self.total, fps=round(fps, 1), eta_s=int(eta), output=self.out), open(tmp, "w"))
            os.replace(tmp, self.path); self.last = now
        return pct, fps, eta


def video_fx_filters(vfx, W, H):
    """EDIT mode: ffmpeg-level looks applied to the source video itself -> (linear filter list, bloom opacity or 0)."""
    f, S = [], min(W, H) / 720.0
    punches = [p for p in (vfx.get("punch") or []) if isinstance(p, dict)]
    shake = float(vfx.get("shake", 0) or 0)
    if punches or shake:
        z = "+".join(f"{float(p.get('amount', 0.12))}*gte(t,{float(p.get('at', 0))})*pow(max(0,1-(t-{float(p.get('at', 0))})/{max(0.05, float(p.get('dur', 0.4)))}),2)"
                     for p in punches) or "0"
        base = 1.0 + (0.05 if shake else 0.0)
        f.append(f"scale=w='trunc(iw*({base}+{z})/2)*2':h='trunc(ih*({base}+{z})/2)*2':eval=frame")
        a = shake * S
        ox = f"(in_w-out_w)/2+{a:.2f}*sin(t*37)+{a * 0.5:.2f}*sin(t*53)" if shake else "(in_w-out_w)/2"
        oy = f"(in_h-out_h)/2+{a:.2f}*sin(t*41)+{a * 0.5:.2f}*sin(t*59)" if shake else "(in_h-out_h)/2"
        f.append(f"crop=w={W}:h={H}:x='{ox}':y='{oy}'")
    if vfx.get("sharpen"): f.append(f"unsharp=5:5:{float(vfx['sharpen']):.2f}:5:5:0")
    if vfx.get("blur"): f.append(f"gblur=sigma={float(vfx['blur']):.2f}")
    if vfx.get("rgbshift"): n = int(vfx["rgbshift"]); f.append(f"rgbashift=rh={n}:bh={-n}")
    if vfx.get("vignette"): f.append(f"vignette=angle={0.25 + 0.55 * min(1.0, float(vfx['vignette'])):.3f}")
    if vfx.get("grain"): f.append(f"noise=alls={int(vfx['grain'])}:allf=t+u")
    if vfx.get("letterbox"):
        bar = int(H * (0.12 if vfx["letterbox"] is True else float(vfx["letterbox"])))
        f.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    return f, (0.4 * min(1.5, float(vfx["bloom"])) if vfx.get("bloom") else 0.0)


def make_cmd(spec, st, out, ffm_log):
    W, H, fps, D = st.W, st.H, st.fps, st.D; th = st.ctx.theme
    bh = max(3, int(5 * st.ctx.S)); bar = spec.get("progress_bar", True)
    accent = "0x%02x%02x%02x" % tuple(th["accent"])
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if st.video_mode:
        trimmed = bool(spec.get("trim")) or st.D < st.info["duration"] / st.speed - 0.05
        if trimmed: cmd += ["-ss", str(st.t0), "-t", f"{min(D * st.speed, st.t1 - st.t0):.3f}"]
        cmd += ["-i", spec["input_video"], "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
        nxt = 2
    else:
        cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]; nxt = 1
    if bar:
        cmd += ["-f", "lavfi", "-i", f"color=c={accent}:s={W}x{bh}:r={fps}"]; bar_idx = nxt; nxt += 1
    ain, afilt, amap = build_audio(spec, D, st.video_mode and st.info["audio"], nxt, st.speed)
    cmd += ain
    if st.video_mode:
        vf = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
        if spec.get("grade") in GRADES: vf += "," + GRADES[spec["grade"]]
        if st.speed != 1: vf += f",setpts=PTS/{st.speed}"
        vlin, bloom = video_fx_filters(spec.get("video_fx") or {}, W, H)
        if vlin: vf += "," + ",".join(vlin)
        if bloom: vf += f",split[fa][fb];[fb]gblur=sigma={max(6, int(14 * st.ctx.S))},eq=brightness=-0.12:contrast=1.5[fbl];[fa][fbl]blend=all_mode=screen:all_opacity={bloom:.2f}"
        vf += "[base];[base][1:v]overlay=format=auto:shortest=1[v1]"
        last = "[v1]"
    else:
        vf, last = "[0:v]null[v1]", "[v1]"
    if bar: vf += f";{last}[{bar_idx}:v]overlay=x='-overlay_w*(1-t/{D:.3f})':y=main_h-overlay_h:shortest=1[v2]"; last = "[v2]"
    vf += f";{last}scale=out_color_matrix=bt709:out_range=tv:flags=bicubic,format=yuv420p[v]"
    cmd += ["-filter_complex", vf + (";" + afilt if afilt else ""), "-map", "[v]", *amap]
    hevc = str(spec.get("codec", "h264")).lower() in ("h265", "hevc") and have_encoder("libx265")
    crf = int(spec["crf"]) if str(spec.get("crf", "")).isdigit() and st.quality != "draft" else st.prof["crf"] + (4 if hevc else 0)
    cmd += ["-c:v", "libx265" if hevc else "libx264", "-preset", st.prof["preset"], "-crf", str(crf), "-threads", "0"]
    cmd += ["-tag:v", "hvc1"] if hevc else ["-profile:v", "high"]
    cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-t", f"{D:.3f}", "-movflags", "+faststart", out]
    return cmd


def render(spec, opts, out, workers, progress_path):
    st = build_state(spec, opts)
    n_frames = int(round(st.D * st.fps))
    if st.video_mode:
        for i, s_ in enumerate(st.scenes):
            if s_["start"] >= st.D - 0.05: print(f"  ! scene {i + 1} starts at {s_['start']:.1f}s, after the video ends ({st.D:.1f}s): it will not appear")
            elif s_["end"] > st.D + 0.05: print(f"  ! scene {i + 1} is cut by the end of the video ({st.D:.1f}s)")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    log = out + ".ffmpeg.log"; logf = open(log, "w")
    cmd = make_cmd(spec, st, out, log)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=logf)
    frame_bytes = st.W * st.H * (4 if st.video_mode else 3)
    chunk = max(2, min(max(8, st.fps) if not st.video_mode else st.fps * 2, int(48 * 2 ** 20 / frame_bytes)))   # <= ~48 MB per chunk
    capped = safe_workers(workers, st.W, st.H)
    if capped < workers:
        print(f"  note: {st.W}x{st.H} frames are big: using {capped} worker(s) instead of {workers} to stay inside available RAM")
        workers = capped
    if st.W * st.H >= 1.5 * 1920 * 1080 and st.quality != "draft":
        print(f"  {st.W}x{st.H} render: expect roughly {st.W * st.H / (1280 * 720):.0f}x the time of 720p (run --estimate; check pacing first with --quality draft)")
    ranges = [(n, min(n + chunk, n_frames)) for n in range(0, n_frames, chunk)]
    pg = Progress(progress_path, n_frames, out); done = 0; step = max(1, n_frames // 20); next_print = 0
    print(f"rendering {n_frames} frames {st.W}x{st.H}@{st.fps} ({st.D:.1f}s, quality={st.quality}, workers={workers})", flush=True)
    pool = None
    try:
        if workers > 1 and len(ranges) > 1:
            pool = mp.Pool(workers, initializer=_init_worker, initargs=(spec, opts))
            it = pool.imap(_render_chunk, ranges)
        else:
            _init_worker(spec, opts); it = map(_render_chunk, ranges)
        for runs in it:
            for data, rep in runs:
                for _ in range(rep): proc.stdin.write(data)
                done += rep
            pct, fps, eta = pg.update(done)
            if done >= next_print:
                print(f"  {pct}%  {fps:.1f} fps  eta {int(eta)}s", flush=True); next_print = done + step
        proc.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        if pool: pool.terminate()
    rc = proc.wait(); logf.close()
    if rc != 0:
        pg.update(done, "error"); tail = open(log).read()[-600:]
        raise SystemExit("ffmpeg failed:\n" + tail)
    os.remove(log); pg.update(n_frames, "done")
    print(f"done: {out}  ({st.W}x{st.H}, {st.D:.1f}s, {st.fps}fps, {time.time() - pg.t0:.0f}s render time)")


def estimate(spec, opts, workers):
    st = build_state(spec, opts); n = int(round(st.D * st.fps)); pts = [int(n * (i + 0.5) / 12) for i in range(12)]
    t0 = time.time()
    for p in pts: compose(st, p / st.fps).tobytes()
    per = (time.time() - t0) / len(pts)
    workers = safe_workers(workers, st.W, st.H)
    eff = min(workers, os.cpu_count() or 1) * 0.85 if workers > 1 else 1
    secs = per * n / max(1, eff) + n * (st.W * st.H) / (1280 * 720) / 150.0
    out = dict(frames=n, resolution=f"{st.W}x{st.H}", fps=st.fps, seconds_per_frame=round(per, 4), workers=workers,
               estimated_render_seconds=int(secs), estimated_render_minutes=round(secs / 60, 1))
    tips = []
    if secs > 900 and st.quality != "draft": tips.append("long render: check pacing with --quality draft first, or lower --resolution (1080 is the sweet spot), or use more cores")
    if st.W * st.H > 3000000: tips.append("4K tips: codec h265 makes ~40% smaller files; platforms (Instagram, TikTok, WhatsApp) re-encode to 1080p anyway")
    if tips: out["tips"] = tips
    print(json.dumps(out, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="motion-video-editor renderer")
    ap.add_argument("spec", nargs="?"); ap.add_argument("-o", "--output")
    ap.add_argument("--preview", action="store_true"); ap.add_argument("--still", type=float)
    ap.add_argument("--quality", choices=list(PROFILES)); ap.add_argument("--workers", type=int)
    ap.add_argument("--resolution", help="short side: 720, 1080, 1440, 2160 or 720p/1080p/2k/4k/uhd (overrides the spec)")
    ap.add_argument("--validate", action="store_true"); ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--doctor", action="store_true"); ap.add_argument("--progress-file")
    a = ap.parse_args()
    if a.doctor: doctor(); return
    if a.resolution and not parse_resolution(a.resolution): ap.error(f"unreadable --resolution {a.resolution!r} (try 1080p, 2k, 4k)")
    if not a.spec: ap.error("spec.json required")
    spec = json.load(open(a.spec, encoding="utf-8"))
    base_dir = os.path.dirname(os.path.abspath(a.spec))
    for k in ("input_video", "music", "voiceover", "logo", "font", "font_ur", "font_ar", "font_hi", "srt"):
        if spec.get(k) and not os.path.isabs(spec[k]): spec[k] = os.path.join(base_dir, spec[k])
    for sc in spec.get("scenes", []):
        for k in ("path", "bg_image"):
            if sc.get(k) and not os.path.isabs(sc[k]): sc[k] = os.path.join(base_dir, sc[k])
        for ly in sc.get("layers") or []:
            if isinstance(ly, dict) and ly.get("path") and not os.path.isabs(ly["path"]): ly["path"] = os.path.join(base_dir, ly["path"])
    apply_presets(spec)
    video_mode = bool(spec.get("input_video"))
    if spec.get("srt") and os.path.exists(spec["srt"]) and video_mode:
        trim = spec.get("trim", {}) or {}; t0 = float(trim.get("start", 0)); sp = float(spec.get("speed", 1) or 1)
        for s0, s1, txt in parse_srt(spec["srt"]):
            if s1 <= t0: continue
            spec["scenes"].append(dict(type="caption", text=txt, start=round((max(s0, t0) - t0) / sp, 3),
                                       duration=round(max(0.8, (s1 - max(s0, t0)) / sp), 3)))
    errs, warns = validate(spec, base_dir, fix=not a.validate)
    if a.validate:
        print(json.dumps(dict(ok=not errs, errors=errs, warnings=warns, scenes=len(spec["scenes"])), indent=2, ensure_ascii=False))
        sys.exit(1 if errs else 0)
    for w in warns: print("  warning:", w)
    if errs: raise SystemExit("spec errors:\n  " + "\n  ".join(errs))
    workers = a.workers or default_workers()
    opts = dict(quality=a.quality, preview=a.preview, resolution=a.resolution)
    out = a.output or spec.get("output", "output.mp4")
    if not os.path.isabs(out) and not a.output: out = os.path.join(base_dir, out)
    if a.preview and not a.output: out = out.replace(".mp4", "_preview.mp4")
    if a.still is None and not a.estimate and any(sc.get("narration") for sc in spec["scenes"]):
        prepare_narration(spec, video_mode, os.path.join(os.path.dirname(os.path.abspath(out)), "_tts_tmp"), workers)
    if video_mode and not any(sc.get("narration") for sc in spec["scenes"]): fix_overlaps(spec)
    if a.still is None and not a.estimate: prepare_sfx(spec, video_mode, os.path.join(os.path.dirname(os.path.abspath(out)), "_tts_tmp"))
    if a.estimate: estimate(spec, opts, workers); return
    if a.still is not None:
        st = build_state(spec, opts); out = a.output or "still.png"; t = a.still
        base = compose(st, t)
        if video_mode:
            tmp = out + ".src.png"
            sh(["ffmpeg", "-y", "-ss", str(st.t0 + t * st.speed), "-i", spec["input_video"], "-frames:v", "1", "-vf",
                f"scale={st.W}:{st.H}:force_original_aspect_ratio=decrease,pad={st.W}:{st.H}:(ow-iw)/2:(oh-ih)/2", tmp])
            frame = Image.open(tmp).convert("RGBA"); os.remove(tmp)
            base = Image.alpha_composite(frame, base)
        base.convert("RGB").save(out); print("still saved:", out); return
    render(spec, opts, out, workers, a.progress_file)


if __name__ == "__main__":
    mp.freeze_support()
    main()
