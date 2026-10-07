import hashlib
import json
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


stage = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/stage_media_executables.py"))["stage"]


class MediaExecutableStagingTests(unittest.TestCase):
    def fixture(self, root):
        repo, graph, originals = root / "repo", root / "graph", root / "originals"
        (repo / "config").mkdir(parents=True)
        graph.mkdir()
        originals.mkdir()
        native = []
        for name in ("ffmpeg", "ffprobe", "magick"):
            content = ("Synthetic " + name + " fixture, not executable").encode()
            path = graph / name
            path.write_bytes(content)
            path.chmod(0o755)
            native.append({"path": name, "size": len(content), "staged_sha256": hashlib.sha256(content).hexdigest()})
        (graph / "native-staging.json").write_text(json.dumps({"schema_version": 1,
            "files": native, "executables": ["ffmpeg", "ffprobe", "magick"]}))
        rows, inputs, pins = [], {}, {}
        for engine, legal in (("ffmpeg", "COPYING.GPLv2"), ("imagemagick", "LICENSE")):
            source_id = engine + "-corresponding-source"
            for relative, role, identifier, content in (
                    ("licenses/tools/" + engine + "-corresponding/" + legal, "license", engine + "-license", b"Synthetic legal text fixture"),
                    ("sources/" + engine + ".tar", "support", source_id, b"Synthetic corresponding source fixture")):
                path = originals / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                row = {"path": relative, "role": role, "identifier": identifier,
                    "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
                rows.append(row)
                if role == "support":
                    pins[source_id] = {"source_archive_sha256": row["sha256"]}
            inputs[engine + "-source"] = {"commit": "a" * 40, "url": "https://example.org/" + engine,
                "license": "GPL-2.0-or-later" if engine == "ffmpeg" else "ImageMagick"}
            inputs[source_id] = {"commit": "a" * 40}
        (repo / "config/native-sources.json").write_text(json.dumps({"inputs": inputs}))
        (repo / "config/tool-source-notices.json").write_text(json.dumps({"sources": pins}))
        (originals / "component-specification.json").write_text(json.dumps({"schema_version": 1,
            "architecture": "arm64", "files": rows}))
        return repo, graph, originals

    def test_exact_native_tools_keep_source_and_license_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, graph, originals = self.fixture(root)
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="arm64")):
                result = stage(repo, graph, originals, root / "stage", "arm64")
            self.assertEqual(result["tools"], 3)
            self.assertFalse(result["acceptance_tested"])
            spec = json.loads((root / "stage/component-specification.json").read_text())
            ffmpeg = next(row for row in spec["files"] if row["identifier"] == "ffmpeg")
            self.assertEqual(ffmpeg["corresponding_source"], "ffmpeg-corresponding-source")
            self.assertEqual(ffmpeg["license_file"], "ffmpeg-license")
            self.assertTrue((root / "stage/engines/media/ffmpeg").stat().st_mode & 0o111)

    def test_changed_executable_and_wrong_architecture_fail_before_copying(self):
        for changed in (True, False):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo, graph, originals = self.fixture(root)
                if changed:
                    (graph / "ffmpeg").write_bytes(b"changed")
                with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="x86_64")):
                    with self.assertRaises(ValueError):
                        stage(repo, graph, originals, root / "stage", "arm64")
                self.assertFalse((root / "stage").exists())
