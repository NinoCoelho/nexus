#!/usr/bin/env python3
"""Silence cut for talking-head videos.

- Uses ffmpeg silencedetect to find silent ranges.
- Inverts them into speaking segments with padding.
- Concats segments using concat demuxer with inpoint/outpoint.

Outputs:
- <out>.mp4 stitched
- <out>.json segments

Usage:
  python3 silence_cut.py --input in.mp4 --output out.mp4 \
    --noise -34 --min-silence 0.5 --pad-before 0.08 --pad-after 0.08
"""

import argparse, json, re, subprocess
from dataclasses import asdict, dataclass

@dataclass
class Segment:
  start: float
  end: float


def run(cmd):
  return subprocess.run(cmd, capture_output=True, text=True, check=True)


def ffprobe_duration(path: str) -> float:
  r = run([
    'ffprobe','-v','error','-show_entries','format=duration',
    '-of','default=noprint_wrappers=1:nokey=1', path
  ])
  return float(r.stdout.strip())


def detect_silences(path: str, noise_db: float, min_silence: float):
  p = subprocess.run([
    'ffmpeg','-hide_banner','-i', path,
    '-af', f'silencedetect=noise={noise_db}dB:d={min_silence}',
    '-f','null','-'
  ], capture_output=True, text=True)

  silences = []
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
        silences.append(cur)
        cur = None
  return silences


def invert_to_segments(duration: float, silences, pad_before: float, pad_after: float):
  segs = []
  t = 0.0
  for s in silences:
    start = s['start']
    end = s.get('end', start)
    a = max(0.0, t + pad_before)
    b = max(0.0, start - pad_after)
    if b > a:
      segs.append(Segment(a, b))
    t = max(t, end)
  if duration - t > 0.2:
    a = max(0.0, t + pad_before)
    b = duration
    if b > a:
      segs.append(Segment(a, b))
  return segs


def write_concat_file(input_path: str, segments, concat_path: str):
  with open(concat_path, 'w') as f:
    for seg in segments:
      f.write(f"file '{input_path}'\n")
      f.write(f"inpoint {seg.start}\n")
      f.write(f"outpoint {seg.end}\n")


def concat_segments(concat_path: str, output_path: str):
  """Concat segments using trim filter to avoid audio desync."""
  # Read segments from concat file
  import re
  with open(concat_path) as f:
    content = f.read()
  
  # Parse inpoint/outpoint pairs
  segments = []
  current_file = None
  for line in content.splitlines():
    if line.startswith("file '"):
      current_file = line.split("'")[1]
    elif line.startswith("inpoint "):
      start = float(line.split()[1])
    elif line.startswith("outpoint "):
      end = float(line.split()[1])
      if current_file:
        segments.append((current_file, start, end))
  
  if not segments:
    raise ValueError("No segments found in concat file")
  
  # Build ffmpeg filter chain using trim/asetpts to avoid desync
  filter_parts = []
  for i, (fpath, start, end) in enumerate(segments):
    filter_parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]")
    filter_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]")
  
  # Build concat filter
  v_inputs = ''.join(f'[v{i}]' for i in range(len(segments)))
  a_inputs = ''.join(f'[a{i}]' for i in range(len(segments)))
  filter_parts.append(f"{v_inputs}concat=n={len(segments)}:v=1:a=0[outv]")
  filter_parts.append(f"{a_inputs}concat=n={len(segments)}:v=0:a=1[outa]")
  
  filter_complex = ';'.join(filter_parts)
  
  # Use first segment's file as input (all segments reference same file)
  input_file = segments[0][0]
  
  cmd = [
    'ffmpeg', '-y', '-hide_banner',
    '-i', input_file,
    '-filter_complex', filter_complex,
    '-map', '[outv]', '-map', '[outa]',
    '-c:v', 'libx264', '-crf', '20', '-preset', 'medium',
    '-c:a', 'aac', '-b:a', '160k',
    '-movflags', '+faststart',
    output_path
  ]
  
  subprocess.run(cmd, check=True)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument('--input', required=True)
  ap.add_argument('--output', required=True)
  ap.add_argument('--noise', type=float, default=-34)
  ap.add_argument('--min-silence', type=float, default=0.5)
  ap.add_argument('--pad-before', type=float, default=0.08)
  ap.add_argument('--pad-after', type=float, default=0.08)
  ap.add_argument('--min-seg', type=float, default=0.6)
  args = ap.parse_args()

  dur = ffprobe_duration(args.input)
  sil = detect_silences(args.input, args.noise, args.min_silence)
  segs = invert_to_segments(dur, sil, args.pad_before, args.pad_after)
  segs = [s for s in segs if (s.end - s.start) >= args.min_seg]

  concat_path = args.output + '.concat.txt'
  json_path = args.output + '.segments.json'

  write_concat_file(args.input, segs, concat_path)
  with open(json_path, 'w') as f:
    json.dump({'input': args.input, 'duration': dur, 'segments': [asdict(s) for s in segs]}, f, indent=2)

  concat_segments(concat_path, args.output)
  print(json.dumps({'status':'ok','output': args.output, 'segments': json_path, 'count': len(segs)}))

if __name__ == '__main__':
  main()
