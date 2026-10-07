from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.cli import build_parser
from ksi_local.job_store import SCHEMA_VERSION
from ksi_local.update_manager import plan_offline_update
from ksi_local.runtime_portability import (
    audit_runtime_portability,
    prepare_runtime_layout,
)


ROOT = Path(__file__).resolve().parents[1]
VERIFIED = {
    "signature_verifier": lambda _path: True,
    "notarization_verifier": lambda _path: True,
}


def release(root: Path, *, version: str = "2.0.1", notarized: bool = True, models: bool = False, minimum: int = 1, maximum: int = SCHEMA_VERSION) -> tuple[Path, Path]:
    package = root / "KSI Local Studio.dmg"
    package.write_bytes(b"verified-offline-package")
    manifest = root / "release.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "product": "KSI Local Studio",
                "version": version,
                "package_filename": package.name,
                "size_bytes": package.stat().st_size,
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "minimum_database_schema": minimum,
                "maximum_database_schema": maximum,
                "architecture": "arm64",
                "offline_complete": True,
                "models_included": models,
                "notarized": notarized,
                "signing": "developer-id" if notarized else "ad-hoc",
            }
        )
    )
    return manifest, package


class Phase37Tests(unittest.TestCase):
    def test_verified_offline_update_is_planned_without_modifying_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root)
            before = package.read_bytes()
            plan = plan_offline_update(
                manifest, package, current_version="2.0.0", **VERIFIED
            )
            self.assertTrue(plan.ready)
            self.assertTrue(plan.offline)
            self.assertTrue(plan.rollback_backup_required)
            self.assertEqual(plan.direction, "upgrade")
            self.assertEqual(package.read_bytes(), before)

    def test_corrupt_or_mismatched_package_is_rejected_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root)
            package.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "bozuk veya eksik"):
                plan_offline_update(manifest, package, current_version="2.0.0")
            self.assertEqual(package.read_bytes(), b"corrupt")

    def test_manifest_and_package_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root)
            manifest_link = root / "manifest-link.json"
            package_link = root / "package-link.dmg"
            manifest_link.symlink_to(manifest)
            package_link.symlink_to(package)
            with self.assertRaisesRegex(ValueError, "normal dosya"):
                plan_offline_update(manifest_link, package, current_version="2.0.0")
            with self.assertRaisesRegex(ValueError, "normal dosya"):
                plan_offline_update(manifest, package_link, current_version="2.0.0")

    def test_numeric_prerelease_versions_are_ordered_numerically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root, version="2.0.0.dev10")
            plan = plan_offline_update(
                manifest,
                package,
                current_version="2.0.0.dev2",
                **VERIFIED,
            )
            self.assertEqual(plan.direction, "upgrade")

    def test_downgrade_schema_model_and_notarization_gates_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root, version="1.9.0")
            with self.assertRaises(PermissionError):
                plan_offline_update(manifest, package, current_version="2.0.0")
            self.assertEqual(plan_offline_update(manifest, package, current_version="2.0.0", allow_downgrade=True, **VERIFIED).direction, "downgrade")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root, minimum=SCHEMA_VERSION + 1, maximum=SCHEMA_VERSION + 2)
            with self.assertRaisesRegex(ValueError, "şemasıyla uyumlu"):
                plan_offline_update(manifest, package, current_version="2.0.0")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root, models=True)
            with self.assertRaisesRegex(ValueError, "model ağırlığı"):
                plan_offline_update(manifest, package, current_version="2.0.0")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, package = release(root, notarized=False)
            with self.assertRaisesRegex(PermissionError, "noter onaylı"):
                plan_offline_update(manifest, package, current_version="2.0.0")
            self.assertTrue(plan_offline_update(manifest, package, current_version="2.0.0", require_notarization=False).ready)

    def test_manifest_cannot_forge_public_update_trust(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest, package = release(Path(directory), notarized=True)
            with self.assertRaisesRegex(PermissionError, "bağımsız doğrulanamadı"):
                plan_offline_update(
                    manifest,
                    package,
                    current_version="2.0.0",
                    signature_verifier=lambda _path: False,
                    notarization_verifier=lambda _path: False,
                )

    def test_offline_builder_verifies_payload_and_never_swaps_user_runtime(self) -> None:
        builder = (ROOT / "src/ksi_local/dmg_transport.py").read_text()
        self.assertLess(builder.index("payload.verify(entry)"), builder.index("destination.mkdir"))
        self.assertIn('"models_included": True', builder)
        self.assertIn('"installation_tested": False', builder)
        self.assertIn('"/usr/bin/hdiutil", "verify"', builder)
        self.assertNotIn("RUNTIME_SWAPPED", builder)
        self.assertNotIn("Path.home()", builder)

    def test_offline_runtime_rejects_machine_external_python_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "venv/bin").mkdir(parents=True)
            external = root.parent / "python3.12"
            external.write_bytes(b"external")
            (root / "venv/bin/python").symlink_to(external)
            findings = audit_runtime_portability(root)
            self.assertIn("external-symlink", {item.rule for item in findings})

    def test_offline_runtime_rejects_machine_paths_in_venv_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_root = "/" + "Users/example"
            (root / "venv").mkdir()
            (root / "venv/pyvenv.cfg").write_text(
                f"home = {user_root}/dev/python/bin\n",
                encoding="utf-8",
            )
            findings = audit_runtime_portability(root)
            self.assertIn("machine-local-python-path", {item.rule for item in findings})

    def test_runtime_layout_relinks_both_venvs_and_prunes_unused_external_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            user_root = "/" + "Users/example"
            python = root / "python/bin/python3.12"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"portable-python")
            python.chmod(0o755)
            for name in ("venv", "chatterbox-venv"):
                bin_dir = root / name / "bin"
                bin_dir.mkdir(parents=True)
                (bin_dir / "python").symlink_to("/opt/homebrew/bin/python3.12")
                (bin_dir / "pip").write_text(
                    f"#!{user_root}/.venv/bin/python\n",
                    encoding="utf-8",
                )
                (root / name / "pyvenv.cfg").write_text(
                    "home = /opt/homebrew/bin\nversion = 3.12.14\n",
                    encoding="utf-8",
                )
            plugins = root / "venv/lib/python3.12/site-packages/PySide6/Qt/plugins/sqldrivers"
            plugins.mkdir(parents=True)
            for filename in (
                "libqsqlmimer.dylib",
                "libqsqlodbc.dylib",
                "libqsqlpsql.dylib",
            ):
                (plugins / filename).write_bytes(b"unused")
            cache = root / "venv/lib/python3.12/site-packages/example/__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.cpython-312.pyc").write_bytes(
                f"{user_root}/private/source.py".encode("utf-8")
            )

            prepare_runtime_layout(root)

            for name in ("venv", "chatterbox-venv"):
                bin_dir = root / name / "bin"
                self.assertEqual((bin_dir / "python3.12").readlink(), Path("../../python/bin/python3.12"))
                self.assertEqual((bin_dir / "python").readlink(), Path("python3.12"))
                self.assertEqual((bin_dir / "python3").readlink(), Path("python3.12"))
                self.assertFalse((bin_dir / "pip").exists())
                config = (root / name / "pyvenv.cfg").read_text(encoding="utf-8")
                self.assertEqual(config, "include-system-site-packages = false\nversion = 3.12.13\n")
            self.assertFalse((plugins / "libqsqlmimer.dylib").exists())
            self.assertFalse((plugins / "libqsqlodbc.dylib").exists())
            self.assertFalse((plugins / "libqsqlpsql.dylib").exists())
            self.assertFalse(cache.exists())
            self.assertEqual(audit_runtime_portability(root), ())

    def test_macho_audit_ignores_architecture_headers_and_dylib_self_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "libexample.dylib"
            library.write_bytes(b"\xcf\xfa\xed\xfe")
            output = (
                f"{library} (architecture x86_64):\n"
                "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
                f"{library} (architecture arm64):\n"
                f"\t/private/build/libexample.dylib (compatibility version 1.0.0)\n"
                "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
            )
            with patch(
                "ksi_local.runtime_portability.subprocess.run",
                return_value=SimpleNamespace(stdout=output),
            ):
                self.assertEqual(audit_runtime_portability(root), ())

    def test_macho_audit_rejects_a_real_external_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "libexample.dylib"
            library.write_bytes(b"\xcf\xfa\xed\xfe")
            output = (
                f"{library}:\n"
                "\t/opt/vendor/lib/libmissing.dylib (compatibility version 1.0.0)\n"
            )
            with patch(
                "ksi_local.runtime_portability.subprocess.run",
                return_value=SimpleNamespace(stdout=output),
            ):
                findings = audit_runtime_portability(root)
            self.assertEqual([item.rule for item in findings], ["external-library"])

    def test_notarization_requires_keychain_profile_and_cli_has_read_only_plan(self) -> None:
        script = (ROOT / "scripts/notarize_release.sh").read_text()
        self.assertIn("KSI_APPLE_SIGNING_IDENTITY", script)
        self.assertIn("KSI_NOTARY_PROFILE", script)
        self.assertIn("notarytool submit", script)
        self.assertIn("stapler validate", script)
        args = build_parser().parse_args(["plan-update", "/tmp/release.json", "/tmp/app.dmg", "--current-version", "2.0.0"])
        self.assertEqual(args.command, "plan-update")


if __name__ == "__main__":
    unittest.main()
