#!/bin/zsh

set -euo pipefail
umask 022

PROJECT_ROOT="${0:A:h:h}"
SOURCE="$PROJECT_ROOT/assets/ksi-logo.png"
ICONSET="$PROJECT_ROOT/build/KSI-Local-Studio.iconset"
OUTPUT="$PROJECT_ROOT/packaging/KSI-Local-Studio.icns"

if [[ ! -f "$SOURCE" ]]; then
  print -u2 "KSI Local Studio logo kaynağı bulunamadı: $SOURCE"
  exit 1
fi

/bin/rm -rf "$ICONSET"
/bin/mkdir -p "$ICONSET"

for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" "$SOURCE" \
    --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  /usr/bin/sips -z "$double" "$double" "$SOURCE" \
    --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done

"$PROJECT_ROOT/.venv/bin/python" -c \
  'from PIL import Image; import sys; Image.open(sys.argv[1]).convert("RGBA").save(sys.argv[2], format="ICNS")' \
  "$SOURCE" "$OUTPUT"
print "KSI Local Studio simgesi hazır: $OUTPUT"
