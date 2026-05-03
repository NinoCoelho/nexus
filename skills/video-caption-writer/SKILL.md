---
name: video-caption-writer
description: Use this whenever you need to write a social media caption for a finished video. Transcribes the video content and generates a scroll-stopping caption with strategic hashtags.
type: procedure
role: content
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- Video is finished and ready to post on social media
- Need a caption based on the actual video content
- Need strategic hashtags (not a random dump of 30)
- Prefer this over writing captions manually or from memory

## Prerequisites

Run this preflight before transcription. If anything is missing, surface the install hint and stop.

```bash
command -v ffmpeg >/dev/null || { echo "missing: ffmpeg -- install with: brew install ffmpeg (macOS) | apt-get install ffmpeg (Debian/Ubuntu)"; exit 1; }
PYCAPS_ENV="$HOME/.venvs/pycaps"
if [ ! -d "$PYCAPS_ENV" ]; then
  echo "missing: pycaps venv at $PYCAPS_ENV -- create with: python3 -m venv $PYCAPS_ENV && source $PYCAPS_ENV/bin/activate && pip install faster-whisper"
  exit 1
fi
```

## Steps

### 1. Transcribe the video
```bash
source ~/.venvs/pycaps/bin/activate && python3 -c "
from faster_whisper import WhisperModel
model = WhisperModel('base', device='cpu', compute_type='float32')
segments, info = model.transcribe('video.mp4', vad_filter=True)
print(f'Language: {info.language}')
for s in segments:
    print(f'[{s.start:.1f}-{s.end:.1f}] {s.text}')
"
```

### 2. Write the caption

Follow this structure:

1. **HEADLINE** -- ALL CAPS, provocative or emotional, summarizing the core message. Must stop the scroll.
2. **CONTEXT** -- 2-3 lines setting up the situation (personal story, challenge, turning point)
3. **TWIST** -- The unexpected detail or emotional beat that reframes everything
4. **TAKEAWAY** -- The lesson, stated directly and personally. Use em-dash, not comma chains.
5. **CLOSING** -- Short closer + CTA (share, follow, stay tuned)
6. **SIGN-OFF** -- Personal sign-off matching the content tone

Rules:
- Write in the user's preferred language (default: match the video language)
- Short paragraphs, 1-2 lines max
- Use emojis sparingly (2-4 total, at emotional peaks)
- Never sound preachy. Sound like a friend telling a story over coffee
- The headline must work as a standalone hook that makes someone stop scrolling

### 3. Select hashtags (2-5 only)

**Selection strategy:**
- Mix 1 broad + 1 format + 1 niche
- Broad: general theme of the content
- Format: content type (#tutorial, #behindthescenes, #howto, etc.)
- Niche: audience-specific to the content topic

**Best practices for hashtags:**
- Fewer targeted hashtags outperform large generic sets on most platforms in 2026
- Match the hashtag language to the caption language
- Avoid banned or shadowbanned hashtags

### 4. Save caption
Write to vault with format:
```
notes/caption-{video-filename}.md
```

With sections:
- Caption (ready to copy and paste)
- Hashtags
- Strategy note (brief explanation of hashtag choices)

## Gotchas
- Always transcribe from the actual video, never guess the content
- Match the caption tone to the video energy (reflective, excited, challenging, etc.)
- The headline in ALL CAPS is non-negotiable -- it doubles as the feed thumbnail hook
- Keep hashtags to 2-5 for Reels/TikTok, 3-5 for LinkedIn, up to 15 for Instagram feed posts
- If the video is in a different language than the user's preference, confirm which language to use for the caption
