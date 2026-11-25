#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 input.mp4 output.gif [start-end:speed ...]" >&2
  echo "  e.g. $0 rec.mp4 docs/img/demo-deep-pick.gif 0-12:1 12-95:8 95-110:1" >&2
  exit 1
fi

input=$1; output=$2; shift 2
fps=${FPS:-12}; width=${WIDTH:-820}

if [ $# -eq 0 ]; then set -- "0-1e9:1"; fi

filter=""; labels=""; i=0
for seg in "$@"; do
  range=${seg%%:*}; speed=${seg##*:}
  start=${range%%-*}; end=${range##*-}
  filter+="[0:v]trim=start=${start}:end=${end},setpts=(PTS-STARTPTS)/${speed}[s${i}];"
  labels+="[s${i}]"; i=$((i+1))
done
filter+="${labels}concat=n=${i}:v=1:a=0,fps=${fps},scale=${width}:-1:flags=lanczos,split[a][b];"
filter+="[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"

ffmpeg -y -loglevel error -i "$input" -filter_complex "$filter" "$output"
echo "wrote $output ($(du -h "$output" | cut -f1))"
