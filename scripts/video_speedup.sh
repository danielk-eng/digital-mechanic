#!/usr/bin/env bash
# Single-take demo video: speed up the waits (model answering) without cutting them, keep the rest real-time.
#   scripts/video_speedup.sh take.mkv out.mp4 "12-58:6" "75-90:3" ...
# Each segment START-END:FACTOR (seconds of the raw take). Real-time parts keep their sound; sped-up parts are silent
# (voice-over goes on top later or is spoken over real-time parts). Output: H.264 1080p max, 30 fps, AAC, faststart.
set -euo pipefail
IN=$1; OUT=$2; shift 2
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$IN")
HAS_A=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$IN" | head -1)
f=""; n=0; t=0
add() {  # start end factor
  local s=$1 e=$2 k=$3
  f+="[0:v]trim=$s:$e,setpts=(PTS-STARTPTS)/$k[v$n];"
  if [ "$k" = 1 ] && [ -n "$HAS_A" ]; then f+="[0:a]atrim=$s:$e,asetpts=PTS-STARTPTS[a$n];"
  else f+="anullsrc=r=48000:cl=stereo,atrim=0:$(python3 -c "print(($e-$s)/$k)")[a$n];"; fi
  n=$((n+1))
}
for seg in "$@"; do
  s=${seg%%-*}; rest=${seg#*-}; e=${rest%%:*}; k=${rest#*:}
  if python3 -c "import sys; sys.exit(0 if $s > $t else 1)"; then add "$t" "$s" 1; fi
  add "$s" "$e" "$k"; t=$e
done
if python3 -c "import sys; sys.exit(0 if $DUR - $t > 0.05 else 1)"; then add "$t" "$DUR" 1; fi
c=""; for i in $(seq 0 $((n-1))); do c+="[v$i][a$i]"; done
f+="${c}concat=n=$n:v=1:a=1[v][a];[v]scale='min(1920,iw)':-2,fps=30[vo]"
ffmpeg -hide_banner -loglevel error -y -i "$IN" -filter_complex "$f" -map "[vo]" -map "[a]" \
  -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart "$OUT"
echo "$OUT: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT") s (raw $DUR s)"
