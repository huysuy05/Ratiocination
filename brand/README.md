# Ratiocination brand assets

## Logo (`logo/`)

Cut from `logo/source/logo-original.png` with transparent backgrounds.

| File | Use |
| --- | --- |
| `ratiocination-logo.png` | Lockup (R mark + wordmark) on light backgrounds |
| `ratiocination-logo-white.png` | Lockup on dark backgrounds (navy or black) |
| `ratiocination-logo-mono-navy.png` | One-colour lockup (print, embossing, single-ink) |
| `ratiocination-mark.png`, `ratiocination-mark-white.png` | R mark alone: app icon, favicon, avatar |
| `ratiocination-wordmark.png`, `ratiocination-wordmark-white.png` | Wordmark alone |
| `ratiocination-mark.svg`, `ratiocination-logo.svg` | Vector mark and lockup, traced from the PNGs (navy + blue) |

Colours, sampled from the logo:

| Name | Hex | Role |
| --- | --- | --- |
| Navy | `#162641` | Mark, wordmark, text, dark backgrounds |
| Blue | `#5477EF` | The mark's triangle; accents and links |
| White | `#FFFFFF` | Backgrounds, the logo on navy |

The website uses a warm palette instead (2026-10-13): Smoky Black `#11120D`, Olive Drab `#565449`,
Bone `#D8CFBC`, Floral White `#FFFBF4` (`web/src/styles/tokens.css`, docs/UI.md §2). The logo copies in
`web/public/landing/` are recoloured to it: Smoky Black + Olive Drab on light, Floral White + Bone on
dark. The files in this folder keep the original navy and blue.

### In the app

The app draws the logo as inline SVG with the same paths as the `.svg` files here
(`web/src/components/ui/BrandMark.tsx`). The mark was traced from `ratiocination-mark.png`: its outline
is exact lines, tangent arcs and small corner fillets fitted to the alpha mask; the wordmark is the
traced outline. The R and the wordmark take the text colour and the triangle the accent, so one SVG
serves both themes: Smoky Black + Olive Drab on light, Floral White + Bone on dark.

| Where | What |
| --- | --- |
| Sidebar (≥ 1024 px) | `BrandLockup`, 22 px tall |
| Icon rail (768–1023 px), mobile top bar | `BrandMark`, 30 px and 26 px |
| Sign-in pages, above the card | `BrandLockup`, 30 px tall |
| Loading screen | `BrandMark`, 44 px |
| Browser tab, home screen | `web/public/favicon.svg` (Floral White R + Bone triangle on a #11120D rounded square), `favicon-32.png`, `apple-touch-icon.png` (180 px, full bleed) |

The radar rings stay the app's motif for loading, empty states and backdrops; they are never the logo.
Keep the mark at 16 px or larger and the lockup at 16 px tall or larger.

## Hero loop (`motion/`)

The portrait (`motion/source/portrait.png`) animated into an 8-second loop at 24 fps, 1080 × 1486.
The camera pushes in 2 % and back. The eyes scan, blink once, narrow and glance aside. Smoke curls into a
question mark that dissolves into a graphite knowledge graph, then rises freely again. The last frame
leads straight back into the first.

| File | Use |
| --- | --- |
| `ratiocination-loop.webm` | Web, first choice (VP9, 3.2 MB) |
| `ratiocination-loop-web.mp4` | Web fallback for older Safari (H.264, 4.5 MB) |
| `ratiocination-loop.mp4` | Master (H.264, 9 Mbps) for editing and presentations |
| `ratiocination-loop-poster.jpg` | First frame: poster while loading, and the still for reduced motion |

```html
<video autoplay muted loop playsinline poster="ratiocination-loop-poster.jpg" aria-hidden="true">
  <source src="ratiocination-loop.webm" type="video/webm" />
  <source src="ratiocination-loop-web.mp4" type="video/mp4" />
</video>
```

For visitors who prefer reduced motion, show the poster instead of the video.

### Re-rendering

`render_portrait_loop.py` renders every frame from the portrait (no frames are stored). The timing of
each move lives in the keyframe lists at the top. Run it from this folder with the repo's virtualenv.
H.264 uses macOS's own encoder (`encode_h264.swift`); WebM needs any ffmpeg with libvpx-vp9.

```bash
../../.venv/bin/python render_portrait_loop.py stills 0 1.1 3.2 4.9   # PNG stills in ./stills
swiftc -O encode_h264.swift -o encode_h264
../../.venv/bin/python render_portrait_loop.py raw | ./encode_h264 ratiocination-loop.mp4 1080 1486 24 9000000
../../.venv/bin/python render_portrait_loop.py jpeg | ffmpeg -f image2pipe -framerate 24 -c:v mjpeg -i - \
  -c:v libvpx-vp9 -b:v 0 -crf 34 -row-mt 1 -pix_fmt yuv420p -an ratiocination-loop.webm
```
