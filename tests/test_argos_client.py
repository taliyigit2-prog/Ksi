"""The isolated translation boundary preserves IDs, whitespace and markers."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.argos_client import ArgosClient
from ksi_local.glossary import Glossary


class ArgosBoundaryTests(unittest.TestCase):
    def test_protected_terms_are_not_sent_to_the_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = ArgosClient.__new__(ArgosClient)
            client.packages = Path(temporary)
            captured = []

            def worker(argv, **options):
                request = json.loads(Path(argv[-2]).read_text(encoding="utf-8"))
                captured.extend(request["texts"])
                Path(argv[-1]).write_text(json.dumps({"ok": True, "texts": ["Merhaba"]}), encoding="utf-8")

            with patch("ksi_local.argos_client.run_engine", worker):
                result = json.loads(client.translate_items(
                    [{"id": "B000001", "text": " Hello KSI VTRTOKEN001X "}],
                    source_language="en", target_language="tr",
                    glossary=Glossary("en", (), ("KSI",)),
                ))
            self.assertEqual(captured, ["Hello"])
            self.assertEqual(result["translations"], [{"id": "B000001", "text": " Merhaba KSI VTRTOKEN001X "}])

    def test_entirely_protected_items_do_not_start_a_worker(self):
        client = ArgosClient.__new__(ArgosClient)
        with patch("ksi_local.argos_client.run_engine") as worker:
            result = json.loads(client.translate_items([{"id": 1, "text": "KSI"}],
                source_language="en", target_language="tr", glossary=Glossary("en", (), ("KSI",))))
        worker.assert_not_called()
        self.assertEqual(result["translations"][0]["text"], "KSI")
