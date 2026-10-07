"""Deterministic, network-blocked acceptance checks for the Phase 12 foundation."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from ksi_local.locality_audit import audit_locality
from ksi_local.media import verify_media_file
from ksi_local.network_policy import NetworkPolicyError, local_only_socket_guard
from ksi_local.subtitles import Cue, parse_plain_transcript, parse_srt_text
from ksi_local.summarization import summarize_cues
from ksi_local.translation import translate_cues


class _OfflineAcceptanceClient:
    """Deterministic local stand-in; any accidental network call is still blocked."""

    def generate(self, **kwargs: Any) -> str:
        prompt = str(kwargs.get("prompt") or "")
        if "INPUT:\n" in prompt and '"translations"' in prompt:
            records = json.loads(prompt.rsplit("INPUT:\n", 1)[1])
            return json.dumps(
                {
                    "translations": [
                        {"id": item["id"], "text": f"Türkçe: {item['text']}"}
                        for item in records
                    ]
                },
                ensure_ascii=False,
            )
        if kwargs.get("max_tokens") == 1000:
            schema = kwargs["json_schema"]
            source_id = schema["properties"]["claims"]["items"]["properties"][
                "source_ids"
            ]["items"]["enum"][0]
            return json.dumps(
                {
                    "claims": [
                        {
                            "text": "Yerel örnek, çevrimdışı özet akışını doğrular.",
                            "category": "ana_konu",
                            "source_ids": [source_id],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "short_summary": ["C000001"],
                "main_topics": ["C000001"],
                "important_ideas": [],
                "conclusions": [],
                "actions": [],
            }
        )


@dataclass(frozen=True)
class PhaseTwelveAcceptance:
    passed: bool
    remote_network_blocked: bool
    local_srt_translation_ok: bool
    local_txt_summary_ok: bool
    local_video_verified: bool
    source_audit_ok: bool
    scanned_source_files: int
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_phase12_acceptance(project_root: str | Path, *, media_fixture: str | Path | None = None) -> PhaseTwelveAcceptance:
    root = Path(project_root).expanduser().resolve()
    client = _OfflineAcceptanceClient()
    source_srt = parse_srt_text(
        "1\n00:00:00,000 --> 00:00:03,000\nThis is a local test.\n\n"
        "2\n00:00:03,000 --> 00:00:07,000\nNo cloud account is required.\n"
    )
    txt_cues = parse_plain_transcript(
        "This local transcript is summarized without any subscription or cloud account. "
        "The source remains on the user's own storage."
    )
    network_blocked = False
    with local_only_socket_guard():
        try:
            urlopen("http://example.com", timeout=0.1)
        except NetworkPolicyError:
            network_blocked = True
        translated = translate_cues(
            source_srt,
            source_language="en",
            client=client,  # type: ignore[arg-type]
        )
        summary = summarize_cues(
            txt_cues,
            client=client,  # type: ignore[arg-type]
            source_title="Yerel TXT kabul örneği",
            source_reference="Yerel dosya",
            transcript_sha256="0" * 64,
        )
    translation_ok = len(translated) == len(source_srt) and all(
        source.start == target.start and source.end == target.end
        for source, target in zip(source_srt, translated, strict=True)
    )
    summary_ok = bool(summary.quality["passed"]) and "[00:00:00]" in summary.markdown

    fixture = Path(media_fixture).resolve() if media_fixture is not None else root / ".phase1/fixtures/local-smoke.mp4"
    ffprobe = shutil.which("ffprobe")
    video_ok = False
    if fixture.is_file() and ffprobe:
        try:
            verification = verify_media_file(fixture, ffprobe_path=ffprobe)
            video_ok = bool(verification["sha256"])
        except (OSError, RuntimeError, ValueError):
            video_ok = False
    audit = audit_locality(root / "src/ksi_local")
    passed = all(
        (network_blocked, translation_ok, summary_ok, video_ok, audit.passed)
    )
    return PhaseTwelveAcceptance(
        passed=passed,
        remote_network_blocked=network_blocked,
        local_srt_translation_ok=translation_ok,
        local_txt_summary_ok=summary_ok,
        local_video_verified=video_ok,
        source_audit_ok=audit.passed,
        scanned_source_files=audit.scanned_files,
        notes=(
            "Kabul sırasında dış DNS/TCP bağlantısı Python katmanında engellendi.",
            "Model yanıtları deterministik yerel test ikamesidir; gerçek Ollama manuel test 12-A'dadır.",
        ),
    )
