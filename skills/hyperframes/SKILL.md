---
name: hyperframes
description: Use this whenever you need to create HTML-based video compositions using Hyperframes — the HeyGen open-source framework that renders HTML + GSAP to MP4. Prefer over Remotion for agent-driven video authoring, or when you need a no-build-step rendering pipeline. Not for avatar/talking-head generation (use heygen-skills for that).
---

## When to use

- Creating motion graphics, animated explainers, social overlays, data visualizations
- Agent-driven video authoring — HTML is what LLMs write best
- Beat-synced compositions with GSAP timelines
- Adding shader transitions, lower thirds, social overlays to videos
- As an alternative renderer to Remotion (`video-editor`) — compare outputs
- Post-production compositing on top of HeyGen avatar videos

## What Hyperframes is NOT

- **Not an avatar generator.** Use `heygen-skills` / `heygen-headshot` for talking-head videos.
- **Not a replacement for FFmpeg post-processing.** Use `silence-cutter`, `audio-polish`, `video-zoom`, `music-selector` for those steps.
- **Not for simple concat/trim.** Use FFmpeg directly for that.

## MANDATORY RULES (violation = broken render)

These rules are non-negotiable. Violating any one produces a silently broken render (blank frames, static output, missing content).

1. **Every timed element MUST have `class="clip"`.** Any element with `data-start`/`data-duration` — `<video>`, `<audio>`, `<img>`, `<div>` — gets `class="clip"`. Without it, the Hyperframes runtime cannot manage element visibility. Elements will be always-visible or always-hidden, producing blank frames.

2. **Register every timeline on `window.__timelines`.** After creating a GSAP timeline, you MUST register it:
   ```js
   window.__timelines = window.__timelines || {};
   var tl = gsap.timeline({ paused: true });
   // ... tweens ...
   window.__timelines["YOUR-COMPOSITION-ID"] = tl;
   ```
   The key MUST exactly match the `data-composition-id` attribute on the root element. Mismatched keys = no animation = static output.

3. **Always use `gsap.fromTo()`, never `gsap.from()` alone.** Hyperframes seeks to individual frames in any order. `gsap.from()` relies on the element's current CSS state during seek — which is unpredictable. `fromTo()` with explicit start AND end states is the only reliable method.

4. **`<video>` must be `muted playsinline` with audio on a separate `<audio>` element.** Never put audio on a `<video>` inside a Hyperframes composition.

5. **Never animate `<video>` dimensions directly.** GSAP animating `width`, `height`, `top`, `left` on a `<video>` element causes browsers to stop rendering frames. Wrap the video in a `<div>` and animate the wrapper instead.

6. **Never call `video.play()` / `audio.play()` / `audio.currentTime`.** The framework owns all media playback. It reads `data-start`, `data-media-start`, and `data-volume` to control when and how media plays.

7. **No network fetches, no nondeterministic logic.** No `@import url()` for fonts, no `Math.random()`, no `Date.now()`, no `performance.now()`, no `setInterval`/`setTimeout`. Same input must produce identical output every render.

8. **Run `npm run check` after every edit.** This runs lint + validate + inspect. It catches missing `class="clip"`, timeline registration errors, duplicate IDs, and timing conflicts. Never skip this step.

## Prerequisites

- Node.js >= 22
- FFmpeg installed

## Steps

### 1. Initialize a project

```bash
npx hyperframes init my-video
cd my-video
```

This scaffolds the project and installs skills automatically. You can hand off to an AI agent at any point.

### 2. Install catalog blocks (optional but recommended)

Browse: https://hyperframes.heygen.com/catalog

```bash
npx hyperframes add flash-through-white   # shader transition
npx hyperframes add instagram-follow      # social overlay
npx hyperframes add data-chart            # animated chart
```

### 3. Prepare video assets (keyframe fix)

