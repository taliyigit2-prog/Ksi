from __future__ import annotations

import tempfile
import errno
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ksi_local.atomic_files import atomic_write_bytes
from ksi_local.job_store import JobStore
from ksi_local.reliability_audit import audit_policy_consistency, run_safe_fault_probes


class PhaseThirtyNineReliabilityTests(unittest.TestCase):
    def test_product_decisions_have_no_runtime_contradiction(self) -> None:
        self.assertEqual(audit_policy_consistency(), ())

    def test_safe_fault_probe_preserves_sources_and_blocks_remote_dns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_safe_fault_probes(directory)
        self.assertTrue(report.passed, report.findings)
        self.assertTrue(all(report.checks.values()))
        self.assertTrue(report.checks["remote_dns_blocked"])
        self.assertTrue(report.checks["unrelated_partial_is_not_deleted"])

    def test_disk_full_and_read_only_failures_preserve_published_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "result.bin"
            target.write_bytes(b"completed-output")
            with patch("ksi_local.atomic_files.os.replace", side_effect=OSError(errno.ENOSPC, "full")):
                with self.assertRaises(OSError):
                    atomic_write_bytes(target, b"replacement")
            self.assertEqual(target.read_bytes(), b"completed-output")
            self.assertEqual(list(root.glob("*.part")), [])
            with patch("ksi_local.atomic_files.tempfile.mkstemp", side_effect=PermissionError("read only")):
                with self.assertRaises(PermissionError):
                    atomic_write_bytes(target, b"replacement")
            self.assertEqual(target.read_bytes(), b"completed-output")

    def test_concurrent_claims_are_unique_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            store = JobStore(database)
            for index in range(12):
                store.create_job(
                    job_id=f"job-{index:02d}",
                    source=str(Path(directory) / f"source-{index}.mp4"),
                    source_language="en",
                    want_subtitle=True,
                    want_summary=False,
                    want_dub=False,
                    job_directory=Path(directory) / f"job-{index:02d}",
                )

            def claim_all() -> list[str]:
                claimed: list[str] = []
                local = JobStore(database)
                while job := local.claim_next_job(workspace_available=True):
                    claimed.append(job.id)
                return claimed

            with ThreadPoolExecutor(max_workers=4) as pool:
                groups = list(pool.map(lambda _index: claim_all(), range(4)))
            identifiers = [identifier for group in groups for identifier in group]
            self.assertEqual(len(identifiers), 12)
            self.assertEqual(len(set(identifiers)), 12)


if __name__ == "__main__":
    unittest.main()
