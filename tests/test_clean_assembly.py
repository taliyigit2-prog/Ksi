import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.app_assembly import bind_signed_tool_manifest, copy_clean_tree, normalize_build_shebangs


class CleanTreeTests(unittest.TestCase):
    def test_all_isolated_interpreter_scripts_lose_private_build_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            resources = root / "resources"
            engine = resources / "engines/piper/python/bin"
            engine.mkdir(parents=True)
            build = root / "generated-input"
            script = engine / "piper"
            script.write_text(f"#!{build}/python\nprint('public engine')\n")
            original = engine / "normal"
            original.write_text("#!/usr/bin/env python3\npass\n")
            normalize_build_shebangs(resources, (build,))
            self.assertEqual(script.read_text(), "#!/usr/bin/env python3.12\nprint('public engine')\n")
            self.assertEqual(original.read_text(), "#!/usr/bin/env python3\npass\n")

    def test_signed_tools_replace_only_binary_digests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "runtime/config"
            config.mkdir(parents=True)
            (root / "tools").mkdir()
            manifest = {"schema_version": 1, "tools": {"yt-dlp": {"version": "pinned", "sha256": "old"}, "deno": {"version": "pinned", "binary_sha256": "old", "archive_sha256": "original"}}}
            (config / "tool-manifest.json").write_text(json.dumps(manifest))
            rows = []
            for name in ("yt-dlp", "deno"):
                (root / "tools" / name).write_bytes(name.encode())
                rows.append({"role": "tool", "identifier": name, "path": "tools/" + name})
            bind_signed_tool_manifest(root, {"architecture": "arm64", "files": rows})
            result = json.loads((config / "tool-manifest.json").read_text())
            self.assertEqual(result["tools"]["yt-dlp"]["sha256"], hashlib.sha256(b"yt-dlp").hexdigest())
            self.assertEqual(result["tools"]["deno"]["binary_sha256"], hashlib.sha256(b"deno").hexdigest())
            self.assertEqual(result["tools"]["deno"]["archive_sha256"], "original")
            self.assertEqual(result["tools"]["deno"]["version"], "pinned")
            with self.assertRaises(ValueError):
                bind_signed_tool_manifest(root, {"architecture": "arm64", "files": rows + [rows[0]]})

    def test_native_manifest_does_not_keep_arm_archive_or_old_python_on_intel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "runtime/config"
            config.mkdir(parents=True)
            (root / "tools").mkdir()
            (config / "tool-manifest.json").write_text(json.dumps({"tools": {
                "deno": {"artifact": "arm.zip", "verification": {"apple_team_identifier": "historical"}},
                "yt-dlp": {"verification": {"gpg_signature_verified": True}},
                "python": {"architecture": "arm64", "version": "old"}}}))
            (root / "runtime/runtime-provenance.json").write_text(json.dumps({"python_version": "3.12.15", "python_source_sha256": "1" * 64}))
            rows = []
            for name in ("deno", "yt-dlp"):
                (root / "tools" / name).write_bytes(name.encode())
                rows.append({"identifier": name, "role": "tool", "path": "tools/" + name})
            source_inputs = {"deno-x86_64": {"url": "https://example.com/intel.zip", "sha256": "2" * 64, "version": "2.9.6"},
                "yt-dlp-macos": {"version": "2026.08.19", "sha256": "3" * 64}}
            bind_signed_tool_manifest(root, {"architecture": "x86_64", "files": rows}, source_inputs=source_inputs)
            result = json.loads((config / "tool-manifest.json").read_text())["tools"]
            self.assertEqual(result["deno"]["artifact"], "intel.zip")
            self.assertEqual(result["deno"]["archive_sha256"], "2" * 64)
            self.assertEqual(result["python"]["architecture"], "x86_64")
            self.assertEqual(result["python"]["version"], "3.12.15")
            self.assertNotIn("gpg_signature_verified", result["yt-dlp"]["verification"])
            self.assertNotIn("apple_team_identifier", result["deno"]["verification"])

    def test_internal_link_is_independent_regular_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            (source / "original.txt").write_text("original")
            (source / "alias.txt").symlink_to("original.txt")
            output = root / "output"
            copy_clean_tree(source, output)
            self.assertFalse((output / "alias.txt").is_symlink())
            self.assertNotEqual((source / "original.txt").stat().st_ino, (output / "alias.txt").stat().st_ino)
            (output / "alias.txt").write_text("changed")
            self.assertEqual((source / "original.txt").read_text(), "original")

    def test_external_link_and_cycle_rejected(self):
        for cyclic in (False, True):
            with self.subTest(cyclic=cyclic), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "input"
                source.mkdir()
                (root / "outside.txt").write_text("outside")
                (source / "link").symlink_to("." if cyclic else "../outside.txt")
                with self.assertRaises(ValueError):
                    copy_clean_tree(source, root / "output")

    def test_existing_destination_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            output.mkdir()
            (output / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                copy_clean_tree(source, output)
            self.assertEqual((output / "keep.txt").read_text(), "keep")

    def test_bytecode_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "__pycache__").mkdir()
            (source / "__pycache__/module.pyc").write_bytes(b"cache")
            (source / "module.py").write_text("pass\n")
            output = root / "output"
            copy_clean_tree(source, output)
            self.assertFalse((output / "__pycache__").exists())
            self.assertTrue((output / "module.py").is_file())
