---
name: motion-video-editor
description: "Turns a normal video into a motion-graphics edit (animated titles, lower thirds, SRT captions, stats, charts, color grades, music with auto-ducking), OR builds a faceless product/promo video from a text script or idea, with optional voice-over in English, Urdu, Hindi, Arabic and more. Includes After-Effects-style motion: kinetic typography, keyframes, layers, particles, glow/glitch/RGB-split, camera moves, 13 transitions, animated backgrounds, logo reveals, progress rings and auto sound design. Use when the user wants to edit a video, add text/animations/captions/effects, or make a promo, reel, short, intro, logo sting, explainer or slideshow video (\"motion edit\", \"after effects style\", \"video edit kr do\", \"video bana do\"), or gives a script/bullets and wants a video. Output: MP4 up to ~5 min (Python + FFmpeg). Not for photorealistic AI footage."
---

# Motion Video Editor v3

Renders MP4 from a small JSON "spec" with `scripts/render.py` (Pillow + FFmpeg + numpy, multi-core).
Fields: `references/spec.md`. **After-Effects-style motion (kinetic text, layers, fx, camera, transitions, sfx): `references/motion.md`** - read it before writing motion scenes. Patterns + troubleshooting: `references/recipes.md`.

## Modes
- **EDIT**: spec has `input_video` (an upload). Scenes are timed overlays (`start` = seconds in the OUTPUT clip, after trim and speed). Source audio is kept.
- **CREATE**: no `input_video`. Scenes play one after another on animated backgrounds (faceless video).

## Fast path (do this, don't over-plan)
1. **Don't interrogate.** Defaults: 16:9 (9:16 for reel/short/TikTok/story), theme `neon`, 30 fps, language = user's language. Ask ONE short question only if there is no topic/script at all.
2. **CREATE from text**: `python scripts/script_to_spec.py script.txt -o spec.json --aspect 9:16 --theme neon [--narrate --lang ur] [--cta "Order now"]`
   Add `--resolution 4k` (default 1080) when the user wants a specific quality. Add `--style kinetic` (or `cinematic`, `pop`) for an After-Effects look in one flag (presets, transitions, backgrounds, grain/vignette, camera, sfx). Or write the spec by hand when the user wants specific scenes. Then edit the JSON: better wording, one idea per scene, add `stat`/`chart`/`steps` where the content has numbers or a process.
3. **EDIT**: `ffprobe` the upload first. Never place scenes past its end. Speech captions: if the user gives a `.srt` use `"srt": "file.srt"`; otherwise they must give the lines (no speech-to-text is built in).
4. **Lint**: `python scripts/render.py spec.json --validate` (JSON report; fixes typos, too-short scenes, bad files). Fix every `errors` entry.
5. **Look before you render**: `--still <seconds> -o frame.png` for 1-3 key moments (view the PNG; check text fits, Urdu shapes OK). For motion scenes also check one still mid-entrance and one mid-transition.
6. **Render**: draft first when the video is >60 s or the user is unsure: `--quality draft` (640 px, 15 fps, ~5x faster). Then the final (`standard`, or `--quality high`).
7. **Deliver**: write to `/mnt/user-data/outputs/`, `present_files`, give a 2-3 line summary (length, size, what to tweak).

## Commands
```
python scripts/render.py --doctor                  # ffmpeg / fonts / TTS / cores check (run first on a new machine)
python scripts/render.py spec.json --validate      # lint, no render
python scripts/render.py spec.json --estimate      # predicted render time
python scripts/render.py spec.json --still 4.5 -o f.png
python scripts/render.py spec.json --preview       # draft, first 12 s
python scripts/render.py spec.json                 # final; --workers N, --quality draft|standard|high, --progress-file p.json
```
Long renders (>~60 s of output): run in the background (`nohup ... &`) with `--progress-file`, then poll that JSON (`pct`, `eta_s`, `state`) and tell the user the ETA. Never block silently.

