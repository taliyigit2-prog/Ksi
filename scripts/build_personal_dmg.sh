#!/bin/zsh

set -euo pipefail
umask 077

PROJECT_ROOT="${0:A:h:h}"
VERSION="2.0.0.dev0"
SOURCE_APP="$HOME/Desktop/KSI Local Studio/1 - Programı Aç.app"
SOURCE_RUNTIME="$HOME/Library/Application Support/KSI Local Studio/runtime"
DIST_ROOT="$PROJECT_ROOT/dist"
DMG_PATH="$DIST_ROOT/KSI Local Studio-$VERSION-Kisisel.dmg"
STAGE_ROOT="$(/usr/bin/mktemp -d /private/tmp/ksi_local-dmg.XXXXXX)"

cleanup() {
  [[ -d "$STAGE_ROOT" && "$STAGE_ROOT" == /private/tmp/ksi_local-dmg.* ]] && /bin/rm -rf "$STAGE_ROOT"
}
trap cleanup EXIT HUP INT TERM

if [[ ! -d "$SOURCE_APP" || ! -x "$SOURCE_RUNTIME/venv/bin/python" ]]; then
  print -u2 "Önce scripts/install_macos_app.sh ile güncel KSI Local Studio paketini kurun."
  exit 1
fi
/usr/bin/codesign --verify --deep --strict "$SOURCE_APP"
if [[ "$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SOURCE_RUNTIME/src" "$SOURCE_RUNTIME/venv/bin/python" -c 'import ksi_local; print(ksi_local.__version__)')" != "$VERSION" ]]; then
  print -u2 "Kurulu KSI Local Studio runtime sürümü $VERSION değil."
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT/src" \
  "$PROJECT_ROOT/.venv/bin/python" -m ksi_local.runtime_portability "$SOURCE_RUNTIME"; then
  print -u2 "Kurulu runtime temiz Mac için taşınabilir değil; DMG çevrimdışı eksiksiz olarak üretilemez."
  exit 1
fi

/bin/mkdir -p "$STAGE_ROOT/.payload" "$STAGE_ROOT/KSI Local Studio Kur.app/Contents/MacOS" \
  "$STAGE_ROOT/KSI Local Studio Kur.app/Contents/Resources" "$DIST_ROOT"
/usr/bin/ditto "$SOURCE_APP" "$STAGE_ROOT/.payload/KSI Local Studio.app"
/usr/bin/ditto "$SOURCE_RUNTIME" "$STAGE_ROOT/.payload/runtime"
/usr/bin/install -m 755 "$PROJECT_ROOT/packaging/KSI-Local-Studio-installer" \
  "$STAGE_ROOT/KSI Local Studio Kur.app/Contents/MacOS/KSI-Local-Studio Kur"
/usr/bin/install -m 600 "$PROJECT_ROOT/packaging/KSI-Local-Studio-Installer-Info.plist" \
  "$STAGE_ROOT/KSI Local Studio Kur.app/Contents/Info.plist"
/usr/bin/install -m 644 "$PROJECT_ROOT/packaging/KSI-Local-Studio.icns" \
  "$STAGE_ROOT/KSI Local Studio Kur.app/Contents/Resources/KSI-Local-Studio.icns"
/usr/bin/codesign --force --deep --sign - "$STAGE_ROOT/KSI Local Studio Kur.app"
/usr/bin/codesign --verify --deep --strict "$STAGE_ROOT/KSI Local Studio Kur.app"

# The external DMG checksum detects transfer corruption. This inner manifest also
# verifies every offline payload file before an existing installation is touched.
# One Python process avoids spawning tens of thousands of shasum processes.
PYTHONDONTWRITEBYTECODE=1 "$SOURCE_RUNTIME/venv/bin/python" - "$STAGE_ROOT" <<'PY'
import hashlib
import sys
from pathlib import Path

stage = Path(sys.argv[1])
lines = []
for path in sorted((stage / ".payload").rglob("*")):
    relative = path.relative_to(stage).as_posix()
    if "\n" in relative or "\r" in relative:
        raise SystemExit("Payload yolunda satır sonuna izin verilmez.")
    if path.is_symlink():
        digest = hashlib.sha256(path.readlink().as_posix().encode("utf-8")).hexdigest()
        lines.append(f"L {digest}  {relative}")
        continue
    if not path.is_file():
        continue
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    lines.append(f"F {digest.hexdigest()}  {relative}")
(stage / "payload.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
/bin/chmod 644 "$STAGE_ROOT/payload.sha256"

{
  print "KSI Local Studio $VERSION — kişisel yerel kurulum"
  print ""
  print "1. KSI Local Studio Kur simgesine çift tıklayın."
  print "2. Kurulum tamamlanınca KSI Local Studio Masaüstüne yerleşir ve açılır."
  print "3. Modeller harici SSD üzerinde kalır; internet ve abonelik gerekmez."
  print ""
  print "Bu paket Apple Silicon (ARM64) ve macOS 14+ içindir."
  print "Ad-hoc imzalı kişisel paket olduğu için başka bir Mac'e indirildiğinde"
  print "ilk açılışta sağ tık > Aç gerekebilir."
} >"$STAGE_ROOT/Kurulum Bilgisi.txt"

TEMP_DMG="$DIST_ROOT/.KSI Local Studio-$VERSION-$$.dmg"
/usr/bin/hdiutil create -quiet -volname "KSI Local Studio $VERSION" -srcfolder "$STAGE_ROOT" \
  -format UDZO -imagekey zlib-level=6 "$TEMP_DMG"
/usr/bin/hdiutil verify "$TEMP_DMG" >/dev/null
/usr/bin/codesign --force --sign - "$TEMP_DMG"
/usr/bin/codesign --verify --verbose=2 "$TEMP_DMG"
/bin/mv -f "$TEMP_DMG" "$DMG_PATH"
DMG_DIGEST="$(/usr/bin/shasum -a 256 "$DMG_PATH" | /usr/bin/awk '{print $1}')"
SOURCE_DIGEST="$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT/src" "$SOURCE_RUNTIME/venv/bin/python" -c \
  'from ksi_local.final_release import release_source_sha256; import sys; print(release_source_sha256(sys.argv[1]))' \
  "$PROJECT_ROOT")"
print "$DMG_DIGEST  ${DMG_PATH:t}" >"$DMG_PATH.sha256"

cat >"$DMG_PATH.release.json" <<EOF
{
  "schema_version": 1,
  "product": "KSI Local Studio",
  "version": "$VERSION",
  "package_filename": "${DMG_PATH:t}",
  "size_bytes": $(/usr/bin/stat -f %z "$DMG_PATH"),
  "sha256": "$DMG_DIGEST",
  "source_sha256": "$SOURCE_DIGEST",
  "minimum_database_schema": 1,
  "maximum_database_schema": 5,
  "architecture": "arm64",
  "offline_complete": true,
  "models_included": false,
  "notarized": false,
  "signing": "ad-hoc"
}
EOF
/bin/chmod 644 "$DMG_PATH.release.json"

print "Kişisel KSI Local Studio DMG hazır: $DMG_PATH"
