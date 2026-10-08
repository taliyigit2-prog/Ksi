import hashlib
import gzip
import io
import json
import tarfile
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from ksi_local.bundle_runtime import digest_file
from ksi_local.python_runtime_notices import stage_python_runtime_notices


class PythonRuntimeNoticeTests(unittest.TestCase):
    def test_stream_decoded_original_tar_preserves_the_same_member_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            archive, pin, _ = self.fixture(root)
            with patch("ksi_local.python_runtime_notices.subprocess.run") as host_tar:
                result = stage_python_runtime_notices(archive, pin, root / "stage",
                    original_tar=io.BytesIO(gzip.decompress(archive.read_bytes())))
            host_tar.assert_not_called()
            self.assertEqual(result["original_notices"], 2)

    def test_stream_decoded_link_or_duplicate_members_are_rejected_before_writes(self):
        for linked in (False, True):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                archive, pin, members = self.fixture(root)
                decoded = io.BytesIO()
                with tarfile.open(fileobj=decoded, mode="w") as stream:
                    for name, data in members.items():
                        item = tarfile.TarInfo(name)
                        item.size = len(data)
                        stream.addfile(item, io.BytesIO(data))
                    item = tarfile.TarInfo("python/PYTHON.json")
                    item.size = len(members[item.name])
                    if linked:
                        item.type = tarfile.SYMTYPE
                        item.linkname = "outside"
                    stream.addfile(item, None if linked else io.BytesIO(members[item.name]))
                decoded.seek(0)
                with self.assertRaises(ValueError):
                    stage_python_runtime_notices(archive, pin, root / "stage", original_tar=decoded)
                self.assertFalse((root / "stage").exists())

    def fixture(self, root, *, architecture="arm64", missing=False, static=False):
        metadata = {"python_version": "3.12.15", "target_triple": "aarch64-apple-darwin" if architecture == "arm64" else "x86_64-apple-darwin",
            "license_path": "licenses/LICENSE.cpython.txt", "build_info": {"extensions": {}}}
        if missing:
            for name in ("_sqlite3", "_tkinter", "binascii", "zlib"):
                metadata["build_info"]["extensions"][name] = [{"license_paths": ["licenses/LICENSE.zlib-ng.txt"],
                    "links": [{"name": "z", "path_static": "build/lib/libz.a"} if static else {"name": "z", "system": True}]}]
        members = {"python/PYTHON.json": json.dumps(metadata).encode(),
            "python/licenses/LICENSE.cpython.txt": b"Synthetic original grant fixture, not a real distribution license.\n"}
        archive = root / "original.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for name, data in members.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                stream.addfile(item, io.BytesIO(data))
        pin = {"architecture": architecture, "python_version": "3.12.15",
            "full_archive": {"url": "https://example.com/synthetic-original.tar.gz", "size": archive.stat().st_size, "sha256": digest_file(archive)},
            "install_only_archive": {"sha256": "a" * 64},
            "notices": [{"original_archive_member": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in members.items()],
            "missing_original_license_paths": ["licenses/LICENSE.zlib-ng.txt"] if missing else []}
        return archive, pin, members

    def test_exact_original_bytes_are_staged_on_both_architectures_without_approval(self):
        for architecture in ("arm64", "x86_64"):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                archive, pin, members = self.fixture(root, architecture=architecture)
                destination = root / "stage"
                result = stage_python_runtime_notices(archive, pin, destination)
                self.assertEqual(result["original_notices"], 2)
                self.assertFalse(result["final_binary_license_acceptance"])
                for name, data in members.items():
                    self.assertEqual((destination / "licenses/python-standalone" / name.removeprefix("python/")).read_bytes(), data)
                specification = json.loads((destination / "component-specification.json").read_bytes())
                self.assertEqual(specification["architecture"], architecture)
                self.assertEqual(len(specification["files"]), 3)
                for row in specification["files"]:
                    self.assertEqual(digest_file(destination / row["path"]), row["sha256"])

    def test_archive_digest_and_link_fail_before_tar_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            archive, pin, _ = self.fixture(root)
            bad = deepcopy(pin)
            bad["full_archive"]["sha256"] = "b" * 64
            alias = root / "alias.tar.gz"
            alias.symlink_to(archive)
            for path, definition in ((archive, bad), (alias, pin)):
                with self.subTest(linked=path == alias), patch("ksi_local.python_runtime_notices.subprocess.run") as command:
                    with self.assertRaises(ValueError):
                        stage_python_runtime_notices(path, definition, root / "output")
                    command.assert_not_called()
                    self.assertFalse((root / "output").exists())

    def test_changed_member_pin_or_foreign_processor_never_writes_stage(self):
        for mutation in ("digest", "size", "processor"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                archive, pin, _ = self.fixture(root)
                if mutation == "digest":
                    pin["notices"][0]["sha256"] = "c" * 64
                elif mutation == "size":
                    pin["notices"][0]["size"] += 1
                else:
                    pin["architecture"] = "x86_64"
                with self.assertRaises(ValueError):
                    stage_python_runtime_notices(archive, pin, root / "output")
                self.assertFalse((root / "output").exists())

    def test_unsafe_empty_duplicate_or_unreviewed_missing_notice_closes_stage(self):
        for mutation in ("escape", "empty", "duplicate", "unreviewed"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                archive, pin, _ = self.fixture(root)
                if mutation == "escape":
                    pin["notices"][0]["original_archive_member"] = "../escape"
                elif mutation == "empty":
                    pin["notices"][0]["size"] = 0
                elif mutation == "duplicate":
                    pin["notices"].append(pin["notices"][0])
                else:
                    pin["missing_original_license_paths"] = ["licenses/missing-grant.txt"]
                with self.assertRaises(ValueError):
                    stage_python_runtime_notices(archive, pin, root / "output")
                self.assertFalse((root / "output").exists())

    def test_system_z_alternative_remains_visible_but_static_provider_is_rejected(self):
        for static in (False, True):
            with self.subTest(static=static), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                archive, pin, _ = self.fixture(root, missing=True, static=static)
                output = root / "output"
                if static:
                    with self.assertRaisesRegex(ValueError, "static"):
                        stage_python_runtime_notices(archive, pin, output)
                    self.assertFalse(output.exists())
                else:
                    result = stage_python_runtime_notices(archive, pin, output)
                    self.assertEqual(result["missing_original_license_paths"], ["licenses/LICENSE.zlib-ng.txt"])
                    binding = json.loads((output / "licenses/python-standalone/original-distribution-binding.json").read_bytes())
                    self.assertEqual(len(binding["original_system_z_provider_references"]), 4)
                    self.assertFalse(binding["redistribution_review_complete"])

    def test_existing_linked_relative_or_invalid_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            archive, pin, _ = self.fixture(root)
            existing = root / "existing"
            existing.mkdir()
            (existing / "keep").write_text("keep")
            alias = root / "alias"
            alias.symlink_to(existing)
            for output in (existing, alias, Path("relative")):
                with self.subTest(output=output.name), patch("ksi_local.python_runtime_notices.subprocess.run") as command:
                    with self.assertRaises(ValueError):
                        stage_python_runtime_notices(archive, pin, output)
                    command.assert_not_called()
            self.assertEqual((existing / "keep").read_text(), "keep")

    def test_committed_original_pins_bind_the_exact_install_only_inputs(self):
        root = Path(__file__).resolve().parents[1]
        legal = json.loads((root / "config/python-runtime-notices.json").read_bytes())
        runtimes = json.loads((root / "config/runtime-sources.json").read_bytes())["inputs"]
        self.assertFalse(legal["redistribution_review_complete"])
        self.assertEqual(set(legal["inputs"]), {"arm64", "x86_64"})
        for architecture, pin in legal["inputs"].items():
            self.assertEqual(pin["install_only_archive"], runtimes["python-" + architecture])
            self.assertEqual(pin["release_tag"], "20261003")
            self.assertEqual(len(pin["notices"]), 21)
            self.assertEqual(len({row["original_archive_member"] for row in pin["notices"]}), 21)