## Resolution (match what the user asks for)
`"resolution"` in the spec (or `--resolution` on the command line) = pixels on the SHORT side, so it works for any aspect: 1080 -> 1920x1080 landscape or 1080x1920 portrait.
| user says | use |
|---|---|
| nothing / "high quality" / "Full HD" / "1080p" / YouTube / Instagram / TikTok | `1080` (the default) |
| "fast" / "preview" / "small file" / "720p" / "HD" | `720` |
| "2K" / "1440p" / "QHD" | `1440` |
| "4K" / "UHD" / "Ultra HD" / "cinema" | `2160` (3840x2160, or 2160x3840 for 9:16) |
Words, numbers and sizes all work: `4k`, `"1080p"`, `2160`, `"1920x1080"`. `--quality draft` always renders small (640 px) for pacing checks, whatever the resolution.
- Cost: pixels grow ~2.3x at 1080p and ~9x at 4K versus 720p; a 4K frame is ~0.5-1 s of CPU. Run `--estimate` first, pick the resolution the user really needs, check pacing with `--quality draft`, then render final. 4K auto-limits workers to what the RAM allows.
- 4K files are large; `"codec":"h265"` makes them ~40% smaller (plays on modern devices; default `h264` plays everywhere). `"crf"` (lower = better) and `"fps"` (24/30/60) are optional.
- Be honest with the user: social platforms re-encode to 1080p, so 4K mainly matters for YouTube/TV/archive. In EDIT mode footage keeps its own resolution (up to 1080p-class) unless `resolution` is set; asking for more than the source upscales it (graphics are sharp, footage can't gain detail).
- Text, fx, particles and fonts all scale with the resolution, so the same spec looks the same at 720p and 4K.

## Scene types
Classic: `title, text, bullets, quote, stat (counts up), chart (animated bars), steps, outro, lower_third, caption, image`.
Motion (v3): `kinetic` (per-letter/word typography), `logo_reveal`, `ring` (progress ring + counter), `motion` (free layers: text, counter, shape, image, particles, solid, null + keyframes/expressions/parenting).
Animations: `fade slide_up slide_left slide_right pop typewriter words` plus 17 AE presets (`tracking mask_up blur_in scatter elastic glitch drop bounce spin_in flip stretch zoom_in zoom_out rise slide_down mask_down`).
Per-scene: `fx` (glow, rgb_split, glitch, shake, grain, vignette...), `camera` (push_in, drift, handheld...), `bg_style` (aurora, grid, rays, dots, stars, waves), `transition` (fade slide wipe none + full-frame push push_up whip zoom spin circle barn flash dip glitch blur pixelate rgb).
Top-level extras: `sfx: true` (auto sound design), `video_fx` + extra `grade`s for EDIT footage.
Backgrounds (CREATE): `bg` palette index, or `bg_image` (Ken Burns zoom + auto-dim). Themes: neon, clean, sunset, mono, forest, gold, cyber, ocean, rose, or a custom theme object.

## Motion recipe (what a good AE-style video looks like)
Hook (kinetic title, `tracking`/`mask_up`, glow) -> 2-4 beats (kinetic lines, ring/stat, motion scene) -> logo_reveal or outro. Vary transitions (whip, zoom, glitch, circle), keep ONE camera preset and ONE grain+vignette pair for the whole video, `sfx: true`. 3-4 s per beat for reels. Don't stack more than 2-3 fx on a scene.

## What the renderer now does for you
- Parallel frames on all CPU cores; identical frames are not re-rendered (EDIT); progress bar drawn by FFmpeg.
- Auto-fits text (shrinks to fit), auto-extends scenes too short to read, clamps scenes to the video end, fixes overlapping full-screen scenes in EDIT mode.
- Narration: scenes stretch to fit the speech, lines are synthesized in parallel and cached, music auto-ducks under the voice, Urdu/Hindi text is auto-detected for font + voice. If no TTS engine works the render still completes (silent, same timing) and prints the fix.
- Fonts are checked glyph by glyph (bundled Poppins, Noto Nastaliq/Naskh, Devanagari) so Urdu/Hindi never shows empty boxes; `--validate` warns if a font lacks a glyph.
- Scene numbers in messages are 1-based.
- Source audio follows `speed` (pitch kept).

## Good-edit rules
- One idea per scene, <= 12 words per headline, <= 5 bullets. 2-5 s per scene; shorter for reels.
- Hook in the first 2 s for reels/shorts; end with an `outro` call to action.
- Max ~3 different animations per video; consistent theme; don't cover faces (lower thirds at the bottom-left, captions at the bottom).
- Roman Urdu / Hinglish text is fine as-is; real Urdu script uses the bundled Nastaliq font automatically (needs raqm, check `--doctor`). Urdu animates per word, never per letter.

## Limits (be honest with the user)
- No AI-generated footage, no speech-to-text, no 3D scenes or video clips as layers (that is Remotion/Three.js territory); 2-D motion graphics only. No web access in the render sandbox beyond PyPI/GitHub.
- Natural voices need `edge-tts` + the user's internet; the sandbox only has the robotic offline voice (espeak-ng). Say so and give the local command: `pip install pillow numpy edge-tts` + FFmpeg, then `python render.py spec.json` on their PC.
- Cap ~5 minutes of output. For longer or cinematic work, suggest splitting or Remotion.
- Rendering speed scales with CPU cores; on a weak PC use `--quality draft` to check and render the final overnight/in the background.
