---
name: video-editor
description: Use this whenever creating or editing videos with Remotion — React-based deterministic video rendering with beat-synced compositions. For post-processing steps (silence cutting, music selection, zoom, audio polish), use the dedicated skills instead. Prefer over manual Remotion code for any video production task.
---

## When to use

- Creating complex video compositions with Remotion (React/TSX)
- Beat-synced audio visualization in video
- Multi-layer compositions with `<Sequence>`, `<Series>`, `<AbsoluteFill>`
- When Hyperframes (`hyperframes` skill) doesn't fit the need (complex React component trees, distributed rendering via Remotion Lambda)

## When NOT to use

- **Silence removal** → use `silence-cutter`
- **Music selection + beat analysis** → use `music-selector`
- **Voice normalization** → use `audio-polish`
- **Dynamic zoom effects** → use `video-zoom`
- **Captions** → use pycaps directly or the caption step in `heygen-headshot`
- **Simple FFmpeg operations** (concat, trim, overlay) → use FFmpeg directly

## Skill responsibility map

| Responsibility | Skill | When to use independently |
|---|---|---|
| **Silence removal** | `silence-cutter` | Any talking-head video needing dead-air removal |
| **Music selection** | `music-selector` | Picking BGM from the local library + beat analysis |
| **Audio polish** | `audio-polish` | Voice normalization/cleanup |
| **Dynamic zoom** | `video-zoom` | FFmpeg-based zoom on talking-head videos |
| **Remotion rendering** | `video-editor` (this skill) | Beat-synced compositions, multi-layer renders |

## Pipeline (Remotion rendering only)

```
Pre-processed assets (from other skills):
  - Cut video (silence-cutter)
  - Polished audio (audio-polish)
  - Music track + beat data (music-selector)
  - Zoomed video (video-zoom) — optional
                │
                ▼
       [Remotion render] → base video
                │
                ▼
       [pycaps captions] → final video
```

## Steps

### 1. Project Setup

```bash
npx create-video@latest --yes --blank --no-tailwind my-video
cd my-video
```

Start preview:
```bash
npx remotion studio
```

Install required packages:
```bash
npx remotion add @remotion/media @remotion/media-utils @remotion/captions
npm i zod
```

### 2. Prepare assets

Copy pre-processed assets to the Remotion `public/` folder:

```bash
cp /path/to/cut-video.mp4 public/video.mp4
cp /path/to/beat-data.json public/beats.json
cp /path/to/selected-track.wav public/music.wav
```

### 3. Write the Remotion composition (TSX)

**Key Remotion rules:**
- **ALL animations must use `useCurrentFrame()` + `interpolate()`** — CSS animations/transitions are FORBIDDEN
- Use `Easing.bezier()` for timing curves
- Use `<Sequence>` for timing, `<Series>` for sequential playback
- Always premount `<Sequence>` components
- Use `<Audio>` from `@remotion/media` for music tracks
- Use `useWindowedAudioData()` + `visualizeAudio()` for beat-reactive visuals
- **Static files:** access via `staticFile()`, never raw paths

#### Example: Beat-synced video composition

```tsx
import { useCurrentFrame, useVideoConfig, interpolate, Sequence, staticFile, AbsoluteFill } from "remotion";
import { Audio } from "@remotion/media";
import { useWindowedAudioData, visualizeAudio } from "@remotion/media-utils";
import { z } from "zod";

export const BeatVideoSchema = z.object({
  videoSrc: z.string(),
  musicSrc: z.string(),
  captions: z.array(z.object({
    text: z.string(),
    startMs: z.number(),
    endMs: z.number(),
  })),
  musicVolume: z.number().min(0).max(1).default(0.3),
});

export const BeatVideo: React.FC<z.infer<typeof BeatVideoSchema>> = ({
  videoSrc, musicSrc, captions, musicVolume,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const { audioData, dataOffsetInSeconds } = useWindowedAudioData({
    src: staticFile(musicSrc), frame, fps, windowInSeconds: 30,
  });

  let bassIntensity = 0;
  if (audioData) {
    const frequencies = visualizeAudio({
      fps, frame, audioData, numberOfSamples: 128,
      optimizeFor: "speed", dataOffsetInSeconds,
    });
    const bass = frequencies.slice(0, 16);
    bassIntensity = bass.reduce((s, v) => s + v, 0) / bass.length;
  }

  const beatScale = interpolate(bassIntensity, [0, 0.5], [1, 1.08], {
    extrapolateRight: "clamp",
  });

  const currentTimeMs = (frame / fps) * 1000;
  const activeCaption = captions.find(
    c => currentTimeMs >= c.startMs && currentTimeMs <= c.endMs
  );

  return (
    <AbsoluteFill>
      <div style={{ transform: `scale(${beatScale})`, transformOrigin: "center" }}>
        <video src={staticFile(videoSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </div>
      <Audio src={staticFile(musicSrc)} volume={musicVolume} />
      {activeCaption && (
        <div style={{
          position: "absolute", bottom: "15%", left: "50%",
          transform: "translateX(-50%)", color: "white", fontSize: 48,
          fontWeight: "bold", textAlign: "center", maxWidth: "90%",
          textShadow: "0 0 10px rgba(0,0,0,0.8), 0 2px 4px rgba(0,0,0,0.6)",
        }}>
          {activeCaption.text}
        </div>
      )}
    </AbsoluteFill>
  );
};
```

### 4. Render

```bash
# Full render
npx remotion render BeatVideo output/raw_video.mp4

# Quick test (single frame at low res)
npx remotion still BeatVideo --frame=30 --scale=0.25
```

### 5. Add captions (post-render)

```bash
source ~/.venvs/pycaps/bin/activate
pycaps render --input output/raw_video.mp4 --template redpill --faster-whisper
```

## Gotchas

- **CSS animations forbidden in Remotion** — use `interpolate()` + `useCurrentFrame()` only.
- **Frame inside Sequence** — `useCurrentFrame()` returns local frame, not composition frame.
- **Audio data offset** — always pass `dataOffsetInSeconds` to `visualizeAudio`.
- **Static files** — Remotion `public/` accessed via `staticFile()`. Never raw paths.
- **Build step required** — Remotion needs a bundler. No plain HTML authoring like Hyperframes.
- **Remotion license** — source-available, NOT open source. Requires paid license above small-team thresholds. Consider `hyperframes` (Apache 2.0) for open-source needs.
- **pycaps requires Python 3.10+** — use venv at `~/.venvs/pycaps`. System is 3.9.6.
