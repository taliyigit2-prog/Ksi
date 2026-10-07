import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.distribution_notices import stage_distribution_notices, validate_notice_inventory, validate_piper_source_binding


class DistributionNoticeTests(unittest.TestCase):
    def test_piper_requires_its_primary_gpl_notice_and_both_matching_source_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows, pins = [], {}
            for name, role, identifier, content in (
                    ("engines/piper/python/COPYING", "license", "piper-engine-original-notice", b"Synthetic GPL fixture"),
                    ("licenses/tools/piper-corresponding/COPYING", "license", "original-copying", b"Synthetic GPL fixture"),
                    ("sources/piper.tar", "support", "piper-corresponding-source", b"Synthetic Piper source fixture"),
                    ("sources/espeak.tar", "support", "piper-espeak-source", b"Synthetic eSpeak source fixture")):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                row = {"path": name, "role": role, "identifier": identifier,
                    "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
                rows.append(row)
                if role == "support":
                    pins[identifier] = row["sha256"]
            validate_piper_source_binding(root, {"files": rows}, pins)
            with self.assertRaises(ValueError):
                validate_piper_source_binding(root, {"files": rows}, None)
            with self.assertRaises(ValueError):
                validate_piper_source_binding(root, {"files": rows[:-1]}, pins)
            with self.assertRaises(ValueError):
                validate_piper_source_binding(root, {"files": rows + [rows[0]]}, pins)
            (root / rows[0]["path"]).write_bytes(b"Synthetic nested dependency notice instead")
            with self.assertRaises(ValueError):
                validate_piper_source_binding(root, {"files": rows}, pins)

    def fixture(self, root, *, present=True):
        source = root / "original"
        source.mkdir()
        text = source / "LICENSE"
        text.write_text("Synthetic upstream notice fixture", encoding="utf-8")
        digest = hashlib.sha256(text.read_bytes()).hexdigest()
        wheel = {"name": "example", "version": "1.0", "filename": "example-1.0-py3-none-any.whl",
            "url": "https://files.pythonhosted.org/packages/example-1.0-py3-none-any.whl",
            "sha256": "1" * 64, "size": 100, "license": "MIT"}
        lock = {"schema_version": 1, "architecture": "arm64", "python": "3.12", "wheels": [wheel]}
        file = {"path": "LICENSE", "size": text.stat().st_size, "sha256": digest, "kind": "license"}
        package = {"name": "example", "version": "1.0", "wheel_sha256": wheel["sha256"],
            "source_url": wheel["url"], "files": [file] if present else []}
        report = {"schema_version": 1, "architecture": "arm64", "packages": [package]}
        supplement = {"example": {"version": "1.0", "files": [dict(file, root=source,
            target="licenses/source/example/LICENSE", source_url="https://example.org/LICENSE")]}}
        return source, text, lock, report, supplement

    def test_original_notices_are_independent_and_paths_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, original, lock, report, _ = self.fixture(root)
            destination = root / "distribution"
            result = stage_distribution_notices(lock, report, source, {}, destination)
            self.assertEqual(result["missing_license_texts"], [])
            self.assertFalse(result["redistribution_review_complete"])
            self.assertNotIn(str(source), json.dumps(result))
            copied = destination / result["files"][0]["path"]
            self.assertNotEqual(copied.stat().st_ino, original.stat().st_ino)
            copied.write_text("modified")
            self.assertEqual(original.read_text(), "Synthetic upstream notice fixture")

    def test_missing_text_requires_matching_version_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, lock, report, supplement = self.fixture(root, present=False)
            with self.assertRaises(ValueError):
                stage_distribution_notices(lock, report, source, {}, root / "missing")
            supplement["example"]["version"] = "2.0"
            with self.assertRaises(ValueError):
                stage_distribution_notices(lock, report, source, supplement, root / "wrong")
            supplement["example"]["version"] = "1.0"
            result = stage_distribution_notices(lock, report, source, supplement, root / "correct")
            self.assertEqual(len(result["packages"]), 1)

    def test_wrong_wheel_and_tampered_text_cannot_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, original, lock, report, _ = self.fixture(root)
            report["packages"][0]["wheel_sha256"] = "2" * 64
            with self.assertRaises(ValueError):
                stage_distribution_notices(lock, report, source, {}, root / "wrong")
            report["packages"][0]["wheel_sha256"] = "1" * 64
            original.write_text("changed notice")
            with self.assertRaises(ValueError):
                stage_distribution_notices(lock, report, source, {}, root / "tampered")

    def test_actual_app_must_seal_inventory_and_every_notice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, lock, report, _ = self.fixture(root)
            destination = root / "distribution"
            result = stage_distribution_notices(lock, report, source, {}, destination)
            inventory = destination / "python-notice-inventory.json"
            entry = {"path": inventory.name, "role": "support", "identifier": "python-notice-inventory-main",
                "sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(), "size": inventory.stat().st_size}
            spec = {"files": result["files"] + [entry]}
            validate_notice_inventory(destination, spec, lock, scope="main")
            with self.assertRaises(ValueError):
                validate_notice_inventory(destination, {"files": [entry]}, lock, scope="main")
            notice = destination / result["files"][0]["path"]
            notice.write_text("tampered")
            with self.assertRaises(ValueError):
                validate_notice_inventory(destination, spec, lock, scope="main")

    def test_existing_destination_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, lock, report, _ = self.fixture(root)
            destination = root / "existing"
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                stage_distribution_notices(lock, report, source, {}, destination)
