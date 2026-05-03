#!/usr/bin/env python3
"""VAD-based cut (better than silencedetect for phrase endings).

Uses WebRTC VAD on 16kHz mono PCM to detect speech frames.
Builds speech segments, merges close gaps, adds breath padding.
Then renders with ffmpeg filter_complex (hard cuts + audio acrossfade).

Usage:
  python3 vad_cut.py --input in.mp4 --output out.mp4

Tuning:
  --vad-mode 2   (0 least aggressive, 3 most)
  --frame-ms 30
  --min-speech 0.4
  --min-silence 0.35
  --pad-before 0.15 --pad-after 0.20
  --merge-gap 0.33
  --audio-xfade 0.06
"""

import argparse, json, os, subprocess, wave, sys
from dataclasses import dataclass, asdict
try:
  import webrtcvad
except Exception as e:
  print("[vad_cut] Missing dependency 'webrtcvad'. Install with:")
  print("  uv pip install webrtcvad")
  print(f"  error: {e}")
  sys.exit(2)

@dataclass
class Seg:
  start: float
  end: float


def run(cmd):
  subprocess.run(cmd, check=True, capture_output=False)


def capture(cmd):
  return subprocess.run(cmd, check=True, capture_output=True, text=True)


def extract_wav(input_path: str, wav_path: str, sr: int = 16000):
  run([
    'ffmpeg','-hide_banner','-y','-i', input_path,
    '-ac','1','-ar', str(sr),
    '-vn',
    '-f','wav', wav_path
  ])


def duration(path: str) -> float:
  r = capture(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1', path])
  return float(r.stdout.strip())


def read_frames(wav_path: str, frame_ms: int):
  wf = wave.open(wav_path, 'rb')
  assert wf.getnchannels() == 1
  assert wf.getsampwidth() == 2
  sr = wf.getframerate()
  frame_len = int(sr * frame_ms / 1000)
  bytes_per_frame = frame_len * 2
  idx = 0
  while True:
    data = wf.readframes(frame_len)
    if len(data) < bytes_per_frame:
      break
    ts = idx * frame_ms / 1000.0
    yield ts, data
    idx += 1
  wf.close()


def frames_to_segments(speech_flags, frame_ms, min_speech, min_silence):
  # speech_flags: list[bool]
  segs=[]
  in_speech=False
  start_idx=0
  min_speech_frames = int(min_speech / (frame_ms/1000))
  min_silence_frames = int(min_silence / (frame_ms/1000))
  silence_run=0

  for i,flag in enumerate(speech_flags):
    if flag:
      if not in_speech:
        in_speech=True
        start_idx=i
        silence_run=0
      else:
        silence_run=0
    else:
      if in_speech:
        silence_run += 1
        if silence_run >= min_silence_frames:
          end_idx = i - silence_run + 1
          if end_idx - start_idx >= min_speech_frames:
            segs.append((start_idx, end_idx))
          in_speech=False
          silence_run=0

  if in_speech:
    end_idx = len(speech_flags)
    if end_idx - start_idx >= min_speech_frames:
      segs.append((start_idx, end_idx))

  # convert to seconds
  out=[]
  for s,e in segs:
    out.append(Seg(s*frame_ms/1000.0, e*frame_ms/1000.0))
  return out


def merge_close(segs, gap):
  if not segs:
    return []
  segs=sorted(segs, key=lambda s:s.start)
  merged=[segs[0]]
  for s in segs[1:]:
    prev=merged[-1]
    if s.start - prev.end <= gap:
      prev.end = max(prev.end, s.end)
    else:
      merged.append(s)
  return merged


def pad(segs, dur, before, after):
  out=[]
  for s in segs:
    a=max(0.0, s.start-before)
    b=min(dur, s.end+after)
    out.append(Seg(a,b))
  return out


def build_filter(segs, audio_xfade):
  """Build filter graph with concat (no acrossfade to avoid audio desync).

  Using concat instead of acrossfade prevents progressive audio shortening.
  acrossfade overlaps audio streams, losing ~audio_xfade seconds per segment.
  With 10 segments and 0.06s xfade, that's ~0.54s of audio lost.
  """
  parts=[]
  vlabels=[]
  alabels=[]
  for i,s in enumerate(segs):
    parts.append(f"[0:v]trim=start={s.start}:end={s.end},setpts=PTS-STARTPTS[v{i}]")
    parts.append(f"[0:a]atrim=start={s.start}:end={s.end},asetpts=PTS-STARTPTS[a{i}]")
    vlabels.append(f"[v{i}]")
    alabels.append(f"[a{i}]")

  # Use concat for both video and audio (no acrossfade to maintain sync)
  parts.append(''.join(vlabels) + f"concat=n={len(segs)}:v=1:a=0[vout]")
  parts.append(''.join(alabels) + f"concat=n={len(segs)}:v=0:a=1[aout]")

  return ';'.join(parts)


def main():
  ap=argparse.ArgumentParser()
  ap.add_argument('--input', required=True)
  ap.add_argument('--output', required=True)
  ap.add_argument('--vad-mode', type=int, default=2)
  ap.add_argument('--frame-ms', type=int, default=30)
  ap.add_argument('--min-speech', type=float, default=0.45)
  ap.add_argument('--min-silence', type=float, default=0.35)
  ap.add_argument('--pad-before', type=float, default=0.15)
  ap.add_argument('--pad-after', type=float, default=0.22)
  ap.add_argument('--merge-gap', type=float, default=0.33)
  ap.add_argument('--audio-xfade', type=float, default=0.06)
  ap.add_argument('--tail-pad', type=float, default=1.0)
  args=ap.parse_args()

  dur=duration(args.input)

  wav_path=args.output + '.tmp.wav'
  extract_wav(args.input, wav_path)

  vad=webrtcvad.Vad(args.vad_mode)
  flags=[]
  for ts, frame in read_frames(wav_path, args.frame_ms):
    flags.append(vad.is_speech(frame, 16000))

  segs=frames_to_segments(flags, args.frame_ms, args.min_speech, args.min_silence)
  segs=merge_close(segs, args.merge_gap)
  segs=pad(segs, dur, args.pad_before, args.pad_after)

  # re-merge after padding
  segs=merge_close(segs, args.merge_gap)

  # ensure last segment has extra tail padding
  if segs:
    last=segs[-1]
    last.end=min(dur, last.end + args.tail_pad)

  fc=build_filter(segs, args.audio_xfade)
  json_path=args.output + '.segments.json'
  with open(json_path,'w') as f:
    json.dump({'input': args.input, 'vad_mode': args.vad_mode, 'frame_ms': args.frame_ms,
               'segments':[asdict(s) for s in segs]}, f, indent=2)

  run([
    'ffmpeg','-hide_banner','-y',
    '-i', args.input,
    '-filter_complex', fc,
    '-map','[vout]','-map','[aout]',
    '-c:v','libx264','-crf','20','-preset','medium',
    '-pix_fmt','yuv420p',
    '-c:a','aac','-b:a','128k',
    '-movflags','+faststart',
    args.output
  ])

  os.remove(wav_path)
  print(json.dumps({'status':'ok','output': args.output, 'segments': len(segs), 'segments_json': json_path}))

if __name__=='__main__':
  main()
