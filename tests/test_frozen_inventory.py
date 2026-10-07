import marshal
import unittest

from ksi_local.frozen_inventory import _data_table, _inflate


class FrozenInventoryTests(unittest.TestCase):
    def test_module_table_cannot_load_code_objects(self):
        data = marshal.dumps(compile("1 + 1", "synthetic", "eval"))
        with self.assertRaisesRegex(ValueError, "cannot contain code"):
            _data_table(data)

    def test_ordinary_module_records_are_read_without_code_execution(self):
        fixture = [("package.module", (0, 12, 10))]
        self.assertEqual(_data_table(marshal.dumps(fixture)), fixture)

    def test_cyclic_and_trailing_table_data_are_rejected(self):
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaises(ValueError):
            _data_table(marshal.dumps(cyclic))
        with self.assertRaises(ValueError):
            _data_table(marshal.dumps([]) + b"trailing")

    def test_truncated_compressed_content_is_rejected(self):
        import zlib
        content = zlib.compress(b"synthetic")
        self.assertEqual(_inflate(content, 9), b"synthetic")
        with self.assertRaises(ValueError):
            _inflate(content[:-1], 9)
        with self.assertRaises(ValueError):
            _inflate(content, 8)
