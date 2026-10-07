import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ksi_local.workspace_job_migration import import_completed_jobs


class WorkspaceJobMigrationTests(unittest.TestCase):
    def prepare(self, root):
        source, target = root / "legacy", root / "internal"
        source.mkdir()
        target.mkdir()
        identity = str(uuid.uuid4())
        (source / ".workspace-id").write_text(json.dumps({"workspace_id": identity}))
        job = source / "jobs" / "completed-fixture"
        (job / "outputs").mkdir(parents=True)
        (job / "source.txt").write_text("original source")
        (job / "outputs" / "summary.md").write_text("original result")
        database = root / "jobs.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, job_directory TEXT, status TEXT)")
            connection.execute("INSERT INTO jobs VALUES ('done', ?, 'completed')", (str(job),))
            connection.execute("INSERT INTO jobs VALUES ('pending', ?, 'queued')", (str(source / "jobs" / "unfinished"),))
        return source, target, identity, job, database

    def test_completed_job_copies_and_rebinds_only_after_hash_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target, identity, job, database = self.prepare(Path(temporary).resolve())
            with patch("ksi_local.workspace_job_migration.shutil.disk_usage") as usage:
                usage.return_value.free = 100 * 1024**3
                report = import_completed_jobs(database, source, target, workspace_id=identity)
            self.assertEqual(report["imported"], ["done"])
            self.assertEqual(report["pending"], ["pending"])
            self.assertEqual((job / "source.txt").read_text(), "original source")
            self.assertEqual((target / "jobs/completed-fixture/outputs/summary.md").read_text(), "original result")
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT job_directory FROM jobs WHERE id='done'").fetchone()[0], str(target / "jobs/completed-fixture"))
                self.assertEqual(connection.execute("SELECT status FROM jobs WHERE id='pending'").fetchone()[0], "queued")
            self.assertTrue((database.parent / "jobs-before-internal-migration.sqlite3").is_file())

    def test_missing_legacy_disk_preserves_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target, identity, job, database = self.prepare(Path(temporary).resolve())
            report = import_completed_jobs(database, source / "missing", target, workspace_id=identity)
            self.assertFalse(report["source_available"])
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT job_directory FROM jobs WHERE id='done'").fetchone()[0], str(job))

    def test_unrelated_existing_target_is_never_overwritten_or_adopted(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target, identity, job, database = self.prepare(Path(temporary).resolve())
            collision = target / "jobs/completed-fixture"
            collision.mkdir(parents=True)
            (collision / "keep.txt").write_text("private")
            with patch("ksi_local.workspace_job_migration.shutil.disk_usage") as usage:
                usage.return_value.free = 100 * 1024**3
                report = import_completed_jobs(database, source, target, workspace_id=identity)
            self.assertIn("done", report["pending"])
            self.assertEqual((collision / "keep.txt").read_text(), "private")
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT job_directory FROM jobs WHERE id='done'").fetchone()[0], str(job))


if __name__ == "__main__":
    unittest.main()
