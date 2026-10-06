"""Versioned JSONL protocol shared by workers, the queue and the GUI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ksi_local.privacy import redact_sensitive_text


PROTOCOL_VERSION = 1
EVENT_TYPES = {"started", "progress", "language_detected", "artifact", "completed", "error", "log"}


@dataclass(frozen=True)
class WorkerEvent:
    event: str
    stage: str
    completed: int | None = None
    total: int | None = None
    message: str | None = None
    payload: dict[str, Any] | None = None


def parse_worker_event(line: str) -> WorkerEvent:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError("İşçi mesajı geçerli JSON değil.") from error
    if not isinstance(data, dict):
        raise ValueError("İşçi mesajı bir JSON nesnesi olmalıdır.")
    if data.get("version", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        raise ValueError("İşçi protokol sürümü desteklenmiyor.")
    event = data.get("event")
    stage = data.get("stage")
    if event not in EVENT_TYPES or not isinstance(stage, str) or not stage:
        raise ValueError("İşçi mesajında geçerli event ve stage alanları bulunmalıdır.")
    completed = data.get("completed")
    total = data.get("total")
    if event == "progress":
        if not isinstance(completed, int) or not isinstance(total, int):
            raise ValueError("İlerleme mesajında completed ve total tamsayı olmalıdır.")
        if completed < 0 or total < 1 or completed > total:
            raise ValueError("İşçi ilerleme değerleri geçersiz.")
    message = data.get("message")
    safe_message = redact_sensitive_text(message) if isinstance(message, str) else None
    reserved = {"version", "event", "stage", "completed", "total", "message"}
    payload = {key: value for key, value in data.items() if key not in reserved}
    return WorkerEvent(event, stage, completed, total, safe_message, payload or None)


def encode_worker_event(event: WorkerEvent) -> str:
    data: dict[str, Any] = {
        "version": PROTOCOL_VERSION,
        "event": event.event,
        "stage": event.stage,
    }
    if event.completed is not None:
        data["completed"] = event.completed
    if event.total is not None:
        data["total"] = event.total
    if event.message is not None:
        data["message"] = redact_sensitive_text(event.message)
    if event.payload:
        data.update(event.payload)
    # One event is always exactly one physical line.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class JSONLBuffer:
    """Reassemble complete lines from arbitrarily split process-output chunks."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        return [line for line in lines if line]

    def flush(self) -> list[str]:
        if not self._buffer:
            return []
        line, self._buffer = self._buffer, ""
        return [line]
