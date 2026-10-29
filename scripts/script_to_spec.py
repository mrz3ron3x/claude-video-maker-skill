#!/usr/bin/env python3
"""
Turn plain text (a script, a product description, bullet notes) into a ready-to-render spec.json.
This is the FAST PATH for "make a video about X": generate a first draft in one command, then tweak the JSON.

  python script_to_spec.py script.txt -o spec.json --aspect 9:16 --theme neon --narrate --lang en
  echo "..." | python script_to_spec.py - -o spec.json

Input conventions (all optional, the parser guesses sensibly):
  first line                 -> title (second short line becomes its subtitle)
  blank line                 -> scene break
  lines starting - * • 1.    -> bullets (a preceding line ending with ':' becomes the heading)
  "10,000+ customers"        -> stat scene (number first, <= 8 words)
  "quoted line" - Author     -> quote scene
  last line with order/visit/subscribe/follow/www/.com -> outro with call to action
  Lines of the form  [bg:2]  or  [3s]  at the end of a line set background index / duration.

Styles (--style): classic (default) | kinetic (After-Effects typography, whip/zoom/glitch transitions, animated
backgrounds, grain + vignette, push-in camera, auto sound design) | cinematic | pop.
"""
import argparse, json, re, sys

CTA = re.compile(r"\b(order|visit|subscribe|follow|download|buy|join|shop|call|contact|sign up|register|www\.|\.com|\.pk|\.io)\b", re.I)
STAT = re.compile(r"^\W*(\d[\d,\.]*\s?(?:%|x|X|\+|k|K|m|M|b|B)?\+?)\s+(.{2,60})$")
BULLET = re.compile(r"^\s*(?:[-*\u2022\u25CF]|\d+[\.\)])\s+(.*)$")
QUOTE = re.compile(r"^[\"\u201C](.+?)[\"\u201D]\s*(?:[-\u2014\u2013~]+\s*(.+))?$")
TAG_DUR = re.compile(r"\s*\[(\d+(?:\.\d+)?)s\]\s*$")
TAG_BG = re.compile(r"\s*\[bg:(\d+)\]\s*$")


def words(s): return len(s.split())


def read_time(n): return max(2.5, min(8.0, 1.0 + 0.36 * n))


def split_long(text, limit=16):
    if words(text) <= limit: return [text]
    parts = re.split(r"(?<=[\.\!\?\u061F\u06D4;:])\s+|,\s+(?=\w)", text)
    out, cur = [], ""
    for p in parts:
        if cur and words(cur + " " + p) > limit: out.append(cur); cur = p
        else: cur = (cur + " " + p).strip()
    if cur: out.append(cur)
    return out or [text]


def build(text, a):
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    lines_all = [l.strip() for b in blocks for l in b.split("\n") if l.strip()]
    scenes = []
    def add(sc, narr=None, raw=""):
        m = TAG_DUR.search(raw)
        if m: sc["duration"] = float(m.group(1))
        m = TAG_BG.search(raw)
        if m: sc["bg"] = int(m.group(1))
        if a.narrate and narr: sc["narration"] = narr
        scenes.append(sc)
    first = True
    for b in blocks:
        lines = [l.rstrip() for l in b.split("\n") if l.strip()]
        i = 0
        while i < len(lines):
            raw = lines[i]; l = TAG_BG.sub("", TAG_DUR.sub("", raw)).strip()
            bm = BULLET.match(l)
            if bm:                                   # collect consecutive bullets
                items, heading = [], ""
                if scenes and scenes[-1].get("_pending_heading"):
                    heading = scenes.pop()["text"]
                while i < len(lines) and BULLET.match(TAG_BG.sub("", TAG_DUR.sub("", lines[i]))):
                    items.append(BULLET.match(TAG_BG.sub("", TAG_DUR.sub("", lines[i]))).group(1).strip()); i += 1
                sc = dict(type="bullets", items=items[:6])
                if heading: sc["text"] = heading
                add(sc, narr=(heading + ". " if heading else "") + ". ".join(items), raw=raw); first = False; continue
            if l.endswith(":") and i + 1 < len(lines) and BULLET.match(lines[i + 1]):
                scenes.append(dict(type="text", text=l.rstrip(":"), _pending_heading=True)); i += 1; continue
            qm = QUOTE.match(l)
            if qm:
                sc = dict(type="quote", text=qm.group(1).strip())
                if qm.group(2): sc["subtext"] = qm.group(2).strip()
                add(sc, narr=qm.group(1), raw=raw); i += 1; first = False; continue
            if first and words(l) <= 10:
                sc = dict(type="title", text=l)
                if i + 1 < len(lines) and words(lines[i + 1]) <= 12 and not BULLET.match(lines[i + 1]) and not STAT.match(lines[i + 1]):
                    sc["subtext"] = TAG_BG.sub("", TAG_DUR.sub("", lines[i + 1])).strip(); i += 1
                add(sc, narr=l + (". " + sc["subtext"] if sc.get("subtext") else ""), raw=raw); i += 1; first = False; continue
            first = False
            sm = STAT.match(l)
            if sm and words(l) <= 8:
                add(dict(type="stat", value=sm.group(1).strip(), subtext=sm.group(2).strip()), narr=l, raw=raw); i += 1; continue
            if CTA.search(l) and l == lines_all[-1] and words(l) <= 14:
                m = re.search(r"((?:https?://)?(?:www\.)?[\w\-]+\.(?:com|pk|io|net|org|app)\S*)", l)
                sc = dict(type="outro", text=re.sub(re.escape(m.group(1)), "", l).strip(" -:|,") if m else l)
                if m: sc["subtext"] = m.group(1)
                if not sc["text"]: sc["text"] = m.group(1) if m else l; sc.pop("subtext", None)
                add(sc, narr=l, raw=raw); i += 1; continue
            for piece in split_long(l):
                add(dict(type="text", text=piece), narr=piece, raw=raw if piece == split_long(l)[-1] else "")
            i += 1
    for s in scenes: s.pop("_pending_heading", None)
    for n, s in enumerate(scenes):
        if "duration" not in s:
            w = words(str(s.get("text", ""))) + words(str(s.get("subtext", ""))) + sum(words(str(x)) for x in s.get("items", []))
            s["duration"] = round(read_time(w), 1)
        if "bg" not in s: s["bg"] = n % 3
    if a.cta and scenes and scenes[-1]["type"] != "outro":
        scenes.append(dict(type="outro", text=a.cta, duration=3.5, bg=len(scenes) % 3))
    return scenes


