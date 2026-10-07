import hashlib
import io
import runpy
import tarfile
import tempfile
import unittest
from pathlib import Path


collect = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/collect_source_license_texts.py"))["collect"]


class SourceLicenseTextTests(unittest.TestCase):
    def test_explicit_selection_excludes_fixture_licenses_and_requires_every_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar"
            with tarfile.open(archive, "w") as stream:
                for name in ("LICENSE", "tests/fixture/LICENSE", "THIRD_PARTY_LICENSES.txt"):
                    member = tarfile.TarInfo(name)
                    content = b"Synthetic notice, not a real license grant"
                    member.size = len(content)
                    stream.addfile(member, io.BytesIO(content))
            pin = hashlib.sha256(archive.read_bytes()).hexdigest()
            result = collect(archive, pin, root / "selected", members=("LICENSE", "THIRD_PARTY_LICENSES.txt"))
            self.assertEqual({row["path"] for row in result["files"]}, {"LICENSE", "THIRD_PARTY_LICENSES.txt"})
            for names in (("missing/LICENSE",), ("LICENSE", "LICENSE"), ("../LICENSE",)):
                destination = root / "rejected"
                with self.assertRaises(ValueError):
                    collect(archive, pin, destination, members=names)
                self.assertFalse(destination.exists())

    def test_images_named_license_are_not_mistaken_for_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                for name, data in (("source/LICENSE", b"Synthetic license"), ("source/COPYING.LIB", b"Synthetic legal-text fixture"), ("source/licensewizard.png", b"\x89PNG\xff"), ("source/program.py", b"raise RuntimeError()"), ("source/license_command.rs", b"fn main() {}"), ("source/license_test.ts", b"throw new Error('do not execute')"), ("source/libwinapi_oemlicense.a", b"!<arch>\n\x00\xff")):
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    stream.addfile(member, io.BytesIO(data))
            result = collect(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), root / "notices")
            self.assertEqual({row["path"] for row in result["files"]}, {"source/LICENSE", "source/COPYING.LIB"})
            self.assertFalse(result["redistribution_review_complete"])

    def test_escape_is_rejected_before_any_notice_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                member = tarfile.TarInfo("../LICENSE")
                member.size = 1
                stream.addfile(member, io.BytesIO(b"x"))
            destination = root / "notices"
            with self.assertRaises(ValueError):
                collect(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), destination)
            self.assertFalse(destination.exists())

    def archive(self, root):
        path = root / "fixture.tar"
        with tarfile.open(path, "w") as stream:
            for name in ("LICENSE", "ACKNOWLEDGMENTS.md", "unrelated.md"):
                data = b"Synthetic upstream text fixture, not a legal grant\n"
                member = tarfile.TarInfo(name)
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_original_legal_acknowledgement_requires_explicit_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, digest = self.archive(root)
            result = collect(archive, digest, root / "selected", members=("LICENSE", "ACKNOWLEDGMENTS.md"))
            self.assertEqual({row["path"] for row in result["files"]}, {"LICENSE", "ACKNOWLEDGMENTS.md"})
            result = collect(archive, digest, root / "default")
            self.assertEqual({row["path"] for row in result["files"]}, {"LICENSE"})

    def test_arbitrary_document_is_not_a_license_by_explicit_selection_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, digest = self.archive(root)
            with self.assertRaises(ValueError):
                collect(archive, digest, root / "unrelated", members=("unrelated.md",))
