"""Static acceptance checks for KSI Local Studio's subscription-free local AI boundary."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path


_CLOUD_ENDPOINTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.com",
    "api.mistral.ai",
    "api.groq.com",
    "ollama.com/api",
)
_SECRET_READ = re.compile(
    r"(?:getenv|environ\.get)\(\s*['\"](?:OPENAI|ANTHROPIC|GEMINI|GOOGLE|COHERE|"
    r"MISTRAL|GROQ|TOGETHER|AZURE_OPENAI)[A-Z0-9_]*['\"]"
)
_UPLOAD_CALL = re.compile(
    r"\b(?:requests|httpx)\.(?:post|put|patch)\s*\(|\b(?:boto3|dropbox)\.client\s*\("
)


@dataclass(frozen=True)
class LocalityAudit:
    passed: bool
    scanned_files: int
    cloud_endpoint_hits: tuple[str, ...]
    paid_secret_reads: tuple[str, ...]
    automatic_upload_calls: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def audit_locality(source_root: str | Path) -> LocalityAudit:
    """Scan runtime Python sources for cloud AI or automatic upload wiring."""
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("KSI Local Studio kaynak klasörü bulunamadı.")
    cloud_hits: list[str] = []
    secret_hits: list[str] = []
    upload_hits: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        # The auditor necessarily contains the forbidden marker catalogue itself.
        if path.name == Path(__file__).name:
            continue
        scanned += 1
        content = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(content.splitlines(), start=1):
            lowered = line.casefold()
            if any(endpoint in lowered for endpoint in _CLOUD_ENDPOINTS):
                cloud_hits.append(f"{relative}:{line_number}")
            if _SECRET_READ.search(line):
                secret_hits.append(f"{relative}:{line_number}")
            if _UPLOAD_CALL.search(line):
                upload_hits.append(f"{relative}:{line_number}")
    passed = not cloud_hits and not secret_hits and not upload_hits
    return LocalityAudit(
        passed=passed,
        scanned_files=scanned,
        cloud_endpoint_hits=tuple(cloud_hits),
        paid_secret_reads=tuple(secret_hits),
        automatic_upload_calls=tuple(upload_hits),
        notes=(
            "YouTube/X/Udemy bağlantıları yalnız kullanıcı isteğiyle medya indirmek içindir.",
            "Codex inceleme paketi yalnız yerel dosya üretir ve temel akış için zorunlu değildir.",
            "Yapay zekâ istekleri doğrulanmış loopback Ollama adresiyle sınırlıdır.",
        ),
    )
