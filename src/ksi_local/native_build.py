"""Build-time native components from exact public commits and verified archives."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, host_architecture


def fetch_git_source(url: str, *, tag: str, commit: str, destination: Path) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", parsed.path):
        raise ValueError("Native source must be an explicit public GitHub repository.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("Native source requires an exact commit and safe release tag.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Native source destination must be new and absolute.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ksi-native-source-", dir=destination.parent) as temporary:
        private = Path(temporary)
        checkout = private / "checkout"
        environment = {"HOME": str(private), "PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}
        subprocess.run(["git", "clone", "--depth", "1", "--branch", tag, "--no-checkout", url, str(checkout)], env=environment, check=True, timeout=600)
        actual = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, env=environment, check=True, capture_output=True, text=True).stdout.strip()
        if actual != commit:
            raise ValueError("Native source tag no longer matches the pinned commit.")
        archive = private / "source.tar"
        subprocess.run(["git", "archive", "--format=tar", "-o", str(archive), commit], cwd=checkout, env=environment, check=True, timeout=120)
        with tarfile.open(archive, "r:") as stream:
            members = stream.getmembers()
            if len(members) > 50000 or sum(member.size for member in members) > 2 * 1024**3:
                raise ValueError("Native source archive exceeds safe bounds.")
            for member in members:
                if not (member.isfile() or member.isdir()) or any(part in {"", ".", ".."} for part in member.name.rstrip("/").split("/")) or member.name.startswith("/") or "\\" in member.name:
                    raise ValueError("Native source contains unsafe archive members.")
            destination.mkdir(mode=0o700)
            stream.extractall(destination, members=members, filter="data")
        files = [{"path": path.relative_to(destination).as_posix(), "sha256": digest_file(path)} for path in sorted(destination.rglob("*")) if path.is_file()]
        record = {"schema_version": 1, "url": url, "tag": tag, "commit": commit, "source_archive_sha256": digest_file(archive), "files": files}
        atomic_write_json(destination / "ksi-source-provenance.json", record)
        return record


def _stage_native_source(source: Path, destination: Path, commit: str) -> Path:
    if source.is_symlink() or not source.is_dir() or not destination.is_absolute() or destination.exists() or destination.is_symlink() or destination.is_relative_to(source.absolute()):
        raise ValueError("Native build requires clean source and a separate new build directory.")
    import json
    provenance = json.loads((source / "ksi-source-provenance.json").read_text(encoding="utf-8"))
    if provenance.get("commit") != commit:
        raise ValueError("Native source does not match the pinned commit.")
    from ksi_local.bundle_runtime import safe_member
    files = provenance.get("files")
    if not isinstance(files, list) or not files or len(files) > 50000:
        raise ValueError("Native source inventory is missing.")
    expected = {row["path"] for row in files}
    actual = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file() and path != source / "ksi-source-provenance.json"}
    if actual != expected:
        raise ValueError("Native source inventory changed after fetching.")
    for row in files:
        if digest_file(safe_member(source, row["path"])) != row["sha256"]:
            raise ValueError("Native source content changed after fetching.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    staged_source = destination / "upstream-source"
    staged_source.mkdir()
    for row in files:
        target = staged_source / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / row["path"], target)
    return staged_source


def build_whisper_cpu(source: Path, *, cmake: Path, destination: Path, commit: str) -> Path:
    if not cmake.is_file():
        raise ValueError("The clean CMake executable is missing.")
    staged_source = _stage_native_source(source, destination, commit)
    build_directory = destination / "build"
    with tempfile.TemporaryDirectory(prefix=".ksi-compiler-home-", dir=destination.parent) as temporary:
        environment = {"HOME": temporary, "TMPDIR": temporary, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "MACOSX_DEPLOYMENT_TARGET": "14.0", "GIT_CEILING_DIRECTORIES": str(destination.absolute())}
        prefix_flags = f"-ffile-prefix-map={staged_source.absolute()}=upstream/whisper.cpp -fdebug-prefix-map={staged_source.absolute()}=upstream/whisper.cpp"
        subprocess.run([str(cmake), "-S", str(staged_source.absolute()), "-B", str(build_directory.absolute()),
                        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0",
                        "-DCMAKE_OSX_ARCHITECTURES=" + host_architecture(),
                        "-DCMAKE_C_FLAGS=" + prefix_flags, "-DCMAKE_CXX_FLAGS=" + prefix_flags,
                        "-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local",
                        "-DBUILD_SHARED_LIBS=OFF", "-DGGML_NATIVE=OFF", "-DGGML_METAL=OFF",
                        "-DGGML_BLAS=OFF", "-DGGML_OPENMP=OFF", "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_BUILD_EXAMPLES=ON"], env=environment, check=True, timeout=300)
        subprocess.run([str(cmake), "--build", str(build_directory.absolute()), "--target", "whisper-cli", "--parallel", "4"], env=environment, check=True, timeout=1800)
    binary = build_directory / "bin/whisper-cli"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("The native whisper-cli build produced no executable.")
    return binary


def build_raster_media_engine(engine: str, source: Path, libraries: Path,
                              destination: Path, *, commit: str) -> dict:
    if engine not in {"ffmpeg", "imagemagick"} or libraries.is_symlink() or not (libraries / "bin/pkg-config").is_file():
        raise ValueError("Selected engine or pinned native library prefix is invalid.")
    staged = _stage_native_source(source, destination, commit)
    build = destination / "build"
    build.mkdir()
    prefix = libraries.absolute()
    public_prefix = "/KSI-native"
    maps = f"-ffile-prefix-map={staged}=upstream/{engine} -fdebug-prefix-map={staged}=upstream/{engine} -ffile-prefix-map={prefix}=upstream/native-libraries"
    with tempfile.TemporaryDirectory(prefix=".ksi-engine-home-", dir=destination.parent) as temporary:
        environment = {"HOME": temporary, "TMPDIR": temporary, "PATH": str(prefix / "bin") + ":/usr/bin:/bin", "MACOSX_DEPLOYMENT_TARGET": "14.0", "LC_ALL": "C", "GIT_CEILING_DIRECTORIES": str(destination), "PKG_CONFIG_PATH": str(prefix / "lib/pkgconfig"), "PKG_CONFIG_LIBDIR": str(prefix / "lib/pkgconfig"), "CFLAGS": "-O2 -mmacosx-version-min=14.0 " + maps, "CXXFLAGS": "-O2 -mmacosx-version-min=14.0 " + maps, "CPPFLAGS": "-I" + str(prefix / "include"), "LDFLAGS": "-L" + str(prefix / "lib") + " -mmacosx-version-min=14.0 -Wl,-headerpad_max_install_names"}
        if engine == "ffmpeg":
            options = ["--prefix=" + public_prefix, "--cc=clang", "--cxx=clang++", "--disable-autodetect", "--disable-debug", "--disable-doc", "--disable-ffplay", "--disable-shared", "--enable-static", "--disable-x86asm", "--enable-gpl", "--enable-libx264", "--enable-libvpx", "--enable-libopus", "--enable-libass", "--enable-libmp3lame", "--enable-videotoolbox", "--enable-audiotoolbox", "--enable-securetransport"]
            targets = ["ffmpeg", "ffprobe"]
        else:
            options = ["--prefix=" + public_prefix, "--disable-shared", "--enable-static", "--disable-openmp", "--with-modules=no", "--without-magick-plus-plus", "--without-perl", "--without-x", "--without-gslib", "--without-gs-font-dir", "--without-rsvg", "--without-pango", "--without-xml", "--without-fftw", "--without-gvc", "--without-jxl", "--without-openexr", "--without-raw", "--without-djvu", "--without-lqr", "--without-raqm", "--without-fontconfig", "--without-freetype", "--with-heic=yes", "--with-webp=yes", "--with-jpeg=yes", "--with-png=yes", "--with-tiff=yes", "--with-zlib=yes"]
            targets = []
        log = destination / "native-build.log"
        print(f"Configuring pinned {engine}; detailed diagnostics stay in the private build log.", flush=True)
        _run_build_command([str(staged / "configure"), *options], cwd=build, environment=environment, log=log, timeout=600)
        if engine == "imagemagick":
            from xml.etree import ElementTree
            config = ElementTree.parse(build / "config/configure.xml")
            delegates = next((row.get("value", "").split() for row in config.iter("configure") if row.get("name") == "DELEGATES"), [])
            if not {"heic", "jpeg", "png", "tiff", "webp"} <= set(delegates):
                raise RuntimeError("Raster build is missing required delegates; no incomplete engine is staged.")
        print(f"Compiling pinned {engine} with four workers.", flush=True)
        _run_build_command(["/usr/bin/make", "-j4", *targets], cwd=build, environment=environment, log=log, timeout=3600)
    binaries = [build / "ffmpeg", build / "ffprobe"] if engine == "ffmpeg" else [build / "utilities/magick"]
    if any(not path.is_file() or not os.access(path, os.X_OK) for path in binaries):
        raise RuntimeError("Selected native engine did not produce its expected executables.")
    result = {"schema_version": 1, "engine": engine, "architecture": host_architecture(), "source_commit": commit, "configure_options": options, "acceptance_tested": False, "binaries": [{"filename": path.name, "sha256": digest_file(path), "size": path.stat().st_size} for path in binaries]}
    atomic_write_json(destination / "native-build-provenance.json", result)
    return result


def _run_build_command(command, *, cwd, environment, log, timeout):
    with log.open("ab") as output:
        result = subprocess.run(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT, check=False, timeout=timeout)
    if result.returncode:
        with log.open("rb") as source:
            source.seek(max(0, log.stat().st_size - 4096))
            details = source.read().decode("utf-8", errors="replace")
        raise RuntimeError("Native build failed; private diagnostic tail:\n" + details)


def extract_oxipng(archive: Path, *, sha256: str, destination: Path) -> Path:
    if archive.is_symlink() or not archive.is_file() or digest_file(archive) != sha256 or destination.exists() or destination.is_symlink():
        raise ValueError("Oxipng extraction requires a verified archive and new destination.")
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        if not 1 <= len(members) <= 100 or sum(member.size for member in members) > 32 * 1024**2:
            raise ValueError("Oxipng archive exceeds safe bounds.")
        directories = set()
        for member in members:
            parts = member.name.split("/")
            if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts) or not member.isfile() or "\\" in member.name:
                raise ValueError("Oxipng archive layout is invalid.")
            directories.add(parts[0])
        if len(directories) != 1:
            raise ValueError("Oxipng archive has multiple roots.")
        destination.mkdir(parents=True, mode=0o700)
        for member in members:
            candidate = destination / member.name.split("/")[1]
            with stream.extractfile(member) as incoming, candidate.open("xb") as output:
                shutil.copyfileobj(incoming, output)
            candidate.chmod(0o755 if candidate.name == "oxipng" else 0o644)
    if not (destination / "LICENSE").is_file() or not (destination / "oxipng").is_file():
        raise ValueError("Oxipng archive lacks the executable or license.")
    return destination / "oxipng"
