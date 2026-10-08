"""Synthetic adapter tests, never native inference or model-quality evidence."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ksi_local.cpu_transcription import transcribe_cpu
from ksi_local.subtitles import read_srt
from ksi_local.transcription import transcribe_media


class CpuTranscriptionTests(unittest.TestCase):
    def fixture(self, root):
        source, model = root / 'synthetic.wav', root / 'synthetic.bin'
        source.write_bytes(b'Synthetic audio placeholder, never decoded')
        model.write_bytes(b'Synthetic model placeholder, never executed')
        return source, model

    def test_intel_and_explicit_arm_cpu_route_preserve_language_and_clamped_srt(self):
        for architecture in ('arm64', 'x86_64'):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, model = self.fixture(root)
                output = root / 'output.srt'
                commands = []
                def engine(argv, **options):
                    commands.append((argv, options))
                    if '-of' in argv:
                        path = Path(argv[argv.index('-of') + 1]).with_suffix('.json')
                        path.write_text(json.dumps(dict(result=dict(language='es'), transcription=[
                            dict(offsets=dict(**{'from': 0, 'to': 1500}), text='Hola mundo')])) )
                    return ''
                with (patch('ksi_local.transcription.host_architecture', return_value=architecture),
                      patch('ksi_local.transcription.probe_local_media', return_value=dict(duration_seconds=1)),
                      patch('ksi_local.cpu_transcription.bundle_root', return_value=None),
                      patch('ksi_local.cpu_transcription.tool_path', side_effect=lambda name: '/synthetic/' + name),
                      patch('ksi_local.cpu_transcription.run_engine', side_effect=engine)):
                    result = transcribe_media(source, output, model=str(model), language='auto', initial_prompt='  Test  prompt  ')
                self.assertEqual(result['backend'], 'whisper.cpp-cpu')
                self.assertEqual(result['detected_language'], 'es')
                self.assertEqual(result['segments'], 1)
                cues = read_srt(output)
                self.assertEqual(cues[0].text, 'Hola mundo')
                self.assertEqual(cues[0].end, '00:00:01,000')
                self.assertEqual(len(commands), 2)
                ffmpeg, whisper = (row[0] for row in commands)
                self.assertEqual(ffmpeg[ffmpeg.index('-protocol_whitelist') + 1], 'file,pipe')
                self.assertEqual(whisper[whisper.index('-l') + 1], 'auto')
                self.assertEqual(whisper[whisper.index('--prompt') + 1], 'Test prompt')
                self.assertIn('-ng', whisper)
                self.assertEqual(commands[1][1]['timeout'], 600)

    def test_invalid_durations_do_not_start_an_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, model = self.fixture(root)
            with patch('ksi_local.cpu_transcription.bundle_root', return_value=None), patch('ksi_local.cpu_transcription.run_engine') as engine:
                for duration in (None, 0, -1, float('nan'), float('inf')):
                    with self.subTest(duration=duration), self.assertRaises(ValueError):
                        transcribe_cpu(source, root / 'out.srt', model=str(model), language='tr', duration_seconds=duration, initial_prompt=None)
                engine.assert_not_called()

    def test_turkish_language_reaches_cpu_engine_and_srt_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, model = self.fixture(root)
            commands = []
            def engine(argv, **_options):
                commands.append(argv)
                if '-of' in argv:
                    Path(argv[argv.index('-of') + 1]).with_suffix('.json').write_text(json.dumps(
                        dict(result=dict(language='tr'), transcription=[
                            dict(offsets=dict(**{'from': 0, 'to': 1000}), text='Merhaba')])) )
                return ''
            with (patch('ksi_local.cpu_transcription.bundle_root', return_value=None),
                  patch('ksi_local.cpu_transcription.tool_path', side_effect=lambda name: '/synthetic/' + name),
                  patch('ksi_local.cpu_transcription.run_engine', side_effect=engine)):
                result = transcribe_cpu(source, root / 'out.srt', model=str(model), language='tr', duration_seconds=1, initial_prompt=None)
            self.assertEqual(result['detected_language'], 'tr')
            self.assertEqual(read_srt(root / 'out.srt')[0].text, 'Merhaba')
            self.assertEqual(commands[1][commands[1].index('-l') + 1], 'tr')

    def test_changed_packaged_model_is_rejected_before_engine_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, model = self.fixture(root)
            original = model.read_bytes()
            entry = SimpleNamespace(role='model', identifier='whisper-cpp-turbo', size=len(original), sha256=hashlib.sha256(original).hexdigest())
            model.write_bytes(b'X' * len(original))
            with (patch('ksi_local.cpu_transcription.bundle_root', return_value=root),
                  patch('ksi_local.cpu_transcription.OfflinePayload.load', return_value=SimpleNamespace(files=[entry])),
                  patch('ksi_local.cpu_transcription.run_engine') as engine,
                  self.assertRaisesRegex(RuntimeError, 'bütünlük')):
                transcribe_cpu(source, root / 'out.srt', model=str(model), language='tr', duration_seconds=1, initial_prompt=None)
            engine.assert_not_called()

    def test_missing_or_wrong_format_model_cannot_start_an_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, model = self.fixture(root)
            with patch('ksi_local.cpu_transcription.run_engine') as engine:
                for candidate in (root / 'missing.bin', source):
                    with self.subTest(model=candidate.name), self.assertRaises(RuntimeError):
                        transcribe_cpu(source, root / 'out.srt', model=str(candidate), language='tr', duration_seconds=1, initial_prompt=None)
                engine.assert_not_called()
