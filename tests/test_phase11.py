from __future__ import annotations

import hashlib
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local import __version__
from ksi_local.acceptance import THREE_HOURS_SECONDS, run_three_hour_timeline_acceptance
from ksi_local.gui import MainWindow
from ksi_local.maintenance import inspect_app_bundle, inspect_job_cache, inspect_models
from ksi_local.settings import WorkspacePaths
from ksi_local.storage import GIB


def workspace_at(root: Path) -> WorkspacePaths:
    workspace = root / "KSI-Workspace"
    result = WorkspacePaths(
        root=workspace,
        jobs=workspace / "jobs",
        outputs=workspace / "outputs",
        models_ollama=workspace / "models/ollama",
        models_whisper=workspace / "models/whisper/large-v3-turbo-8bit",
        yt_dlp=workspace / "tools/yt-dlp/test/yt-dlp",
        deno=workspace / "tools/deno/test/deno",
    )
    result.jobs.mkdir(parents=True)
    result.outputs.mkdir(parents=True)
    return result


class ModelAndCacheManagementTests(unittest.TestCase):
    def test_all_required_models_can_be_fully_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_at(Path(directory))
            payloads = {
                "qwen": b"summary-model",
                "translate": b"translation-model",
                "whisper": b"whisper-model",
                "s3": b"s3-model",
                "t3": b"t3-model",
                "ve": b"voice-encoder",
            }
            digests = {
                name: hashlib.sha256(value).hexdigest()
                for name, value in payloads.items()
            }
            blobs = workspace.models_ollama / "blobs"
            blobs.mkdir(parents=True)
            (blobs / f"sha256-{digests['qwen']}").write_bytes(payloads["qwen"])
            (blobs / f"sha256-{digests['translate']}").write_bytes(payloads["translate"])
            workspace.models_whisper.mkdir(parents=True)
            (workspace.models_whisper / "weights.safetensors").write_bytes(
                payloads["whisper"]
            )
            tts = workspace.root / "models/tts/chatterbox-multilingual-v3"
            tts.mkdir(parents=True)
            (tts / "s3gen.pt").write_bytes(payloads["s3"])
            (tts / "t3_mtl23ls_v3.safetensors").write_bytes(payloads["t3"])
            (tts / "ve.pt").write_bytes(payloads["ve"])
            manifest = {
                "models": {
                    "qwen3.5:4b": {
                        "size_bytes": len(payloads["qwen"]),
                        "sha256": digests["qwen"],
                    },
                    "translategemma:4b-it-q8_0": {
                        "size_bytes": len(payloads["translate"]),
                        "sha256": digests["translate"],
                    },
                    "mlx-community/whisper-large-v3-turbo-8bit": {
                        "weights_filename": "weights.safetensors",
                        "size_bytes": len(payloads["whisper"]),
                        "sha256": digests["whisper"],
                    },
                    "ResembleAI/chatterbox-multilingual-v3": {
                        "s3gen_size_bytes": len(payloads["s3"]),
                        "s3gen_sha256": digests["s3"],
                        "t3_v3_size_bytes": len(payloads["t3"]),
                        "t3_v3_sha256": digests["t3"],
                        "voice_encoder_size_bytes": len(payloads["ve"]),
                        "voice_encoder_sha256": digests["ve"],
                    },
                }
            }
            statuses = inspect_models(workspace, verify_hashes=True, manifest=manifest)
            self.assertEqual(len(statuses), 4)
            self.assertTrue(all(item.ready for item in statuses))
            self.assertTrue(all(item.digest_verified is True for item in statuses))

    def test_missing_or_changed_model_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_at(Path(directory))
            manifest = {
                "models": {
                    "qwen3.5:4b": {"size_bytes": 1, "sha256": "a" * 64},
                    "translategemma:4b-it-q8_0": {
                        "size_bytes": 1,
                        "sha256": "b" * 64,
                    },
                    "mlx-community/whisper-large-v3-turbo-8bit": {
                        "weights_filename": "weights.safetensors",
                        "size_bytes": 1,
                        "sha256": "c" * 64,
                    },
                    "ResembleAI/chatterbox-multilingual-v3": {
                        "s3gen_size_bytes": 1,
                        "s3gen_sha256": "d" * 64,
                        "t3_v3_size_bytes": 1,
                        "t3_v3_sha256": "e" * 64,
                        "voice_encoder_size_bytes": 1,
                        "voice_encoder_sha256": "f" * 64,
                    },
                }
            }
            statuses = inspect_models(workspace, manifest=manifest)
            self.assertTrue(all(not item.ready for item in statuses))

    def test_cache_inventory_only_counts_regenerable_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = workspace_at(Path(directory))
            job = workspace.jobs / "JOB-11"
            (job / "source").mkdir(parents=True)
            (job / "work/dub-segments").mkdir(parents=True)
            (job / "outputs").mkdir()
            (job / "source/source.mp4").write_bytes(b"source-is-protected")
            (job / "work/transcript.auto.srt").write_bytes(b"transcript-is-protected")
            (job / "work/dub-segments/1.wav").write_bytes(b"cache")
            (job / "work/.summary.checkpoint.json").write_bytes(b"checkpoint")
            status = inspect_job_cache(workspace)
            self.assertEqual(status.job_count, 1)
            self.assertEqual(status.candidate_file_count, 2)
            self.assertEqual(status.candidate_bytes, len(b"cachecheckpoint"))


