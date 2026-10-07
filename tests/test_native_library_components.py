import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


stage = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/stage_native_library_components.py"))["stage"]


class NativeLibraryComponentTests(unittest.TestCase):
    def fixture(self, root):
        notices, graph, cache = root / "notices", root / "graph", root / "sources"
        for path in (notices, graph, cache):
            path.mkdir()
        source = cache / "example-source.tar.gz"
        source.write_bytes(b"synthetic corresponding source fixture")
        pin = {"version": "1.0", "filename": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "size": source.stat().st_size}
        files = {}
        for path, content in {"example/info/licenses/LICENSE": b"Synthetic LGPL fixture", "example/info/recipe/build.patch": b"Original recipe patch",
                "example/info/recipe/meta.yaml": pin["sha256"].encode()}.items():
            target = notices / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            files[path] = {"path": path, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        binary = graph / "lib/example.dylib"
        binary.parent.mkdir()
        binary.write_bytes(b"synthetic native member")
        library = {"package": "example", "version": "1.0", "path": "lib/example.dylib", "size": binary.stat().st_size,
            "staged_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "declared_license": "LGPL-2.1-only",
            "archive_url": "https://example.org/package.conda", "archive_sha256": "1" * 64,
            "license_files": [files["example/info/licenses/LICENSE"]],
            "recipe_files": [row for name, row in files.items() if "/recipe/" in name]}
        inventory = {"missing_license_texts": [], "libraries": [library]}
        return {"architecture": "arm64"}, {"schema_version": 1, "sources": {"example": pin}}, notices, graph, cache, inventory

    def test_original_recipe_patches_and_source_are_bound_to_shared_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, sources, notices, graph, cache, inventory = self.fixture(root)
            destination = root / "output"
            with patch.dict(stage.__globals__, {"inventory_native_attribution": lambda *args, **kwargs: inventory}):
                result = stage(lock, sources, notices, graph, cache, destination)
            spec = json.loads((destination / "component-specification.json").read_text())
            library = next(row for row in spec["files"] if row["path"].endswith("example.dylib"))
            self.assertEqual(library["role"], "support")
            self.assertEqual(library["corresponding_source"], "native-source-example")
            self.assertTrue(any(row["path"].endswith("build.patch") for row in spec["files"]))
            self.assertFalse(result["redistribution_review_complete"])
            self.assertFalse(result["acceptance_tested"])

    def test_unbound_source_digest_fails_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, sources, notices, graph, cache, inventory = self.fixture(root)
            sources["sources"]["example"]["sha256"] = "0" * 64
            with patch.dict(stage.__globals__, {"inventory_native_attribution": lambda *args, **kwargs: inventory}):
                with self.assertRaises(ValueError):
                    stage(lock, sources, notices, graph, cache, root / "output")
            self.assertFalse((root / "output").exists())

    def test_wrong_source_version_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, sources, notices, graph, cache, inventory = self.fixture(root)
            sources["sources"]["example"]["version"] = "2.0"
            with patch.dict(stage.__globals__, {"inventory_native_attribution": lambda *args, **kwargs: inventory}):
                with self.assertRaises(ValueError):
                    stage(lock, sources, notices, graph, cache, root / "output")

    def test_changed_original_source_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock, sources, notices, graph, cache, inventory = self.fixture(root)
            (cache / "example-source.tar.gz").write_bytes(b"changed")
            with patch.dict(stage.__globals__, {"inventory_native_attribution": lambda *args, **kwargs: inventory}):
                with self.assertRaises(ValueError):
                    stage(lock, sources, notices, graph, cache, root / "output")
