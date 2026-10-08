import os
import builtins
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.local_ai_worker import _background_session
from ksi_local.worker_caches import prepare_model_worker_cache


class ModelWorkerCacheTests(unittest.TestCase):
    def test_native_telemetry_disabled_before_onnx_import_and_rembg_initialization(self):
        imported = builtins.__import__
        events = []
        class ProbeComplete(Exception):
            pass
        def controlled_import(name, *args, **kwargs):
            if name == "onnxruntime":
                self.assertEqual(os.environ["ORT_DISABLE_TELEMETRY"], "1")
                events.append("onnx-import-with-opt-out")
                return SimpleNamespace(disable_telemetry_events=lambda: events.append("api-disabled"))
            if name == "rembg":
                self.assertEqual(events, ["onnx-import-with-opt-out", "api-disabled"])
                raise ProbeComplete()
            return imported(name, *args, **kwargs)
        with patch.dict(os.environ, {"ORT_DISABLE_TELEMETRY": "0"}), \
             patch("ksi_local.local_ai_worker._verified_model", return_value=Path("/unused")), \
             patch("ksi_local.worker_caches.prepare_model_worker_cache"), \
             patch("builtins.__import__", side_effect=controlled_import):
            with self.assertRaises(ProbeComplete):
                _background_session(dict(model="/unused", model_sha256="unused"))
        self.assertEqual(events, ["onnx-import-with-opt-out", "api-disabled"])

    def test_private_internal_cache_overrides_foreign_location_and_is_reusable(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NUMBA_CACHE_DIR": "/Volumes/foreign-cache"}):
            state = Path(temporary).resolve() / "state"
            with patch("ksi_local.worker_caches.default_database_path", return_value=state / "jobs.sqlite3"):
                first = prepare_model_worker_cache()
                self.assertEqual(first, prepare_model_worker_cache())
            self.assertEqual(os.environ["NUMBA_CACHE_DIR"], str(first))
            self.assertTrue(first.is_relative_to(state))
            self.assertIn(first.name, {"arm64", "x86_64"})
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o700)
            self.assertEqual(list(first.iterdir()), [])

    def test_external_state_is_rejected_before_directory_creation(self):
        with patch("ksi_local.worker_caches.default_database_path", return_value=Path("/Volumes/foreign/state/jobs.sqlite3")), \
             patch("ksi_local.worker_caches.Path.mkdir") as mkdir:
            with self.assertRaises(RuntimeError):
                prepare_model_worker_cache()
            mkdir.assert_not_called()

    def test_shared_temporary_root_is_rejected_without_any_directory_change(self):
        with patch("ksi_local.worker_caches.default_database_path", return_value=Path("/private/tmp/jobs.sqlite3")), \
             patch("ksi_local.worker_caches.Path.mkdir") as mkdir, \
             patch("ksi_local.worker_caches.Path.chmod") as chmod:
            with self.assertRaises(RuntimeError):
                prepare_model_worker_cache()
            mkdir.assert_not_called()
            chmod.assert_not_called()

    def test_existing_state_directory_permissions_are_not_changed(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve()
            state.chmod(0o755)
            with patch("ksi_local.worker_caches.default_database_path", return_value=state / "jobs.sqlite3"), \
                 patch.dict(os.environ):
                cache = prepare_model_worker_cache()
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)

    def test_linked_cache_does_not_touch_its_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state, other = root / "state", root / "other"
            state.mkdir()
            other.mkdir()
            (state / "cache").symlink_to(other)
            with patch("ksi_local.worker_caches.default_database_path", return_value=state / "jobs.sqlite3"):
                with self.assertRaises(RuntimeError):
                    prepare_model_worker_cache()
            self.assertEqual(list(other.iterdir()), [])

    def test_non_directory_cache_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve()
            (state / "cache").write_bytes(b"preserve existing ordinary file")
            with patch("ksi_local.worker_caches.default_database_path", return_value=state / "jobs.sqlite3"):
                with self.assertRaises(RuntimeError):
                    prepare_model_worker_cache()
            self.assertEqual((state / "cache").read_bytes(), b"preserve existing ordinary file")

    def test_unwritable_probe_fails_without_setting_a_fallback_cache(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NUMBA_CACHE_DIR": "unchanged"}):
            state = Path(temporary).resolve()
            with patch("ksi_local.worker_caches.default_database_path", return_value=state / "jobs.sqlite3"), \
                 patch("ksi_local.worker_caches.tempfile.NamedTemporaryFile", side_effect=PermissionError("synthetic readonly cache")):
                with self.assertRaises(PermissionError):
                    prepare_model_worker_cache()
            self.assertEqual(os.environ["NUMBA_CACHE_DIR"], "unchanged")

    def test_already_loaded_engine_is_rejected_before_any_state_write(self):
        with patch("ksi_local.worker_caches.sys", SimpleNamespace(modules={"numba": object()})), \
             patch("ksi_local.worker_caches.default_database_path") as database:
            with self.assertRaises(RuntimeError):
                prepare_model_worker_cache()
            database.assert_not_called()

    def test_invalid_architecture_is_rejected_before_any_state_write(self):
        with patch("ksi_local.worker_caches.host_architecture", return_value="unsupported"), \
             patch("ksi_local.worker_caches.default_database_path") as database:
            with self.assertRaises(RuntimeError):
                prepare_model_worker_cache()
            database.assert_not_called()

    def test_invalid_model_rejects_before_cache_preparation_or_engine_imports(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / "synthetic.onnx"
            model.write_bytes(b"invalid hash fixture, not an ONNX model")
            with patch("ksi_local.worker_caches.prepare_model_worker_cache") as prepare:
                with self.assertRaises(RuntimeError):
                    _background_session(dict(model=str(model), model_sha256="a" * 64))
                prepare.assert_not_called()
