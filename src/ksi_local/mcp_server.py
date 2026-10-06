"""Dependency-free MCP stdio adapter for :mod:`ksi_local.core_service`."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from ksi_local.core_service import CoreService
from ksi_local.privacy import redact_sensitive_text
from ksi_local.settings import resolve_workspace


TOOLS: tuple[dict[str, Any], ...] = (
    {"name": "ksi_preflight", "description": "Inspect a source without downloading it.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "download_only": {"type": "boolean"}, "want_subtitle": {"type": "boolean"}, "want_summary": {"type": "boolean"}, "want_dub": {"type": "boolean"}, "job_kind": {"type": "string", "enum": ["video", "document"]}}, "required": ["source"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    {"name": "ksi_job_create", "description": "Create a queued local KSI job.", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "job_directory": {"type": "string"}, "source_language": {"type": "string"}, "want_subtitle": {"type": "boolean"}, "want_summary": {"type": "boolean"}, "want_dub": {"type": "boolean"}, "download_only": {"type": "boolean"}, "job_kind": {"type": "string", "enum": ["video", "document"]}}, "required": ["source", "job_directory"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "ksi_job_status", "description": "Read job and stage status.", "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    {"name": "ksi_job_stop", "description": "Stop a job without deleting sources or outputs.", "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["job_id", "confirm"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": True}},
    {"name": "ksi_job_resume", "description": "Queue a resumable stopped or failed job.", "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "ksi_results_list", "description": "List preserved artifacts inside a job directory.", "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False}},
    {"name": "ksi_export", "description": "Copy verified job artifacts to an allowed local destination.", "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}, "destination_directory": {"type": "string"}, "folder_name": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["job_id", "destination_directory", "confirm"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": False}},
)


def service_from_environment() -> CoreService:
    roots = tuple(item for item in os.environ.get("KSI_MCP_ROOTS", "").split(os.pathsep) if item)
    try:
        workspace = resolve_workspace()
    except RuntimeError:
        workspace = None
    return CoreService(
        workspace=workspace,
        allowed_roots=roots,
        network_allowed=os.environ.get("KSI_MCP_ALLOW_NETWORK") == "1",
    )


def call_tool(service: CoreService, name: str, arguments: dict[str, Any]) -> Any:
    if name == "ksi_preflight":
        return service.preflight(arguments.pop("source"), download_only=arguments.pop("download_only", False), want_subtitle=arguments.pop("want_subtitle", False), want_summary=arguments.pop("want_summary", False), want_dub=arguments.pop("want_dub", False), job_kind=arguments.pop("job_kind", "video"), **arguments)
    if name == "ksi_job_create":
        return service.create_job(**arguments)
    if name == "ksi_job_status":
        return service.status(**arguments)
    if name == "ksi_job_stop":
        return service.stop(**arguments)
    if name == "ksi_job_resume":
        return service.resume(**arguments)
    if name == "ksi_results_list":
        return service.results(**arguments)
    if name == "ksi_export":
        return service.export(**arguments)
    raise KeyError("Bilinmeyen MCP aracı.")


def _invalid_request(identifier: Any = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": -32600, "message": "Invalid Request"},
    }


def handle_message(service: CoreService, message: object) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _invalid_request()
    identifier = message.get("id")
    method = message.get("method")
    if identifier is None:
        return None
    try:
        if method == "initialize":
            params = message.get("params", {})
            if not isinstance(params, dict):
                return _invalid_request(identifier)
            requested = str(params.get("protocolVersion", "2024-11-05"))
            result = {"protocolVersion": requested, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "ksi-local-studio", "version": "2.0.0.dev0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": list(TOOLS)}
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict):
                return _invalid_request(identifier)
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return _invalid_request(identifier)
            payload = call_tool(service, str(params.get("name", "")), dict(arguments))
            result = {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                ],
                "isError": False,
            }
        else:
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}}
        return {"jsonrpc": "2.0", "id": identifier, "result": result}
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, PermissionError) as error:
        safe = redact_sensitive_text(str(error))[:2000]
        result = {"content": [{"type": "text", "text": safe}], "isError": True}
        return {"jsonrpc": "2.0", "id": identifier, "result": result}


def serve(input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
    service = service_from_environment()
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle_message(service, message)
        except (json.JSONDecodeError, TypeError, ValueError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
