---
name: voice-isolate
description: Use this whenever you need to isolate vocals from a video or audio file, removing background music while keeping speech clean. Prefer over any manual EQ/filtering approach.
type: procedure
role: media
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- Video has music mixed in and you need voice-only audio
- Need to replace background music on a video that already has music
- Extracting clean dialogue/narration from any audio/video source

## Prerequisites

Run this preflight before the steps below. If anything is missing, surface the install hint and stop — do not attempt fallback hacks.

```bash
command -v ffmpeg  >/dev/null || { echo "missing: ffmpeg — install with: brew install ffmpeg (macOS) | apt-get install ffmpeg (Debian/Ubuntu)"; exit 1; }
DEMUCS=$(command -v demucs || true)
if [ -z "$DEMUCS" ]; then
  echo "missing: demucs — install with: pip install demucs (or: pipx install demucs)"
  exit 1
fi
```

`$DEMUCS` resolves to whatever path the user has (Homebrew, pipx, system pip, venv). The first run also downloads the htdemucs model (~80MB) into `~/.cache/torch/hub/checkpoints/`.

## Steps

### 1. Extract audio from video (if needed)

```bash
ffmpeg -y -i input.mp4 -vn -acodec pcm_s16le -ar 44100 -ac 2 /tmp/demucs-input.wav
```

### 2. Run demucs (htdemucs model, two-stem mode)

```bash
"$DEMUCS" --two-stem=vocals -o /tmp/demucs-output /tmp/demucs-input.wav
```

Output lands at `/tmp/demucs-output/htdemucs/<basename>/`:
- `vocals.wav` — voice only
- `no_vocals.wav` — music/instrumental only

### 3. Use the isolated vocals

**Replace music on a video:**

```bash
ffmpeg -y \
  -i original.mp4 \
  -i /tmp/demucs-output/htdemucs/demucs-input/vocals.wav \
  -map 0:v -map 1:a \
  -c:v copy -c:a aac -b:a 192k \
  -shortest output-vocalsonly.mp4
```

**Mix in new background music:**

```bash
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 output-vocalsonly.mp4)
FADE_OUT=$(echo "$DUR - 3" | bc)

ffmpeg -y \
  -i output-vocalsonly.mp4 \
  -stream_loop -1 -i "new_music.mp3" \
  -filter_complex "[1:a]volume=0.30,afade=t=in:st=0:d=2,afade=t=out:st=${FADE_OUT}:d=3[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[aout]" \
  -map 0:v -map "[aout]" \
  -c:v libx264 -crf 18 -preset fast -c:a aac -b:a 192k \
  -shortest final.mp4
```

## Gotchas
- **`--two-stem=vocals`** is faster — splits into just 2 stems (vocals + no_vocals) instead of 4. Use this mode unless you need drum/bass separation.
- **Processing time**: ~18s for a 70s track on Apple Silicon CPU. GPU (MPS) can be faster but CPU is reliable.
- **Use `/tmp/`** for intermediates — WAV files are large (~12MB per minute). Clean up after.
- **`-stream_loop -1`** loops the new music track if it's shorter than the video.
- **`normalize=0`** in amix prevents ffmpeg from lowering the voice volume when mixing.
