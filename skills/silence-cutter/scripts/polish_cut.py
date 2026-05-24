#!/usr/bin/env python3
"""Polished cut for talking-head: less jumpy cuts + smooth audio.

Pipeline:
1) Detect silences (ffmpeg silencedetect)
2) Invert into speaking segments with padding
3) Merge close segments, enforce min clip duration
4) Build a filter_complex selecting segments from video+audio
5) Apply short audio crossfades between segments

Note: We keep video hard cuts (fast), but smooth audio with acrossfade.

Usage:
  python3 polish_cut.py --input in.mp4 --output out.mp4

Tuning:
  --noise -34 --min-silence 0.6 --pad-before 0.12 --pad-after 0.18
  --merge-gap 0.35 --min-clip 1.2 --audio-xfade 0.06
"""

import argparse, json, re, subprocess
from dataclasses import dataclass, asdict

@dataclass
class Seg:
  start: float
  end: float


def run(cmd):
  subprocess.run(cmd, check=True)


def capture(cmd):
  return subprocess.run(cmd, capture_output=True, text=True, check=True)


def duration(path: str) -> float:
  r = capture(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1', path])
  return float(r.stdout.strip())


def detect_silences(path: str, noise_db: float, min_silence: float):
  p = subprocess.run([
    'ffmpeg','-hide_banner','-i', path,
    '-af', f'silencedetect=noise={noise_db}dB:d={min_silence}',
    '-f','null','-'
  ], capture_output=True, text=True)

  sil = []
  cur = None
  for line in p.stderr.splitlines():
    if 'silence_start:' in line:
      m = re.search(r'silence_start: ([0-9.]+)', line)
      if m:
        cur = {'start': float(m.group(1))}
    if 'silence_end:' in line:
      m = re.search(r'silence_end: ([0-9.]+)', line)
      if m and cur:
        cur['end'] = float(m.group(1))
        sil.append(cur)
        cur = None
  return sil


def invert(dur, sil, pad_before, pad_after):
  out=[]
  t=0.0
  for s in sil:
    a=max(0.0, t+pad_before)
    b=max(0.0, s['start']-pad_after)
    if b>a:
      out.append(Seg(a,b))
    t=max(t, s.get('end', s['start']))
  if dur-t>0.2:
    a=max(0.0, t+pad_before)
    b=dur
    if b>a:
      out.append(Seg(a,b))
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


def enforce_min_clip(segs, min_clip):
  # merge tiny clips into neighbors
  if not segs:
    return []
  out=[]
  i=0
  while i<len(segs):
    s=segs[i]
    if (s.end-s.start) >= min_clip:
      out.append(s); i+=1; continue
    # try merge with previous else next
    if out:
      out[-1].end = s.end
    elif i+1<len(segs):
      segs[i+1].start = s.start
    i+=1
  # second pass: drop any still tiny
  return [s for s in out if (s.end-s.start) >= (min_clip*0.6)]


def build_filter(segs, audio_xfade):
  # Build per-segment trim for v and a
  parts=[]
  vlabels=[]
  alabels=[]
  for idx,s in enumerate(segs):
    v=f"v{idx}"
    a=f"a{idx}"
    parts.append(f"[0:v]trim=start={s.start}:end={s.end},setpts=PTS-STARTPTS[{v}]"
                 )
    parts.append(f"[0:a]atrim=start={s.start}:end={s.end},asetpts=PTS-STARTPTS[{a}]"
                 )
    vlabels.append(f"[{v}]")
    alabels.append(f"[{a}]")

  # Video concat (hard cuts)
  parts.append(''.join(vlabels) + f"concat=n={len(segs)}:v=1:a=0[vout]")

  # Audio: chain acrossfade to smooth cuts
  if len(segs)==1:
    parts.append(f"{alabels[0]}anull[aout]")
  else:
    cur = f"{alabels[0]}"
    for i in range(1,len(segs)):
      nxt = alabels[i]
      out = f"ax{i}"
      # acrossfade duration must be <= shortest clip. use audio_xfade
      parts.append(f"{cur}{nxt}acrossfade=d={audio_xfade}:c1=tri:c2=tri[{out}]")
      cur = f"[{out}]"
    parts.append(f"{cur}anull[aout]")

  return ';'.join(parts)


def main():
  ap=argparse.ArgumentParser()
  ap.add_argument('--input', required=True)
  ap.add_argument('--output', required=True)
  ap.add_argument('--noise', type=float, default=-34)
  ap.add_argument('--min-silence', type=float, default=0.6)
  ap.add_argument('--pad-before', type=float, default=0.12)
  ap.add_argument('--pad-after', type=float, default=0.18)
  ap.add_argument('--merge-gap', type=float, default=0.35)
  ap.add_argument('--min-clip', type=float, default=1.2)
  ap.add_argument('--audio-xfade', type=float, default=0.06)
  args=ap.parse_args()

  dur=duration(args.input)
  sil=detect_silences(args.input, args.noise, args.min_silence)
  segs=invert(dur, sil, args.pad_before, args.pad_after)
  segs=merge_close(segs, args.merge_gap)
  segs=enforce_min_clip(segs, args.min_clip)

  fc=build_filter(segs, args.audio_xfade)
  json_path=args.output + '.segments.json'
  with open(json_path,'w') as f:
    json.dump({'input': args.input, 'segments':[asdict(s) for s in segs]}, f, indent=2)

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

  print(json.dumps({'status':'ok','output': args.output, 'segments': len(segs), 'segments_json': json_path}))

if __name__=='__main__':
  main()
