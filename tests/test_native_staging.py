import tempfile
import unittest
from pathlib import Path

from ksi_local.native_staging import library_candidate, staged_dependency, system_dependency


class NativeStagingTests(unittest.TestCase):
    def test_final_graph_rejects_missing_external_and_escaping_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "graph"
            (root / "lib").mkdir(parents=True)
            library = root / "lib/libexample.dylib"
            library.write_bytes(b"fixture")
            self.assertEqual(staged_dependency("@loader_path/lib/libexample.dylib", origin=root / "tool", root=root), library.resolve())
            for value in ("@rpath/libexample.dylib", "@loader_path/lib/missing.dylib", "@loader_path/../../outside.dylib"):
                with self.subTest(value=value), self.assertRaises((ValueError, FileNotFoundError)):
                    staged_dependency(value, origin=root / "tool", root=root)

    def test_only_canonical_system_roots_are_exempt(self):
        self.assertTrue(system_dependency("/usr/lib/libSystem.B.dylib"))
        self.assertTrue(system_dependency("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"))
        self.assertFalse(system_dependency("/usr/lib/../../private/unsafe.dylib"))
        self.assertFalse(system_dependency("/usr/local/lib/library.dylib"))

    def test_vendor_absolute_reference_rebound_without_reading_vendor_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            (prefix / "lib").mkdir()
            library = prefix / "lib/libexample.1.dylib"
            library.write_bytes(b"fixture")
            result = library_candidate("/opt/vendor/lib/libexample.1.dylib", origin=prefix / "bin/tool", prefix=prefix)
            self.assertEqual(result, library)
            with self.assertRaises(ValueError):
                library_candidate("/opt/vendor/lib/unlisted.dylib", origin=prefix / "bin/tool", prefix=prefix)

    def test_internal_alias_is_materializable_but_external_link_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "prefix"
            (prefix / "lib").mkdir(parents=True)
            (prefix / "libcxx/lib").mkdir(parents=True)
            internal = prefix / "libcxx/lib/libexample.1.dylib"
            internal.write_bytes(b"fixture")
            alias = prefix / "lib/libexample.1.dylib"
            alias.symlink_to("../libcxx/lib/libexample.1.dylib")
            self.assertEqual(library_candidate("@rpath/libexample.1.dylib", origin=prefix / "bin/tool", prefix=prefix), alias)
            outside = Path(temporary) / "outside.dylib"
            outside.write_bytes(b"private")
            (prefix / "lib/outside.dylib").symlink_to(outside)
            with self.assertRaises(ValueError):
                library_candidate("@rpath/outside.dylib", origin=prefix / "bin/tool", prefix=prefix)

    def test_unknown_rpath_layout_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            for reference in ("@rpath/../../outside.dylib", "@executable_path/unknown.dylib", "relative.dylib"):
                with self.subTest(reference=reference), self.assertRaises(ValueError):
                    library_candidate(reference, origin=prefix / "bin/tool", prefix=prefix)