class PackagingAcceptanceTests(unittest.TestCase):
    def test_package_requires_arm64_and_valid_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "KSI Local Studio.app"
            macos = app / "Contents/MacOS"
            macos.mkdir(parents=True)
            (macos / "KSI-Local-Studio").write_text("#!/bin/zsh\n", encoding="utf-8")
            plist = {
                "CFBundleIdentifier": "local.ksi.studio",
                "CFBundleShortVersionString": "1.0.0",
                "CFBundleVersion": "11",
                "LSArchitecturePriority": ["arm64"],
                "LSRequiresNativeExecution": True,
            }
            with (app / "Contents/Info.plist").open("wb") as handle:
                plistlib.dump(plist, handle)
            with patch(
                "ksi_local.maintenance.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ):
                status = inspect_app_bundle(app)
            self.assertTrue(status.exists)
            self.assertTrue(status.signed)
            self.assertTrue(status.native_arm64_only)
            self.assertEqual(status.version, "1.0.0")

    def test_project_package_metadata_is_final_and_native(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "packaging/Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        launcher = (root / "packaging/KSI-Local-Studio-portable-launcher").read_text(encoding="utf-8")
        self.assertEqual(__version__, "2.0.0")
        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleVersion"], "19")
        self.assertEqual(plist["LSArchitecturePriority"], ["arm64"])
        self.assertTrue(plist["LSRequiresNativeExecution"])
        self.assertIn("/usr/bin/uname -m", launcher)
        self.assertIn("KSIArchitecture", launcher)


class ThreeHourAcceptanceTests(unittest.TestCase):
    def test_exact_three_hour_timeline_passes_without_loading_ai(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_three_hour_timeline_acceptance(
                directory, free_bytes=75 * GIB
            )
        self.assertTrue(result.passed)
        self.assertEqual(result.duration_seconds, THREE_HOURS_SECONDS)
        self.assertEqual(result.cue_count, 2160)
        self.assertGreaterEqual(result.summary_chunk_count, 36)
        self.assertTrue(result.srt_roundtrip_ok)
        self.assertTrue(result.timestamp_structure_ok)

    def test_three_hour_timeline_respects_twenty_gib_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_three_hour_timeline_acceptance(
                directory, free_bytes=25 * GIB
            )
        self.assertFalse(result.passed)
        self.assertFalse(result.storage_fits)


class PhaseElevenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_system_status_control_is_available_without_starting_ai(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            with patch.dict(
                os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}
            ), patch("ksi_local.gui.resolve_workspace", return_value=workspace):
                window = MainWindow()
                window.workspace_initialized = True
                window._workspace_resolution_finished(workspace, None, True)
                self.assertEqual(window.system_status_button.text(), "Sistem Bilgilerini Yenile")
                self.assertTrue(window.system_status_button.isEnabled())
                self.assertEqual(
                    window.external_review_label.text(),
                    "İsteğe bağlı harici inceleme",
                )
                self.assertEqual(
                    window.create_codex_package_button.text(),
                    "İnceleme Paketi Oluştur",
                )
                self.assertFalse(window.maintenance_pending)
                window.close()


if __name__ == "__main__":
    unittest.main()
