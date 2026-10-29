# Spec reference (v3)

All paths may be relative to the spec file. `output` defaults to `output.mp4` next to the spec.

## Top level
| key | meaning |
|---|---|
| `input_video` | upload path -> EDIT mode. Omit for CREATE mode |
| `output` | mp4 path |
| `preset` | `reel/tiktok/short/story` (9:16), `youtube/promo` (16:9, 1080p), `square`, `instagram` (4:5). Explicit keys win |
| `aspect` | `16:9` (1280x720) `9:16` `1:1` `4:5` (CREATE only; EDIT keeps the source size) |
| `resolution` | CREATE: short side in px, e.g. 1080 for Full HD |
| `fps` | default 30 |
| `quality` | `draft` / `standard` / `high` (CLI `--quality` overrides) |
| `theme` | name or object `{accent,text,muted,bgs:[[c1,c2],...]}` (colors = [r,g,b]) |
| `font`, `font_ur`, `font_hi` | .ttf paths: default / Urdu-Arabic / Hindi font |
| `transition` | default scene transition: `fade slide wipe none push push_up whip zoom spin circle barn flash dip glitch blur pixelate rgb` |
| `watermark` | small text bottom-right |
| `logo` | PNG shown top-right |
| `progress_bar` | default true |
| `auto_duration` | CREATE default true: scenes too short to read are extended |
| `grade` | EDIT: `warm cool punchy cinematic bw teal_orange vintage faded dramatic` |
| `trim` | EDIT: `{start, end}` seconds of the source |
| `speed` | EDIT: 0.25-4; source audio is time-stretched too |
| `srt` | EDIT: .srt file -> caption scenes (SRT times are SOURCE time; trim/speed handled) |
| `music`, `music_volume` | background track (looped). Auto-ducks under voice-over (`"duck": false` to disable) |
| `voiceover`, `voiceover_volume`, `source_volume` | ready-made voice file / levels |
| `tts` | `{lang, voice, engine, rate}` e.g. `{"lang":"ur"}`, `{"voice":"en-US-GuyNeural","rate":"+8%"}`; voice `"male"` shortcut |

## Scene (common)
`type`, `duration` (default 3), `start` (EDIT only; omitted in CREATE = sequential), `animation`, `transition`, `transition_duration`,
`narration` (text spoken; the scene is stretched to fit), `lang`, `voice` (per scene), `size` (font size), `bg` (palette index or `[[r,g,b],[r,g,b]]`),
`bg_image` + `bg_dim` (CREATE), `dim`/`dim_alpha` (EDIT full-screen scenes darken the video).

## Types
| type | fields |
|---|---|
| `title` / `outro` | `text`, `subtext`, (`cta` for outro) |
| `text` | `text`, `subtext` |
| `quote` | `text`, `subtext` (author) |
| `stat` | `value` ("10,000+", "98%", "2x"; counts up unless `"count_up": false`), `subtext` |
| `bullets` | `text` (heading), `items` (<=6) |
| `steps` | `text`, `items` (numbered badges) |
| `chart` | `text`, `items`: `[{"label":"A","value":40}]` or `["A:40"]` (animated bars) |
| `lower_third` | `text` (name), `subtext` (role) |
| `caption` | `text` (<= 14 words) |
| `image` | `path`, `text` (optional caption) |

## Timing rules
- EDIT: `start` is in OUTPUT time. Full-screen scenes may not overlap: with narration the later one is pushed back, otherwise the earlier one is shortened.
- CREATE: total length = sum of durations (after auto-extension / narration stretch).

## v3 additions (details + examples: `references/motion.md`)
| key | where | meaning |
|---|---|---|
| `sfx` | top / scene | `true`, `{volume, events:[{at,name,gain}]}`; per scene `false` or `[{at,name}]`. `sfx_volume` default 0.7 |
| `fx`, `camera`, `bg_style` | top (default) / scene | frame effects list, camera preset or object, animated background |
| `video_fx` | top, EDIT | `{vignette, grain, bloom, sharpen, blur, rgbshift, letterbox, punch:[{at,amount,dur}], shake}` on the footage |
| `urdu_style` | top | `nastaliq` (default for Urdu/Persian letters) or `naskh` |
| types | scene | `kinetic` (`text|lines, preset, by, stagger, highlight, loop`), `logo_reveal` (`path, text, subtext`), `ring` (`value, max, text, subtext, suffix`), `motion` (`layers`) |
| `animation` | scene | classic names + presets: `rise drop slide_down zoom_in zoom_out blur_in tracking spin_in flip bounce elastic scatter mask_up mask_down stretch glitch` |
| `resolution` | top | short side in px: `720 1080(default) 1440 2160` or `"1080p" "2k" "4k" "uhd" "1920x1080"`; CLI `--resolution`. EDIT mode: scales the footage to it |
| `aspect` | top | `16:9 9:16 1:1 4:5 21:9 4:3 3:4` or any `W:H` |
| `codec`, `crf`, `fps` | top | `h264` (default) or `h265`; quality 14-28 (lower = better); frame rate 24/30/60 |