**BEFORE adding any `.mp4` video to a composition, re-encode for dense keyframes.** Hyperframes needs every frame seekable — sparse keyframes (common in HeyGen output, screen recordings, and downloaded videos) cause frame freezing during render.

```bash
ffmpeg -y -i assets/video.mp4 \
  -c:v libx264 -r 30 -g 30 -keyint_min 30 \
  -movflags +faststart -c:a copy \
  assets/video-dense.mp4
```

Then use `video-dense.mp4` in the composition. Skip this step only for sources already encoded at `-g 30` or less.

### 4. Author the composition

Compositions are plain HTML with `data-` attributes. No React, no build step.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=1920, height=1080">
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body {
      width: 1920px; height: 1080px;
      overflow: hidden; background: #000;
    }
  </style>
</head>
<body>
  <div id="root"
       data-composition-id="demo"
       data-start="0"
       data-duration="5"
       data-width="1920"
       data-height="1080">

    <video id="clip-1" class="clip"
           data-start="0" data-duration="5" data-track-index="0"
           src="intro-dense.mp4" muted playsinline></video>

    <h1 id="title" class="clip"
        data-start="1" data-duration="4" data-track-index="1"
        style="position:absolute; top:400px; left:0; width:1920px;
               text-align:center; font-size:72px; color:white;">
      Welcome to Hyperframes
    </h1>

    <audio id="bg-music" class="clip"
           data-start="0" data-duration="5" data-track-index="2"
           data-volume="0.5" src="music.wav"></audio>
  </div>

  <script>
    window.__timelines = window.__timelines || {};
    var tl = gsap.timeline({ paused: true });

    tl.fromTo("#title",
      { opacity: 0, y: 50 },
      { opacity: 1, y: 0, duration: 1, ease: "power2.out" },
      1
    );

    window.__timelines["demo"] = tl;
  </script>
</body>
</html>
```

Key data attributes:
- `data-composition-id` — unique ID for the composition (used as `window.__timelines` key)
- `data-start` — start time in seconds (or a clip ID for relative timing: `"intro + 2"`)
- `data-duration` — duration in seconds. Required on images/divs. Video/audio defaults to source duration.
- `data-track-index` — layer ordering (higher = on top). Same-track clips cannot overlap.
- `data-volume` — audio volume (0-1)

### 5. Timeline registration and duration

**Timeline registration is mandatory.** The Hyperframes engine reads `window.__timelines` to find animations. Without it, the composition renders as a static frame.

```js
// 1. Create a paused timeline
var tl = gsap.timeline({ paused: true });

// 2. Add tweens using the position parameter (3rd arg) for absolute timing
tl.fromTo("#el", { opacity: 0 }, { opacity: 1, duration: 0.5 }, 1.5);

// 3. Register using the EXACT data-composition-id value
window.__timelines = window.__timelines || {};
window.__timelines["demo"] = tl;
```

**Composition duration** equals the GSAP timeline duration. If your last animation ends at 8 seconds but a video plays for 60 seconds, the composition will only be 8 seconds long. Extend the timeline:

```js
// Extend timeline to 60 seconds without affecting any elements
tl.set({}, {}, 60);
```

Alternatively, set `data-duration` on the root composition div — it takes precedence over timeline duration.

### 6. ALWAYS lint after editing

```bash
npm run check
```

This runs `lint + validate + inspect`. Fix all errors before rendering. The linter catches:
- Missing `class="clip"` on timed elements
- Timeline key mismatches
- Duplicate IDs
- Overlapping clips on the same track

### 7. Preview in browser

```bash
npx hyperframes preview      # opens browser with live reload
```

### 8. Render to MP4

```bash
npx hyperframes render --output output.mp4
```

Deterministic, frame-by-frame capture via headless Chrome + FFmpeg.

### 9. Media preprocessing (built-in)

Hyperframes CLI includes asset preprocessing — no external tools needed:

```bash
# TTS narration (Kokoro engine)
npx hyperframes tts --text "Hello world" --voice af_bella --output speech.wav

