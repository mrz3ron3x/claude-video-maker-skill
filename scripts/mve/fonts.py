"""Font discovery with per-glyph coverage checks and per-script fallback chains.

Why: Pillow silently draws a "tofu" box for glyphs a font lacks (e.g. DejaVu has no Urdu 'ے').
FontKit picks, for every string, the first font in the script's chain that really covers all its characters.
Bundled OFL fonts live in ../assets/fonts (Poppins, Noto Nastaliq/Naskh, Noto Sans Devanagari).
"""
from __future__ import annotations

import os
import re
import unicodedata

from PIL import ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSET_DIR = os.path.join(ROOT, "assets", "fonts")

ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
DEVA_RE = re.compile(r"[\u0900-\u097F]")
URDU_ONLY_RE = re.compile(r"[\u0679\u0688\u0691\u06BA\u06BE\u06C1\u06D2\u06C3\u067E\u0686\u0698\u06AF\u06A9\u06CC]")  # Urdu/Persian letters (ٹ ڈ ڑ ں ھ ہ ے پ چ ژ گ ک ی); plain Arabic uses Naskh

_A = lambda name: os.path.join(ASSET_DIR, name)  # noqa: E731

LATIN = [
    _A("Poppins-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial Bold.ttf",
]
ARABIC_NASTALIQ = [
    "C:/Windows/Fonts/JameelNooriNastaleeq.ttf", "C:/Windows/Fonts/NooriNastaleeq.ttf",
    _A("NotoNastaliqUrdu.ttf"), "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Bold.ttf",
]
ARABIC_NASKH = [
    _A("NotoNaskhArabic.ttf"), "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/tahomabd.ttf", "C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/GeezaPro.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
DEVANAGARI = [
    _A("NotoSansDevanagari.ttf"), "C:/Windows/Fonts/NirmalaB.ttf", "C:/Windows/Fonts/mangalb.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf", "/System/Library/Fonts/Kohinoor.ttc",
]


def script_of(text: str) -> str:
    """'arabic' (Urdu/Arabic/Persian), 'deva' (Hindi) or 'latin'."""
    if ARABIC_RE.search(text):
        return "arabic"
    if DEVA_RE.search(text):
        return "deva"
    return "latin"


def is_rtl(text: str) -> bool:
    return script_of(text) == "arabic"


def _existing(paths):
    return [p for p in paths if p and os.path.exists(p)]


class FontKit:
    """Resolves (text, size) -> a PIL font that has every glyph, caching everything."""

    def __init__(self, spec: dict | None = None):
        spec = spec or {}
        self.user = {
            "latin": spec.get("font"),
            "arabic": spec.get("font_ur") or spec.get("font_ar"),
            "deva": spec.get("font_hi"),
        }
        self.urdu_style = spec.get("urdu_style", "nastaliq")  # or "naskh"
        self._cover: dict[tuple, bool] = {}
        self._path: dict[tuple, str] = {}
        self._fonts: dict[tuple, ImageFont.FreeTypeFont] = {}
        self._notdef: dict[tuple, bytes] = {}

    # ------------------------------------------------------------------ chains
    def chain(self, text: str) -> list[str]:
        sc = script_of(text)
        if sc == "arabic":
            first = ARABIC_NASTALIQ if (self.urdu_style == "nastaliq" and URDU_ONLY_RE.search(text)) else ARABIC_NASKH
            rest = ARABIC_NASKH if first is ARABIC_NASTALIQ else ARABIC_NASTALIQ
            base = [self.user["arabic"]] + first + rest + LATIN
        elif sc == "deva":
            base = [self.user["deva"]] + DEVANAGARI + LATIN
        else:
            base = [self.user["latin"]] + LATIN
        seen, out = set(), []
        for p in _existing(base):
            if p not in seen:
                seen.add(p); out.append(p)
        return out

    # ------------------------------------------------------------------ coverage
    def _load(self, path: str, px: int) -> ImageFont.FreeTypeFont:
        key = (path, px)
        if key not in self._fonts:
            try:
                f = ImageFont.truetype(path, px)
                try:
                    names = [n.decode() if isinstance(n, bytes) else n for n in f.get_variation_names()]
                    if "Bold" in names:
                        f.set_variation_by_name("Bold")
                except Exception:
                    pass  # static font: nothing to set
            except Exception:
                f = ImageFont.load_default(px)
            self._fonts[key] = f
        return self._fonts[key]

    def _tofu(self, path: str, ch: str) -> bool:
        """True when `path` draws .notdef for `ch` (glyph missing)."""
        key = (path, ch)
        if key not in self._cover:
            f = self._load(path, 48)
            nd = self._notdef.get(path)
            if nd is None:
                m = f.getmask("\uffff"); nd = self._notdef[path] = bytes(m) + bytes(str(m.size), "ascii")
            m = f.getmask(ch)
            self._cover[key] = (bytes(m) + bytes(str(m.size), "ascii")) == nd
        return self._cover[key]

    @staticmethod
    def _checkable(text: str):
        for ch in set(text):
            if ch.isspace() or unicodedata.category(ch) in ("Cf", "Cc"):
                continue
            yield ch

    def missing(self, path: str, text: str) -> list[str]:
        return sorted(ch for ch in self._checkable(text) if self._tofu(path, ch))

    # ------------------------------------------------------------------ public
    def path_for(self, text: str) -> str | None:
        """First font in the chain covering the whole text; else the best partial match."""
        key = (text,)
        if key in self._path:
            return self._path[key]
        chain, best, best_n = self.chain(text), None, 10 ** 9
        for p in chain:
            n = len(self.missing(p, text))
            if n == 0:
                best = p; break
            if n < best_n:
                best, best_n = p, n
        self._path[key] = best
        return best

    def get(self, text: str, px: int) -> ImageFont.FreeTypeFont:
        p = self.path_for(text)
        return self._load(p, max(6, int(px))) if p else ImageFont.load_default(max(6, int(px)))

    def report(self, samples: dict[str, str] | None = None) -> list[str]:
        """Human-readable lines for --doctor."""
        samples = samples or {"latin": "Motion Edit 123", "arabic": "چھوٹے قدم، بڑا فرق", "deva": "नमस्ते दुनिया"}
        lines = []
        for name, s in samples.items():
            p = self.path_for(s)
            miss = self.missing(p, s) if p else list(s)
            lines.append(f"{name:7s} -> {os.path.basename(p) if p else 'none'}" +
                         ("   OK (all glyphs present)" if not miss else f"   MISSING GLYPHS: {''.join(miss)}"))
        return lines
