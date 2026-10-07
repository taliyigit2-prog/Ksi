from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.cli import build_parser
from ksi_local.core_service import CoreService
from ksi_local.job_store import JobStore
from ksi_local.mcp_server import TOOLS, handle_message


class Phase35Tests(unittest.TestCase):
    def make_service(self, root: Path) -> CoreService:
        return CoreService(store=JobStore(root / "state" / "jobs.sqlite3"), allowed_roots=(root,))

    def test_core_create_status_stop_resume_and_results_share_one_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
            service = self.make_service(root)
            created = service.create_job(source=str(source), job_directory=root / "jobs" / "one", want_summary=True)
            identifier = created["id"]
            artifact = root / "jobs" / "one" / "outputs" / "summary.md"
            artifact.parent.mkdir()
            artifact.write_text("done")
            self.assertEqual(service.status(identifier)["status"], "queued")
            self.assertEqual(service.results(identifier)[0]["relative_path"], "outputs/summary.md")
            with self.assertRaises(PermissionError):
                service.stop(identifier, confirm=False)
            self.assertEqual(service.stop(identifier, confirm=True)["status"], "cancelled")
            self.assertEqual(service.resume(identifier)["status"], "queued")
            self.assertTrue(source.exists())
            self.assertTrue(artifact.exists())

    def test_confirmation_is_a_real_boolean_not_a_truthy_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("safe")
            service = self.make_service(root)
            record = service.create_job(source=str(source), job_directory=root / "jobs/one")
            for value in ("false", "true", 1, [], {}):
                with self.subTest(value=value), self.assertRaises(PermissionError):
                    service.stop(record["id"], confirm=value)
            self.assertEqual(service.status(record["id"])["status"], "queued")

    def test_other_root_cannot_read_or_mutate_private_job_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("safe")
            service = self.make_service(root)
            record = service.create_job(source=str(source), job_directory=root / "jobs/private")
            limited = CoreService(store=service.store, allowed_roots=(root / "unrelated",))
            for action in (lambda: limited.status(record["id"]),
                           lambda: limited.stop(record["id"], confirm=True),
                           lambda: limited.resume(record["id"])):
                with self.assertRaises(PermissionError):
                    action()

    def test_core_enforces_roots_network_and_new_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("safe")
            service = self.make_service(root)
            with self.assertRaises(PermissionError):
                service.create_job(source="https://example.com/watch?token=secret", job_directory=root / "job")
            with self.assertRaises(PermissionError):
                service.create_job(source="/etc/hosts", job_directory=root / "job")
            occupied = root / "occupied"
            occupied.mkdir()
            (occupied / "keep.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                service.create_job(source=str(source), job_directory=occupied)
            self.assertEqual((occupied / "keep.txt").read_text(), "keep")

    def test_mcp_advertises_required_tools_and_annotations(self) -> None:
        names = {item["name"] for item in TOOLS}
        self.assertEqual(names, {"ksi_preflight", "ksi_job_create", "ksi_job_status", "ksi_job_stop", "ksi_job_resume", "ksi_results_list", "ksi_export", "ksi_tool_job_execute", "ksi_image_tool_submit", "ksi_media_tool_submit"})
        stop = next(item for item in TOOLS if item["name"] == "ksi_job_stop")
        self.assertTrue(stop["annotations"]["destructiveHint"])
        self.assertIn("confirm", stop["inputSchema"]["required"])

    def test_mcp_initialize_list_and_error_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            initialized = handle_message(service, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
            self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
            listed = handle_message(service, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            self.assertEqual(len(listed["result"]["tools"]), len(TOOLS))
            self.assertIn("ksi_tool_job_execute", {tool["name"] for tool in TOOLS})
            failed = handle_message(service, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "ksi_job_create", "arguments": {"source": "https://example.com/x?token=secret", "job_directory": str(Path(directory) / "job")}}})
            serialized = json.dumps(failed, ensure_ascii=False)
            self.assertTrue(failed["result"]["isError"])
            self.assertNotIn("secret", serialized)

    def test_mcp_rejects_malformed_json_shapes_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            malformed_messages = (
                [],
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": []},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": []},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "ksi_job_status", "arguments": {"job_id": "x", "extra": True}},
                },
            )
            for message in malformed_messages:
                with self.subTest(message=message):
                    response = handle_message(service, message)  # type: ignore[arg-type]
                    self.assertIsInstance(response, dict)
                    self.assertTrue(
                        "error" in response or response.get("result", {}).get("isError") is True
                    )

    def test_cli_keeps_json_core_and_mcp_entry_points(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["mcp-server"]).command, "mcp-server")
        status = parser.parse_args(["core-job-status", "job-1", "--allow-root", "/tmp"])
        self.assertEqual(status.core_action, "status")
        creative = parser.parse_args(["lab-generate", "svg", "/tmp/a.svg", "--seed", "7"])
        self.assertEqual(creative.lab_kind, "svg")


if __name__ == "__main__":
    unittest.main()
