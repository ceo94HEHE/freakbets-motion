#!/usr/bin/env bash
# Blend each frame's 4 subframes (motion blur), then mux the audio.
#   ./build.sh               → out/ui-morph.mp4
#   ./build.sh rtp-channel   → rtp-channel/out/rtp-channel.mp4
#   ./build.sh rtp-channel out-story → rtp-channel/out-story/rtp-channel-story.mp4
set -euo pipefail
cd "$(dirname "$0")/${1:-.}"
name=$( [ -n "${1:-}" ] && basename "$1" || echo ui-morph )
out=${2:-out}
if [ "$out" != out ]; then name="$name-${out#out-}"; fi
frames=$(python3 -c "import json;print(json.load(open('$out/timeline.json'))['frames'])")
ffmpeg -y -v error -nostats -framerate 240 -i "$out/sub/%05d.png" -i "$out/audio.wav" \
  -filter_complex "[0:v]tmix=frames=4:weights='1 1 1 1',select='eq(mod(n\,4)\,3)',setpts=N/(60*TB),scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p[v]" \
  -map "[v]" -map 1:a -frames:v "$frames" -r 60 \
  -c:v libx264 -preset slow -crf 14 -profile:v high -tune animation \
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv \
  -c:a aac -b:a 320k -ar 48000 -movflags +faststart "$out/$name.mp4"
echo "wrote $out/$name.mp4 ($frames frames)"
