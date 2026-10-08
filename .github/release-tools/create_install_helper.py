"""Generate a simple native Mac download/open helper only after final gates."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from ksi_local.atomic_files import atomic_write_bytes
from ksi_local.autonomous_release import verify_distribution


SHELL = r'''#!/bin/bash
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
KSI_ARCH=x86_64
if [ "$(/usr/sbin/sysctl -in hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
  KSI_ARCH=arm64
elif [ "$(/usr/bin/uname -m)" != x86_64 ]; then
  echo "Bu bilgisayarın işlemcisi desteklenmiyor."; exit 1
fi
__PLATFORM_CASES__
KSI_VERIFY_ONLY=0
if [ "${1:-}" = --verify-only ]; then
  KSI_VERIFY_ONLY=1
  KSI_FOLDER="${2:?Doğrulanacak kurulum klasörü gerekli}"
else
  KSI_PARENT="$HOME/Downloads"
  [ -d "$KSI_PARENT" ] && [ ! -L "$KSI_PARENT" ] || { echo "Normal İndirilenler klasörü bulunamadı."; exit 1; }
  KSI_FOLDER="$KSI_PARENT/KSI-Local-Studio-__VERSION__-$KSI_ARCH-Install"
fi
[ ! -L "$KSI_FOLDER" ] || { echo "Kurulum klasörü bağlantı olamaz."; exit 1; }
KSI_VOLUME_CHECK="$KSI_FOLDER"
if [ ! -e "$KSI_VOLUME_CHECK" ]; then KSI_VOLUME_CHECK="$KSI_PARENT"; fi
case "$KSI_VOLUME_CHECK" in /*) ;; *) echo "Mutlak bir kurulum klasörü gerekli."; exit 1 ;; esac
KSI_PATH_CHECK="$KSI_VOLUME_CHECK"
while [ "$KSI_PATH_CHECK" != / ]; do
  if [ -L "$KSI_PATH_CHECK" ] && [ "$KSI_PATH_CHECK" != /tmp ] && [ "$KSI_PATH_CHECK" != /var ]; then
    echo "Kurulum yolunda bağlantı bulundu; işlem durduruldu."; exit 1
  fi
  KSI_PATH_CHECK="${KSI_PATH_CHECK%/*}"
  if [ -z "$KSI_PATH_CHECK" ]; then KSI_PATH_CHECK=/; fi
done
# Same filesystem-device boundary as the application's internal_storage policy;
# APFS logical volumes do not consistently expose diskutil's Internal field.
KSI_BASE_DEVICE=$(/usr/bin/stat -f %d /System/Volumes/Data)
KSI_INSTALL_DEVICE=$(/usr/bin/stat -f %d "$KSI_VOLUME_CHECK")
[ "$KSI_INSTALL_DEVICE" = "$KSI_BASE_DEVICE" ] || { echo "Kurulum yalnızca bilgisayarın iç veri diskinde çalışır."; exit 1; }
if [ "$KSI_VERIFY_ONLY" = 0 ]; then /bin/mkdir -p "$KSI_FOLDER"; fi
[ -d "$KSI_FOLDER" ] || { echo "Kurulum klasörü bulunamadı."; exit 1; }
KSI_FREE_BYTES=$(/bin/df -Pk "$KSI_FOLDER" | /usr/bin/awk 'NR==2 {printf "%.0f", $4*1024}')
KSI_MISSING_BYTES=0
for ((KSI_INDEX=0; KSI_INDEX<${#KSI_NAMES[@]}; KSI_INDEX++)); do
  KSI_FILE="$KSI_FOLDER/${KSI_NAMES[$KSI_INDEX]}"
  [ ! -L "$KSI_FILE" ] || { echo "Bir kurulum parçası bağlantı; işlem durduruldu."; exit 1; }
  if [ -e "$KSI_FILE" ]; then
    [ -f "$KSI_FILE" ] || { echo "Bir kurulum parçası normal dosya değil."; exit 1; }
    KSI_ACTUAL_SIZE=$(/usr/bin/stat -f %z "$KSI_FILE")
    KSI_ACTUAL_SHA=$(/usr/bin/shasum -a 256 -- "$KSI_FILE" | /usr/bin/awk '{print $1}')
    [ "$KSI_ACTUAL_SIZE" = "${KSI_SIZES[$KSI_INDEX]}" ] && [ "$KSI_ACTUAL_SHA" = "${KSI_HASHES[$KSI_INDEX]}" ] || {
      echo "Mevcut kurulum parçası doğrulanmadı. Üzerine yazılmadı: ${KSI_NAMES[$KSI_INDEX]}"; exit 1;
    }
  else
    [ "$KSI_VERIFY_ONLY" = 0 ] || { echo "Eksik parça: ${KSI_NAMES[$KSI_INDEX]}"; exit 1; }
    KSI_MISSING_BYTES=$((KSI_MISSING_BYTES + KSI_SIZES[KSI_INDEX]))
  fi
done
if [ "$KSI_VERIFY_ONLY" = 1 ]; then
  echo "Tüm kurulum parçalarının boyut ve SHA-256 değerleri doğrulandı."; exit 0
fi
KSI_TEMP=''
ksi_cleanup_owned_partial() {
  if [ -n "$KSI_TEMP" ] && [ "${KSI_TEMP%/*}" = "$KSI_FOLDER" ] && \
      [[ "${KSI_TEMP##*/}" = .ksi-install.* ]] && [ -f "$KSI_TEMP" ] && [ ! -L "$KSI_TEMP" ]; then
    /bin/rm -- "$KSI_TEMP"
  fi
}
trap ksi_cleanup_owned_partial EXIT
[ "$KSI_FREE_BYTES" -ge "$((KSI_MISSING_BYTES + KSI_INSTALL_BYTES))" ] || {
  echo "Uygulama, çevrimdışı modeller ve kurulum parçaları için yeterli boş alan yok."; exit 1;
}
for ((KSI_INDEX=0; KSI_INDEX<${#KSI_NAMES[@]}; KSI_INDEX++)); do
  KSI_NAME="${KSI_NAMES[$KSI_INDEX]}"
  KSI_FILE="$KSI_FOLDER/$KSI_NAME"
  if [ -f "$KSI_FILE" ]; then continue; fi
  echo "İndiriliyor: $KSI_NAME"
  KSI_TEMP=$(/usr/bin/mktemp "$KSI_FOLDER/.ksi-install.XXXXXX")
  # No personal curl config, account token, cookie, netrc or proxy credentials.
  if ! /usr/bin/env -u CURL_HOME -u CURL_CA_BUNDLE -u SSL_CERT_FILE -u SSL_CERT_DIR \
      /usr/bin/curl -q --noproxy '*' --proto '=https' --proto-redir '=https' --tlsv1.2 \
      --fail --location --retry 3 --connect-timeout 30 --max-time 14400 \
      --output "$KSI_TEMP" "https://github.com/taliyigit2-prog/Ksi/releases/download/v__VERSION__/$KSI_NAME"; then
    echo "Bağlantı kesildi. Tamamlanan parçalar korunmuştur; bu dosyayı yeniden açabilirsiniz."; exit 1
  fi
  KSI_ACTUAL_SIZE=$(/usr/bin/stat -f %z "$KSI_TEMP")
  KSI_ACTUAL_SHA=$(/usr/bin/shasum -a 256 -- "$KSI_TEMP" | /usr/bin/awk '{print $1}')
  [ "$KSI_ACTUAL_SIZE" = "${KSI_SIZES[$KSI_INDEX]}" ] && [ "$KSI_ACTUAL_SHA" = "${KSI_HASHES[$KSI_INDEX]}" ] || {
    echo "İndirilen dosya doğrulanmadı; kuruluma devam edilmiyor."; exit 1;
  }
  # Publish without overwriting an existing user file, then unlink only our
  # exact mktemp-owned temporary name. No broad cleanup or recursive deletion.
  /bin/ln "$KSI_TEMP" "$KSI_FILE"
  /bin/rm -- "$KSI_TEMP"
  KSI_TEMP=''
done
echo "DMG doğrulanıyor; bu işlem biraz sürebilir."
/usr/bin/hdiutil verify "$KSI_FOLDER/$KSI_PRIMARY"
echo "Açılan pencerede KSI Local Studio uygulamasını Applications klasörüne sürükleyin."
echo "Apple noterlemesi yoktur. İlk açılışta Sistem Ayarları > Gizlilik ve Güvenlik bölümünden açılışa izin vermeniz gerekebilir."
/usr/bin/open "$KSI_FOLDER/$KSI_PRIMARY"
'''


def render(report, storage):
    if report.get('ready_to_publish') is not True or set(report.get('architectures',{})) != {'arm64'}:
        raise ValueError('The final Apple Silicon distribution gate must pass')
    version=report['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):
        raise ValueError('Final numeric version required')
    branches=['case "$KSI_ARCH" in']
    for architecture in ('arm64',):
        files=report['architectures'][architecture]['files']
        if not files or len(files)>100:
            raise ValueError('Missing native installer parts')
        names,hashes,sizes=[],[],[]
        for row in files:
            name=row['filename']
            stem='KSI-Local-Studio-'+version+'-'+architecture
            if not re.fullmatch(re.escape(stem)+r'(?:\.dmg|\.[0-9]{3}\.dmgpart)',name):
                raise ValueError('Unsafe installer filename')
            if not re.fullmatch(r'[0-9a-f]{64}',row['sha256']) or type(row['size']) is not int or not 0<row['size']<2*1024**3:
                raise ValueError('Unsafe installer checksum/size')
            names.append(name);hashes.append(row['sha256']);sizes.append(str(row['size']))
        primary=[name for name in names if name.endswith('.dmg')]
        if len(primary)!=1 or type(storage[architecture]) is not int or storage[architecture]<=0:
            raise ValueError('Primary media or actual storage budget missing')
        branches.extend([architecture+')',"KSI_NAMES=('"+"' '".join(names)+"')",
                         "KSI_HASHES=('"+"' '".join(hashes)+"')",'KSI_SIZES=('+ ' '.join(sizes)+')',
                         "KSI_PRIMARY='"+primary[0]+"'",'KSI_INSTALL_BYTES='+str(storage[architecture]),';;'])
    branches.extend(['*) echo "Bu sürüm yalnız Apple Silicon Mac içindir; Intel desteklenmiyor."; exit 1 ;;','esac'])
    return SHELL.replace('__PLATFORM_CASES__','\n'.join(branches)).replace('__VERSION__',version)


def create(platforms,source_commit,output):
    if output.exists() or output.is_symlink():
        raise FileExistsError('Installer helper destination must be new')
    if set(platforms) != {'arm64'}:
        raise ValueError('This installer is Apple Silicon only')
    paths=platforms['arm64']
    native=verify_distribution(Path(paths['transport']),Path(paths['application']),Path(paths['evidence']),source_commit)
    if native['architecture'] != 'arm64':
        raise ValueError('Apple Silicon native acceptance required')
    report=dict(ready_to_publish=True,version=native['version'],architectures={'arm64':native})
    from ksi_local.bundle_runtime import OfflinePayload
    storage={}
    for architecture,paths in platforms.items():
        app=Path(paths['application'])
        payload=OfflinePayload.load(app/'Contents/Resources',architecture=architecture)
        storage[architecture]=sum(path.stat().st_size for path in app.rglob('*') if path.is_file()) + sum(entry.size for entry in payload.files if entry.role=='model') + 2*1024**3
    content=render(report,storage)
    subprocess.run(['/bin/bash','-n'],input=content.encode(),check=True,timeout=30)
    atomic_write_bytes(output.absolute(),content.encode(),mode=0o755)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platforms',type=Path)
    parser.add_argument('source_commit')
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    create(json.loads(args.platforms.read_bytes()),args.source_commit,args.output)