# Transcription (Whisper)
npx hyperframes transcribe --input video.mp4 --output transcript.json

# Background removal (u2net)
npx hyperframes remove-background --input image.png --output transparent.png
```

### 10. Integration with HeyGen headshot pipeline

Use Hyperframes to add overlays/transitions on top of HeyGen avatar videos:

```
heygen-headshot → raw avatar video (.mp4)
                       ↓
              [voice-isolate] → vocals only (optional, if video has BGM)
                       ↓
              [silence-cutter] → cut video
                       ↓
              [audio-polish] → polished video
                       ↓
              [ffmpeg keyframe re-encode] → dense keyframes for Hyperframes
                       ↓
         ┌─────────────────────────────┐
         │  Hyperframes composition:    │
         │  - Base layer: polished video│
         │  - Overlay: lower third      │
         │  - Transition: shader effect │
         │  - BGM: from music-selector  │
         └─────────────────────────────┘
                       ↓
              npx hyperframes render
                       ↓
              [pycaps captions] → final video
```

In the composition HTML, reference the HeyGen output as a `<video>` source:

```html
<video id="avatar" class="clip" data-start="0" data-duration="60"
       data-track-index="0" src="headshot-polished.mp4" muted playsinline></video>
<div id="lower-third" class="clip" data-start="3" data-duration="5" data-track-index="1"
     style="position: absolute; bottom: 20%; left: 5%; ...">
  Pastor Nino Coelho
</div>
```

## Minimal composition template

Copy-paste this as a starting point. It passes `npm run check` with zero errors.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=1920, height=1080">
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1920px; height: 1080px; overflow: hidden; background: #000; }
    .scene {
      position: absolute; top: 0; left: 0;
      width: 1920px; height: 1080px; overflow: hidden;
    }
  </style>
</head>
<body>
  <div id="main"
       data-composition-id="main"
       data-start="0"
       data-duration="10"
       data-width="1920"
       data-height="1080">

    <div id="s1" class="scene clip" data-start="0" data-duration="5" data-track-index="0">
      <h1 id="s1-title" style="font-size:72px; color:#fff; text-align:center;
         padding-top:400px;">Scene 1</h1>
    </div>

    <div id="s2" class="scene clip" data-start="5" data-duration="5" data-track-index="0"
         style="opacity:0;">
      <h1 id="s2-title" style="font-size:72px; color:#fff; text-align:center;
         padding-top:400px;">Scene 2</h1>
    </div>
  </div>

  <script>
    window.__timelines = window.__timelines || {};
    var tl = gsap.timeline({ paused: true });

    // Scene 1 entrance
    tl.fromTo("#s1-title",
      { opacity: 0, y: 40 },
      { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" },
      0.2
    );

    // Scene 1 → Scene 2 transition
    tl.to("#s1", { opacity: 0, duration: 0.5, ease: "power1.inOut" }, 4.5);
    tl.fromTo("#s2", { opacity: 0 }, { opacity: 1, duration: 0.5, ease: "power1.inOut" }, 4.5);

    // Scene 2 entrance
    tl.fromTo("#s2-title",
      { opacity: 0, y: 40 },
      { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" },
      5.2
    );

    window.__timelines["main"] = tl;
  </script>
</body>
</html>
```

## Prompting patterns for Hyperframes

These patterns produce the best results when describing videos to an agent (or to yourself for later).

### Cold start — describe the video from scratch

Specify: **duration**, **aspect ratio**, **mood/style**, **key elements** (title, lower third, captions, background video, music).

```
Using /hyperframes, create a 10-second product intro (9:16) with a fade-in title
over a dark background, warm amber accents, and subtle background music.
```

```
Make a 45-second pastoral talking-head video (9:16) using /hyperframes, with:
- Base layer: polished headshot video
- Hook text at 1s: "LIDERANÇA SEM EGO" (bouncy entrance)
- Lower third at 3s: speaker name + series title (slide from left)
- Cinematic flash transition at 15s
- Second hook: key phrase from the script
- End card: CTA + social handle
- BGM at 25% volume with fade-in/fade-out
- Vignette overlay for cinematic depth
```

