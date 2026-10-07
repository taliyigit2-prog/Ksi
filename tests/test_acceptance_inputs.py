"""All input archives and payloads here are synthetic and non-executable."""
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.acceptance_inputs import import_inputs
from ksi_local.model_input_restore import restore_model_inputs


class AcceptanceInputTests(unittest.TestCase):
    def fixture(self, root, *, extra=None, inventory_fault=None, defer=False, identifier="example"):
        transport = root / "transport"
        transport.mkdir()
        model = b"synthetic model input"
        row = dict(path="models/example", role="model", identifier=identifier, size=len(model),
                   sha256=hashlib.sha256(model).hexdigest())
        rows = [row]
        if inventory_fault == "digest":
            row["sha256"] = "b" * 64
        elif inventory_fault == "boolean-size":
            row["size"] = True
        elif inventory_fault == "duplicate":
            rows.append(dict(row))
        elif inventory_fault == "non-object":
            rows.append(None)
        provenance = json.dumps(dict(architecture="x86_64", main_runtime_wheel_lock_sha256="a"*64)).encode()
        files = {
            "components/component-specification.json": json.dumps(dict(architecture="x86_64", files=rows)).encode(),
            "components/models/example": model,
            "runtime/runtime-provenance.json": json.dumps(dict(architecture="x86_64", wheel_lock_sha256="a"*64)).encode(),
            "input-provenance.json": provenance,
        }
        if defer:
            files.pop("components/models/example")
            files["deferred-model-inputs.json"] = json.dumps(dict(schema_version=1, architecture="x86_64", files=[row])).encode()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(content))
            if extra is not None:
                member, content = extra
                archive.addfile(member, io.BytesIO(content) if content else None)
        content = buffer.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        filename = "native-intel-inputs.tar.gz.part001"
        (transport / filename).write_bytes(content)
        (transport / "transfer.json").write_text(json.dumps(dict(schema_version=1, architecture="x86_64",
            archive_format="tar.gz", archive_bytes=len(content), archive_sha256=digest,
            input_provenance_sha256=hashlib.sha256(provenance).hexdigest(),
            files=[dict(filename=filename, size=len(content), sha256=digest)])))
        return transport, digest

    def test_valid_reviewed_inputs_never_claim_native_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport, digest = self.fixture(root)
            result = import_inputs(transport, root / "new", digest, architecture="x86_64")
            self.assertFalse(result["native_acceptance_performed"])
            self.assertEqual((root / "new/components/models/example").read_bytes(), b"synthetic model input")

    def test_changed_part_or_wrong_out_of_band_digest_preserves_destination(self):
        for kind in ("part", "reviewed-digest"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                transport, digest = self.fixture(root)
                if kind == "part":
                    (transport / "native-intel-inputs.tar.gz.part001").write_bytes(b"changed")
                else:
                    digest = "b"*64
                with self.assertRaises(ValueError):
                    import_inputs(transport, root / "new", digest, architecture="x86_64")
                self.assertFalse((root / "new").exists())

    def test_existing_user_directory_and_contents_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport, digest = self.fixture(root)
            destination = root / "original"
            destination.mkdir()
            (destination / "keep").write_bytes(b"original")
            with self.assertRaises(ValueError):
                import_inputs(transport, destination, digest, architecture="x86_64")
            self.assertEqual((destination / "keep").read_bytes(), b"original")

    def test_archive_paths_links_case_duplicates_and_owner_metadata_are_rejected(self):
        for kind in ("traversal", "absolute", "symlink", "duplicate", "owner", "unrelated-root"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                member = tarfile.TarInfo("runtime/extra")
                if kind == "traversal":
                    member.name = "runtime/../../outside"
                elif kind == "absolute":
                    member.name = "/outside"
                elif kind == "symlink":
                    member.type, member.linkname = tarfile.SYMTYPE, "../../outside"
                elif kind == "duplicate":
                    member.name = "RUNTIME/runtime-provenance.json"
                elif kind == "owner":
                    member.uname = "synthetic-owner"
                else:
                    member.name = "unapproved/extra"
                transport, digest = self.fixture(root, extra=(member, None))
                with self.assertRaises(ValueError):
                    import_inputs(transport, root / "new", digest, architecture="x86_64")
                self.assertFalse((root / "new").exists())
                self.assertFalse((root / "outside").exists())

    def test_other_architecture_is_not_accepted_as_native_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport, digest = self.fixture(root)
            with self.assertRaises(ValueError):
                import_inputs(transport, root / "new", digest, architecture="arm64")

    def test_invalid_component_rows_never_promote_payload(self):
        for fault in ("digest", "boolean-size", "duplicate", "non-object"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                transport, digest = self.fixture(root, inventory_fault=fault)
                with self.assertRaises(ValueError):
                    import_inputs(transport, root / "new", digest, architecture="x86_64")
                self.assertFalse((root / "new").exists())

    def test_deferred_model_import_is_an_explicit_incomplete_build_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport, digest = self.fixture(root, defer=True)
            with self.assertRaises(ValueError):
                import_inputs(transport, root / "denied", digest, architecture="x86_64")
            self.assertFalse((root / "denied").exists())
            result = import_inputs(transport, root / "allowed", digest, architecture="x86_64", allow_deferred=True)
            self.assertEqual(result["deferred_model_files"], 1)
            self.assertFalse(result["native_acceptance_performed"])
            self.assertFalse((root / "allowed/components/models/example").exists())

    def test_restoration_requires_network_opt_in_and_verifies_restored_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport, digest = self.fixture(root, defer=True)
            imported = root / "imported"
            import_inputs(transport, imported, digest, architecture="x86_64", allow_deferred=True)
            content = b"synthetic model input"
            pins = {"inputs": {"example": dict(size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    url="https://huggingface.co/synthetic/fixture/resolve/" + "a"*40 + "/model.bin")}}
            def generated_input(_pin, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            with patch("ksi_local.model_input_restore.fetch_pinned_input", side_effect=generated_input) as fetch:
                with self.assertRaises(ValueError):
                    restore_model_inputs(imported, pins, {"models": []})
                fetch.assert_not_called()
                result = restore_model_inputs(imported, pins, {"models": []}, allow_network=True)
                self.assertEqual(result["restored_model_files"], 1)
                self.assertFalse(result["native_acceptance_performed"])

    def test_unknown_or_unofficial_restore_pins_are_rejected_before_download(self):
        for fault in ("unknown", "digest", "unofficial", "credentials"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                transport, digest = self.fixture(root, defer=True)
                imported = root / "imported"
                import_inputs(transport, imported, digest, architecture="x86_64", allow_deferred=True)
                content = b"synthetic model input"
                row = dict(size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                           url="https://huggingface.co/synthetic/fixture/resolve/" + "a"*40 + "/model.bin")
                if fault == "digest":
                    row["sha256"] = "b"*64
                elif fault == "unofficial":
                    row["url"] = "https://example.invalid/model.bin"
                elif fault == "credentials":
                    row["url"] = "https://user:pass@huggingface.co/model.bin"
                pins = {"inputs": {} if fault == "unknown" else {"example": row}}
                with patch("ksi_local.model_input_restore.fetch_pinned_input") as fetch:
                    with self.assertRaises(ValueError):
                        restore_model_inputs(imported, pins, {"models": []}, allow_network=True)
                    fetch.assert_not_called()

    def test_ollama_blob_url_is_derived_only_from_committed_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transport, digest = self.fixture(root, defer=True, identifier="example-model")
            imported = root / "imported"
            import_inputs(transport, imported, digest, architecture="x86_64", allow_deferred=True)
            content = b"synthetic model input"
            sha = hashlib.sha256(content).hexdigest()
            sources = {"models": [dict(name="example", layers=[dict(role="model", size=len(content), sha256=sha)])]}
            def generated_input(pin, target):
                self.assertEqual(pin["url"], "https://registry.ollama.ai/v2/library/example/blobs/sha256:" + sha)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            with patch("ksi_local.model_input_restore.fetch_pinned_input", side_effect=generated_input):
                self.assertEqual(restore_model_inputs(imported, {"inputs": {}}, sources, allow_network=True)["restored_model_files"], 1)
