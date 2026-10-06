import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.preflight import _missing_model_budget
from ksi_local.turkish_tts_adapter import turkish_tokenizer_scope


class NativeReadinessTests(unittest.TestCase):
    def test_intel_whisper_file_and_piper_voice_are_not_arm_model_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ollama = root / "models/ollama"
            ollama.mkdir(parents=True)
            whisper = root / "models/whisper/ggml-large-v3-turbo.bin"
            whisper.parent.mkdir()
            whisper.write_bytes(b"fixture")
            piper = root / "models/tts/piper"
            piper.mkdir(parents=True)
            (piper / "tr_TR-fettah-medium.onnx").write_bytes(b"fixture")
            (piper / "tr_TR-fettah-medium.onnx.json").write_text("{}")
            workspace = SimpleNamespace(root=root, models_ollama=ollama, models_whisper=whisper)
            with patch("ksi_local.bundle_runtime.bundle_root", return_value=root), patch("ksi_local.bundle_runtime.host_architecture", return_value="x86_64"):
                self.assertEqual(_missing_model_budget(workspace, True, want_dub=True), 0)
                whisper.unlink()
                self.assertGreater(_missing_model_budget(workspace, True, want_dub=True), 0)

    def test_turkish_scope_never_initializes_unused_chinese_downloads(self):
        class Original:
            def __init__(self, *args):
                raise AssertionError("Unused Chinese loader must not run")
        module = SimpleNamespace(ChineseCangjieConverter=Original)
        with self.assertRaisesRegex(ValueError, "fixture"):
            with turkish_tokenizer_scope(module):
                converter = module.ChineseCangjieConverter("unused")
                with self.assertRaises(ValueError):
                    converter("unsupported")
                raise ValueError("fixture")
        self.assertIs(module.ChineseCangjieConverter, Original)
