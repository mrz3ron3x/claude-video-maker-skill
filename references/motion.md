# After-Effects-style motion (v3)

Everything here is plain JSON. The engine lives in `scripts/mve/`. Look at `assets/example_ae_kinetic.json` first.

## 1. Quick wins (no layers needed)
| want | write |
|---|---|
| Kinetic title | `{"type":"kinetic","text":"MAKE IT MOVE","preset":"tracking","highlight":["MOVE"]}` |
| Stacked punchy lines | `{"type":"kinetic","lines":["Type that",{"text":"slides","preset":"mask_up"},"and lands"],"line_delay":0.45}` |
| AE preset on a classic scene | `{"type":"title","text":"Hi","animation":"blur_in"}` (works on title/text/quote/outro) |
| Progress ring + counter | `{"type":"ring","value":87,"text":"Quality"}` |
| Logo / brand sting | `{"type":"logo_reveal","path":"logo.png","text":"BRAND","subtext":"tagline"}` (ring pulse, sparkles, flash, shine, glow) |
| Free composition | `{"type":"motion","layers":[...]}` (section 3) |
| Looks | scene or top-level `"fx":[...]`, `"camera":"push_in"`, `"bg_style":"aurora"` |
| Transitions | `"transition":"whip"` (per scene; the transition plays at the START of that scene) |
| Sound design | `"sfx": true` (auto whooshes/pops/impacts) or `{"volume":0.6,"events":[{"at":2.0,"name":"impact"}]}` |

**Presets** (`preset` / `animation` / layer `enter` / `exit`): `fade rise drop slide_up slide_down slide_left slide_right pop zoom_in zoom_out blur_in tracking spin_in flip bounce elastic scatter mask_up mask_down stretch glitch`.
Per-letter presets (tracking scatter glitch drop elastic bounce spin_in flip rise blur_in pop stretch) split Latin text per character; Urdu/Arabic/Hindi automatically animate per word (per-letter would break joining).
Loops (on top of an entrance): `wave float pulse jitter sway breathe`.

## 2. Properties = the stopwatch
Any numeric layer/camera/fx property accepts:
```
0.5                                                   constant
[[0,0],[0.6,1,"out_back"],[2,1]]                      keyframes [time, value, ease-to-next]
{"from":0,"to":1,"start":0.2,"dur":0.6,"ease":"out_cubic"}     tween
{"keys":[[0,0],[1,1]],"loop":"pingpong"}              keyframes + loop (repeat|pingpong)
{"value":0.5,"wiggle":{"freq":3,"amp":0.02,"seed":1}} smooth noise (AE wiggle)
"sin(t*4)*0.1+0.5"                                    expression: t = layer seconds, d = scene length
```
Expression helpers: `sin cos tan abs min max sqrt floor ceil pow exp log clamp(x,a,b) lerp(a,b,k) smooth(a,b,x) ease(name,x) noise(x,seed) wiggle(freq,amp,seed)`, constants `pi e tau`. Sandboxed (no attribute access/imports).
Eases: `linear in/out/in_out_{quad,cubic,quart,quint,sine,expo,back,bounce,elastic} spring hold` + aliases `ease ease_in ease_out smooth snap overshoot bounce elastic step`, or a 4-number cubic-bezier `[0.25,0.1,0.25,1]` (the graph editor). Keyframes default to `in_out_cubic`.

## 3. Layers (`type: motion`)
List order = bottom to top. Common keys on every layer:
`id, parent, start, end|duration, x, y` (fractions, 0.5 = centre) `dx, dy` (px at 720p) `scale scale_x scale_y rotation` (deg, clockwise) `opacity blur glow glow_color anchor [ax,ay] blend` (`normal add screen multiply`) `shadow` (true or `{dx,dy,blur,alpha}`) `shine` (`{start,dur}` light sweep) `enter exit enter_dur exit_dur fade_in fade_out loop`.

**Parenting** (AE null objects): `"parent":"pivot"` -> x/y are ignored, `dx/dy` are relative to the parent and scaled + rotated with it; scale/rotation/opacity inherit. Use `{"type":"null","id":"pivot","rotation":"t*70"}` for orbits.

