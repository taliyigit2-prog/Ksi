"""Separate evidence gate for approved, explicitly ad-hoc offline distribution.

Historical human/notarized gates are not weakened or supplied invented scores.
Missing native acceptance, privacy or license evidence keeps this gate closed.
"""

import json
import plistlib
import re
import subprocess
from pathlib import Path

from ksi_local.bundle_runtime import OfflinePayload, digest_file, safe_member
from ksi_local.distribution_integrity import verify_embedded_application


REQUIRED_CASES = frozenset({
    "unit-suite", "gui-routes", "gui-controls", "offline-install", "media",
    "image", "ocr", "translation", "asr", "tts", "summary", "lifecycle",
    "privacy", "licenses",
})
REQUIRED_CHECKS = {
    "unit-suite": {"suite-success", "test-count-positive"},
    "gui-routes": {"routes-visible", "navigation-preserves-state", "window-closes"},
    "gui-controls": {"forms-validation", "buttons-dispatch", "cancel-responsive", "preferences-persist"},
    "offline-install": {"dmg-verified", "installation-copy-matches", "no-external-disk", "no-internet", "no-homebrew", "launch-success", "first-model-ready"},
    "media": {"convert", "lossless-cut", "compress", "subtitles", "cancel", "output-integrity"},
    "image": {"convert", "optimize-pixels", "background-removal", "metadata-private"},
    "ocr": {"recognized-reference", "no-network"},
    "translation": {"gemma-reference", "argos-reference", "numbers-and-glossary", "no-network"},
    "asr": {"reference-words", "srt-timestamps", "no-network"},
    "tts": {"audible-waveform", "speech-reference", "duration-bounds", "no-network"},
    "summary": {"source-evidence", "key-facts", "no-network"},
    "lifecycle": {"single-heavy-model", "cancel-releases", "owned-daemon-only", "crash-recovery"},
    "privacy": {"source-index", "git-history", "published-artifacts", "app-content", "dmg-content"},
    "licenses": {"binary-inventory", "original-notices", "corresponding-sources", "unresolved-zero"},
}


def _document(path, limit=16 * 1024**2):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Acceptance evidence is missing, linked or oversized")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Acceptance evidence must be an object")
    return value


def verify_acceptance(app: Path, evidence_root: Path, source_commit: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Release requires an immutable full source commit")
    if app.is_symlink() or not app.is_dir() or app.name != "KSI Local Studio.app":
        raise ValueError("Release application root is invalid")
    info_path = app / "Contents/Info.plist"
    if info_path.is_symlink() or info_path.stat().st_size > 65536:
        raise ValueError("Release application metadata is unsafe")
    info = plistlib.loads(info_path.read_bytes())
    architecture = info.get("KSIArchitecture")
    if architecture not in {"arm64", "x86_64"} or info.get("KSISourceCommit") != source_commit:
        raise ValueError("Release application does not match its architecture/source")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                   check=True, capture_output=True, timeout=900)
    signature = subprocess.run(["/usr/bin/codesign", "-dv", str(app)],
                               check=True, capture_output=True, text=True, timeout=30)
    if "Signature=adhoc" not in signature.stderr:
        raise ValueError("This approved gate is specifically for ad-hoc builds")
    resources = app / "Contents/Resources"
    payload = OfflinePayload.load(resources, architecture=architecture)
    for entry in payload.files:
        payload.verify(entry)
    manifest_sha256 = digest_file(resources / "offline-manifest.json")
    acceptance = _document(safe_member(evidence_root, "acceptance.json"))
    if (acceptance.get("schema_version") != 1 or acceptance.get("source_commit") != source_commit
            or acceptance.get("architecture") != architecture
            or acceptance.get("offline_manifest_sha256") != manifest_sha256):
        raise ValueError("Acceptance is not bound to this exact application")
    cases = acceptance.get("cases")
    if not isinstance(cases, list) or len(cases) != len(REQUIRED_CASES):
        raise ValueError("Every required automatic acceptance case must be recorded")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or case.get("name") not in REQUIRED_CASES or case["name"] in seen:
            raise ValueError("Acceptance case is unexpected or duplicated")
        if type(case.get("exit_code")) is not int or case["exit_code"] != 0:
            raise ValueError("Automatic acceptance process did not succeed")
        result = safe_member(evidence_root, case.get("result_file", ""))
        measured = _document(result)
        if not re.fullmatch(r"[0-9a-f]{64}", str(case.get("result_sha256", ""))) or digest_file(result) != case["result_sha256"]:
            raise ValueError("Acceptance result file changed")
        checks = measured.get("checks")
        if (measured.get("case") != case["name"] or measured.get("source_commit") != source_commit
                or measured.get("architecture") != architecture
                or measured.get("offline_manifest_sha256") != manifest_sha256
                or measured.get("native_process") is not True or measured.get("rosetta_translated") is not False
                or not isinstance(checks, list) or not checks
                or not all(isinstance(check, dict) and isinstance(check.get("name"), str)
                           and check.get("passed") is True for check in checks)):
            raise ValueError("Measured native acceptance checks are incomplete or failed")
        names = [check["name"] for check in checks]
        if len(set(names)) != len(names) or not REQUIRED_CHECKS[case["name"]].issubset(names):
            raise ValueError("Required automatic acceptance checks were not measured")
        if case["name"] in {"privacy", "licenses"} and measured.get("unresolved_findings") != []:
            raise ValueError("Privacy or binary-license findings remain unresolved")
        seen.add(case["name"])
    return {"architecture": architecture, "source_commit": source_commit,
            "version": info.get("CFBundleShortVersionString"),
            "offline_manifest_sha256": manifest_sha256, "automatic_cases": len(seen),
            "signing": "ad-hoc", "notarized": False, "human_scores_used": False}


