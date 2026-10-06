"""Resource-bounded local-only process for untrusted document inspection."""

from __future__ import annotations

import argparse
import json
import resource
import sys
from pathlib import Path

from ksi_local.network_policy import local_only_socket_guard
from ksi_local.worker_protocol import WorkerEvent, encode_worker_event


def _limits(*, extraction: bool = False) -> None:
    def lower_soft_limit(kind: int, requested: int) -> None:
        soft, hard = resource.getrlimit(kind)
        target = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
        if soft != resource.RLIM_INFINITY:
            target = min(target, soft)
        try:
            resource.setrlimit(kind, (target, hard))
        except (OSError, ValueError):
            # The parent still enforces a wall-clock timeout. Some macOS builds
            # refuse lowering address space after Python shared libraries load.
            pass

    lower_soft_limit(resource.RLIMIT_CPU, 3_600 if extraction else 20)
    lower_soft_limit(resource.RLIMIT_NOFILE, 64)
    if hasattr(resource, "RLIMIT_AS"):
        memory_limit = 2_048 * 1024**2 if extraction else 1536 * 1024**2
        lower_soft_limit(resource.RLIMIT_AS, memory_limit)


def _emit(event: WorkerEvent) -> None:
    print(encode_worker_event(event), flush=True)


def _inspect(path: str) -> int:
    try:
        _limits()
        with local_only_socket_guard():
            from ksi_local.document_security import inspect_document

            result = inspect_document(Path(path))
    except Exception as error:
        message = (
            str(error)
            if isinstance(error, (OSError, RuntimeError, ValueError))
            else "Belge güvenlik işçisi beklenmeyen bir yapıyı güvenle reddetti."
        )
        print(json.dumps({"error": message}, ensure_ascii=False))
        return 1
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0


def _extract(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ksi_local.document_worker extract")
    parser.add_argument("source")
    parser.add_argument("output_directory")
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--ocr-helper")
    options = parser.parse_args(arguments)
    try:
        _limits(extraction=True)
        with local_only_socket_guard():
            from ksi_local.document_extraction import extract_document

            _emit(WorkerEvent("started", "document_extract"))

            def progress(completed: int, total: int) -> None:
                _emit(
                    WorkerEvent(
                        "progress",
                        "document_extract",
                        completed=completed,
                        total=total,
                    )
                )

            result = extract_document(
                options.source,
                options.output_directory,
                expected_sha256=options.expected_sha256,
                ocr_helper=options.ocr_helper,
                progress=progress,
            )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Hata: {error}", file=sys.stderr)
        return 1
    _emit(
        WorkerEvent(
            "completed",
            "document_extract",
            payload={
                "jsonl": str(result.jsonl_path),
                "text": str(result.text_path),
                "quality": str(result.quality_path),
                "source_sha256": result.source_sha256,
                "block_count": result.block_count,
                "character_count": result.character_count,
                "ocr_block_count": result.quality.get("ocr_block_count", 0),
                "low_ocr_confidence_block_count": result.quality.get(
                    "low_ocr_confidence_block_count", 0
                ),
            },
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "inspect":
        return _inspect(arguments[1])
    if arguments and arguments[0] == "extract":
        return _extract(arguments[1:])
    else:
        print(json.dumps({"error": "Geçersiz belge işçisi komutu."}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
