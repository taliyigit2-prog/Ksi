#!/bin/zsh

set -euo pipefail
umask 077

if [[ -z "${KSI_APPLE_SIGNING_IDENTITY:-}" || -z "${KSI_NOTARY_PROFILE:-}" ]]; then
  print -u2 "KSI_APPLE_SIGNING_IDENTITY ve KSI_NOTARY_PROFILE anahtar zinciri ayarları gereklidir."
  exit 2
fi
if (( $# != 2 )); then
  print -u2 "Kullanım: notarize_release.sh <KSI Local Studio.app> <paket.dmg>"
  exit 2
fi

APP_PATH="$1"
DMG_PATH="$2"
if [[ ! -d "$APP_PATH" || ! -f "$DMG_PATH" || -L "$APP_PATH" || -L "$DMG_PATH" ]]; then
  print -u2 "İmzalanacak uygulama veya DMG normal dosya değil."
  exit 2
fi

/usr/bin/codesign --force --deep --options runtime --timestamp \
  --sign "$KSI_APPLE_SIGNING_IDENTITY" "$APP_PATH"
/usr/bin/codesign --verify --deep --strict "$APP_PATH"
/usr/bin/codesign --force --timestamp --sign "$KSI_APPLE_SIGNING_IDENTITY" "$DMG_PATH"
/usr/bin/xcrun notarytool submit "$DMG_PATH" --keychain-profile "$KSI_NOTARY_PROFILE" --wait
/usr/bin/xcrun stapler staple "$DMG_PATH"
/usr/bin/xcrun stapler validate "$DMG_PATH"
/usr/sbin/spctl --assess --type open --context context:primary-signature --verbose=4 "$DMG_PATH"