STYLES = {   # per-style cycles used to give every scene its own motion personality
    "kinetic": dict(anim=["tracking", "mask_up", "blur_in", "scatter", "elastic", "rise"], bg=["aurora", "grid", "rays", "stars", "waves"],
                    tr=["whip", "zoom", "glitch", "push", "circle", "flash"], camera="push_in",
                    fx=[{"type": "vignette", "strength": 0.5}, {"type": "grain", "strength": 0.04}]),
    "cinematic": dict(anim=["blur_in", "rise", "mask_up"], bg=["stars", "aurora", "waves"], tr=["dip", "blur", "zoom"], camera="drift",
                      fx=[{"type": "vignette", "strength": 0.7}, {"type": "grain", "strength": 0.05}, {"type": "light_leak", "strength": 0.25}]),
    "pop": dict(anim=["pop", "elastic", "bounce", "drop"], bg=["dots", "waves", "aurora"], tr=["push", "barn", "whip", "circle"], camera=None,
                fx=[{"type": "glow", "amount": 0.35}]),
}


def apply_style(scenes, spec, style):
    st = STYLES.get(style)
    if not st: return
    for n, sc in enumerate(scenes):
        if sc["type"] in ("title", "text", "quote", "outro"): sc["animation"] = st["anim"][n % len(st["anim"])]
        sc["bg_style"] = st["bg"][n % len(st["bg"])]
        if n: sc["transition"] = st["tr"][(n - 1) % len(st["tr"])]
        if sc["type"] == "title": sc["duration"] = max(sc.get("duration", 3), 3.2)
    spec["fx"] = st["fx"]; spec["sfx"] = True
    if st["camera"]: spec["camera"] = st["camera"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="text file, or - for stdin"); ap.add_argument("-o", "--output", default="spec.json")
    ap.add_argument("--aspect", default="16:9"); ap.add_argument("--theme", default="neon"); ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--narrate", action="store_true", help="add a voice-over line to every scene")
    ap.add_argument("--lang", default="en", help="narration language: en, ur, hi, ar ...")
    ap.add_argument("--voice"); ap.add_argument("--music"); ap.add_argument("--cta", help="append an outro with this text")
    ap.add_argument("--max-seconds", type=float, default=300)
    ap.add_argument("--resolution", help="short side: 720, 1080 (default), 1440/2k, 2160/4k")
    ap.add_argument("--style", choices=["classic", "kinetic", "cinematic", "pop"], default="classic", help="motion personality")
    ap.add_argument("--video-out", default="video.mp4")
    a = ap.parse_args()
    text = sys.stdin.read() if a.input == "-" else open(a.input, encoding="utf-8").read()
    scenes = build(text, a)
    total = sum(s["duration"] for s in scenes)
    if total > a.max_seconds:
        k = a.max_seconds / total
        for s in scenes: s["duration"] = round(max(2.0, s["duration"] * k), 1)
    spec = dict(output=a.video_out, aspect=a.aspect, fps=a.fps, theme=a.theme, scenes=scenes)
    if a.narrate: spec["tts"] = dict(lang=a.lang, **({"voice": a.voice} if a.voice else {}))
    if a.music: spec["music"] = a.music
    apply_style(scenes, spec, a.style)
    if a.resolution: spec["resolution"] = int(a.resolution) if str(a.resolution).isdigit() else a.resolution
    json.dump(spec, open(a.output, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"{a.output}: {len(scenes)} scenes, ~{sum(s['duration'] for s in scenes):.0f}s "
          f"({', '.join(s['type'] for s in scenes)})")


if __name__ == "__main__":
    main()
