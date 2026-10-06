#!/bin/zsh

set -euo pipefail
umask 077

PROJECT_ROOT="${0:A:h:h}"
BUILD_ROOT="$PROJECT_ROOT/build"
MODULE_CACHE="$BUILD_ROOT/swift-module-cache"
OUTPUT="$BUILD_ROOT/KSIOCR"

/bin/mkdir -p "$BUILD_ROOT" "$MODULE_CACHE"
export CLANG_MODULE_CACHE_PATH="$MODULE_CACHE"
export SWIFT_MODULECACHE_PATH="$MODULE_CACHE"

/usr/bin/xcrun swiftc \
  -O \
  -framework AppKit \
  -framework Foundation \
  -framework PDFKit \
  -framework Vision \
  "$PROJECT_ROOT/native/KSIOCR.swift" \
  -o "$OUTPUT"
/bin/chmod 700 "$OUTPUT"
/usr/bin/codesign --force --sign - "$OUTPUT"
/usr/bin/codesign --verify --strict "$OUTPUT"

"$OUTPUT" capabilities