### Warm start — turn context into a video

Give the agent a URL, doc, CSV, transcript and ask it to synthesize into a video.

```
Summarize the attached PDF into a 45-second pitch video using /hyperframes.
```

```
Turn this CSV into an animated bar chart race using /hyperframes.
(Then install: npx hyperframes add data-chart)
```

```
Read this changelog and turn the top three changes into a 30-second
release announcement video using /hyperframes.
```

### Iteration patterns

Talk to the agent like a video editor — no need to re-describe everything:

```
Make the title 2x bigger, swap to dark mode, and add a fade-out at the end.
Add a lower third at 0:03 with my name and title.
Use the flash-through-white transition between scene 1 and scene 2.
Swap the music to something calmer.
```

### Vocabulary cheat sheet

| You say | Hyperframes understands |
|---------|----------------------|
| "fade in/out" | GSAP opacity tween on a black overlay |
| "slide in from left/right" | GSAP `x` tween on overlay element |
| "bounce in" | GSAP `back.out` easing |
| "flash transition" | `npx hyperframes add flash-through-white` |
| "lower third" | Positioned div at bottom 15-20% with semi-transparent background |
| "cinematic zoom" | `npx hyperframes add cinematic-zoom` |
| "grain overlay" | `npx hyperframes add grain-overlay` (component, not block) |
| "subscribe button" | `npx hyperframes add yt-lower-third` or `instagram-follow` |
| "bar chart race" | `npx hyperframes add data-chart` with animated data |
| "logo outro" | `npx hyperframes add logo-outro` |
| "iPhone mockup" | `npx hyperframes add vfx-iphone-device` |

## Adapter runtimes supported

| Runtime | Use for |
|---------|---------|
| GSAP | Primary — timelines, easing, sequencing |
| Anime.js | Lightweight animations on `window.__hfAnime` |
| CSS keyframes | Simple animations (discoverable by engine) |
| Lottie | `lottie-web` / dotLottie on `window.__hfLottie` |
| Three.js | 3D scenes via `window.__hfThreeTime` |
| WAAPI | Web Animations API via `document.getAnimations()` |

## Agent integration

### Nexus skills (this system)

The skill you're reading IS the Hyperframes skill for Nexus. It follows the same `SKILL.md` spec used by the [vercel-labs/skills](https://github.com/vercel-labs/skills) ecosystem (Claude Code, Cursor, Codex, OpenClaw, and 50+ other agents).

### Hyperframes first-party skills (external agents)

For use in Claude Code, Cursor, Gemini CLI, or Codex:

```bash
npx skills add heygen-com/hyperframes
```

Registers slash commands: `/hyperframes`, `/hyperframes-cli`, `/hyperframes-media`, `/tailwind`, `/gsap`, plus adapter skills (`/animejs`, `/css-animations`, `/lottie`, `/three`, `/waapi`).

### Skills ecosystem

