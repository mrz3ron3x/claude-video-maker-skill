# Recipes & troubleshooting

## 1. 30 s product reel from a description
`script_to_spec.py desc.txt -o spec.json --aspect 9:16 --theme cyber --cta "Order now: shop.com" --music track.mp3`
then: `--validate`, `--still 1`, `--still 12`, final render. Typical shape: title (hook) -> 2-3 bullets/stat -> quote/review -> outro.

## 2. Explainer 1-2 min with voice (Urdu)
Spec: `"tts":{"lang":"ur"}`, every scene has `narration`. Use `steps` for processes, `chart` for numbers.
Run `--quality draft` first to check pacing, then final. Natural voice only with edge-tts on a machine with internet.

## 3. Edit an uploaded video
`ffprobe` -> plan: lower_third at ~1 s, captions from `.srt`, one `stat` at the key moment, `outro` in the last 3 s.
`"grade":"punchy"`, `"music_volume":0.12`. Starts are output time; keep scenes within the duration.

## 4. Slideshow with photos
Scenes with `bg_image` (Ken Burns) + `text`; 4-5 s each; `"transition":"slide"`.

## 5. Bilingual (Urdu + English)
Mixed text in one scene works (font chosen by script). Put Urdu in its own scene for clean shaping. Set `font_ur` for a Nastaliq look.

## 6. After-Effects style reel (kinetic)
`script_to_spec.py notes.txt -o spec.json --aspect 9:16 --style kinetic --theme cyber` then hand-edit: make the hook a `kinetic` scene with `highlight`, add a `ring` or `stat` for numbers, end with `logo_reveal`. `--still` mid-entrance and mid-transition, `--quality draft`, then final.

## 7. Intro / logo sting (5 s)
One `logo_reveal` scene (`path` = PNG logo with transparent background, `text` brand, `subtext` tagline), `bg_style` `rays` or `aurora`, `sfx: true`, 4.2 s.

## 8. Cinematic edit of footage (EDIT)
`"grade":"teal_orange"`, `"video_fx":{"vignette":0.6,"grain":8,"bloom":0.4,"letterbox":true,"punch":[{"at":2.0,"amount":0.1,"dur":0.4}]}`, a `lower_third`, one `kinetic` title with `fx` glow + `rgb_split` pulse, `sfx: true`.

## Troubleshooting
| symptom | fix |
|---|---|
| `ffmpeg: command not found` | install FFmpeg (Windows: `winget install Gyan.FFmpeg`), reopen terminal |
| Urdu letters disconnected | Pillow without raqm: `pip install -U pillow` (wheels include it) ; check `--doctor` |
| TTS failed | `pip install edge-tts` (needs internet) or install espeak-ng / `pip install pyttsx3`; `python scripts/tts.py --check` |
| render slow | `--quality draft` to iterate; `--workers N`; lower `resolution`; shorter `fps` (24) |
| text too small/large | set scene `size`; long text auto-shrinks, but split it instead |
| out of memory on Windows | `--workers 2` |
| scene missing in EDIT | its `start` is after the video end (the renderer prints a warning) |
| text shows empty boxes | font lacks the glyph: `--validate` names the font + characters; set `font` / `font_ur` / `font_hi` |
| voice-over missing | no TTS engine worked: the render printed why; `pip install edge-tts` (internet) or install espeak-ng |
| motion scene looks cropped | camera zoom crops edges; keep text inside 85% width or use a gentler preset (`drift`) |
| glow/grain make it slow | drop them on busy scenes, use `--quality draft` while iterating |
| "unknown preset/fx/type" | the message suggests the closest valid name (did you mean ...?) |
| 4K render is slow / out of memory | `--estimate` first; `--resolution 1080`; keep fx to 2-3 per scene; workers auto-limit to RAM; close other apps |
| output looks soft / blocky | raise `resolution`, lower `crf` (e.g. 16), use `--quality high`; do not judge a `--quality draft` render |
