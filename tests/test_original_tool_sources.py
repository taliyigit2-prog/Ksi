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
stage = runpy.run_path(str(repository / "scripts/stage_original_tool_sources.py"))["stage"]


class OriginalToolSourceTests(unittest.TestCase):
    def fixture(self, root):
        repo, sources = root / "repo", root / "sources"
        (repo / "scripts").mkdir(parents=True)
        (repo / "config").mkdir()
        shutil.copyfile(repository / "scripts/collect_source_license_texts.py", repo / "scripts/collect_source_license_texts.py")
        origin = sources / "synthetic-notices-pristine"
        origin.mkdir(parents=True)
        archive = origin / "corresponding-source.tar"
        with tarfile.open(archive, "w") as stream:
            for name in ("LICENSE", "tests/fixture/LICENSE"):
                member = tarfile.TarInfo(name)
                content = b"Synthetic legal-text fixture, not a real license"
                member.size = len(content)
                stream.addfile(member, io.BytesIO(content))
        pin = hashlib.sha256(archive.read_bytes()).hexdigest()
        provenance = {"schema_version": 1, "commit": "synthetic-commit", "url": "https://example.org/public.git", "source_archive_sha256": pin}
        (origin / "ksi-source-provenance.json").write_text(json.dumps(provenance))
        (repo / "config/native-sources.json").write_text(json.dumps({"inputs": {"synthetic-source": {
            "commit": provenance["commit"], "url": provenance["url"], "version": "synthetic"}}}))
        (repo / "config/tool-source-notices.json").write_text(json.dumps({"schema_version": 1, "redistribution_review_complete": False,
            "sources": {"synthetic-source": {"source_archive_sha256": pin, "members": ["LICENSE"], "pending_closure": ["synthetic dependency"]}}}))
        return repo, sources, origin

    def test_original_notice_and_archive_are_retained_without_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, sources, origin = self.fixture(root)
            destination = root / "stage"
            result = stage(repo, sources, destination, "arm64")
            self.assertFalse(result["redistribution_review_complete"])
            self.assertFalse(result["acceptance_tested"])
            spec = json.loads((destination / "component-specification.json").read_text())
            self.assertEqual(len(spec["files"]), 4)
            self.assertFalse((destination / "licenses/tools/synthetic/tests").exists())
            copied = destination / "sources/tools/synthetic/corresponding-source.tar"
            self.assertEqual(copied.read_bytes(), (origin / "corresponding-source.tar").read_bytes())
            self.assertNotEqual(copied.stat().st_ino, (origin / "corresponding-source.tar").stat().st_ino)

    def test_changed_source_and_revision_are_rejected_before_writing(self):
        for mutate_archive in (True, False):
            with self.subTest(archive=mutate_archive), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repo, sources, origin = self.fixture(root)
                if mutate_archive:
                    (origin / "corresponding-source.tar").write_bytes(b"changed")
                else:
                    record = json.loads((origin / "ksi-source-provenance.json").read_text())
                    record["commit"] = "changed"
                    (origin / "ksi-source-provenance.json").write_text(json.dumps(record))
                destination = root / "rejected"
                with self.assertRaises(ValueError):
                    stage(repo, sources, destination, "arm64")
                self.assertFalse(destination.exists())
