#!/bin/zsh

set -euo pipefail
umask 077

PROJECT_ROOT="${0:A:h:h}"
PORTABLE_PYTHON_ROOT="${KSI_PORTABLE_PYTHON_ROOT:-$PROJECT_ROOT/build/python-standalone/cpython-3.12.13-macos-aarch64-none}"
SUPPORT_ROOT="$HOME/Library/Application Support/KSI Local Studio"
RUNTIME_ROOT="$SUPPORT_ROOT/runtime"
APP_ROOT="$HOME/Desktop/KSI Local Studio/1 - Programı Aç.app"
BACKUP_ROOT="$SUPPORT_ROOT/backups"
STAMP="$(/bin/date +%Y%m%d-%H%M%S)-$$"
STAGED_RUNTIME="$SUPPORT_ROOT/.runtime-install-$STAMP"
STAGED_APP="$SUPPORT_ROOT/.KSI Local Studio-install-$STAMP.app"
RUNTIME_BACKUP=""
APP_BACKUP=""
RUNTIME_SWAPPED=false
APP_SWAPPED=false
LOCK_READY="$SUPPORT_ROOT/.install-lock-ready-$STAMP"
LOCK_RELEASE="$SUPPORT_ROOT/.install-lock-release-$STAMP"
LOCK_PID=""

release_install_lock() {
  if [[ -n "$LOCK_PID" ]]; then
    : >"$LOCK_RELEASE"
    wait "$LOCK_PID" 2>/dev/null || true
    LOCK_PID=""
  fi
  /bin/rm -f "$LOCK_READY" "$LOCK_RELEASE"
}

acquire_install_lock() {
  PYTHONDONTWRITEBYTECODE=1 "$STAGED_RUNTIME/venv/bin/python" - "$SUPPORT_ROOT/app.lock" \
    "$LOCK_READY" "$LOCK_RELEASE" <<'PY' &
import fcntl
import sys
import time
from pathlib import Path

lock_path, ready_path, release_path = map(Path, sys.argv[1:])
with lock_path.open("a+", encoding="utf-8") as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(73)
    ready_path.write_text("ready\n", encoding="utf-8")
    while not release_path.exists():
        time.sleep(0.05)
PY
  LOCK_PID=$!
  local attempt
  for attempt in {1..100}; do
    [[ -e "$LOCK_READY" ]] && return 0
    if ! /bin/kill -0 "$LOCK_PID" 2>/dev/null; then
      wait "$LOCK_PID" 2>/dev/null || true
      LOCK_PID=""
      return 1
    fi
    /bin/sleep 0.05
  done
  release_install_lock
  return 1
}

rollback() {
  local rollback_status=$?
  trap - EXIT HUP INT TERM
  if (( rollback_status == 0 )); then
    return
  fi
  if [[ "$APP_SWAPPED" == true ]]; then
    [[ -e "$APP_ROOT" ]] && /bin/mv "$APP_ROOT" "$STAGED_APP"
    [[ -n "$APP_BACKUP" && -e "$APP_BACKUP" ]] && /bin/mv "$APP_BACKUP" "$APP_ROOT"
  fi
  if [[ "$RUNTIME_SWAPPED" == true ]]; then
    [[ -e "$RUNTIME_ROOT" ]] && /bin/mv "$RUNTIME_ROOT" "$STAGED_RUNTIME"
    [[ -n "$RUNTIME_BACKUP" && -e "$RUNTIME_BACKUP" ]] && /bin/mv "$RUNTIME_BACKUP" "$RUNTIME_ROOT"
  fi
  [[ -e "$STAGED_APP" ]] && /bin/rm -rf "$STAGED_APP"
  [[ -e "$STAGED_RUNTIME" ]] && /bin/rm -rf "$STAGED_RUNTIME"
  release_install_lock
  exit "$rollback_status"
}
trap rollback EXIT HUP INT TERM

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  print -u2 "KSI Local Studio Python ortamı bulunamadı: $PROJECT_ROOT/.venv"
  exit 1
fi
if [[ ! -x "$PROJECT_ROOT/.venv-chatterbox/bin/python" ]]; then
  print -u2 "Chatterbox ARM64 ortamı bulunamadı: $PROJECT_ROOT/.venv-chatterbox"
  exit 1
fi
if [[ -L "$PORTABLE_PYTHON_ROOT" || ! -x "$PORTABLE_PYTHON_ROOT/bin/python3.12" ]]; then
  print -u2 "Taşınabilir ARM64 CPython 3.12.13 bulunamadı: $PORTABLE_PYTHON_ROOT"
  exit 1
fi

"$PROJECT_ROOT/scripts/build_ocr_helper.sh" >/dev/null
"$PROJECT_ROOT/scripts/build_app_icon.sh" >/dev/null

/bin/mkdir -p "$SUPPORT_ROOT" "$BACKUP_ROOT" "$STAGED_RUNTIME/venv" \
  "$STAGED_RUNTIME/chatterbox-venv" "$STAGED_RUNTIME/python" "$STAGED_RUNTIME/bin" \
  "$STAGED_RUNTIME/src" "$STAGED_RUNTIME/config" "$STAGED_RUNTIME/assets"
