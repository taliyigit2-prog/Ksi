import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ksi_local.bundle_runtime import digest_file
from ksi_local.native_build import build_whisper_cpu, extract_oxipng, fetch_git_source


class NativeBuildTests(unittest.TestCase):
    def archive(self, root, names):
        archive = root / "engine.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for name in names:
                entry = tarfile.TarInfo(name)
                entry.size = 7
                stream.addfile(entry, io.BytesIO(b"fixture"))
        return archive

    def test_archive_checks_hash_and_preserves_license(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.archive(root, ["release/oxipng", "release/LICENSE"])
            binary = extract_oxipng(archive, sha256=digest_file(archive), destination=root / "output")
            self.assertEqual(binary.read_bytes(), b"fixture")
            self.assertTrue((binary.parent / "LICENSE").is_file())
            with self.assertRaises(ValueError):
                extract_oxipng(archive, sha256="a" * 64, destination=root / "another")

    def test_archive_path_escape_rejected_before_extract(self):
        for name in ("../escape", "/root/escape", "release/../escape", "a/b/c"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = self.archive(root, [name])
                with self.assertRaises(ValueError):
                    extract_oxipng(archive, sha256=digest_file(archive), destination=root / "output")
                self.assertFalse((root / "output").exists())

    def test_source_fetch_rejects_unsafe_identity_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "source"
            with self.assertRaises(ValueError):
                fetch_git_source("http://github.com/example/repository.git", tag="v1", commit="a" * 40, destination=destination)
            with self.assertRaises(ValueError):
                fetch_git_source("https://github.com/example/repository.git", tag="--unsafe", commit="a" * 40, destination=destination)
            self.assertFalse(destination.exists())

    def test_changed_source_fails_before_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            file = source / "CMakeLists.txt"
            file.write_text("original")
            commit = "a" * 40
            (source / "ksi-source-provenance.json").write_text(json.dumps({"commit": commit, "files": [{"path": file.name, "sha256": digest_file(file)}]}))
            file.write_text("changed")
            cmake = root / "cmake"
            cmake.write_text("unused")
            with self.assertRaises(ValueError):
                build_whisper_cpu(source, cmake=cmake, destination=root / "build", commit=commit)
            self.assertFalse((root / "build").exists())