The `npx skills` CLI (from [vercel-labs/skills](https://github.com/vercel-labs/skills)) is a cross-agent skill installer. It supports Claude Code, Cursor, Codex, OpenClaw, Gemini CLI, Windsurf, Roo, and 50+ other agents. Skills are `SKILL.md` files — the same format Nexus uses. You can browse community skills at [skills.sh](https://skills.sh).

If you want to use Hyperframes with another agent alongside Nexus:
```bash
# Install to all detected agents
npx skills add heygen-com/hyperframes --all

# Or target a specific one
npx skills add heygen-com/hyperframes -a claude-code -a cursor
```

## Catalog blocks (50+)

Install ready-made components:

```bash
npx hyperframes add <block-name>
```

### Transitions (use between scenes)

| Block | Effect | Best for |
|-------|--------|----------|
| `flash-through-white` | White flash crossfade | Energy shifts, topic changes |
| `glitch` | Digital glitch artifacts | Tech content, disruption theme |
| `cinematic-zoom` | Dramatic zoom blur | Impact moments |
| `cross-warp-morph` | Cross-warped morphing | Surreal/dream transitions |
| `domain-warp-dissolve` | Fractal noise dissolve | Smooth scene changes |
| `chromatic-radial-split` | Chromatic aberration split | High-energy reveals |
| `gravitational-lens` | Gravity lensing distortion | Epic/sci-fi mood |
| `light-leak` | Cinematic light leak | Warm/analog feel |
| `ridged-burn` | Turbulent burn effect | Intense/dark theme |
| `ripple-waves` | Concentric ripple distortion | Calm/flowing mood |
| `swirl-vortex` | Swirling vortex | Disorientation/chaos |
| `thermal-distortion` | Heat haze distortion | Tension/pressure |
| `whip-pan` | Fast camera whip pan | Energy/momentum |
| `sdf-iris` | SDF iris reveal | Cinematic open/close |
| `transitions-3d` | 3D perspective flip/rotate | Product showcases |
| `transitions-blur` | Blur-based transitions | Soft/mood shifts |
| `transitions-cover` | Cover/uncover slide | Clean/corporate |
| `transitions-dissolve` | Dissolve and fade | Classic/cinema |
| `transitions-grid` | Grid-based tile | Tech/data content |
| `transitions-scale` | Scale and zoom | Impact/reveal |

### Social overlays

| Block | Platform |
|-------|----------|
| `instagram-follow` | Instagram follow card + button |
| `tiktok-follow` | TikTok follow card + button |
| `x-post` | X/Twitter post card with metrics |
| `reddit-post` | Reddit post card with upvotes |
| `spotify-card` | Spotify now-playing with album art |
| `yt-lower-third` | YouTube subscribe lower third |
| `macos-notification` | macOS notification banner |

### Data & infographics

| Block | Effect |
|-------|--------|
| `data-chart` | Animated bar + line chart, NYT-style typography, staggered reveal |
| `flowchart` | Animated decision tree with SVG connectors and sticky-note nodes |
| `apple-money-count` | Apple-style counter ($0 → $10K) with burst icons |

### VFX & showcases

| Block | Effect |
|-------|--------|
| `vfx-iphone-device` | Real GLTF iPhone/MacBook with live screen content |
| `vfx-liquid-background` | Organic liquid simulation under HTML content |
| `vfx-liquid-glass` | Liquid glass effect |
| `vfx-magnetic` | Magnetic attraction effect |
| `vfx-portal` | Portal effect |
| `vfx-shatter` | Shatter/break effect |
| `vfx-text-cursor` | Dramatic text reveal with cursor glow + chromatic rays |
| `app-showcase` | Fitness app showcase with floating phone screens |

### Components (layer on top of any composition)

| Component | Effect |
|-----------|--------|
| `grain-overlay` | Film grain texture — adds warmth and analog character |
| `grid-pixelate-wipe` | Screen dissolves into grid of squares |
| `shimmer-sweep` | Light sweep across text — premium reveals |
| `texture-mask-text` | Text with texture mask |

Browse all: https://hyperframes.heygen.com/catalog

## Video overlay transitions (burn, glitch, light leak footage)

Black-background footage (fire burns, glitch effects, light leaks) can be composited as scene transitions using CSS `mix-blend-mode: screen` — black pixels become transparent, only the bright effects show on top.

### Prepare 16:9 footage for 9:16 (crop + scale)

Source footage is typically 1920×1080. Must scale to fill 1920 height, then center-crop to 1080 width:

```bash
ffmpeg -y -i footage.mov \
  -vf "scale=-1:1920,crop=1080:1920" \
  -c:v libx264 -r 30 -g 30 -keyint_min 30 -an \
  -movflags +faststart assets/vtrans-burn.mp4
```

### HTML overlay with screen blend

```html
<div id="vt1" class="clip" style="position:absolute;inset:0;z-index:45;opacity:0;
     mix-blend-mode:screen;pointer-events:none"
     data-start="3.3" data-duration="0.83" data-track-index="40">
  <video src="assets/vtrans-burn.mp4" muted playsinline
         style="width:1080px;height:1920px;object-fit:cover"></video>
</div>
```

### Crossfade pattern (proper transitions)

Good transitions crossfade scene A out while scene B fades in, with the video overlay bridging both. NOT a hard cut + flash.

```javascript
function crossfade(outScene, inScene, overlay, t, crossDur) {
  crossDur = crossDur || 0.8;
  tl.to(outScene, { opacity: 0, duration: crossDur, ease: "power1.inOut" }, t);
  tl.fromTo(overlay, { opacity: 0 }, { opacity: 1, duration: crossDur * 0.4, ease: "power2.out" }, t);
  tl.to(overlay, { opacity: 0, duration: crossDur * 0.6, ease: "power2.in" }, t + crossDur * 0.4);
  tl.fromTo(inScene, { opacity: 0 }, { opacity: 1, duration: crossDur * 0.7, ease: "power1.inOut" }, t + crossDur * 0.3);
}
```

### Video footage × SFX pairing table

**Rule: always match the SFX category to the video overlay category.** Burn fire gets whoosh/swoosh. Glitch footage gets glitch SFX. Mismatches feel wrong.

| Video footage type | Source folder | Visual | Paired SFX | SFX source folder |
|---|---|---|---|---|
| Burn (default) | `!Burn and Leaks Transitions/Burn_01–28.mov` | Orange/yellow fire flash | `Whoosh SFX/Whoosh Animation *.WAV`, `Swoosh *.WAV` | `!SFX/Whoosh SFX/` |
| Burn Red | `!Burn and Leaks Transitions/007_Transitions_Burn_Red.mov` | Red fire flash | `Whoosh SFX/Whoosh Animation *.WAV` | `!SFX/Whoosh SFX/` |
| Burn Blue | `!Burn and Leaks Transitions/006_Transitions_Burn_Blue.mov` | Blue flame flash | `Whoosh SFX/Sci-Fi Epic Whoosh 01.WAV` | `!SFX/Whoosh SFX/` |
| Burn Purple | `!Burn and Leaks Transitions/004_Transitions Purple.mov` | Purple burn | `Whoosh SFX/Bass Drop Whoosh *.WAV` | `!SFX/Whoosh SFX/` |
| Glitch Digital | `!Glitch Transitions Footage/Glitch Transitions 01–04.mov` | Digital glitch artifacts | `Glitch SFX/Glitch 02.WAV`, `Glitch 05.WAV`, `Digital Glitch 04.WAV` | `!SFX/Glitch SFX/` |
| Glitch Heavy | `!Glitch Transitions Footage/Glitch Transitions 07–13.mov` | Heavy glitch, screen tear | `Glitch SFX/Hard Glitch 03.WAV`, `Data Glitch 02.WAV`, `Cyber Glitch 10.WAV` | `!SFX/Glitch SFX/` |
| Glitch VHS | `!Glitch Transitions Footage/VHS/` | VHS tracking lines | `Glitch SFX/VHS.WAV`, `VHS 02.WAV` | `!SFX/Glitch SFX/` |
| Light Leak | (from catalog block `light-leak` if needed) | Warm light sweep | `Swoosh 01–05.WAV` (soft) | `!SFX/Whoosh SFX/` |

**SFX duration should roughly match overlay duration** (0.5–1s whoosh for 0.6–0.8s burn, 1–2s glitch for 1s glitch footage). Trim SFX with ffmpeg if needed: `ffmpeg -i sfx.wav -t 1.0 sfx-trimmed.wav`.

### Alpha channel vs black-background footage

- **Black background** (most stock transitions): use `mix-blend-mode: screen` — black becomes transparent, only bright effects show.
- **Alpha channel** (ProRes 4444, WebM VP9 with alpha): do NOT use screen blend — use normal compositing. The alpha handles transparency. Set `opacity` on the overlay div to fade in/out.

Check which you have: `ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,pix_fmt -of csv=p=0 footage.mov`. If `pix_fmt` contains `alpha` or `a` (e.g. `yuva444p`), it has an alpha channel.

### "Two transitions in one" — simultaneous scene crossfade + overlay

The crossfade function above runs **two transitions simultaneously**:
1. **Scene crossfade**: Scene A opacity → 0, Scene B opacity → 1 (overlapping dissolve)
2. **Video overlay bridge**: overlay fades in at the midpoint, peaks, then fades out — visually connecting the two scenes

The overlay acts as a "visual bridge" between scenes A and B. The timing is:
- Scene A starts fading at `t`
- Overlay fades in at `t`, peaks at `t + 0.4×dur`
- Scene B starts fading in at `t + 0.3×dur`
- Overlay fades out from `t + 0.4×dur` to end

This creates a seamless transition where you never see a hard cut — always a dissolve with a fire/glitch layer on top.

### Transition duration guidelines

| Footage type | Recommended overlay duration | Reason |
|---|---|---|
| Burn (fast flash) | 0.6–0.8s | Fire is quick, extending looks fake |
| Glitch (digital) | 0.8–1.2s | Glitch needs a beat to register |
| Burn (slow burn) | 1.0–1.5s | Longer fire trail can work for dramatic moments |

## Audio fade-out at the end (post-render)

Hyperframes does NOT interpolate GSAP `attr` changes on `<audio>` elements. `gsap.to("#bgm", { attr: { "data-volume": 0 } })` will NOT fade the music. Apply the fade post-render:

```bash
ffmpeg -y -i output.mp4 -af "afade=t=out:st=49:d=6" -c:v copy -c:a aac output-final.mp4
```

Combine with a GSAP fade-to-black (tween a black overlay div) so video and audio fade together.

## Font sizes for 9:16 at 1080×1920

The viewport is large — browser-equivalent sizes look tiny in rendered output. Minimums:
- Section headers: **28px**
- Card titles / role names: **38px**
- Company/location lines: **24px**
- Body text / descriptions: **28px**
- Big hero numbers: **120–280px**
- CTA headings: **72px**
- Links / URLs: **28px**

Reading time: career/experience cards with a paragraph need **4–5 seconds** hold. People can't read faster on mobile.

## Gotchas

- **Music fade requires FFmpeg post-processing.** GSAP `attr: { "data-volume": 0 }` does NOT work — Hyperframes reads `data-volume` as a static attribute per seek, not an animated property. Always use `ffmpeg -af "afade=t=out:st=X:d=Y"` after render.
- **Dense keyframes required for video sources.** HeyGen output, screen recordings, and most downloaded videos have sparse keyframes (up to 10s apart). Hyperframes renders frame-by-frame and needs every frame seekable. **Always re-encode with `-g 30 -keyint_min 30`** before adding to a composition. Without this, you'll see frame freezing.
- **Non-blocking 404s during render are harmless.** You'll see `[non-blocking] Failed to load resource: 404` — the engine probing for optional assets. Ignore them.
- **Node.js >= 22 required.** Check with `node --version`.
- **Not a replacement for HeyGen avatars.** This renders HTML — it doesn't generate talking heads.
- **Apache 2.0 license** — free, unlimited renders. No per-render fees or seat caps.
- **No build step.** The HTML file IS the composition. No bundler, no React.
- **Same input = identical output.** Deterministic rendering — CI-friendly.
- **Docs:** https://hyperframes.heygen.com/introduction
- **LLM-friendly docs index:** https://hyperframes.mintlify.app/llms.txt
