"""Sequential actual-model references on owned synthetic data; not final release."""
import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path
import ksi_local
from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, digest_file, tool_path
from ksi_local.engine_runner import run_engine
from ksi_local.network_policy import local_only_socket_guard
from ksi_local.native_processor import require_native_build_process
from ksi_local.subtitles import Cue, read_srt
from ksi_local.dubbing import timestamp_seconds

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=('background', 'translation', 'tts'))
parser.add_argument('target', type=Path)
args = parser.parse_args()
require_native_build_process()
resources = bundle_root()
assert resources is not None and Path(ksi_local.__file__).resolve().is_relative_to(resources / 'runtime/src')
assert os.environ['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'
state = Path(os.environ['KSI_STATE_DIRECTORY'])
provenance = json.loads((resources / 'build-provenance.json').read_bytes())
from ksi_local.internal_storage import validate_internal_path
from ksi_local.bundle_runtime import host_architecture
architecture = host_architecture()
assert provenance['architecture'] == architecture
validate_internal_path(state)
target = args.target.absolute()
validate_internal_path(target)
assert not target.exists() and not target.is_symlink()
target.mkdir(mode=0o700)
payload = OfflinePayload.load(resources, architecture=architecture)
checks = {}
with local_only_socket_guard():
    if args.mode == 'background':
        from PIL import Image, ImageDraw
        from ksi_local.settings import resolve_workspace
        from ksi_local.job_store import JobStore, JobStatus
        from ksi_local.tool_jobs import ToolJobService
        source, output = target / 'synthetic-object.png', target / 'foreground.png'
        image = Image.new('RGB', (400, 400), 'white')
        drawing = ImageDraw.Draw(image)
        drawing.ellipse((65, 65, 335, 335), fill=(180, 40, 30), outline=(50, 5, 5), width=5)
        image.save(source)
        original_sha256 = digest_file(source)
        service = ToolJobService(resolve_workspace(initialize=False), JobStore(state / 'jobs.sqlite3'))
        identifier = service.submit_image(dict(operation='remove_background', source=str(source), destination=str(output)))
        job_result = service.execute(identifier)
        output = Path(job_result['output'])
        assert output.resolve().is_relative_to(state / 'KSI-Workspace/jobs')
        checks['output_owned_by_job'] = True
        with Image.open(output) as foreground:
            alpha = foreground.getchannel('A')
            checks.update(output_size=foreground.size == (400, 400), foreground_retained=alpha.getpixel((200, 200)) > 100,
                background_removed=alpha.getpixel((0, 0)) < 30, nonempty_alpha=alpha.getextrema()[1] > 0)
        checks['source_preserved'] = digest_file(source) == original_sha256
        checks['job_completed'] = service.store.get_job(identifier).status is JobStatus.COMPLETED
    elif args.mode == 'translation':
        from ksi_local.glossary import Glossary
        from ksi_local.ollama_client import OllamaClient
        from ksi_local.ollama_runtime import managed_ollama, _is_ready
        from ksi_local.translation import translate_cues
        base = 'http://127.0.0.1:11459'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 11459))
        cues = [Cue(1, '00:00:00,000', '00:00:03,000', 'KSI processes 25 documents on the internal disk.'),
                Cue(2, '00:00:03,000', '00:00:06,000', 'Private files are not sent to any server.')]
        with managed_ollama(executable=str(tool_path('ollama')), models_directory=state / 'KSI-Workspace/models/ollama', base_url=base) as owned:
            assert owned
            translated = translate_cues(cues, source_language='en', client=OllamaClient(base), glossary=Glossary('en', (), ('KSI',)))
        text = ' '.join(cue.text for cue in translated)
        checks.update(gemma_reference='belge' in text.casefold() and 'sunucu' in text.casefold(),
            names_and_numbers='KSI' in text and '25' in text,
            timestamps_preserved=[(c.index, c.start, c.end) for c in translated] == [(c.index, c.start, c.end) for c in cues],
            owned_daemon_stopped=not _is_ready(base))
        atomic_write_json(target / 'gemma-reference.json', dict(text=text))
        request, result = target / 'argos-request.json', target / 'argos-result.json'
        atomic_write_json(request, dict(operation='translate', source_language='en', target_language='tr', texts=['Hello world.'],
            packages=str(state / 'KSI-Workspace/models/argos'), cache=str(target / 'argos-private-cache')))
        run_engine([sys.executable, '-m', 'ksi_local.local_ai_worker', str(request), str(result)], timeout=600)
        argos = json.loads(result.read_bytes())
        checks['argos_reference'] = argos['ok'] is True and 'merhaba' in argos['texts'][0].casefold() and 'dünya' in argos['texts'][0].casefold()
    else:
        import numpy
        import soundfile
        from ksi_local.transcription import transcribe_media
        source, output, report = target / 'synthetic.srt', target / 'generated.wav', target / 'tts-report.json'
        atomic_write_text(source, '1\n00:00:00,000 --> 00:00:03,000\nMerhaba bu program bilgisayarınızda çalışır.\n')
        cpu = architecture == 'x86_64'
        interpreter = sys.executable if cpu else str(payload.component('tool', 'chatterbox-python'))
        voice_models = state / 'KSI-Workspace/models/tts' / ('piper' if cpu else 'chatterbox-multilingual-v3')
        profile = voice_models / 'tr_TR-fettah-medium.onnx.json' if cpu else payload.component('support', 'voice-profile')
        profile_sha256 = digest_file(profile)
        run_engine([str(interpreter), '-m', 'ksi_local.tts_worker', str(source), str(output),
            '--segments-directory', str(target / 'segments'), '--model-directory', str(voice_models),
            '--engine', 'piper' if cpu else 'chatterbox', '--voice-profile', str(profile), '--ffmpeg', str(tool_path('ffmpeg')), '--report', str(report)], timeout=600)
        audio, rate = soundfile.read(output)
        checks.update(audible_waveform=float(numpy.sqrt(numpy.mean(audio ** 2))) > .005,
            duration_bounds=rate == 48000 and len(audio) == rate * 3, finite_samples=bool(numpy.isfinite(audio).all()))
        asr_output = target / 'roundtrip.srt'
        transcription = transcribe_media(output, asr_output, language='tr', model=str(state / 'KSI-Workspace/models/whisper' / ('ggml-large-v3-turbo.bin' if cpu else 'large-v3-turbo-8bit')))
        recognized = read_srt(asr_output)
        words = set(re.findall(r'\w+', ' '.join(cue.text for cue in recognized).casefold()))
        checks['speech_reference'] = {'merhaba', 'bu', 'program', 'bilgisayarınızda', 'çalışır'} <= words
        checks['asr_language_turkish'] = transcription['detected_language'] == 'tr'
        checks['srt_timestamps'] = bool(recognized) and all(
            0 <= timestamp_seconds(cue.start) < timestamp_seconds(cue.end) <= 3.1 for cue in recognized)
        checks['verified_profile_unchanged'] = digest_file(profile) == profile_sha256
checks['no_homebrew_path'] = os.environ['PATH'] == '/usr/bin:/bin:/usr/sbin:/sbin'
atomic_write_json(target / 'result.local.json', dict(source_commit=provenance['source_commit'], architecture=architecture,
    offline_manifest_sha256=digest_file(resources / 'offline-manifest.json'), checks=checks,
    scope='Actual native packaged model references with local-only socket guards and synthetic data. Not final version/DMG or complete 14-case release acceptance.'))
print(json.dumps(dict(mode=args.mode, checks=checks)), flush=True)
assert all(checks.values())
