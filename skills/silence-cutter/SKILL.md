---
name: silence-cutter
description: Use this whenever you need to remove silence/dead-air from talking-head videos while preserving speech order. Supports both VAD-based (recommended) and FFmpeg silencedetect-based cutting with configurable padding, merge gaps, and audio crossfades. Prefer over manual ffmpeg for any silence removal task.
type: procedure
role: media
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- Removing dead air from talking-head / headshot videos
- Pre-processing raw video before adding music/captions
- Any video where the speaker pauses and you want tighter pacing
- Producing a `.segments.json` for downstream zoom/caption/music effects

## Prerequisites

```bash
command -v ffmpeg  >/dev/null || { echo "missing: ffmpeg — install with: brew install ffmpeg (macOS) | apt-get install ffmpeg (Debian/Ubuntu)"; exit 1; }
command -v ffprobe >/dev/null || { echo "missing: ffprobe (ships with ffmpeg)"; exit 1; }
```

This skill has an isolated Python environment managed by Nexus (provides `webrtcvad` for Mode 1). After calling `skill_view(name="silence-cutter")`, use the `python.path` from the response to run scripts. Modes 2 and 3 work without the venv.

The 3 scripts ship inside this skill at `scripts/`. Reference them by skill-relative path. The agent's tools resolve the skill directory through `skill_view`; if running a script directly, use the absolute path `<skill-dir>/scripts/<name>.py`.

## Three modes

### Mode 1 — VAD Cut (recommended for speech)

WebRTC VAD on 16 kHz mono PCM. Better at phrase boundaries than pure silence detection.

```bash
"$SKILL_PYTHON" scripts/vad_cut.py --input raw.mp4 --output cut.mp4
```

| Param | Default | What it does |
|---|---|---|
| `--vad-mode` | 2 | Aggressiveness (0=least, 3=most) |
| `--frame-ms` | 30 | VAD frame size in ms |
| `--min-speech` | 0.45 | Min speech segment to keep (s) |
| `--min-silence` | 0.35 | Min silence to trigger cut (s) |
| `--pad-before` | 0.15 | Padding before speech starts |
| `--pad-after` | 0.22 | Padding after speech ends |
| `--merge-gap` | 0.33 | Merge segments closer than this |
| `--audio-xfade` | 0.06 | Audio crossfade between segments |
| `--tail-pad` | 1.0 | Extra padding on last segment |

Algorithm: extract 16k mono WAV → per-frame VAD → group into segments (`min_speech`/`min_silence`) → merge close gaps → pad → re-merge → tail padding → render via `concat` filter (no `acrossfade` to avoid audio desync).

### Mode 2 — Silence Cut (fallback for non-speech)

`ffmpeg silencedetect`, no VAD dependency.

```bash
python3 scripts/silence_cut.py --input raw.mp4 --output cut.mp4 \
  --noise -34 --min-silence 0.5 --pad-before 0.08 --pad-after 0.08
```

| Param | Default | What it does |
|---|---|---|
| `--noise` | -34 | Silence threshold in dB |
| `--min-silence` | 0.5 | Min silence duration to detect |
| `--pad-before` | 0.08 | Padding before speech |
| `--pad-after` | 0.08 | Padding after speech |
| `--min-seg` | 0.6 | Drop segments shorter than this |

### Mode 3 — Polish Cut (smooth audio transitions)

Like silence cut but with audio crossfades for smoother listening.

```bash
python3 scripts/polish_cut.py --input raw.mp4 --output polished.mp4 \
  --noise -34 --min-silence 0.6 --merge-gap 0.35 --min-clip 1.2 --audio-xfade 0.06
```

| Param | Default | What it does |
|---|---|---|
| `--merge-gap` | 0.35 | Merge close segments |
| `--min-clip` | 1.2 | Min clip duration (shorter merged into neighbors) |
| `--audio-xfade` | 0.06 | Crossfade between audio segments |

## Segments JSON format

All modes write `<output>.segments.json` next to the output:

```json
{
  "input": "raw.mp4",
  "vad_mode": 2,
  "frame_ms": 30,
  "segments": [
    {"start": 0.15, "end": 4.22},
    {"start": 4.88, "end": 12.30}
  ]
}
```

This file is the **interface contract** — downstream skills (zoom, captions, music) consume it.

## Gotchas
- **Audio desync**: VAD cut uses `concat` (not `acrossfade`) to avoid progressive audio shortening. Polish cut uses `acrossfade` but with very short (0.06 s) durations.
- **Segments file is the interface**: keep `.segments.json` alongside the output — zoom and energy-based effects need it.
- **CRF 20**: all outputs are visually lossless. Adjust if file size matters.
- **Tail padding**: VAD cut adds extra padding to the last segment so the video doesn't end abruptly.