| type | fields |
|---|---|
| `text` | `text size color gradient[c1,c2] align width(0-1 of frame) by(char/word/line/all) tracking stroke highlight(["word"] or {word:color}) line_spacing reveal` + `animate:{preset,stagger,dur,delay,loop}`. `reveal` (0-1 Prop) = typewriter |
| `counter` | `from to decimals prefix suffix commas count_dur ease size color` (tabular digits, no jitter) |
| `shape` | `shape` = `rect rounded circle ellipse line ring polygon triangle star`; `w h` (Props), `fill stroke stroke_w radius sides inner gradient[c1,c2] gradient_angle`, `trim` (0-1 Prop: draws strokes on, grows a filled bar left to right, sweeps ring arcs) |
| `image` | `path w h radius` |
| `particles` | `kind` = `confetti sparkle burst bubbles snow embers stars`, `count(<=400) seed colors size speed life spread` (burst/sparkle fire from the layer position at `start`) |
| `solid` | `color` or `gradient`; use for flashes (`blend:"add"`) or dim plates |
| `null` | invisible parent |

## 4. Scene FX and camera
```
"fx":[{"type":"glow","amount":0.8},
      {"type":"rgb_split","start":1.0,"dur":0.4,"pulse":true,"distance":10},
      {"type":"shake","start":2.0,"dur":0.5,"distance":16},
      {"type":"vignette"},{"type":"grain"}]
"camera":"push_in"   or   {"zoom":"1+0.1*smooth(0,d,t)","pan_x":{"value":0,"wiggle":{"freq":1,"amp":0.01}},"rotate":3}
```
FX types: `glow/bloom blur pixelate rgb_split glitch vignette grain scanlines flash tint light_leak shake zoom_punch pulse(bpm)`.
Every fx: `start`, `dur` (default: rest of scene), `attack`/`release` (soft edges), `pulse:true` + `decay` (one-shot impulse), `amount` (Prop), plus own knobs (`distance radius threshold strength color size bpm zoom roll`).
Camera presets: `push_in pull_out pan_left pan_right drift handheld dutch tilt_up` (they zoom a little: keep text inside ~85% of the width).
In EDIT mode scene fx/camera act on the overlay graphics; vignette/grain/scanlines/flash/tint also darken/tint the video underneath.

## 5. Transitions (CREATE)
`fade slide wipe none` (classic, background only) and full-frame `push push_up whip zoom spin circle barn flash dip glitch blur pixelate rgb`. Set per scene (`transition`, `transition_duration` default 0.6) or top-level. Both scenes keep animating during a full-frame transition.

## 6. Backgrounds (CREATE)
`bg_style`: `soft` (default drifting blobs) `aurora grid` (synthwave floor) `rays dots stars waves none`. Scene key or top-level.

## 7. EDIT-mode looks on the footage (ffmpeg)
```
"grade":"teal_orange"   (warm cool punchy cinematic bw teal_orange vintage faded dramatic)
"video_fx":{"vignette":0.6,"grain":8,"bloom":0.5,"sharpen":0.6,"rgbshift":2,"letterbox":true,
            "punch":[{"at":1.0,"amount":0.12,"dur":0.4}],"shake":4}
```

## 8. Sound design
`"sfx": true` adds whoosh/swish per transition, impact on titles/flash, pops per bullet/step, ticks while stats count, riser + impact on logo/ring. Per scene: `"sfx":false` or `"sfx":[{"at":0.5,"name":"pop","gain":0.7}]`. Names: `whoosh swish pop tick impact riser shimmer glitch click`. Volume: `sfx_volume` (default 0.7).

## 9. Fonts
Bundled (SIL OFL): Poppins Bold (Latin), Noto Nastaliq Urdu / Noto Naskh Arabic, Noto Sans Devanagari. Every string is checked glyph-by-glyph and falls back down a chain; `--validate` warns when no font has a glyph. Urdu/Persian text uses Nastaliq, plain Arabic uses Naskh (`"urdu_style":"naskh"` forces Naskh). Override with `font`, `font_ur`, `font_hi`.

## 10. Cost guide (single core, 720p)
Most scenes 15-40 ms/frame; glow/grain/vignette +10-40 ms; full-frame transitions +40-100 ms for their 0.6 s; `--quality draft` is ~6x faster. Check with `--estimate`.

## 11. Gotchas
- Layer positions are fractions of the frame; sizes are px at 720p and scale with the output size.
- A `parent` ignores the child's `x/y`; use `dx/dy`.
- Scene `fx`/`camera` default to the top-level ones; set `"fx":[]` on a scene to opt out.
- `exit`/`fade_out` are skipped while a scene is held under the next scene's full-frame transition (it keeps playing instead).
- Camera zoom crops the edges (RGB canvases never show borders); keep important text inside 85%.
