#!/usr/bin/env python3
"""
Text-to-speech with automatic fallback and caching.

  python tts.py "Hello world" out.wav [--lang en] [--voice NAME] [--engine auto|edge|espeak|pyttsx3] [--rate +10%]
  python tts.py --check            # which engines work on this machine

Engines (auto = try in this order):
  edge     edge-tts (natural neural voices, needs internet + `pip install edge-tts`)
  espeak   espeak-ng (offline, robotic)
  pyttsx3  system voices (Windows SAPI5 / macOS) -> `pip install pyttsx3`
An engine that fails once is skipped for the rest of the run (no repeated timeouts).
Identical (text, lang, voice, rate) requests are served from a cache folder.
"""
import argparse, asyncio, hashlib, os, shutil, subprocess, sys, tempfile

VOICES = {  # default edge voices
    "en": "en-US-AriaNeural", "ur": "ur-PK-UzmaNeural", "hi": "hi-IN-SwaraNeural", "ar": "ar-SA-ZariyahNeural",
    "es": "es-ES-ElviraNeural", "fr": "fr-FR-DeniseNeural", "de": "de-DE-KatjaNeural", "pt": "pt-BR-FranciscaNeural",
    "tr": "tr-TR-EmelNeural", "id": "id-ID-GadisNeural", "bn": "bn-BD-NabanitaNeural", "fa": "fa-IR-DilaraNeural",
}
MALE = {"en": "en-US-GuyNeural", "ur": "ur-PK-AsadNeural", "hi": "hi-IN-MadhurNeural", "ar": "ar-SA-HamedNeural"}
_DEAD = {}


def _ffmpeg_to_wav(src, dst):
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", "44100", "-ac", "1", dst],
                       capture_output=True, text=True)
    if r.returncode: raise RuntimeError("ffmpeg convert failed: " + r.stderr[-200:])


def _edge(text, out, lang, voice, rate):
    import edge_tts
    v = voice or VOICES.get(lang, VOICES["en"])
    if v in ("male", "man"): v = MALE.get(lang, MALE["en"])
    tmp = out + ".mp3"
    async def go():
        kw = {"rate": rate} if rate else {}
        await asyncio.wait_for(edge_tts.Communicate(text, v, **kw).save(tmp), timeout=25)
    last = None
    for _ in range(2):
        try:
            asyncio.run(go()); break
        except Exception as e:
            last = e
    else:
        raise last
    _ffmpeg_to_wav(tmp, out); os.remove(tmp)


def _espeak(text, out, lang, voice, rate):
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe: raise RuntimeError("espeak-ng not installed")
    speed = 160
    if rate:
        try: speed = int(160 * (1 + float(rate.replace("%", "").replace("+", "")) / 100))
        except ValueError: pass
    r = subprocess.run([exe, "-v", voice or lang, "-s", str(speed), "-w", out, text], capture_output=True, text=True)
    if r.returncode or not os.path.exists(out): raise RuntimeError("espeak failed: " + r.stderr[-200:])


def _pyttsx3(text, out, lang, voice, rate):
    import pyttsx3
    eng = pyttsx3.init(); tmp = out + ".tmp.wav"
    eng.save_to_file(text, tmp); eng.runAndWait()
    if not os.path.exists(tmp): raise RuntimeError("pyttsx3 produced no file")
    _ffmpeg_to_wav(tmp, out); os.remove(tmp)


ENGINES = {"edge": _edge, "espeak": _espeak, "pyttsx3": _pyttsx3}


def synth(text, out, lang="en", voice=None, engine="auto", rate=None, cache_dir=None):
    """Synthesize to a wav file. Returns the engine used ('cache' when reused)."""
    key = hashlib.sha1(f"{text}|{lang}|{voice}|{rate}|{engine}".encode("utf-8")).hexdigest()[:16]
    cached = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True); cached = os.path.join(cache_dir, key + ".wav")
        if os.path.exists(cached) and os.path.getsize(cached) > 1000:
            shutil.copyfile(cached, out); return "cache"
    order = ["edge", "espeak", "pyttsx3"] if engine == "auto" else [engine]
    errs = []
    for name in order:
        if name in _DEAD and engine == "auto":
            errs.append(f"{name}: skipped ({_DEAD[name]})"); continue
        try:
            ENGINES[name](text, out, lang, voice, rate)
            if os.path.exists(out) and os.path.getsize(out) > 1000:
                if cached: shutil.copyfile(out, cached)
                return name
            raise RuntimeError("empty audio")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:120]; errs.append(f"{name}: {msg}")
            if engine == "auto": _DEAD[name] = msg
    raise RuntimeError("TTS failed -> " + " | ".join(errs) +
                       "\n  Fix: pip install edge-tts (needs internet)  or  install espeak-ng (offline)  or  pip install pyttsx3")


def available():
    res = {}
    tmp = tempfile.mkdtemp()
    for name in ENGINES:
        try:
            ENGINES[name]("test", os.path.join(tmp, name + ".wav"), "en", None, None)
            res[name] = os.path.exists(os.path.join(tmp, name + ".wav"))
        except Exception:
            res[name] = False
    shutil.rmtree(tmp, ignore_errors=True); return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?"); ap.add_argument("out", nargs="?")
    ap.add_argument("--lang", default="en"); ap.add_argument("--voice"); ap.add_argument("--rate")
    ap.add_argument("--engine", default="auto", choices=["auto"] + list(ENGINES)); ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        for k, v in available().items(): print(f"{k:8s}", "OK" if v else "not available")
        sys.exit(0)
    if not (a.text and a.out): ap.error("text and out are required")
    print("engine:", synth(a.text, a.out, a.lang, a.voice, a.engine, a.rate))