# APFS clone-copy keeps the 3.1 GiB environment independent without initially
# consuming another 3.1 GiB of physical internal-disk space.
/bin/cp -cR "$PROJECT_ROOT/.venv/." "$STAGED_RUNTIME/venv"
/bin/cp -cR "$PROJECT_ROOT/.venv-chatterbox/." "$STAGED_RUNTIME/chatterbox-venv"
/bin/cp -cR "$PORTABLE_PYTHON_ROOT/." "$STAGED_RUNTIME/python"
/bin/cp -cR "$PROJECT_ROOT/src/ksi_local" "$STAGED_RUNTIME/src/ksi_local"
/usr/bin/install -m 644 "$PROJECT_ROOT/assets/ksi-logo.png" \
  "$STAGED_RUNTIME/assets/ksi-logo.png"
/usr/bin/install -m 600 "$PROJECT_ROOT/config/tool-manifest.json" \
  "$STAGED_RUNTIME/config/tool-manifest.json"
/usr/bin/install -m 600 "$PROJECT_ROOT/config/glossary.json" \
  "$STAGED_RUNTIME/config/glossary.json"
/usr/bin/install -m 600 "$PROJECT_ROOT/config/voice-profile.json" \
  "$STAGED_RUNTIME/config/voice-profile.json"
/usr/bin/install -m 644 "$PROJECT_ROOT/config/public-catalog.json" \
  "$STAGED_RUNTIME/config/public-catalog.json"
/usr/bin/install -m 600 "$PROJECT_ROOT/.phase1/workspace-id.json" \
  "$STAGED_RUNTIME/workspace-id.json"
/usr/bin/install -m 700 "$PROJECT_ROOT/build/KSIOCR" \
  "$STAGED_RUNTIME/bin/KSIOCR"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT/src" \
  "$PROJECT_ROOT/.venv/bin/python" -m ksi_local.runtime_portability \
  prepare "$STAGED_RUNTIME" >/dev/null
if [[ "$(PYTHONDONTWRITEBYTECODE=1 "$STAGED_RUNTIME/python/bin/python3.12" -c 'import platform, sys; print(platform.machine() + " " + platform.python_version())')" != "arm64 3.12.13" ]]; then
  print -u2 "Taşınabilir Python sürüm veya mimari doğrulaması başarısız."
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT/src" \
  "$PROJECT_ROOT/.venv/bin/python" -m ksi_local.runtime_portability "$STAGED_RUNTIME" >/dev/null; then
  print -u2 "Sahnelenen runtime temiz Mac için taşınabilir değil."
  exit 1
fi

/bin/mkdir -p "$STAGED_APP/Contents/MacOS" "$STAGED_APP/Contents/Resources"
/usr/bin/install -m 755 "$PROJECT_ROOT/packaging/KSI-Local-Studio-launcher" \
  "$STAGED_APP/Contents/MacOS/KSI-Local-Studio"
/usr/bin/install -m 600 "$PROJECT_ROOT/packaging/Info.plist" \
  "$STAGED_APP/Contents/Info.plist"
/usr/bin/install -m 644 "$PROJECT_ROOT/packaging/KSI-Local-Studio.icns" \
  "$STAGED_APP/Contents/Resources/KSI-Local-Studio.icns"
/usr/bin/codesign --force --deep --sign - "$STAGED_APP"
/usr/bin/codesign --verify --deep --strict "$STAGED_APP"
if [[ "$(PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGED_RUNTIME/src" "$STAGED_RUNTIME/venv/bin/python" -c 'import platform, ksi_local; print(platform.machine() + " " + ksi_local.__version__)')" != "arm64 2.0.0.dev0" ]]; then
  print -u2 "KSI Local Studio sahnelenen runtime doğrulaması başarısız."
  exit 1
fi
if ! acquire_install_lock; then
  print -u2 "KSI Local Studio açık. Kurulum için uygulamayı kapatıp yeniden deneyin."
  exit 1
fi

RUNTIME_SWAPPED=true
if [[ -e "$RUNTIME_ROOT" ]]; then
  RUNTIME_BACKUP="$BACKUP_ROOT/runtime-$STAMP"
  /bin/mv "$RUNTIME_ROOT" "$RUNTIME_BACKUP"
fi
/bin/mv "$STAGED_RUNTIME" "$RUNTIME_ROOT"

APP_SWAPPED=true
if [[ -e "$APP_ROOT" ]]; then
  APP_BACKUP="$BACKUP_ROOT/KSI Local Studio-$STAMP.app"
  /bin/mv "$APP_ROOT" "$APP_BACKUP"
fi
/bin/mv "$STAGED_APP" "$APP_ROOT"

/usr/bin/codesign --verify --deep --strict "$APP_ROOT"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RUNTIME_ROOT/src" "$RUNTIME_ROOT/venv/bin/python" -c \
  'import ksi_local; assert ksi_local.__version__ == "2.0.0.dev0"'

release_install_lock
trap - EXIT HUP INT TERM

print "KSI Local Studio kuruldu: $APP_ROOT"
