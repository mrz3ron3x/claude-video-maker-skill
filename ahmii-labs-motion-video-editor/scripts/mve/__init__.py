"""mve - the After-Effects-style motion engine behind motion-video-editor.

Modules (each one small and single-purpose):
  easing       easing curves + cubic-bezier (the AE graph editor)
  props        keyframed / tweened / expression / wiggle properties
  fonts        font discovery with per-glyph coverage checks and script fallbacks
  textkit      text layout (wrap, per-unit boxes, RTL aware) and glyph sprites
  compositing  2-D affine placement, blend modes, blur, glow, shadow
  presets      entry/exit/loop animation presets shared by layers and kinetic text
  layers       the layer system (text, shape, image, counter, particles, null, solid)
  fx           frame effects (glow, rgb split, glitch, grain...) and the camera rig
  transitions  full-frame scene transitions
  backgrounds  animated backgrounds (aurora, grid, rays, dots, stars, waves)
  builders     scene builders for the motion / kinetic / logo_reveal / ring scene types
  sfx          procedural sound effects (no asset files needed)
"""
__version__ = "3.0"