def verify_distribution(transport_root: Path, app: Path, evidence_root: Path, source_commit: str) -> dict:
    # Never accept a caller-supplied "accepted" boolean/dict as native proof.
    acceptance = verify_acceptance(app, evidence_root, source_commit)
    report = _document(safe_member(transport_root, "transport.json"))
    if (report.get("schema_version") != 1 or report.get("product") != "KSI Local Studio"
            or report.get("source_commit") != source_commit
            or report.get("version") != acceptance["version"]
            or report.get("architecture") != acceptance["architecture"]
            or report.get("offline_manifest_sha256") != acceptance["offline_manifest_sha256"]
            or report.get("models_included") is not True or report.get("notarized") is not False
            or report.get("app_signing") != "ad-hoc"):
        raise ValueError("Distribution transport differs from the accepted offline application")
    files = report.get("files")
    if not isinstance(files, list) or not files or len(files) > 100:
        raise ValueError("Distribution file inventory is invalid")
    names = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Distribution file entry must be an object")
        filename = item.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename or filename in names:
            raise ValueError("Distribution filename is unsafe or duplicated")
        path = safe_member(transport_root, filename)
        if (path.suffix not in {".dmg", ".dmgpart"} or type(item.get("size")) is not int
                or not 0 < item["size"] < 2 * 1024**3 or path.stat().st_size != item["size"]
                or digest_file(path) != item.get("sha256")):
            raise ValueError("Distribution part changed or exceeds its per-asset bound")
        names.add(filename)
    if sum(name.endswith(".dmg") for name in names) != 1:
        raise ValueError("Distribution requires exactly one primary DMG")
    transport = "segmented-udif" if len(files) > 1 else "udif"
    if report.get("transport") != transport:
        raise ValueError("Distribution transport does not match its actual parts")
    primary = next(name for name in names if name.endswith(".dmg"))
    stem = primary[:-4]
    expected_names = {primary} | {f"{stem}.{number:03d}.dmgpart" for number in range(2, len(files) + 1)}
    if names != expected_names:
        raise ValueError("Distribution segments are missing or inconsistent")
    actual_names = {path.name for path in transport_root.iterdir() if path.suffix in {".dmg", ".dmgpart"}}
    if actual_names != names:
        raise ValueError("Distribution contains unlisted installation media")
    embedded = verify_embedded_application(safe_member(transport_root, primary), app)
    return dict(acceptance, **embedded, transport=transport, files=files, transport_verified=True)


def verify_release(platforms: dict, source_commit: str) -> dict:
    if set(platforms) != {"arm64", "x86_64"}:
        raise ValueError("The approved release requires both native Mac architectures")
    reports = {}
    versions = set()
    for architecture, paths in platforms.items():
        app = Path(paths["application"])
        report = verify_distribution(Path(paths["transport"]), app, Path(paths["evidence"]), source_commit)
        if report["architecture"] != architecture:
            raise ValueError("Native acceptance was assigned to another architecture")
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
        version = info.get("CFBundleShortVersionString")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("Development candidates cannot be published as a final release")
        versions.add(version)
        reports[architecture] = report
    if len(versions) != 1:
        raise ValueError("The two native applications have different product versions")
    return {"schema_version": 1, "source_commit": source_commit, "version": versions.pop(),
            "signing": "ad-hoc", "notarized": False, "architectures": reports, "ready_to_publish": True}
