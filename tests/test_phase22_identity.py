from __future__ import annotations

import json
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ksi_local.migration import (
    LEGACY_PACKAGE_NAME,
    LEGACY_PRODUCT_NAME,
    apply_migration,
    inspect_migration,
    migration_paths,
)
from ksi_local.project_metadata import (
    APP_EXECUTABLE_NAME,
    CLI_NAME,
    DEVELOPMENT_BUNDLE_ID,
    PACKAGE_NAME,
    PRODUCT_NAME,
    WORKSPACE_DIRECTORY,
)


class CanonicalIdentityTests(unittest.TestCase):
    def test_canonical_identifiers_are_unambiguous(self) -> None:
        self.assertEqual(PRODUCT_NAME, "KSI Local Studio")
        self.assertEqual(PACKAGE_NAME, "ksi_local")
        self.assertEqual(CLI_NAME, "ksi")
        self.assertEqual(WORKSPACE_DIRECTORY, "KSI-Workspace")

    def test_packaging_uses_the_same_identity(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        with (root / "packaging/Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertIn('name = "ksi-local-studio"', pyproject)
        self.assertIn('ksi = "ksi_local.cli:main"', pyproject)
        self.assertEqual(plist["CFBundleDisplayName"], PRODUCT_NAME)
        self.assertEqual(plist["CFBundleExecutable"], APP_EXECUTABLE_NAME)
        self.assertEqual(plist["CFBundleIdentifier"], DEVELOPMENT_BUNDLE_ID)

    def test_private_predecessor_name_is_isolated_to_migration_module(self) -> None:
        root = Path(__file__).resolve().parents[1]
        excluded = {
            ".git",
            ".phase1",
            ".venv",
            ".venv-chatterbox",
            ".pytest_cache",
            "backups",
            "build",
            "dist",
            "tmp",
            "deployment",
            "public-release",
            "__pycache__",
        }
        allowed = {root / "src/ksi_local/migration.py"}
        hits: list[str] = []
        for path in root.rglob("*"):
            if not path.is_file() or any(part in excluded for part in path.parts):
                continue
            if path.suffix.casefold() not in {
                ".json",
                ".md",
                ".plist",
                ".py",
                ".sh",
                ".swift",
                ".toml",
                ".txt",
            }:
                continue
            if path.resolve() in allowed:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if LEGACY_PRODUCT_NAME in text or LEGACY_PACKAGE_NAME in text:
                hits.append(str(path.relative_to(root)))
        self.assertEqual(hits, [])


class MigrationTests(unittest.TestCase):
    def _create_legacy_fixture(self, root: Path) -> tuple[Path, Path]:
        home = root / "home"
        mount = root / "mount"
        paths = migration_paths(home=home, mount_point=mount)
        paths.legacy_state.mkdir(parents=True)
        paths.legacy_workspace.mkdir(parents=True)
        (paths.legacy_workspace / ".workspace-id").write_text(
            json.dumps({"workspace_id": "test-workspace"}), encoding="utf-8"
        )
        database = paths.legacy_state / "jobs.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, job_directory TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?)",
                ("job-1", str(paths.legacy_workspace / "jobs" / "job-1")),
            )
        return home, mount

    def test_dry_run_does_not_modify_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, mount = self._create_legacy_fixture(Path(directory))
            paths = migration_paths(home=home, mount_point=mount)
            report = inspect_migration(home=home, mount_point=mount)
            self.assertEqual(report.status, "ready")
            self.assertEqual(report.state_action, "rename")
            self.assertEqual(report.workspace_action, "rename")
            self.assertTrue(paths.legacy_state.exists())
            self.assertFalse(paths.state.exists())

    def test_apply_is_atomic_archived_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, mount = self._create_legacy_fixture(Path(directory))
            paths = migration_paths(home=home, mount_point=mount)
            report = apply_migration(home=home, mount_point=mount)
            self.assertEqual(report.status, "completed")
            self.assertEqual(report.updated_job_paths, 1)
            self.assertFalse(paths.legacy_state.exists())
            self.assertFalse(paths.legacy_workspace.exists())
            self.assertTrue(paths.workspace.exists())
            self.assertTrue(paths.state.joinpath("archive/pre-ksi/jobs.sqlite3").is_file())
            with sqlite3.connect(paths.state / "jobs.sqlite3") as connection:
                job_directory = connection.execute(
                    "SELECT job_directory FROM jobs WHERE id = 'job-1'"
                ).fetchone()[0]
            self.assertTrue(job_directory.startswith(str(paths.workspace)))
            repeated = apply_migration(home=home, mount_point=mount)
            self.assertEqual(repeated.state_action, "already_migrated")
            self.assertEqual(repeated.workspace_action, "already_migrated")

    def test_conflict_is_reported_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, mount = self._create_legacy_fixture(Path(directory))
            paths = migration_paths(home=home, mount_point=mount)
            paths.state.mkdir(parents=True)
            report = inspect_migration(home=home, mount_point=mount)
            self.assertEqual(report.status, "blocked")
            with self.assertRaises(RuntimeError):
                apply_migration(home=home, mount_point=mount)
            self.assertTrue(paths.legacy_state.exists())
            self.assertTrue(paths.state.exists())


if __name__ == "__main__":
    unittest.main()
