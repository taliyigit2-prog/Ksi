"""Deterministic Phase 39 policy-consistency and safe fault probes."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ksi_local.acceptance import THREE_HOURS_SECONDS
from ksi_local.atomic_files import atomic_write_json
from ksi_local.languages import SUPPORTED_SOURCE_LANGUAGES
from ksi_local.media import MAX_DURATION_SECONDS
from ksi_local.network_policy import NetworkPolicyError, local_only_socket_guard, local_worker_environment
from ksi_local.project_metadata import PRODUCT_NAME, WORKSPACE_DIRECTORY
from ksi_local.storage import GIB, MIN_FREE_RESERVE_BYTES
from ksi_local.translation_targets import VERIFIED_TARGET_LANGUAGES
from ksi_local.worker_protocol import parse_worker_event


@dataclass(frozen=True)
class ReliabilityFinding:
    rule: str
    message: str
    severity: str = "critical"


@dataclass(frozen=True)
class ReliabilityReport:
    passed: bool
    checks: dict[str, bool]
    findings: tuple[ReliabilityFinding, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "findings": [asdict(item) for item in self.findings],
        }


def audit_policy_consistency() -> tuple[ReliabilityFinding, ...]:
    findings: list[ReliabilityFinding] = []
    expected_languages = {"tr", "en", "ru", "es", "de", "fr", "it", "zh"}
    rules = (
        (MAX_DURATION_SECONDS == THREE_HOURS_SECONDS == 3 * 60 * 60, "duration", "Üç saat sınırı modüller arasında çelişiyor."),
        (MIN_FREE_RESERVE_BYTES == 20 * GIB, "disk-reserve", "Disk güvenlik payı 20 GiB değil."),
        (set(VERIFIED_TARGET_LANGUAGES) == expected_languages, "target-languages", "Doğrulanmış hedef dil kümesi ürün kararıyla çelişiyor."),
        (set(SUPPORTED_SOURCE_LANGUAGES) == expected_languages - {"tr"}, "source-languages", "Kaynak dil kümesi ürün kararıyla çelişiyor."),
        (PRODUCT_NAME == "KSI Local Studio", "product-name", "Kanonik ürün adı değişmiş."),
        (WORKSPACE_DIRECTORY == "KSI-Workspace", "workspace-name", "Kanonik çalışma alanı adı değişmiş."),
    )
    for passed, rule, message in rules:
        if not passed:
            findings.append(ReliabilityFinding(rule, message))
    environment = local_worker_environment({"OPENAI_API_KEY": "secret", "HTTP_PROXY": "http://proxy"})
    if "OPENAI_API_KEY" in environment or "HTTP_PROXY" in environment or environment.get("OLLAMA_NO_CLOUD") != "true":
        findings.append(ReliabilityFinding("local-network", "Yerel işçi ortamı bulut kimlik bilgisini temizlemiyor."))
    return tuple(findings)


def run_safe_fault_probes(directory: str | Path | None = None) -> ReliabilityReport:
    """Exercise failure boundaries without touching user jobs, models or outputs."""
    checks: dict[str, bool] = {}
    findings = list(audit_policy_consistency())
    parent = Path(directory).expanduser().resolve() if directory else None
    with tempfile.TemporaryDirectory(prefix="ksi-reliability-", dir=parent) as temporary:
        root = Path(temporary)
        target = root / "state.json"
        target.write_text('{"generation": 1}\n', encoding="utf-8")
        before = target.read_bytes()
        incomplete = root / ".state.json.interrupted.part"
        incomplete.write_text('{"generation":', encoding="utf-8")
        checks["interrupted_partial_preserves_published_file"] = target.read_bytes() == before
        atomic_write_json(target, {"generation": 2})
        checks["atomic_publish_is_complete"] = json.loads(target.read_text()) == {"generation": 2}
        checks["unrelated_partial_is_not_deleted"] = incomplete.is_file()

        try:
            parse_worker_event('{"event":"progress","stage":"translate","completed":9,"total":2}')
        except ValueError:
            checks["invalid_progress_rejected"] = True
        else:
            checks["invalid_progress_rejected"] = False

        try:
            parse_worker_event("not-json")
        except ValueError:
            checks["corrupt_worker_message_rejected"] = True
        else:
            checks["corrupt_worker_message_rejected"] = False

        try:
            with local_only_socket_guard():
                import socket

                socket.getaddrinfo("example.com", 443)
        except NetworkPolicyError:
            checks["remote_dns_blocked"] = True
        else:
            checks["remote_dns_blocked"] = False

    for rule, passed in checks.items():
        if not passed:
            findings.append(ReliabilityFinding(rule, "Güvenli hata enjeksiyonu denetimi başarısız."))
    return ReliabilityReport(not findings, checks, tuple(findings))
