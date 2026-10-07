import hashlib
import io
import json
import runpy
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path


repository = Path(__file__).resolve().parents[1]
stage = runpy.run_path(str(repository / "scripts/stage_cargo_source_notices.py"))["stage"]


def archive(path, files):
    with tarfile.open(path, "w") as stream:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CargoSourceNoticeTests(unittest.TestCase):
    def fixture(self, root, *, with_notice=True):
        repo, source, cache = root / "repo", root / "source", root / "cache"
        (repo / "scripts").mkdir(parents=True)
        (repo / "config").mkdir()
        source.mkdir()
        registry = cache / "registry/cache/public-registry"
        registry.mkdir(parents=True)
        shutil.copyfile(repository / "scripts/collect_source_license_texts.py", repo / "scripts/collect_source_license_texts.py")
        files = {"synthetic-1.0.0/Cargo.toml": b'[package]\nname="synthetic"\nversion="1.0.0"\nlicense="MIT"\n'}
        if with_notice:
            files["synthetic-1.0.0/LICENSE"] = b"Synthetic notice, no real grant"
        crate = registry / "synthetic-1.0.0.crate"
        checksum = archive(crate, files)
        lock = ('version = 4\n[[package]]\nname="synthetic"\nversion="1.0.0"\n'
                'source="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="' + checksum + '"\n').encode()
        (source / "Cargo.lock").write_bytes(lock)
        source_checksum = archive(source / "corresponding-source.tar", {"Cargo.lock": lock})
        (source / "ksi-source-provenance.json").write_text(json.dumps({"commit": "synthetic", "url": "https://example.org/public.git"}))
        (repo / "config/native-sources.json").write_text(json.dumps({"inputs": {"deno-source": {"commit": "synthetic", "url": "https://example.org/public.git"}}}))
        (repo / "config/tool-source-notices.json").write_text(json.dumps({"sources": {"deno-source": {"source_archive_sha256": source_checksum}}}))
        return repo, source, cache, crate

    def test_original_sources_and_notices_are_recorded_as_a_superset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, source, cache, _ = self.fixture(root)
            destination = root / "stage"
            result = stage(repo, source, cache, destination, "arm64")
            self.assertEqual(result["packages"], 1)
            self.assertEqual(result["missing_original_notice_texts"], [])
            self.assertFalse(result["shipped_binary_dependency_closure_complete"])
            self.assertFalse(result["redistribution_review_complete"])
            inventory = json.loads((destination / "licenses/cargo/workspace-source-inventory.json").read_text())
            self.assertEqual(inventory["coverage"], "locked-workspace-source-superset")
            self.assertEqual(inventory["packages"][0]["declared_license"], "MIT")

    def test_missing_original_license_is_reported_not_invented(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, source, cache, _ = self.fixture(root, with_notice=False)
            result = stage(repo, source, cache, root / "stage", "x86_64")
            self.assertEqual(result["missing_original_notice_texts"], ["synthetic@1.0.0"])

    def test_changed_archive_or_lock_fails_before_destination_creation(self):
        for change_lock in (False, True):
            with self.subTest(lock=change_lock), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo, source, cache, crate = self.fixture(root)
                (source / "Cargo.lock" if change_lock else crate).write_bytes(b"changed")
                destination = root / "stage"
                with self.assertRaises(ValueError):
                    stage(repo, source, cache, destination, "arm64")
                self.assertFalse(destination.exists())
