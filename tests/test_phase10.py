from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local.cleanup import (
    cleanup_intermediates,
    cleanup_inventory,
    recover_cleanup_quarantines,
)
from ksi_local.exporter import (
    ExportArtifact,
    cleanup_stale_export_parts,
    desktop_uses_icloud,
    export_artifacts,
    select_job_artifacts,
    sha256_file,
)
from ksi_local.gui import MainWindow
from ksi_local.job_store import JobStatus
from ksi_local.preferences import UserPreferences, load_preferences, save_preferences
from ksi_local.settings import WorkspacePaths


class VerifiedExportTests(unittest.TestCase):
    def test_subtitled_video_replaces_plain_source_in_video_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "job"
            (job / "source").mkdir(parents=True)
            (job / "outputs").mkdir()
            (job / "source/source.mp4").write_bytes(b"source")
            final = job / "outputs/turkce-altyazili.mp4"
            final.write_bytes(b"embedded-subtitle")

            artifacts = select_job_artifacts(
                job, export_kind="video", title="Ders"
            )

            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].source, final.resolve())
            self.assertEqual(artifacts[0].destination_name, "Ders.tr.mp4")

    def test_all_export_keeps_dubbed_and_subtitled_final_videos_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "job"
            (job / "source").mkdir(parents=True)
            (job / "outputs").mkdir()
            (job / "outputs/turkce-dublaj.mp4").write_bytes(b"dub")
            (job / "outputs/turkce-altyazili.mp4").write_bytes(b"subtitle")

            artifacts = select_job_artifacts(job, export_kind="all", title="Ders")

            names = {item.destination_name for item in artifacts}
            self.assertEqual(
                names,
                {"Ders.tr-dublaj.mp4", "Ders.tr-altyazili.mp4"},
            )

    def test_user_facing_artifacts_are_renamed_verified_and_source_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "jobs/JOB-10"
            (job / "source").mkdir(parents=True)
            (job / "outputs").mkdir()
            (job / "source/source.mp4").write_bytes(b"original-video")
            dubbed = job / "outputs/turkce-dublaj.mp4"
            dubbed.write_bytes(b"dubbed-video")
            subtitle = job / "outputs/turkce.srt"
            subtitle.write_text("subtitle", encoding="utf-8")
            summary = job / "outputs/ozet.md"
            summary.write_text("summary", encoding="utf-8")
            (job / "outputs/turkce.kalite.json").write_text("{}", encoding="utf-8")
            desktop = root / "Desktop"
            desktop.mkdir()

            artifacts = select_job_artifacts(
                job, export_kind="all", title="Unsafe: title/name"
            )
            destination = export_artifacts(
                artifacts, desktop=desktop, folder_name="Unsafe: title [JOB-10]"
            )

            self.assertTrue((destination / "Unsafe_ title_name.tr.mp4").is_file())
            self.assertTrue((destination / "Unsafe_ title_name.tr.srt").is_file())
            self.assertTrue((destination / "Unsafe_ title_name.ozet.md").is_file())
            self.assertFalse((destination / "source.mp4").exists())
            manifest = json.loads((destination / "kopya-manifest.json").read_text())
            self.assertTrue(manifest["source_preserved"])
            for item in manifest["files"]:
                copied = destination / item["filename"]
                self.assertEqual(item["sha256"], sha256_file(copied))
                self.assertEqual(item["size_bytes"], copied.stat().st_size)
            self.assertEqual(dubbed.read_bytes(), b"dubbed-video")
            self.assertEqual((job / "source/source.mp4").read_bytes(), b"original-video")

    def test_collision_never_overwrites_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "turkce.srt"
            source.write_text("test", encoding="utf-8")
            existing = root / "KSI Local Studio"
            existing.mkdir()
            sentinel = existing / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            result = export_artifacts([source], desktop=root, folder_name="KSI Local Studio")
            self.assertEqual(result.name, "KSI Local Studio (2)")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_copy_failure_leaves_no_visible_or_hidden_partial_and_keeps_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "turkce.srt"
            source.write_text("source stays", encoding="utf-8")
            with patch(
                "ksi_local.exporter.shutil.copy2", side_effect=OSError("SSD disconnected")
            ):
                with self.assertRaises(OSError):
                    export_artifacts([source], desktop=root, folder_name="KSI Local Studio")
            self.assertEqual(source.read_text(encoding="utf-8"), "source stays")
            self.assertFalse((root / "KSI-Workspace").exists())
            self.assertEqual(list(root.glob(".ksi_local-export-*.part")), [])

    def test_symlink_and_duplicate_destination_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text("source", encoding="utf-8")
            other = root / "other.srt"
            other.write_text("other", encoding="utf-8")
            link = root / "link.srt"
            link.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "Sembolik"):
                export_artifacts([link], desktop=root, folder_name="link")
            with self.assertRaisesRegex(ValueError, "hedef adı"):
                export_artifacts(
                    [
                        ExportArtifact(source, "same.srt"),
                        ExportArtifact(other, "same.srt"),
                    ],
                    desktop=root,
                    folder_name="duplicate",
                )

    def test_only_old_app_owned_partial_directory_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partial = root / ".ksi_local-export-12345678-1234-1234-1234-123456789abc.part"
            partial.mkdir()
            (partial / "chunk").write_text("partial", encoding="utf-8")
            unrelated = root / "ordinary.part"
            unrelated.mkdir()
            lookalike = root / ".ksi_local-export-user-file.part"
            lookalike.mkdir()
            removed = cleanup_stale_export_parts(root, older_than_seconds=0)
            self.assertEqual(removed, 1)
            self.assertFalse(partial.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(lookalike.exists())

    def test_icloud_layout_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cloud = Path(directory) / "Mobile Documents/com~apple~CloudDocs/Desktop"
            cloud.mkdir(parents=True)
            local = Path(directory) / "Desktop"
            local.mkdir()
            self.assertTrue(desktop_uses_icloud(cloud))
            self.assertFalse(desktop_uses_icloud(local))


class ControlledCleanupTests(unittest.TestCase):
    def _job(self, root: Path) -> Path:
        job = root / "JOB-10"
        (job / "source").mkdir(parents=True)
        (job / "outputs").mkdir()
        (job / "work/dub-segments").mkdir(parents=True)
        (job / "source/source.mp4").write_bytes(b"source")
        (job / "work/transcript.auto.srt").write_text("transcript", encoding="utf-8")
        (job / "work/imported-source.srt").write_text("imported", encoding="utf-8")
        (job / "work/dub-segments/0001.wav").write_bytes(b"segment")
        (job / "work/dub-segments/checkpoint.json").write_text("{}", encoding="utf-8")
        (job / "work/turkce-dublaj.asr.srt").write_text("asr", encoding="utf-8")
        (job / "work/.summary.checkpoint.json").write_text("{}", encoding="utf-8")
        (job / "outputs/turkce-dublaj.wav").write_bytes(b"pcm")
        (job / "outputs/turkce-dublaj.mp4").write_bytes(b"final")
        (job / "outputs/turkce-dublaj.kalite.json").write_text("{}", encoding="utf-8")
        (job / "outputs/turkce.srt").write_text("translation", encoding="utf-8")
        (job / "outputs/ozet.md").write_text("summary", encoding="utf-8")
        (job / "manifest.json").write_text("{}", encoding="utf-8")
        return job

    def test_only_reviewed_regenerable_files_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self._job(Path(directory))
            inventory = cleanup_inventory(job)
            names = {item.relative_path for item in inventory}
            self.assertIn("outputs/turkce-dublaj.wav", names)
            self.assertIn("work/dub-segments/0001.wav", names)
            self.assertIn("work/turkce-dublaj.asr.srt", names)
            self.assertNotIn("source/source.mp4", names)
            self.assertNotIn("work/transcript.auto.srt", names)
            self.assertNotIn("outputs/turkce-dublaj.mp4", names)

            result = cleanup_intermediates(
                job,
                expected_relative_paths=tuple(item.relative_path for item in inventory),
            )
            self.assertEqual(result.removed_files, len(inventory))
            self.assertTrue((job / "source/source.mp4").is_file())
            self.assertTrue((job / "work/transcript.auto.srt").is_file())
            self.assertTrue((job / "work/imported-source.srt").is_file())
            self.assertTrue((job / "outputs/turkce-dublaj.mp4").is_file())
            self.assertTrue((job / "outputs/turkce.srt").is_file())
            self.assertTrue((job / "outputs/ozet.md").is_file())
            self.assertFalse((job / "outputs/turkce-dublaj.wav").exists())

    def test_inventory_change_after_confirmation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = self._job(Path(directory))
            inventory = cleanup_inventory(job)
            (job / "work/dub-segments/new.wav").write_bytes(b"new")
            with self.assertRaisesRegex(RuntimeError, "onaydan sonra değişti"):
                cleanup_intermediates(
                    job,
                    expected_relative_paths=tuple(item.relative_path for item in inventory),
                )

    def test_interrupted_quarantine_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "JOB-10"
            quarantine_name = ".cleanup-quarantine-12345678-1234-1234-1234-123456789abc"
            hidden = job / quarantine_name / "work/dub-segments/0001.wav"
            hidden.parent.mkdir(parents=True)
            hidden.write_bytes(b"recover")
            restored = recover_cleanup_quarantines(job)
            self.assertEqual(restored, 1)
            self.assertEqual(
                (job / "work/dub-segments/0001.wav").read_bytes(), b"recover"
            )
            self.assertFalse((job / quarantine_name).exists())


class PreferenceTests(unittest.TestCase):
    def test_export_choice_and_icloud_acknowledgement_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preferences.json"
            saved = UserPreferences(
                export_kind="summary", icloud_warning_acknowledged=True
            )
            save_preferences(saved, target)
            self.assertEqual(load_preferences(target), saved)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class PhaseTenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}
        )
        self.environment.start()
        workspace_root = root / "KSI-Workspace"
        self.workspace = WorkspacePaths(
            root=workspace_root,
            jobs=workspace_root / "jobs",
            outputs=workspace_root / "outputs",
            models_ollama=workspace_root / "models/ollama",
            models_whisper=workspace_root / "models/whisper",
            yt_dlp=workspace_root / "tools/yt-dlp/2026.08.19/yt-dlp",
            deno=workspace_root / "tools/deno/2.9.6/deno",
        )
        self.workspace.jobs.mkdir(parents=True)
        self.resolver = patch("ksi_local.gui.resolve_workspace", return_value=self.workspace)
        self.resolver.start()
        self.window = MainWindow()
        self.window.workspace_initialized = True
        self.window._workspace_resolution_finished(self.workspace, None, True)

    def tearDown(self) -> None:
        self.window.close()
        self.resolver.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def test_export_and_cleanup_require_completed_job(self) -> None:
        job = self.workspace.jobs / "JOB-10"
        (job / "source").mkdir(parents=True)
        (job / "outputs").mkdir()
        (job / "outputs/ozet.md").write_text("summary", encoding="utf-8")
        self.window.store.create_job(
            job_id="JOB-10",
            source="https://youtu.be/example",
            source_language="en",
            want_subtitle=False,
            want_summary=True,
            want_dub=False,
            job_directory=job,
        )
        self.window.current_job_id = "JOB-10"
        self.window._refresh_history()
        self.assertFalse(self.window.export_button.isEnabled())
        self.window.store.transition_job("JOB-10", JobStatus.RUNNING)
        self.window.store.transition_job("JOB-10", JobStatus.COMPLETED)
        self.window._refresh_history()
        self.assertTrue(self.window.export_button.isEnabled())
        self.assertTrue(self.window.cleanup_button.isEnabled())
        self.assertEqual(self.window.export_kind.currentData(), "all")


if __name__ == "__main__":
    unittest.main()
