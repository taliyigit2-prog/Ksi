import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.native_attribution import inventory_native_attribution


class NativeAttributionTests(unittest.TestCase):
    def fixture(self, root, *, license_present=True, owner=True):
        notices, graph = root / "notices", root / "graph"
        notices.mkdir()
        (graph / "lib").mkdir(parents=True)
        binary = graph / "lib/example.dylib"
        binary.write_bytes(b"synthetic library fixture")
        filename = "example-1.0-0.conda"
        package = {"name": "example", "version": "1.0", "filename": filename,
            "url": "https://conda.anaconda.org/conda-forge/osx-arm64/" + filename,
            "sha256": "1" * 64, "md5": "2" * 32, "size": 100, "license": "MIT"}
        lock = {"schema_version": 1, "architecture": "arm64", "packages": [package]}
        files = []
        data = {"example/info/paths.json": json.dumps({"paths_version": 1,
            "paths": [{"_path": "lib/example.dylib"}] if owner else []}).encode()}
        if license_present:
            data["example/info/licenses/LICENSE"] = b"Synthetic original license"
        for name, content in data.items():
            path = notices / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            files.append({"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        receipt = {"schema_version": 1, "architecture": "arm64", "packages": [{
            "name": "example", "version": "1.0", "archive_url": package["url"],
            "archive_sha256": package["sha256"], "license_declared": "MIT", "files": files}]}
        (notices / "native-notices.json").write_text(json.dumps(receipt))
        graph_receipt = {"schema_version": 1, "files": [{"path": "lib/example.dylib",
            "size": binary.stat().st_size, "staged_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}]}
        (graph / "native-staging.json").write_text(json.dumps(graph_receipt))
        return lock, notices, graph

    def test_exact_archive_paths_determine_owner_not_guessed_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, notices, graph = self.fixture(root)
            result = inventory_native_attribution(lock, notices, graph)
            self.assertEqual(result["libraries"][0]["package"], "example")
            self.assertEqual(result["missing_license_texts"], [])
            self.assertFalse(result["corresponding_sources_complete"])
            self.assertFalse(result["redistribution_review_complete"])
            self.assertNotIn(str(root), json.dumps(result))

    def test_missing_original_license_is_reported_not_approved(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock, notices, graph = self.fixture(Path(temporary), license_present=False)
            result = inventory_native_attribution(lock, notices, graph)
            self.assertEqual(result["missing_license_texts"], ["example"])

    def test_unowned_library_cannot_enter_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock, notices, graph = self.fixture(Path(temporary), owner=False)
            with self.assertRaises(ValueError):
                inventory_native_attribution(lock, notices, graph)

    def test_tampered_graph_or_original_notice_is_rejected(self):
        for target in ("binary", "notice"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                lock, notices, graph = self.fixture(Path(temporary))
                path = graph / "lib/example.dylib" if target == "binary" else notices / "example/info/licenses/LICENSE"
                path.write_bytes(b"modified")
                with self.assertRaises(ValueError):
                    inventory_native_attribution(lock, notices, graph)

    def test_another_architecture_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock, notices, graph = self.fixture(Path(temporary))
            path = notices / "native-notices.json"
            receipt = json.loads(path.read_text())
            receipt["architecture"] = "x86_64"
            path.write_text(json.dumps(receipt))
            with self.assertRaises(ValueError):
                inventory_native_attribution(lock, notices, graph)
