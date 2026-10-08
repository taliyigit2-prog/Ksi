from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ksi_local.job_store import JobKind, JobStore, SCHEMA_VERSION
from ksi_local.locality_audit import audit_locality
from ksi_local.network_policy import (
    NetworkPolicyError,
    local_only_socket_guard,
    local_worker_environment,
    require_loopback_http_url,
)
from ksi_local.ollama_client import OllamaClient
from ksi_local.ollama_runtime import managed_ollama
from ksi_local.phase12_acceptance import run_phase12_acceptance
from ksi_local.release_backup import create_release_backup, verify_release_backup


class JobKindMigrationTests(unittest.TestCase):
    def test_schema_three_jobs_migrate_to_video_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE jobs (
                        id TEXT PRIMARY KEY,
                        source_kind TEXT NOT NULL,
                        source_reference TEXT NOT NULL,
                        source_language TEXT NOT NULL,
                        want_subtitle INTEGER NOT NULL,
                        want_summary INTEGER NOT NULL,
                        want_dub INTEGER NOT NULL,
                        download_only INTEGER NOT NULL DEFAULT 0,
                        max_height INTEGER NOT NULL DEFAULT 1080,
                        media_index INTEGER NOT NULL DEFAULT 1,
                        job_directory TEXT NOT NULL,
                        status TEXT NOT NULL,
                        current_stage TEXT,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO jobs VALUES (
                        'old-video', 'file', 'local.mp4', 'en', 1, 0, 0, 0,
                        720, 1, '/tmp/old-video', 'queued', NULL, NULL,
                        '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                    );
                    PRAGMA user_version = 3;
                    """
                )
            store = JobStore(database)
            record = store.get_job("old-video")
            self.assertEqual(record.job_kind, JobKind.VIDEO)
            self.assertEqual(record.max_height, 720)
            with sqlite3.connect(database) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )

    def test_document_kind_is_stored_and_filterable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            record = store.create_job(
                job_id="document-one",
                job_kind=JobKind.DOCUMENT,
                source=str(root / "source.txt"),
                source_language="auto",
                want_subtitle=False,
                want_summary=True,
                want_dub=False,
                job_directory=root / "document-one",
            )
            self.assertEqual(record.job_kind, JobKind.DOCUMENT)
            self.assertEqual(store.list_jobs(job_kind="document"), [record])
            self.assertEqual(store.list_jobs(job_kind="video"), [])


class LocalNetworkPolicyTests(unittest.TestCase):
    def test_only_loopback_http_model_urls_are_accepted(self) -> None:
        self.assertEqual(
            require_loopback_http_url("http://localhost:11435/"),
            "http://localhost:11435",
        )
        self.assertEqual(
            OllamaClient("http://127.0.0.1:11435").base_url,
            "http://127.0.0.1:11435",
        )
        for value in (
            "https://localhost:11435",
            "http://example.com:11435",
            "http://localhost.evil:11435",
            "http://user@localhost:11435",
            "http://localhost:11435/api",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                OllamaClient(value)

    def test_document_worker_blocks_remote_dns_and_tcp(self) -> None:
        import socket

        with local_only_socket_guard():
            with self.assertRaises(NetworkPolicyError):
                socket.getaddrinfo("example.com", 443)
            local = socket.getaddrinfo("localhost", 11435)
            self.assertTrue(local)

    def test_worker_environment_removes_cloud_credentials_and_forces_offline(self) -> None:
        environment = local_worker_environment(
            {
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "secret",
                "HTTPS_PROXY": "http://proxy.invalid",
                "ORT_DISABLE_TELEMETRY": "0",
            }
        )
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertEqual(environment["OLLAMA_NO_CLOUD"], "true")
        self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(environment["ORT_DISABLE_TELEMETRY"], "1")

    def test_managed_ollama_always_receives_no_cloud_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "ollama"
            executable.write_text("placeholder", encoding="utf-8")
            models = root / "models"
            models.mkdir()
            process = MagicMock()
            process.poll.return_value = None
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}),
                patch("ksi_local.ollama_runtime._is_ready", side_effect=[False, True]),
                patch("ksi_local.ollama_runtime.subprocess.Popen", return_value=process) as popen,
            ):
                with managed_ollama(
                    executable=str(executable), models_directory=models
                ) as started:
                    self.assertTrue(started)
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(environment["OLLAMA_NO_CLOUD"], "true")
            self.assertNotIn("OPENAI_API_KEY", environment)
            process.terminate.assert_called_once()


class LocalityAcceptanceTests(unittest.TestCase):
    def test_runtime_source_has_no_cloud_ai_or_automatic_upload_wiring(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = audit_locality(root / "src/ksi_local")
        self.assertTrue(report.passed, report.to_dict())

    def test_deterministic_offline_video_srt_and_txt_flow(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("ksi_local.phase12_acceptance.shutil.which", return_value="/ffprobe"),
            patch(
                "ksi_local.phase12_acceptance.verify_media_file",
                return_value={"sha256": "a" * 64},
            ),
        ):
            fixture = Path(temporary) / "synthetic-video.mp4"
            fixture.write_bytes(b"Synthetic file-presence fixture; media verifier is explicitly mocked")
            report = run_phase12_acceptance(root, media_fixture=fixture)
        self.assertTrue(report.passed, report.to_dict())


class ReleaseBackupTests(unittest.TestCase):
    def test_rollback_backup_contains_consistent_database_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "KSI Local Studio.app/Contents"
            (app / "MacOS").mkdir(parents=True)
            (app / "MacOS/KSI Local Studio").write_text("launcher", encoding="utf-8")
            state = root / "state"
            runtime = state / "runtime"
            (runtime / "src/ksi_local").mkdir(parents=True)
            (runtime / "src/ksi_local/__init__.py").write_text(
                '__version__ = "1.0.0"\n', encoding="utf-8"
            )
            (runtime / "config").mkdir()
            (runtime / "config/settings.json").write_text("{}", encoding="utf-8")
            with sqlite3.connect(state / "jobs.sqlite3") as connection:
                connection.execute("CREATE TABLE marker (value TEXT)")
                connection.execute("INSERT INTO marker VALUES ('kept')")
            destination = root / "backup"
            create_release_backup(
                destination=destination,
                app_path=root / "KSI Local Studio.app",
                state_root=state,
                release_version="1.0.0",
            )
            valid, failures = verify_release_backup(destination)
            self.assertTrue(valid, failures)
            with sqlite3.connect(destination / "state/jobs.sqlite3") as connection:
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone()[0], "kept"
                )
            manifest = json.loads(
                (destination / "backup-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["release_version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
