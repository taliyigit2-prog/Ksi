"""Command-line entry point for the Phase 1 risk harness."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import sys
from pathlib import Path

from ksi_local.article_reader import Article, fetch_article, save_article_package
from ksi_local.atomic_files import atomic_replace, atomic_write_json, atomic_write_text
from ksi_local.catalog import add_private_bookmark, search_public_catalog
from ksi_local.collection_jobs import build_collection_probe_plan, plan_collection
from ksi_local.core_service import CoreService
from ksi_local.creative_lab import (
    LabFeature,
    LabPolicy,
    candidate_report,
    create_character,
    generate_emoji_svg,
    generate_instrumental,
    generate_pattern,
)
from ksi_local.downloader import (
    BrowserSession,
    DownloadMode,
    Platform,
    SUPPORTED_SUBTITLE_LANGUAGES,
    SourceURLValidationError,
    build_download_plan,
    build_probe_plan,
    run_download_with_retry,
    run_probe,
    summarize_probe,
    validate_browser_session,
)
from ksi_local.dubbing import build_asr_quality, mux_dubbed_video
from ksi_local.document_summarization import summarize_document
from ksi_local.document_translation import document_needs_model, translate_document
from ksi_local.exporter import export_artifacts
from ksi_local.final_release import (
    build_cleanup_preview,
    evaluate_release_gate,
    write_cleanup_preview,
)
from ksi_local.glossary import empty_glossary, load_glossary
from ksi_local.image_editing import EditSession, PLATFORM_PROFILES
from ksi_local.image_tools import (
    inspect_image,
    lossless_transform,
    plan_resize,
    remove_uniform_background,
    resize_image,
)
from ksi_local.language_detection import detect_text_language
from ksi_local.languages import SOURCE_LANGUAGE_CHOICES, turkish_language_name
from ksi_local.maintenance import build_acceptance_report, format_acceptance_report
from ksi_local.manual_acceptance import (
    create_session as create_manual_acceptance_session,
    progress_dict as manual_acceptance_progress,
    record_result as record_manual_acceptance_result,
)
from ksi_local.media import (
    AUDIO_SUFFIXES,
    MEDIA_SUFFIXES,
    probe_local_media,
    sha256_file,
    verify_audio_file,
    verify_media_file,
)
from ksi_local.migration import apply_migration, inspect_migration
from ksi_local.network_policy import local_only_socket_guard
from ksi_local.ollama_client import OllamaClient
from ksi_local.ollama_runtime import managed_ollama
from ksi_local.preflight import inspect_source
from ksi_local.project_metadata import CLI_NAME, PRODUCT_NAME
from ksi_local.release_prep import build_public_tree
from ksi_local.settings import resolve_workspace
from ksi_local.storage import GIB, discover_mounted_volumes, estimate_video_storage, volumes_to_json
from ksi_local.subtitles import (
    clean_rolling_captions,
    discover_best_subtitle,
    read_srt,
    timestamped_transcript,
    transcript_text,
    write_srt,
)
from ksi_local.subtitle_quality import assess_subtitle_quality
from ksi_local.subtitle_video import mux_subtitled_video
from ksi_local.summarization import EvidenceClaim, summarize_cues
from ksi_local.system_health import build_health_report, format_health_report
from ksi_local.tool_integrity import verify_download_tools
from ksi_local.translation import translate_cues
from ksi_local.translation_targets import VERIFIED_TARGET_LANGUAGES
from ksi_local.transcription import DEFAULT_WHISPER_MODEL, transcribe_media
from ksi_local.worker_protocol import WorkerEvent, encode_worker_event
from ksi_local.update_manager import plan_offline_update


def _emit(event: WorkerEvent) -> None:
    print(encode_worker_event(event), flush=True)


def _handle_termination(signum: int, _frame: object) -> None:
    """Unwind active context managers so an owned Ollama server is stopped."""
    raise SystemExit(128 + signum)


def _health(args: argparse.Namespace) -> int:
    report = build_health_report()
    print(report.to_json() if args.json else format_health_report(report))
    return 0


def _read_article(args: argparse.Namespace) -> int:
    robots_text = (
        Path(args.robots_file).read_text(encoding="utf-8")
        if args.robots_file
        else None
    )
    article = fetch_article(
        args.url,
        timeout_seconds=args.timeout,
        robots_text=robots_text,
    )
    output = Path(args.output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(output / "makale-kaynak.json", article.__dict__)
    atomic_write_text(output / "makale-kaynak.txt", article.text + "\n")
    print(json.dumps(article.__dict__, ensure_ascii=False, indent=2))
    return 0


def _package_article(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.article_json).read_text(encoding="utf-8"))
    article = Article(
        source_url=str(payload["source_url"]),
        title=str(payload["title"]),
        author=str(payload["author"]) if payload.get("author") else None,
        published_at=str(payload["published_at"]) if payload.get("published_at") else None,
        accessed_at=str(payload["accessed_at"]),
        text=str(payload["text"]),
        images=tuple(str(item) for item in payload.get("images", ())),
        warnings=tuple(str(item) for item in payload.get("warnings", ())),
    )
    translated = Path(args.translation_file).read_text(encoding="utf-8")
    outputs = save_article_package(article, translated, args.output_directory)
    print(json.dumps([str(item) for item in outputs], ensure_ascii=False))
    return 0


def _acceptance(args: argparse.Namespace) -> int:
    workspace = resolve_workspace()
    report = build_acceptance_report(
        workspace,
        verify_model_hashes=args.full_model_hashes,
        app_path=args.app,
    )
    print(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        if args.json
        else format_acceptance_report(report)
    )
    return 0 if report.passed else 4


def _acceptance_create(args: argparse.Namespace) -> int:
    payload = create_manual_acceptance_session(args.output)
    print(json.dumps({"path": str(Path(args.output).expanduser().resolve()), "case_count": len(payload["cases"])}, ensure_ascii=False, indent=2))
    return 0


def _acceptance_status(args: argparse.Namespace) -> int:
    progress = manual_acceptance_progress(args.session)
    print(json.dumps(progress, ensure_ascii=False, indent=2))
    return 0 if progress["releasable"] else 4


def _acceptance_record(args: argparse.Namespace) -> int:
    ratings: dict[str, int] = {}
    for item in args.rating or ():
        name, separator, value = item.partition("=")
        if not separator:
            raise ValueError("Puan name=1..5 biçiminde olmalıdır.")
        ratings[name] = int(value)
    record_manual_acceptance_result(
        args.session,
        args.case,
        status=args.status,
        ratings=ratings,
        note=args.note or "",
        artifact_sha256=args.artifact_sha256,
        expected=args.expected or "",
        actual=args.actual or "",
        reproduction_steps=tuple(args.step or ()),
    )
    print(json.dumps(manual_acceptance_progress(args.session), ensure_ascii=False, indent=2))
    return 0


def _volumes(args: argparse.Namespace) -> int:
    volumes = discover_mounted_volumes()
    if args.json:
        print(volumes_to_json(volumes))
    elif not volumes:
        print("Bağlı harici disk bulunamadı.")
    else:
        for volume in volumes:
            suitable = "uygun" if volume.suitable_external_workspace else "uygun değil"
            gib = volume.free_bytes / (1024**3)
            print(
                f"- {volume.name}: {volume.mount_point} — {gib:.1f} GiB boş — "
                f"{volume.filesystem or 'bilinmeyen dosya sistemi'} — {suitable}"
            )
    return 0


def _lab_status(args: argparse.Namespace) -> int:
    policy = LabPolicy(set(args.enable or ()))
    print(json.dumps({"features": policy.status(), "model_candidates": candidate_report()}, ensure_ascii=False, indent=2))
    return 0


def _lab_generate(args: argparse.Namespace) -> int:
    feature = LabFeature(args.lab_kind)
    policy = LabPolicy({feature})
    if feature is LabFeature.IMAGE:
        output = generate_pattern(args.output, seed=args.seed, width=args.width, height=args.height, policy=policy)
        result: object = {"output": str(output), "seed": args.seed}
    elif feature is LabFeature.CHARACTER:
        card, portrait = create_character(args.output, name=args.name, seed=args.seed, traits=args.trait, policy=policy)
        result = {"character_card": str(card), "identity_reference": str(portrait), "seed": args.seed}
    elif feature is LabFeature.MUSIC:
        output = generate_instrumental(args.output, seed=args.seed, duration_seconds=args.duration, policy=policy)
        result = {"output": str(output), "seed": args.seed, "instrumental": True}
    elif feature is LabFeature.SVG:
        output = generate_emoji_svg(args.output, seed=args.seed, mood=args.mood, policy=policy)
        result = {"output": str(output), "seed": args.seed}
    else:
        raise ValueError("Video ve ses pilotları doğrulanmış FFmpeg planı üzerinden API ile kullanılır.")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _core_service(args: argparse.Namespace) -> CoreService:
    try:
        workspace = resolve_workspace()
    except RuntimeError:
        workspace = None
    return CoreService(
        workspace=workspace,
        allowed_roots=tuple(args.allow_root or ()),
        network_allowed=bool(args.allow_network),
    )


def _core_command(args: argparse.Namespace) -> int:
    service = _core_service(args)
    if args.core_action == "preflight":
        payload = service.preflight(args.source, download_only=args.download_only, want_subtitle=args.subtitle, want_summary=args.summary, want_dub=args.dub, job_kind=args.job_kind)
    elif args.core_action == "create":
        payload = service.create_job(source=args.source, job_directory=args.job_directory, source_language=args.source_language, want_subtitle=args.subtitle, want_summary=args.summary, want_dub=args.dub, download_only=args.download_only, job_kind=args.job_kind)
    elif args.core_action == "status":
        payload = service.status(args.job_id)
    elif args.core_action == "stop":
        payload = service.stop(args.job_id, confirm=args.confirm)
    elif args.core_action == "resume":
        payload = service.resume(args.job_id)
    elif args.core_action == "results":
        payload = service.results(args.job_id)
    elif args.core_action == "export":
        payload = service.export(args.job_id, destination_directory=args.destination_directory, folder_name=args.folder_name, confirm=args.confirm)
    else:
        raise ValueError("Bilinmeyen çekirdek komutu.")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _mcp_server(_args: argparse.Namespace) -> int:
    from ksi_local.mcp_server import main as mcp_main

    return mcp_main()


def _prepare_public_source(args: argparse.Namespace) -> int:
    report = build_public_tree(Path(__file__).resolve().parents[2], args.destination)
    print(json.dumps({**report.__dict__, "findings": []}, ensure_ascii=False, indent=2))
    return 0


def _release_cleanup_preview(args: argparse.Namespace) -> int:
    project = Path(__file__).resolve().parents[2]
    preview = (
        write_cleanup_preview(project, args.output)
        if args.output
        else build_cleanup_preview(project)
    )
    print(json.dumps(preview.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _release_gate(args: argparse.Namespace) -> int:
    gate = evaluate_release_gate(
        acceptance_session=args.acceptance_session,
        public_tree_findings=args.public_tree_findings,
        public_history_findings=args.public_history_findings,
        package_path=args.package,
        package_manifest_path=args.package_manifest,
        clean_install_accepted=args.clean_install_accepted,
        cleanup_approved_and_completed=args.cleanup_completed,
        project_root=Path(__file__).resolve().parents[2],
    )
    print(json.dumps(gate.to_dict(), ensure_ascii=False, indent=2))
    return 0 if gate.ready_to_publish else 4


def _plan_update(args: argparse.Namespace) -> int:
    plan = plan_offline_update(
        args.manifest,
        args.package,
        current_version=args.current_version,
        allow_downgrade=args.allow_downgrade,
        require_notarization=not args.allow_personal_ad_hoc,
    )
    print(json.dumps(plan.__dict__, ensure_ascii=False, indent=2))
    return 0


def _migrate_predecessor(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser().resolve() if args.home else Path.home()
    mount_point = Path(args.mount).expanduser().resolve()
    report = (
        apply_migration(home=home, mount_point=mount_point)
        if args.apply
        else inspect_migration(home=home, mount_point=mount_point)
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.status in {"ready", "completed"} else 4


def _browser_session(args: argparse.Namespace) -> BrowserSession | None:
    browser = getattr(args, "browser", None)
    profile = getattr(args, "browser_profile", None)
    if bool(browser) != bool(profile):
        raise ValueError(
            "Ayrı oturum için tarayıcı ve profil klasörü birlikte seçilmelidir."
        )
    return validate_browser_session(browser, profile) if browser and profile else None


def _plan_probe(args: argparse.Namespace) -> int:
    yt_dlp_path = args.yt_dlp or shutil.which("yt-dlp") or "yt-dlp"
    js_runtime = None
    if args.deno:
        js_runtime = ("deno", args.deno)
    elif args.node:
        js_runtime = ("node", args.node)
    plan = build_probe_plan(
        args.url,
        yt_dlp_path=yt_dlp_path,
        js_runtime=js_runtime,
        browser_session=_browser_session(args),
        udemy_access_confirmed=args.confirm_udemy_access,
    )
    print(json.dumps(plan.display_dict(), ensure_ascii=False, indent=2))
    return 0


def _probe(args: argparse.Namespace) -> int:
    yt_dlp_path = args.yt_dlp or shutil.which("yt-dlp")
    if yt_dlp_path is None:
        raise RuntimeError("yt-dlp bulunamadı; doğrulanmış yürütülebilir yolu gerekli.")
    js_runtime = None
    if args.deno:
        js_runtime = ("deno", args.deno)
    elif args.node:
        js_runtime = ("node", args.node)
    plan = build_probe_plan(
        args.url,
        yt_dlp_path=yt_dlp_path,
        js_runtime=js_runtime,
        browser_session=_browser_session(args),
        udemy_access_confirmed=args.confirm_udemy_access,
    )
    payload = run_probe(plan, timeout_seconds=args.timeout)
    print(json.dumps(summarize_probe(payload), ensure_ascii=False, indent=2))
    return 0


def _plan_collection_probe(args: argparse.Namespace) -> int:
    js_runtime = ("deno", args.deno) if args.deno else None
    plan = build_collection_probe_plan(
        args.url,
        yt_dlp_path=args.yt_dlp or shutil.which("yt-dlp") or "yt-dlp",
        js_runtime=js_runtime,
    )
    print(json.dumps(plan.display_dict(), ensure_ascii=False, indent=2))
    return 0


def _preflight_collection(args: argparse.Namespace) -> int:
    workspace = resolve_workspace()
    free_bytes = shutil.disk_usage(workspace.root).free
    plan = build_collection_probe_plan(
        args.url,
        yt_dlp_path=str(workspace.yt_dlp),
        js_runtime=("deno", str(workspace.deno)),
    )
    payload = run_probe(plan, timeout_seconds=args.timeout)
    result = plan_collection(
        payload,
        source_url=args.url,
        free_bytes=free_bytes,
        selected_ids=args.video_id,
        max_height=args.max_height,
        processing_requested=not args.download_only,
        allow_active_live=args.confirm_live_recording,
        live_recording_limit_seconds=args.live_limit_seconds,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.fits else 4


def _catalog_search(args: argparse.Namespace) -> int:
    entries = search_public_catalog(args.query)
    print(json.dumps([item.to_dict() for item in entries], ensure_ascii=False, indent=2))
    return 0


def _catalog_add_private(args: argparse.Namespace) -> int:
    bookmark = add_private_bookmark(args.title, args.url, note=args.note or "")
    print(json.dumps(bookmark.__dict__, ensure_ascii=False, indent=2))
    return 0


def _image_inspect(args: argparse.Namespace) -> int:
    print(json.dumps(inspect_image(args.source).__dict__, ensure_ascii=False, indent=2))
    return 0


def _image_resize(args: argparse.Namespace) -> int:
    plan = plan_resize(
        args.source,
        width=args.width,
        height=args.height,
        percent=args.percent,
        lock_aspect=not args.unlock_aspect,
        output_format=args.format,
        lossless=args.lossless,
        strip_metadata=True,
        super_resolution_pilot=args.super_resolution_pilot,
    )
    output = resize_image(plan, args.output)
    print(json.dumps({"output": str(output), "plan": plan.to_dict()}, ensure_ascii=False, indent=2))
    return 0


def _image_remove_background(args: argparse.Namespace) -> int:
    output = remove_uniform_background(
        args.source,
        args.output,
        tolerance=args.tolerance,
        feather=args.feather,
        product_shadow=args.product_shadow,
    )
    print(json.dumps({"output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def _image_transform(args: argparse.Namespace) -> int:
    output = lossless_transform(args.source, args.output, operation=args.operation)
    print(json.dumps({"output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def _image_edit_start(args: argparse.Namespace) -> int:
    session = EditSession.create(
        args.source,
        args.session_directory,
        workflow=args.workflow,
        platform=args.platform,
    )
    print(json.dumps({"session": str(session.manifest)}, ensure_ascii=False, indent=2))
    return 0


def _image_edit_preview(args: argparse.Namespace) -> int:
    output = EditSession(args.session_directory).preview(
        args.prompt,
        protect_mask=args.protect_mask,
        seed=args.seed,
    )
    print(json.dumps({"preview": str(output)}, ensure_ascii=False, indent=2))
    return 0


def _image_edit_commit(args: argparse.Namespace) -> int:
    output = EditSession(args.session_directory).commit(approve_preview=args.approve_preview)
    print(json.dumps({"output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def _image_edit_undo(args: argparse.Namespace) -> int:
    current = EditSession(args.session_directory).undo()
    print(json.dumps({"current": str(current)}, ensure_ascii=False, indent=2))
    return 0


def _image_edit_compare(args: argparse.Namespace) -> int:
    output = EditSession(args.session_directory).comparison(args.output)
    print(json.dumps({"comparison": str(output)}, ensure_ascii=False, indent=2))
    return 0


def _probe_file(args: argparse.Namespace) -> int:
    payload = probe_local_media(args.path, ffprobe_path=args.ffprobe)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _preflight(args: argparse.Namespace) -> int:
    workspace = resolve_workspace()
    selected_any = args.download_only or args.subtitle or args.summary or args.dub
    result = inspect_source(
        args.source,
        workspace=workspace,
        download_only=args.download_only,
        want_subtitle=args.subtitle or not selected_any,
        want_summary=args.summary or not selected_any,
        want_dub=args.dub,
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
        browser_session=_browser_session(args),
        udemy_access_confirmed=args.confirm_udemy_access,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _budget(args: argparse.Namespace) -> int:
    budget = estimate_video_storage(
        args.duration,
        free_bytes=int(args.free_gib * GIB),
        missing_model_bytes=int(args.missing_model_gib * GIB),
    )
    print(json.dumps(budget.to_dict(), ensure_ascii=False, indent=2))
    return 0 if budget.fits else 3


def _plan_download(args: argparse.Namespace) -> int:
    runtime = ("deno", args.deno) if args.deno else None
    plan = build_download_plan(
        args.url,
        output_directory=args.output_directory,
        yt_dlp_path=args.yt_dlp,
        ffmpeg_path=args.ffmpeg,
        js_runtime=runtime,
        max_height=args.max_height,
        subtitle_languages=tuple(args.subtitle_language or ()),
        media_index=args.media_index,
        browser_session=_browser_session(args),
        mode=args.mode,
        udemy_access_confirmed=args.confirm_udemy_access,
    )
    print(json.dumps(plan.display_dict(), ensure_ascii=False, indent=2))
    return 0


def _download(args: argparse.Namespace) -> int:
    runtime = ("deno", args.deno) if args.deno else None
    if runtime is None:
        raise RuntimeError("Çevrimiçi video indirmesi için doğrulanmış Deno yolu gerekli.")
    tools = verify_download_tools(
        yt_dlp_path=args.yt_dlp,
        deno_path=args.deno,
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
    )
    session = _browser_session(args)
    plan = build_download_plan(
        args.url,
        output_directory=args.output_directory,
        yt_dlp_path=args.yt_dlp,
        ffmpeg_path=args.ffmpeg,
        js_runtime=runtime,
        max_height=args.max_height,
        subtitle_languages=tuple(args.subtitle_language or ()),
        media_index=args.media_index,
        browser_session=session,
        mode=args.mode,
        udemy_access_confirmed=args.confirm_udemy_access,
    )
    direct_fallback = None
    if plan.source.platform is Platform.X and plan.mode is DownloadMode.VIDEO:
        direct_fallback = build_download_plan(
            args.url,
            output_directory=args.output_directory,
            yt_dlp_path=args.yt_dlp,
            ffmpeg_path=args.ffmpeg,
            js_runtime=runtime,
            max_height=args.max_height,
            subtitle_languages=tuple(args.subtitle_language or ()),
            media_index=args.media_index,
            browser_session=session,
            direct_https_only=True,
            mode=args.mode,
            udemy_access_confirmed=args.confirm_udemy_access,
        )
    download_result = run_download_with_retry(
        plan,
        timeout_seconds=args.timeout,
        direct_fallback_plan=direct_fallback,
    )
    files = list(download_result.files)
    if plan.mode is DownloadMode.VIDEO:
        media_files = [
            Path(item)
            for item in files
            if Path(item).suffix.casefold() in MEDIA_SUFFIXES
        ]
        if len(media_files) != 1:
            raise RuntimeError(
                "İndirme sonunda tek bir doğrulanabilir video dosyası bulunamadı."
            )
        verification = verify_media_file(
            media_files[0], ffprobe_path=args.ffprobe, require_audio=args.require_audio
        )
    elif plan.mode is DownloadMode.AUDIO:
        audio_files = [
            Path(item)
            for item in files
            if Path(item).suffix.casefold() in AUDIO_SUFFIXES
        ]
        if len(audio_files) != 1:
            raise RuntimeError(
                "Özet için tek bir doğrulanabilir ses dosyası bulunamadı."
            )
        verification = verify_audio_file(audio_files[0], ffprobe_path=args.ffprobe)
    else:
        requested_language = next(iter(args.subtitle_language or ()), "auto")
        selection = discover_best_subtitle(
            plan.output_directory,
            requested_language=requested_language,
            stem_prefix="source",
        )
        if selection is None:
            raise RuntimeError("Özet için doğrulanabilir kaynak altyazısı indirilemedi.")
        cues = clean_rolling_captions(read_srt(selection.path))
        verification = {
            "filename": selection.path.name,
            "size_bytes": selection.path.stat().st_size,
            "sha256": sha256_file(selection.path),
            "cue_count": len(cues),
            "detected_language": selection.detected_language,
        }
    manifest_path = plan.output_directory.parent / "manifest.json"
    try:
        prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prior_manifest = {}
    manifest = prior_manifest if isinstance(prior_manifest, dict) else {}
    manifest.update(
        {
            "schema_version": 1,
            "download": {
                "platform": plan.source.platform.value,
                "mode": plan.mode.value,
                "host": plan.source.hostname,
                "max_height": args.max_height,
                "media_index": args.media_index,
                "attempts": download_result.attempts,
                "fallback": download_result.fallback,
                "session_used": session is not None,
                "require_audio": args.require_audio,
                "verification": verification,
                "tools": tools.to_dict()["tools"],
            },
        }
    )
    atomic_write_json(manifest_path, manifest)
    _emit(
        WorkerEvent(
            "completed",
            "download",
            payload={
                "files": files,
                "verification": verification,
                "attempts": download_result.attempts,
                "fallback": download_result.fallback,
            },
        )
    )
    return 0


def _translate_srt(args: argparse.Namespace) -> int:
    from contextlib import nullcontext
    from ksi_local.argos_client import ArgosClient

    engine = getattr(args, "engine", "gemma")
    if engine == "argos":
        args.model = "argos-direct-cpu"
    cues = clean_rolling_captions(read_srt(args.input))
    source_language = args.source_language
    if source_language == "auto":
        detection = detect_text_language(transcript_text(cues))
        source_language = detection.code
        _emit(
            WorkerEvent(
                "language_detected",
                "translate",
                payload={
                    "language": detection.code,
                    "label": detection.name,
                    "confidence": round(detection.confidence, 3),
                },
            )
        )
    glossary = load_glossary(args.glossary, source_language)
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.partial")
    prefix = read_srt(partial) if partial.is_file() else []
    from ksi_local.media import sha256_file

    identity_file = partial.with_suffix(partial.suffix + ".identity.json")
    identity = {"schema_version": 1, "input_sha256": sha256_file(Path(args.input)),
        "engine": engine, "model": args.model, "source_language": source_language,
        "target_language": args.target_language,
        "glossary_sha256": sha256_file(Path(args.glossary)) if args.glossary else None}
    if identity_file.is_file():
        if identity_file.is_symlink() or identity_file.stat().st_size > 65536 or json.loads(identity_file.read_text(encoding="utf-8")) != identity:
            raise RuntimeError("Çeviri checkpoint'i farklı kaynak, motor veya sözlüğe ait; mevcut dosyalar korundu.")
    elif prefix and engine == "argos":
        raise RuntimeError("Eski çeviri checkpoint'i Argos ile karıştırılamaz; mevcut dosya korundu.")
    atomic_write_json(identity_file, identity)
    if len(prefix) > len(cues):
        raise RuntimeError("Çeviri checkpoint'i kaynak altyazıdan daha uzun.")
    for translated_cue, source_cue in zip(prefix, cues, strict=False):
        if (translated_cue.start, translated_cue.end) != (source_cue.start, source_cue.end):
            raise RuntimeError("Çeviri checkpoint zamanları kaynak altyazıyla eşleşmiyor.")
    remaining = cues[len(prefix) :]
    client = ArgosClient(Path(args.models_directory).parent / "argos") if engine == "argos" else OllamaClient(base_url=args.ollama_url, timeout_seconds=args.timeout)
    translated: list = []
    if remaining:

        def checkpoint(completed: list) -> None:
            write_srt(partial, prefix + completed)

        def progress(update: object) -> None:
            completed = len(prefix) + int(getattr(update, "completed"))
            _emit(
                WorkerEvent(
                    "progress",
                    "translate",
                    completed=completed,
                    total=len(cues),
                )
            )

        with nullcontext() if engine == "argos" else managed_ollama(
            executable=args.ollama,
            models_directory=args.models_directory,
            base_url=args.ollama_url,
        ):
            translated = translate_cues(
                remaining,
                source_language=source_language,
                client=client,
                model=args.model,
                batch_size=args.batch_size,
                on_checkpoint=checkpoint,
                on_progress=progress,
                glossary=glossary,
                target_language=args.target_language,
            )
    final_cues = prefix + translated
    write_srt(partial, final_cues)
    atomic_replace(partial, target)
    quality = assess_subtitle_quality(
        cues,
        final_cues,
        source_language=source_language,
        glossary=glossary,
    )
    quality_path = (
        Path(args.quality_report).expanduser().resolve()
        if args.quality_report
        else target.with_name(f"{target.stem}.kalite.json")
    )
    atomic_write_json(quality_path, quality.to_dict())
    _emit(
        WorkerEvent(
            "completed",
            "translate",
            payload={
                "output": str(target),
                "quality_report": str(quality_path),
                "quality_passed": quality.passed,
                "quality_errors": quality.error_count,
                "quality_warnings": quality.warning_count,
            },
        )
    )
    return 0


def _quality_srt(args: argparse.Namespace) -> int:
    source_cues = clean_rolling_captions(read_srt(args.source))
    translated_cues = read_srt(args.translated)
    source_language = args.source_language
    if source_language == "auto":
        source_language = detect_text_language(transcript_text(source_cues)).code
    glossary = (
        load_glossary(args.glossary, source_language)
        if args.glossary
        else empty_glossary(source_language)
    )
    report = assess_subtitle_quality(
        source_cues,
        translated_cues,
        source_language=source_language,
        glossary=glossary,
    )
    payload = report.to_dict()
    if args.output:
        atomic_write_json(Path(args.output).expanduser().resolve(), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.passed else 3


def _translate_document(args: argparse.Namespace) -> int:
    from ksi_local.argos_client import ArgosClient

    engine = getattr(args, "engine", "gemma")
    if engine == "argos":
        args.model = "argos-direct-cpu"
    canonical = Path(args.input).expanduser().resolve()
    output = Path(args.output_directory).expanduser().resolve()

    def progress(completed: int, total: int) -> None:
        _emit(
            WorkerEvent(
                "progress",
                "document_translate",
                completed=completed,
                total=total,
            )
        )

    _emit(WorkerEvent("started", "document_translate"))
    with local_only_socket_guard():
        needs_model = document_needs_model(
            canonical, source_language=args.source_language
        )
        client = ArgosClient(Path(args.models_directory).parent / "argos") if engine == "argos" and needs_model else OllamaClient(base_url=args.ollama_url, timeout_seconds=args.timeout)
        options = {
            "client": client if needs_model else None,
            "source_language": args.source_language,
            "model": args.model,
            "batch_size": args.batch_size,
            "default_glossary": args.glossary,
            "custom_glossary": args.custom_glossary,
            "checkpoint_path": args.checkpoint,
            "source_title": args.source_title,
            "on_progress": progress,
        }
        if needs_model and engine != "argos":
            with managed_ollama(
                executable=args.ollama,
                models_directory=args.models_directory,
                base_url=args.ollama_url,
            ):
                result = translate_document(canonical, output, **options)
        else:
            result = translate_document(canonical, output, **options)
    _emit(
        WorkerEvent(
            "completed",
            "document_translate",
            payload={
                "jsonl": str(result.jsonl_path),
                "text": str(result.text_path),
                "markdown": str(result.markdown_path),
                "docx": str(result.docx_path),
                "pdf": str(result.pdf_path),
                "quality": str(result.quality_path),
                "checkpoint": str(result.checkpoint_path),
                "block_count": result.block_count,
                "translated_block_count": result.translated_block_count,
                "preserved_block_count": result.preserved_block_count,
                "language_counts": result.language_counts,
                "quality_passed": bool(result.quality["passed"]),
                "quality_errors": int(result.quality["error_count"]),
                "quality_warnings": int(result.quality["warning_count"]),
            },
        )
    )
    return 0 if result.quality["passed"] else 3


def _summarize_document(args: argparse.Namespace) -> int:
    canonical = Path(args.input).expanduser().resolve()
    output = Path(args.output_directory).expanduser().resolve()

    def progress(update: object) -> None:
        _emit(
            WorkerEvent(
                "progress",
                "document_summarize",
                completed=int(getattr(update, "completed")),
                total=int(getattr(update, "total")),
            )
        )

    _emit(WorkerEvent("started", "document_summarize"))
    with local_only_socket_guard(), managed_ollama(
        executable=args.ollama,
        models_directory=args.models_directory,
        base_url=args.ollama_url,
    ):
        result = summarize_document(
            canonical,
            output,
            client=OllamaClient(args.ollama_url, timeout_seconds=args.timeout),
            source_mode=args.summary_source,
            profile=args.profile,
            translation_path=args.translation,
            translation_quality_path=args.translation_quality,
            source_title=args.source_title,
            source_reference=args.source_reference,
            model=args.model,
            max_chars=args.max_chars,
            checkpoint_path=args.checkpoint,
            create_pdf=args.pdf,
            on_progress=progress,
        )
    _emit(
        WorkerEvent(
            "completed",
            "document_summarize",
            payload={
                "markdown": str(result.markdown_path),
                "docx": str(result.docx_path),
                "pdf": str(result.pdf_path) if result.pdf_path else None,
                "trace_report": str(result.trace_path),
                "quality_report": str(result.quality_path),
                "checkpoint": str(result.checkpoint_path),
                "profile": result.profile,
                "source_mode": result.source_mode,
                "chunk_count": result.chunk_count,
                "claim_count": result.claim_count,
                "statement_count": result.statement_count,
                "source_coverage_percent": float(
                    result.quality["source_coverage_percent"]
                ),
                "evidence_coverage_percent": float(
                    result.quality["evidence_coverage_percent"]
                ),
                "quality_passed": bool(result.quality["passed"]),
            },
        )
    )
    return 0 if result.quality["passed"] else 3


def _summarize_srt(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser().resolve()
    cues = clean_rolling_captions(read_srt(input_path))
    transcript_sha256 = sha256_file(input_path)
    target = Path(args.output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = target.with_name(f".{target.name}.checkpoint.json")
    completed_chunks = 0
    initial_claims: list[EvidenceClaim] = []
    if checkpoint_path.is_file():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if (
                not isinstance(checkpoint, dict)
                or checkpoint.get("schema_version") != 1
                or checkpoint.get("transcript_sha256") != transcript_sha256
                or checkpoint.get("model") != args.model
            ):
                raise ValueError("Özet checkpoint'i kaynak veya modelle eşleşmiyor.")
            completed_chunks = int(checkpoint.get("completed_chunks", 0))
            records = checkpoint.get("claims")
            if not isinstance(records, list):
                raise ValueError("Özet checkpoint iddiaları geçersiz.")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Özet checkpoint iddiası geçersiz.")
                initial_claims.append(
                    EvidenceClaim(
                        id=str(record["id"]),
                        text=str(record["text"]),
                        category=str(record["category"]),
                        source_ids=tuple(str(item) for item in record["source_ids"]),
                    )
                )
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValueError("Özet checkpoint dosyası okunamıyor.") from error

    client = OllamaClient(base_url=args.ollama_url, timeout_seconds=args.timeout)

    def checkpoint(done: int, claims: list[EvidenceClaim]) -> None:
        atomic_write_json(
            checkpoint_path,
            {
                "schema_version": 1,
                "transcript_sha256": transcript_sha256,
                "model": args.model,
                "completed_chunks": done,
                "claims": [claim.to_dict() for claim in claims],
            },
        )

    def progress(update: object) -> None:
        _emit(
            WorkerEvent(
                "progress",
                "summarize",
                completed=int(getattr(update, "completed")),
                total=int(getattr(update, "total")),
            )
        )

    with managed_ollama(
        executable=args.ollama,
        models_directory=args.models_directory,
        base_url=args.ollama_url,
    ):
        result = summarize_cues(
            cues,
            client=client,
            source_title=args.source_title,
            source_reference=args.source_reference,
            transcript_sha256=transcript_sha256,
            model=args.model,
            max_chars=args.max_chars,
            target_seconds=args.chunk_seconds,
            completed_chunks=completed_chunks,
            initial_claims=initial_claims,
            on_checkpoint=checkpoint,
            on_progress=progress,
        )
    trace_path = (
        Path(args.trace_report).expanduser().resolve()
        if args.trace_report
        else target.with_name(f"{target.stem}.kaynaklar.json")
    )
    quality_path = (
        Path(args.quality_report).expanduser().resolve()
        if args.quality_report
        else target.with_name(f"{target.stem}.kalite.json")
    )
    atomic_write_text(target, result.markdown.rstrip() + "\n")
    atomic_write_json(trace_path, result.traceability)
    atomic_write_json(quality_path, result.quality)
    checkpoint_path.unlink(missing_ok=True)
    _emit(
        WorkerEvent(
            "completed",
            "summarize",
            payload={
                "output": str(target),
                "trace_report": str(trace_path),
                "quality_report": str(quality_path),
                "quality_passed": bool(result.quality["passed"]),
                "statement_count": int(result.quality["statement_count"]),
                "source_coverage_percent": float(
                    result.quality["source_coverage_percent"]
                ),
            },
        )
    )
    return 0 if result.quality["passed"] else 3


def _export(args: argparse.Namespace) -> int:
    output = export_artifacts(args.artifacts, desktop=args.desktop, folder_name=args.folder_name)
    _emit(WorkerEvent("completed", "export", payload={"output_directory": str(output)}))
    return 0


def _transcribe(args: argparse.Namespace) -> int:
    initial_prompt = None
    if args.glossary and args.source_language != "auto":
        initial_prompt = load_glossary(
            args.glossary, args.source_language
        ).source_prompt()
    result = transcribe_media(
        args.input,
        args.output,
        language=args.source_language,
        model=args.model,
        initial_prompt=initial_prompt,
    )
    result.update({"status": "completed", "output": str(Path(args.output).resolve())})
    detected = str(result.get("detected_language") or "")
    if detected:
        _emit(
            WorkerEvent(
                "language_detected",
                "transcribe",
                payload={
                    "language": detected,
                    "label": turkish_language_name(detected),
                },
            )
        )
    _emit(WorkerEvent("completed", "transcribe", payload=result))
    return 0


def _quality_dub(args: argparse.Namespace) -> int:
    target_cues = read_srt(args.target_srt)
    recognition = Path(args.recognized_srt).expanduser().resolve()
    result = transcribe_media(
        args.audio,
        recognition,
        language="tr",
        model=args.model,
    )
    asr_quality = build_asr_quality(target_cues, read_srt(recognition))
    timing_path = Path(args.timing_report).expanduser().resolve()
    try:
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Dublaj zamanlama kalite raporu okunamadı.") from error
    if not isinstance(timing, dict) or timing.get("schema_version") != 1:
        raise ValueError("Dublaj zamanlama kalite raporu geçersiz.")
    passed = bool(asr_quality["asr_passed"]) and bool(timing.get("passed"))
    payload = {
        "schema_version": 1,
        "passed": passed,
        "semantic_review_required": True,
        "asr": asr_quality,
        "timing": timing,
        "recognized_srt": str(recognition),
        "whisper": result,
    }
    atomic_write_json(args.output, payload)
    _emit(
        WorkerEvent(
            "completed",
            "dub_quality",
            payload={
                "output": str(Path(args.output).expanduser().resolve()),
                "character_error_percent": asr_quality["character_error_percent"],
                "quality_passed": passed,
            },
        )
    )
    # A failed gate stays visible in the report but does not hide the audible
    # result from its human reviewer.
    return 0


def _mux_dub(args: argparse.Namespace) -> int:
    verification = mux_dubbed_video(
        args.source,
        args.audio,
        args.output,
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
        original_volume=args.original_volume,
    )
    final_mix = verification.get("audio_quality", {})
    integrated = final_mix.get("integrated_lufs")
    peak = final_mix.get("true_peak_dbfs")
    mix_passed = (
        isinstance(integrated, float)
        and -18 <= integrated <= -14
        and isinstance(peak, float)
        and peak <= -0.9
    )
    if args.quality_report:
        quality_path = Path(args.quality_report).expanduser().resolve()
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Dublaj kalite raporu son miks için okunamadı.") from error
        if not isinstance(quality, dict) or quality.get("schema_version") != 1:
            raise ValueError("Dublaj kalite raporu son miks için geçersiz.")
        quality["final_mix"] = {**final_mix, "passed": mix_passed}
        quality["passed"] = bool(quality.get("passed")) and mix_passed
        atomic_write_json(quality_path, quality)
    _emit(
        WorkerEvent(
            "completed",
            "mux",
            payload={
                "output": str(Path(args.output).expanduser().resolve()),
                "duration_seconds": verification.get("media", {}).get(
                    "duration_seconds"
                ),
                "integrated_lufs": integrated,
                "true_peak_dbfs": peak,
                "quality_passed": mix_passed,
            },
        )
    )
    return 0


def _mux_subtitle(args: argparse.Namespace) -> int:
    verification = mux_subtitled_video(
        args.source,
        args.subtitle,
        args.output,
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
    )
    subtitle = verification.get("subtitle", {})
    _emit(
        WorkerEvent(
            "completed",
            "subtitle_mux",
            payload={
                "output": str(Path(args.output).expanduser().resolve()),
                "duration_seconds": verification.get("media", {}).get(
                    "duration_seconds"
                ),
                "subtitle_language": subtitle.get("language"),
                "subtitle_default": subtitle.get("default"),
            },
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI_NAME, description=f"{PRODUCT_NAME} yerel işlem aracı"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    lab_status = subparsers.add_parser(
        "lab-status", help="Bağımsız yaratıcı pilot kapılarını ve model aday kanıtlarını göster"
    )
    lab_status.add_argument("--enable", action="append", choices=tuple(LabFeature))
    lab_status.set_defaults(handler=_lab_status)

    lab_generate = subparsers.add_parser(
        "lab-generate", help="Açıkça seçilen hafif ve yerel yaratıcı pilotu çalıştır"
    )
    lab_generate.add_argument("lab_kind", choices=("image", "character", "music", "svg"))
    lab_generate.add_argument("output")
    lab_generate.add_argument("--seed", type=int, required=True)
    lab_generate.add_argument("--width", type=int, default=512)
    lab_generate.add_argument("--height", type=int, default=512)
    lab_generate.add_argument("--name", default="Kurgusal Karakter")
    lab_generate.add_argument("--trait", action="append", default=["özgün"])
    lab_generate.add_argument("--duration", type=float, default=5.0)
    lab_generate.add_argument("--mood", choices=("happy", "calm", "surprised"), default="happy")
    lab_generate.set_defaults(handler=_lab_generate)

    mcp = subparsers.add_parser("mcp-server", help="Yerel KSI Core MCP stdio sunucusunu çalıştır")
    mcp.set_defaults(handler=_mcp_server)

    public_source = subparsers.add_parser(
        "prepare-public-source",
        help="Yeni ve temiz bir klasörde denetlenmiş public kaynak ağacı oluştur",
    )
    public_source.add_argument("destination")
    public_source.set_defaults(handler=_prepare_public_source)

    cleanup_preview = subparsers.add_parser(
        "release-cleanup-preview",
        help="Hiçbir dosyayı silmeden Faz 40 koruma listesi ve temizlik önizlemesi üret",
    )
    cleanup_preview.add_argument("--output", help="Atomik JSON önizleme çıktı yolu")
    cleanup_preview.set_defaults(handler=_release_cleanup_preview)

    release_gate = subparsers.add_parser(
        "release-gate", help="Faz 40 yayın kanıtlarını salt okunur değerlendir"
    )
    release_gate.add_argument("acceptance_session")
    release_gate.add_argument("--public-tree-findings", type=int, default=0)
    release_gate.add_argument("--public-history-findings", type=int, default=0)
    release_gate.add_argument("--package")
    release_gate.add_argument("--package-manifest")
    release_gate.add_argument("--clean-install-accepted", action="store_true")
    release_gate.add_argument("--cleanup-completed", action="store_true")
    release_gate.set_defaults(handler=_release_gate)

    update = subparsers.add_parser(
        "plan-update", help="Çevrimdışı güncelleme paketini değiştirmeden doğrula"
    )
    update.add_argument("manifest")
    update.add_argument("package")
    update.add_argument("--current-version", required=True)
    update.add_argument("--allow-downgrade", action="store_true")
    update.add_argument("--allow-personal-ad-hoc", action="store_true")
    update.set_defaults(handler=_plan_update)

    def add_core_access(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--allow-root", action="append", required=True,
            help="Dosya erişimine açık yerel kök; birden çok kez verilebilir",
        )
        command.add_argument("--allow-network", action="store_true")

    core_preflight = subparsers.add_parser("core-preflight", help="JSON çekirdek ön incelemesi")
    core_preflight.add_argument("source")
    core_preflight.add_argument("--download-only", action="store_true")
    core_preflight.add_argument("--subtitle", action="store_true")
    core_preflight.add_argument("--summary", action="store_true")
    core_preflight.add_argument("--dub", action="store_true")
    core_preflight.add_argument("--job-kind", choices=("video", "document"), default="video")
    add_core_access(core_preflight)
    core_preflight.set_defaults(handler=_core_command, core_action="preflight")

    core_create = subparsers.add_parser("core-job-create", help="JSON çekirdek işi oluştur")
    core_create.add_argument("source")
    core_create.add_argument("job_directory")
    core_create.add_argument("--source-language", default="auto")
    core_create.add_argument("--download-only", action="store_true")
    core_create.add_argument("--subtitle", action="store_true")
    core_create.add_argument("--summary", action="store_true")
    core_create.add_argument("--dub", action="store_true")
    core_create.add_argument("--job-kind", choices=("video", "document"), default="video")
    add_core_access(core_create)
    core_create.set_defaults(handler=_core_command, core_action="create")

    for command_name, action, help_text in (
        ("core-job-status", "status", "JSON iş durumunu oku"),
        ("core-job-stop", "stop", "İşi kaynakları silmeden durdur"),
        ("core-job-resume", "resume", "İşi kaldığı yerden kuyruğa al"),
        ("core-results", "results", "İş sonuçlarını JSON listele"),
    ):
        command = subparsers.add_parser(command_name, help=help_text)
        command.add_argument("job_id")
        if action == "stop":
            command.add_argument("--confirm", action="store_true", required=True)
        add_core_access(command)
        command.set_defaults(handler=_core_command, core_action=action)

    core_export = subparsers.add_parser("core-export", help="İş sonuçlarını doğrulayarak kopyala")
    core_export.add_argument("job_id")
    core_export.add_argument("destination_directory")
    core_export.add_argument("--folder-name", default="KSI Local Studio Çıktısı")
    core_export.add_argument("--confirm", action="store_true", required=True)
    add_core_access(core_export)
    core_export.set_defaults(handler=_core_command, core_action="export")

    article = subparsers.add_parser(
        "read-article", help="HTTPS blog/haber sayfasından ana metni güvenle çıkar"
    )
    article.add_argument("url")
    article.add_argument("output_directory")
    article.add_argument("--robots-file")
    article.add_argument("--timeout", type=int, default=20)
    article.set_defaults(handler=_read_article)

    article_package = subparsers.add_parser(
        "package-article", help="Makale kaynağı ve Türkçe çeviriden izlenebilir PDF üret"
    )
    article_package.add_argument("article_json")
    article_package.add_argument("translation_file")
    article_package.add_argument("output_directory")
    article_package.set_defaults(handler=_package_article)

    def add_session_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--browser", choices=("chrome", "firefox"))
        command.add_argument(
            "--browser-profile",
            help="Kullanıcının açıkça seçtiği ayrı tarayıcı profili klasörü",
        )
        command.add_argument(
            "--confirm-udemy-access",
            action="store_true",
            help="Tek Udemy dersine erişim hakkını açıkça onayla",
        )

    health = subparsers.add_parser("health", help="Kurulu araçları salt okunur denetle")
    health.add_argument("--json", action="store_true", help="JSON çıktı üret")
    health.set_defaults(handler=_health)

    acceptance = subparsers.add_parser(
        "acceptance", help="Paket, SSD, araç, model ve önbellek kabul denetimi"
    )
    acceptance.add_argument("--json", action="store_true", help="JSON çıktı üret")
    acceptance.add_argument(
        "--full-model-hashes",
        action="store_true",
        help="Yaklaşık 12 GiB model dosyasının SHA-256 değerini yeniden doğrula",
    )
    acceptance.add_argument("--app", help="Denetlenecek KSI Local Studio.app yolu")
    acceptance.set_defaults(handler=_acceptance)

    acceptance_create = subparsers.add_parser(
        "acceptance-create", help="Yeni ve gizlilik korumalı Faz 38 kullanıcı kabul kaydı oluştur"
    )
    acceptance_create.add_argument("output")
    acceptance_create.set_defaults(handler=_acceptance_create)

    acceptance_status = subparsers.add_parser(
        "acceptance-status", help="Faz 38 kullanıcı kabul ilerlemesini göster"
    )
    acceptance_status.add_argument("session")
    acceptance_status.set_defaults(handler=_acceptance_status)

    acceptance_record = subparsers.add_parser(
        "acceptance-record", help="Bir Faz 38 kullanıcı kabul sonucunu kaydet"
    )
    acceptance_record.add_argument("session")
    acceptance_record.add_argument("case")
    acceptance_record.add_argument("status", choices=("pending", "passed", "failed"))
    acceptance_record.add_argument("--rating", action="append", help="name=1..5 kalite puanı")
    acceptance_record.add_argument("--note")
    acceptance_record.add_argument("--artifact-sha256")
    acceptance_record.add_argument("--expected")
    acceptance_record.add_argument("--actual")
    acceptance_record.add_argument("--step", action="append")
    acceptance_record.set_defaults(handler=_acceptance_record)

    volumes = subparsers.add_parser("volumes", help="Bağlı harici diskleri salt okunur denetle")
    volumes.add_argument("--json", action="store_true", help="JSON çıktı üret")
    volumes.set_defaults(handler=_volumes)

    migrate = subparsers.add_parser(
        "migrate-predecessor",
        help="Özel önceki sürümün ayar ve çalışma alanını KSI'ye taşı",
    )
    migrate.add_argument("--mount", required=True, help="Harici SSD bağlama noktası")
    migrate.add_argument("--home", help="Test veya özel kullanıcı ana klasörü")
    migrate.add_argument(
        "--apply",
        action="store_true",
        help="Varsayılan salt okunur rapor yerine doğrulanmış geçişi uygula",
    )
    migrate.set_defaults(handler=_migrate_predecessor)

    probe = subparsers.add_parser("plan-probe", help="Ağ isteği yapmadan yt-dlp planı oluştur")
    probe.add_argument("url")
    probe.add_argument("--yt-dlp", help="yt-dlp yürütülebilir yolu")
    runtime = probe.add_mutually_exclusive_group()
    runtime.add_argument("--deno", help="Deno yürütülebilir yolu")
    runtime.add_argument("--node", help="Node yürütülebilir yolu")
    add_session_arguments(probe)
    probe.set_defaults(handler=_plan_probe)

    execute_probe = subparsers.add_parser(
        "probe", help="Doğrulanmış yt-dlp ile yalnız metadata isteği yap"
    )
    execute_probe.add_argument("url")
    execute_probe.add_argument("--yt-dlp", required=True, help="Doğrulanmış yt-dlp yolu")
    execute_runtime = execute_probe.add_mutually_exclusive_group()
    execute_runtime.add_argument("--deno", help="Deno yürütülebilir yolu")
    execute_runtime.add_argument("--node", help="Node yürütülebilir yolu")
    execute_probe.add_argument("--timeout", type=int, default=60)
    add_session_arguments(execute_probe)
    execute_probe.set_defaults(handler=_probe)

    collection_plan = subparsers.add_parser(
        "plan-collection-probe",
        help="Ağ isteği yapmadan sınırsız YouTube kanal/liste inceleme planı oluştur",
    )
    collection_plan.add_argument("url")
    collection_plan.add_argument("--yt-dlp")
    collection_plan.add_argument("--deno")
    collection_plan.set_defaults(handler=_plan_collection_probe)

    collection_preflight = subparsers.add_parser(
        "preflight-collection",
        help="YouTube kanal/listesini tek istekte incele ve kuyruk disk bütçesini hesapla",
    )
    collection_preflight.add_argument("url")
    collection_preflight.add_argument("--video-id", action="append")
    collection_preflight.add_argument("--max-height", type=int, default=1080)
    collection_preflight.add_argument("--download-only", action="store_true")
    collection_preflight.add_argument("--confirm-live-recording", action="store_true")
    collection_preflight.add_argument("--live-limit-seconds", type=int)
    collection_preflight.add_argument("--timeout", type=int, default=15 * 60)
    collection_preflight.set_defaults(handler=_preflight_collection)

    catalog_search = subparsers.add_parser(
        "catalog-search", help="Paketli yasal public katalogda çevrimdışı ara"
    )
    catalog_search.add_argument("query", nargs="?", default="")
    catalog_search.set_defaults(handler=_catalog_search)

    catalog_private = subparsers.add_parser(
        "catalog-add-private", help="Application Support'a kişisel yer imi ekle"
    )
    catalog_private.add_argument("title")
    catalog_private.add_argument("url")
    catalog_private.add_argument("--note")
    catalog_private.set_defaults(handler=_catalog_add_private)

    image_inspect = subparsers.add_parser(
        "image-inspect", help="Görseli değiştirmeden ölçü, metadata ve bellek bütçesini denetle"
    )
    image_inspect.add_argument("source")
    image_inspect.set_defaults(handler=_image_inspect)

    image_resize = subparsers.add_parser(
        "image-resize", help="Görseli yeni dosyaya yerel Lanczos ile boyutlandır"
    )
    image_resize.add_argument("source")
    image_resize.add_argument("output")
    image_resize.add_argument("--width", type=int)
    image_resize.add_argument("--height", type=int)
    image_resize.add_argument("--percent", type=float)
    image_resize.add_argument("--unlock-aspect", action="store_true")
    image_resize.add_argument("--format", choices=("PNG", "WEBP", "JPEG"), default="PNG")
    image_resize.add_argument("--lossless", action=argparse.BooleanOptionalAction, default=True)
    image_resize.add_argument("--super-resolution-pilot", action="store_true")
    image_resize.set_defaults(handler=_image_resize)

    image_remove = subparsers.add_parser(
        "image-remove-background",
        help="Tek renkli arka planı tamamen yerel kaldır ve şeffaf çıktı üret",
    )
    image_remove.add_argument("source")
    image_remove.add_argument("output")
    image_remove.add_argument("--tolerance", type=int, default=28)
    image_remove.add_argument("--feather", type=int, default=12)
    image_remove.add_argument("--product-shadow", action="store_true")
    image_remove.set_defaults(handler=_image_remove_background)

    image_transform = subparsers.add_parser(
        "image-transform", help="Görseli yeniden örneklemeden döndür veya aynala"
    )
    image_transform.add_argument("source")
    image_transform.add_argument("output")
    image_transform.add_argument(
        "--operation",
        required=True,
        choices=("rotate-90", "rotate-180", "rotate-270", "flip-horizontal", "flip-vertical"),
    )
    image_transform.set_defaults(handler=_image_transform)

    edit_start = subparsers.add_parser(
        "image-edit-start", help="Genel veya platforma özel ürün düzenleme oturumu başlat"
    )
    edit_start.add_argument("source")
    edit_start.add_argument("session_directory")
    edit_start.add_argument("--workflow", choices=("general", "product"), required=True)
    edit_start.add_argument("--platform", choices=tuple(PLATFORM_PROFILES))
    edit_start.set_defaults(handler=_image_edit_start)

    edit_preview = subparsers.add_parser(
        "image-edit-preview", help="Tam çıktıdan önce küçük, yerel prompt ön izlemesi üret"
    )
    edit_preview.add_argument("session_directory")
    edit_preview.add_argument("prompt")
    edit_preview.add_argument("--protect-mask")
    edit_preview.add_argument("--seed", type=int)
    edit_preview.set_defaults(handler=_image_edit_preview)

    edit_commit = subparsers.add_parser(
        "image-edit-commit", help="Onaylanmış ön izlemeden yeni tam çözünürlüklü revizyon üret"
    )
    edit_commit.add_argument("session_directory")
    edit_commit.add_argument("--approve-preview", action="store_true", required=True)
    edit_commit.set_defaults(handler=_image_edit_commit)

    edit_undo = subparsers.add_parser(
        "image-edit-undo", help="Dosya silmeden önceki görsel revizyonuna dön"
    )
    edit_undo.add_argument("session_directory")
    edit_undo.set_defaults(handler=_image_edit_undo)

    edit_compare = subparsers.add_parser(
        "image-edit-compare", help="Özgün ve geçerli revizyonu yan yana PNG olarak çıkar"
    )
    edit_compare.add_argument("session_directory")
    edit_compare.add_argument("output")
    edit_compare.set_defaults(handler=_image_edit_compare)

    local_probe = subparsers.add_parser("probe-file", help="Yerel medyayı ffprobe ile doğrula")
    local_probe.add_argument("path")
    local_probe.add_argument("--ffprobe", help="ffprobe yürütülebilir yolu")
    local_probe.set_defaults(handler=_probe_file)

    preflight = subparsers.add_parser(
        "preflight", help="Kaynağı indirmeden incele ve SSD bütçesini hesapla"
    )
    preflight.add_argument("source")
    preflight.add_argument("--download-only", action="store_true")
    preflight.add_argument("--subtitle", action="store_true")
    preflight.add_argument("--summary", action="store_true")
    preflight.add_argument("--dub", action="store_true")
    preflight.add_argument("--ffmpeg")
    preflight.add_argument("--ffprobe")
    add_session_arguments(preflight)
    preflight.set_defaults(handler=_preflight)

    budget = subparsers.add_parser("budget", help="İş için muhafazakâr SSD bütçesi")
    budget.add_argument("--duration", type=float, required=True, help="Video süresi, saniye")
    budget.add_argument("--free-gib", type=float, required=True, help="SSD boş alanı, GiB")
    budget.add_argument(
        "--missing-model-gib",
        type=float,
        default=13.0,
        help="SSD'ye henüz indirilecek modeller, GiB",
    )
    budget.set_defaults(handler=_budget)

    plan_download = subparsers.add_parser(
        "plan-download", help="Ağ isteği yapmadan güvenli indirme planını göster"
    )
    plan_download.add_argument("url")
    plan_download.add_argument("--output-directory", required=True)
    plan_download.add_argument("--yt-dlp", required=True)
    plan_download.add_argument("--ffmpeg")
    plan_download.add_argument("--deno")
    plan_download.add_argument("--max-height", type=int, default=1080)
    plan_download.add_argument("--media-index", type=int, default=1)
    plan_download.add_argument(
        "--mode", choices=tuple(DownloadMode), default=DownloadMode.VIDEO
    )
    add_session_arguments(plan_download)
    plan_download.add_argument(
        "--subtitle-language",
        action="append",
        choices=SUPPORTED_SUBTITLE_LANGUAGES,
    )
    plan_download.set_defaults(handler=_plan_download)

    download = subparsers.add_parser("download", help="Videoyu ve uygun altyazıları indir")
    download.add_argument("url")
    download.add_argument("--output-directory", required=True)
    download.add_argument("--yt-dlp", required=True)
    download.add_argument("--ffmpeg")
    download.add_argument("--ffprobe")
    download.add_argument("--deno")
    download.add_argument("--max-height", type=int, default=1080)
    download.add_argument("--media-index", type=int, default=1)
    download.add_argument(
        "--mode", choices=tuple(DownloadMode), default=DownloadMode.VIDEO
    )
    download.add_argument("--require-audio", action="store_true")
    add_session_arguments(download)
    download.add_argument(
        "--subtitle-language",
        action="append",
        choices=SUPPORTED_SUBTITLE_LANGUAGES,
    )
    download.add_argument("--timeout", type=int, default=4 * 60 * 60)
    download.set_defaults(handler=_download)

    translate = subparsers.add_parser("translate-srt", help="Desteklenen SRT'yi seçili hedef dile çevir")
    translate.add_argument("input")
    translate.add_argument("output")
    translate.add_argument(
        "--source-language", choices=(*SOURCE_LANGUAGE_CHOICES, "tr"), required=True
    )
    translate.add_argument(
        "--target-language", choices=tuple(VERIFIED_TARGET_LANGUAGES), default="tr"
    )
    translate.add_argument("--ollama", required=True)
    translate.add_argument("--models-directory", required=True)
    translate.add_argument("--ollama-url", default="http://127.0.0.1:11435")
    translate.add_argument("--model", default="translategemma:4b-it-q8_0")
    translate.add_argument("--engine", choices=("gemma", "argos"), default="gemma")
    translate.add_argument("--batch-size", type=int, default=12)
    translate.add_argument("--timeout", type=int, default=600)
    translate.add_argument("--glossary")
    translate.add_argument("--quality-report")
    translate.set_defaults(handler=_translate_srt)

    document_translate = subparsers.add_parser(
        "translate-document", help="Kanonik belge bloklarını yerel modelle Türkçeye çevir"
    )
    document_translate.add_argument("input")
    document_translate.add_argument("output_directory")
    document_translate.add_argument(
        "--source-language", choices=SOURCE_LANGUAGE_CHOICES, required=True
    )
    document_translate.add_argument("--ollama", required=True)
    document_translate.add_argument("--models-directory", required=True)
    document_translate.add_argument("--ollama-url", default="http://127.0.0.1:11435")
    document_translate.add_argument("--model", default="translategemma:4b-it-q8_0")
    document_translate.add_argument("--engine", choices=("gemma", "argos"), default="gemma")
    document_translate.add_argument("--batch-size", type=int, default=6)
    document_translate.add_argument("--timeout", type=int, default=600)
    document_translate.add_argument("--glossary")
    document_translate.add_argument("--custom-glossary")
    document_translate.add_argument("--checkpoint")
    document_translate.add_argument("--source-title")
    document_translate.set_defaults(handler=_translate_document)

    document_summary = subparsers.add_parser(
        "summarize-document",
        help="Kanonik belgeyi kaynak bloklarına bağlı Türkçe özetle",
    )
    document_summary.add_argument("input")
    document_summary.add_argument("output_directory")
    document_summary.add_argument("--ollama", required=True)
    document_summary.add_argument("--models-directory", required=True)
    document_summary.add_argument("--ollama-url", default="http://127.0.0.1:11435")
    document_summary.add_argument("--model", default="qwen3.5:4b")
    document_summary.add_argument("--timeout", type=int, default=600)
    document_summary.add_argument(
        "--summary-source", choices=("auto", "source", "translation"), default="auto"
    )
    document_summary.add_argument(
        "--profile", choices=("short", "standard", "detailed"), default="standard"
    )
    document_summary.add_argument("--translation")
    document_summary.add_argument("--translation-quality")
    document_summary.add_argument("--source-title", default="Belge")
    document_summary.add_argument("--source-reference", default="Yerel belge")
    document_summary.add_argument("--max-chars", type=int, default=8000)
    document_summary.add_argument("--checkpoint")
    document_summary.add_argument("--pdf", action="store_true")
    document_summary.set_defaults(handler=_summarize_document)

    quality = subparsers.add_parser(
        "quality-srt", help="Türkçe SRT için mekanik kalite raporu üret"
    )
    quality.add_argument("source")
    quality.add_argument("translated")
    quality.add_argument(
        "--source-language", choices=SOURCE_LANGUAGE_CHOICES, required=True
    )
    quality.add_argument("--glossary")
    quality.add_argument("--output")
    quality.set_defaults(handler=_quality_srt)

    summarize = subparsers.add_parser("summarize-srt", help="SRT konuşmasını Türkçe özetle")
    summarize.add_argument("input")
    summarize.add_argument("output")
    summarize.add_argument("--ollama", required=True)
    summarize.add_argument("--models-directory", required=True)
    summarize.add_argument("--ollama-url", default="http://127.0.0.1:11435")
    summarize.add_argument("--model", default="qwen3.5:4b")
    summarize.add_argument("--timeout", type=int, default=600)
    summarize.add_argument("--source-title", default="Bilinmiyor")
    summarize.add_argument("--source-reference", default="Yerel dosya")
    summarize.add_argument("--trace-report")
    summarize.add_argument("--quality-report")
    summarize.add_argument("--max-chars", type=int, default=9000)
    summarize.add_argument("--chunk-seconds", type=int, default=5 * 60)
    summarize.set_defaults(handler=_summarize_srt)

    export = subparsers.add_parser("export", help="Tamamlanan çıktıları Masaüstüne kopyala")
    export.add_argument("artifacts", nargs="+")
    export.add_argument("--desktop", required=True)
    export.add_argument("--folder-name", default="KSI Local Studio Çıktısı")
    export.set_defaults(handler=_export)

    transcribe = subparsers.add_parser("transcribe", help="Yerel medyayı MLX Whisper ile SRT yap")
    transcribe.add_argument("input")
    transcribe.add_argument("output")
    transcribe.add_argument("--source-language", choices=SOURCE_LANGUAGE_CHOICES, required=True)
    transcribe.add_argument("--model", default=DEFAULT_WHISPER_MODEL)
    transcribe.add_argument("--glossary")
    transcribe.set_defaults(handler=_transcribe)

    dub_quality = subparsers.add_parser(
        "quality-dub", help="Türkçe dublajı yeniden yazıya çevirip kaliteyi ölç"
    )
    dub_quality.add_argument("target_srt")
    dub_quality.add_argument("audio")
    dub_quality.add_argument("recognized_srt")
    dub_quality.add_argument("--timing-report", required=True)
    dub_quality.add_argument("--output", required=True)
    dub_quality.add_argument("--model", default=DEFAULT_WHISPER_MODEL)
    dub_quality.set_defaults(handler=_quality_dub)

    mux_dub = subparsers.add_parser(
        "mux-dub", help="Türkçe dublaj sesini kaynak videoyla birleştir"
    )
    mux_dub.add_argument("source")
    mux_dub.add_argument("audio")
    mux_dub.add_argument("output")
    mux_dub.add_argument("--ffmpeg", required=True)
    mux_dub.add_argument("--ffprobe", required=True)
    mux_dub.add_argument("--original-volume", type=float, default=0.12)
    mux_dub.add_argument("--quality-report")
    mux_dub.set_defaults(handler=_mux_dub)

    mux_subtitle = subparsers.add_parser(
        "mux-subtitle",
        help="Türkçe altyazıyı kalite kaybı olmadan final videoya ekle",
    )
    mux_subtitle.add_argument("source")
    mux_subtitle.add_argument("subtitle")
    mux_subtitle.add_argument("output")
    mux_subtitle.add_argument("--ffmpeg", required=True)
    mux_subtitle.add_argument("--ffprobe", required=True)
    mux_subtitle.set_defaults(handler=_mux_subtitle)

    local_media = subparsers.add_parser("media-tools", help="Yerel dönüştürme, kesme ve altyazı araçları")
    local_media.add_argument("sources", nargs="+")
    local_media.add_argument("--operation", choices=("convert", "trim", "join", "remux", "burn_subtitle", "remove_background_video"), default="convert")
    local_media.add_argument("--format", choices=("mp4", "mkv", "mov", "webm", "mp3", "wav", "flac", "aac", "gif"), default="mp4")
    local_media.add_argument("--profile", choices=("share", "small", "archive"), default="share")
    local_media.add_argument("--start", type=float, default=0)
    local_media.add_argument("--end", type=float)
    local_media.add_argument("--lossless", action="store_true")
    local_media.add_argument("--subtitle")
    local_media.set_defaults(handler=_local_media_tool)

    return parser


def _local_media_tool(args: argparse.Namespace) -> int:
    from ksi_local.core_service import CoreService

    workspace = resolve_workspace()
    sources = [str(Path(source).expanduser().resolve()) for source in args.sources]
    destination = workspace.outputs / (Path(sources[0]).stem + "-ksi." + args.format)
    roots = tuple(Path(source).parent for source in sources)
    if args.subtitle:
        roots += (Path(args.subtitle).expanduser().resolve().parent,)
    service = CoreService(workspace=workspace, allowed_roots=roots)
    job = service.submit_media_tool({
        "sources": sources, "destination": str(destination), "operation": args.operation,
        "profile": args.profile, "start": args.start, "end": args.end,
        "lossless": args.lossless, "subtitle": args.subtitle,
    }, confirm=True)
    result = service.execute_tool_job(job["id"], confirm=True)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    # GUI cancellation can stop yt-dlp/FFmpeg descendants as one worker group.
    # An Ollama child has its own session and is cleaned by its context manager.
    if os.environ.get("KSI_WORKER") == "1" and hasattr(os, "setsid"):
        try:
            os.setsid()
        except OSError:
            pass
        signal.signal(signal.SIGTERM, _handle_termination)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except SourceURLValidationError as error:
        print(f"Geçersiz kaynak: {error}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Hata: {error}", file=sys.stderr)
        return 1
