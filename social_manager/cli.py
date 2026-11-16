from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .core import (
    OUTPUT_ROOT,
    REMOTION_DIR,
    ROOT,
    StoryboardValidationError,
    load_storyboard,
    manifest_template,
    production_directory,
    resolve_scene_timing,
    run_command,
    slugify,
    validate_with_zod,
    wav_duration_seconds,
    write_json,
)
from .tts import TTSResult, TTSUnavailableError, generate_narration
from .connectors import (
    ConnectorError,
    load_competitor_config,
    sync_instagram_competitor,
    sync_instagram_owned,
    sync_youtube_competitor,
    sync_youtube_owned,
)
from .intelligence import (
    DEFAULT_DATABASE,
    IntelligenceError,
    LocalKnowledgeStore,
    import_json_file,
    register_draft,
    write_analysis_artifacts,
)
from .agency.store import AgencyStore


def _parse_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _intelligence_store(database: Path | None) -> LocalKnowledgeStore:
    store = LocalKnowledgeStore(database or DEFAULT_DATABASE)
    store.initialize()
    return store


def _agency_store(database: Path | None) -> AgencyStore:
    store = AgencyStore(database or DEFAULT_DATABASE)
    store.initialize()
    return store


def _sync_dates(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=90)
    if start > end:
        raise ValueError("start date cannot be after end date")
    return start.isoformat(), end.isoformat()


def _print_command_failure(label: str, result: Any) -> None:
    print(f"{label} failed (exit {result.returncode}).", file=sys.stderr)
    if result.stdout:
        print(result.stdout[-4000:], file=sys.stderr)
    if result.stderr:
        print(result.stderr[-4000:], file=sys.stderr)


def _validate_path(path: Path) -> dict[str, Any]:
    storyboard = load_storyboard(path)
    normalized = validate_with_zod(path)
    return normalized or storyboard


def _composition_id(storyboard: dict[str, Any]) -> str:
    if storyboard["aspect_ratio"] == "16:9":
        return "Long-Form-YouTube"
    return "Production-Short"


def _thumbnail_id(storyboard: dict[str, Any]) -> str:
    return "Thumbnail-YouTube" if storyboard["aspect_ratio"] == "16:9" else "Thumbnail-Vertical"


def _write_content_files(storyboard: dict[str, Any], output_dir: Path) -> None:
    metadata = storyboard["metadata"]
    (output_dir / "caption.txt").write_text(str(metadata["caption"]).strip() + "\n", encoding="utf-8")
    (output_dir / "script.md").write_text(f"# {storyboard['title']}\n\n{storyboard['narration_script'].strip()}\n", encoding="utf-8")
    sources = ["# Sources", ""]
    if storyboard.get("fictional"):
        sources.append("This is a fictional engineering simulation. It makes no claim about a real company or incident.")
    for source in storyboard.get("source_references", []):
        url = source.get("url")
        suffix = f" — {url}" if url else ""
        note = f" ({source['note']})" if source.get("note") else ""
        sources.append(f"- {source['label']}{note}{suffix}")
    (output_dir / "sources.md").write_text("\n".join(sources) + "\n", encoding="utf-8")
    if storyboard["aspect_ratio"] == "16:9":
        (output_dir / "youtube_description.txt").write_text(str(metadata.get("youtube_description") or metadata["caption"]).strip() + "\n", encoding="utf-8")
        (output_dir / "youtube_tags.txt").write_text(", ".join(str(tag) for tag in metadata.get("tags", [])) + "\n", encoding="utf-8")


def _remotion(args: list[str]) -> Any:
    npx = "npx.cmd" if os.name == "nt" else "npx"
    return run_command([npx, "remotion", *args], REMOTION_DIR)


def _render_media(storyboard: dict[str, Any], props_path: Path, output_dir: Path, quality: str) -> tuple[Any, Any]:
    composition = _composition_id(storyboard)
    video_path = output_dir / ("preview.mp4" if quality == "preview" else "video.mp4")
    default_concurrency = max(1, min(4, (os.cpu_count() or 2) // 2))
    concurrency = max(1, int(os.environ.get("SOCIAL_MANAGER_RENDER_CONCURRENCY", str(default_concurrency))))
    render_args = ["render", "src/index.ts", composition, str(video_path), "--props", str(props_path), "--codec", "h264", "--concurrency", str(concurrency)]
    if quality == "preview":
        render_args.extend(["--scale", "0.5"])
    render_result = _remotion(render_args)
    if render_result.returncode != 0:
        _print_command_failure("Remotion video render", render_result)
        raise RuntimeError("Remotion video render failed")
    thumbnail_result = _remotion(["still", "src/index.ts", _thumbnail_id(storyboard), str(output_dir / "thumbnail.png"), "--props", str(props_path)])
    if thumbnail_result.returncode != 0:
        _print_command_failure("Remotion thumbnail render", thumbnail_result)
        raise RuntimeError("Remotion thumbnail render failed")
    return render_result, thumbnail_result


def inspect_output(output_dir: Path, preview: bool = False) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    video_path = output_dir / ("preview.mp4" if preview else "video.mp4")
    if not video_path.exists():
        raise RuntimeError(f"Video is missing: {video_path}")
    probe = _remotion(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate", "-show_entries", "format=duration", "-of", "json", str(video_path)])
    if probe.returncode != 0:
        _print_command_failure("FFprobe", probe)
        raise RuntimeError("FFprobe validation failed")
    payload = json.loads(probe.stdout)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None or audio is None:
        raise RuntimeError("Video validation failed: expected both video and audio streams")
    if not (output_dir / "thumbnail.png").exists():
        raise RuntimeError("Thumbnail validation failed: thumbnail.png is missing")
    if not (output_dir / "caption.txt").exists():
        raise RuntimeError("Caption validation failed: caption.txt is missing")
    if len((output_dir / "caption.txt").read_text(encoding="utf-8")) > 2200:
        raise RuntimeError("Caption validation failed: caption exceeds 2200 characters")
    decode_dir = output_dir / "inspection"
    decode_dir.mkdir(exist_ok=True)
    decode_frame = decode_dir / ".decode-smoke.png"
    decoded = _remotion(["ffmpeg", "-y", "-v", "error", "-i", str(video_path), "-frames:v", "1", str(decode_frame)])
    if decoded.returncode != 0:
        _print_command_failure("FFmpeg decode", decoded)
        raise RuntimeError("Decode validation failed")
    if not decode_frame.exists() or decode_frame.stat().st_size == 0:
        raise RuntimeError("Decode validation failed: frame extraction produced no image")
    decode_frame.unlink()
    return {"video": video, "audio": audio, "duration_seconds": float(payload["format"]["duration"])}


def extract_inspection_frames(output_dir: Path, duration_seconds: float, preview: bool = False) -> list[Path]:
    output_dir = output_dir.resolve()
    video_path = output_dir / ("preview.mp4" if preview else "video.mp4")
    frames_dir = output_dir / "inspection"
    frames_dir.mkdir(exist_ok=True)
    positions = [0.2, min(2.0, duration_seconds * .2), duration_seconds * .5, duration_seconds * .78, max(.1, duration_seconds - .45)]
    paths = [frames_dir / f"frame-{index:02d}.png" for index in range(1, len(positions) + 1)]
    arguments = ["ffmpeg", "-y"]
    for position in positions:
        arguments.extend(["-ss", f"{position:.3f}", "-i", str(video_path)])
    for index, path in enumerate(paths):
        arguments.extend(["-map", f"{index}:v:0", "-frames:v", "1", str(path)])
    result = _remotion(arguments)
    if result.returncode != 0:
        _print_command_failure("FFmpeg frame extraction", result)
        raise RuntimeError("Representative frame extraction failed")
    if any(not path.exists() or path.stat().st_size == 0 for path in paths):
        raise RuntimeError("Representative frame extraction failed: one or more frames are missing")
    return paths


def render_thumbnail_from_package(output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    storyboard_path = output_dir / "storyboard.json"
    storyboard = _validate_path(storyboard_path)
    job_dir = ROOT / ".work" / f"thumbnail-{uuid.uuid4().hex[:10]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        props_path = job_dir / "props.json"
        write_json(props_path, {"storyboard": storyboard})
        result = _remotion(["still", "src/index.ts", _thumbnail_id(storyboard), str(output_dir / "thumbnail.png"), "--props", str(props_path)])
        if result.returncode != 0:
            _print_command_failure("Remotion thumbnail render", result)
            raise RuntimeError("Remotion thumbnail render failed")
        return output_dir / "thumbnail.png"
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def finalize_package(output_dir: Path, preview: bool = False) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    inspection = inspect_output(output_dir, preview=preview)
    frames = extract_inspection_frames(output_dir, inspection["duration_seconds"], preview=preview)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "success"
    manifest["validation_status"] = "passed"
    manifest["errors"] = []
    manifest["render_finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["duration_seconds"] = inspection["duration_seconds"]
    manifest.setdefault("outputs", {}).update({"video": str(output_dir / ("preview.mp4" if preview else "video.mp4")), "thumbnail": str(output_dir / "thumbnail.png"), "inspection_frames": [str(path) for path in frames]})
    manifest["technical_validation"] = inspection
    write_json(manifest_path, manifest)
    return manifest


def produce(storyboard_path: Path, production_date: date, overwrite: bool, quality: str, no_render: bool, narration_wav: Path | None = None, narration_voice_name: str | None = None) -> Path:
    storyboard = _validate_path(storyboard_path)
    output_dir = production_directory(storyboard, production_date)
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise RuntimeError(f"Refusing to overwrite existing package: {output_dir}. Use --overwrite only for an intentional replacement.")
    output_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    manifest = manifest_template(storyboard, output_dir, job_id)
    manifest["render_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(output_dir / "manifest.json", manifest)
    work_dir = ROOT / ".work" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        narration_work = work_dir / "narration.wav"
        if narration_wav is not None:
            if not narration_wav.exists():
                raise TTSUnavailableError(f"Provided narration WAV is missing: {narration_wav}")
            shutil.copy2(narration_wav, narration_work)
            result = TTSResult(narration_work, "provided_local_wav", narration_voice_name or "provided local narrator", wav_duration_seconds(narration_work))
        else:
            result = generate_narration(storyboard["narration_script"], storyboard["narration_voice"], narration_work, {"provider": "auto", "speed": 180})
        audio_duration = result.duration_seconds
        resolved = resolve_scene_timing(storyboard, audio_duration)
        slug = slugify(str(resolved["title"]))
        public_audio = REMOTION_DIR / "public" / "jobs" / slug / "narration.wav"
        public_audio.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(narration_work, public_audio)
        shutil.copy2(narration_work, output_dir / "narration.wav")
        resolved["narration_audio"] = f"jobs/{slug}/narration.wav"
        resolved["output_directory"] = str(output_dir)
        write_json(output_dir / "storyboard.json", resolved)
        _write_content_files(resolved, output_dir)
        props_path = work_dir / "props.json"
        write_json(props_path, {"storyboard": resolved})
        manifest["narration_status"] = "success"
        manifest["narration_provider"] = result.provider
        manifest["narration_voice_resolved"] = result.voice_id
        manifest["audio_duration_seconds"] = round(audio_duration, 3)
        manifest["duration_seconds"] = round(sum(scene["duration_seconds"] for scene in resolved["scenes"]), 3)
        manifest["caption_timing"] = "estimated"
        manifest["asset_status"] = "success"
        manifest["outputs"] = {"narration": str(output_dir / "narration.wav"), "storyboard": str(output_dir / "storyboard.json"), "caption": str(output_dir / "caption.txt")}
        write_json(output_dir / "manifest.json", manifest)
        if no_render:
            manifest["status"] = "packaged_without_render"
            manifest["validation_status"] = "not_rendered"
            write_json(output_dir / "manifest.json", manifest)
            return output_dir
        _render_media(resolved, props_path, output_dir, quality)
        finalize_package(output_dir, preview=quality == "preview")
        return output_dir
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["validation_status"] = "failed"
        manifest["errors"].append(str(exc))
        write_json(output_dir / "manifest.json", manifest)
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m social_manager", description="Local AI video production engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="Validate a storyboard using Python checks and Zod")
    validate_parser.add_argument("--storyboard", required=True, type=Path)
    for command in ("produce", "render", "preview", "package"):
        child = subparsers.add_parser(command, help=f"{command.title()} a production package")
        child.add_argument("--storyboard", required=True, type=Path)
        child.add_argument("--date", help="Output date in YYYY-MM-DD (defaults to today)")
        child.add_argument("--overwrite", action="store_true", help="Replace an existing package intentionally")
        child.add_argument("--narration-wav", type=Path, help="Use an already generated, locally sourced WAV instead of synthesizing it")
        child.add_argument("--narration-voice-name", help="Actual voice name for a supplied local narration WAV")
    inspect_parser = subparsers.add_parser("inspect", help="Validate an existing rendered package")
    inspect_parser.add_argument("--output", required=True, type=Path)
    inspect_parser.add_argument("--preview", action="store_true")
    thumbnail_parser = subparsers.add_parser("thumbnail", help="Render a thumbnail for an existing package")
    thumbnail_parser.add_argument("--output", required=True, type=Path)
    finalize_parser = subparsers.add_parser("finalize", help="Validate video, extract frames, and mark an existing package successful")
    finalize_parser.add_argument("--output", required=True, type=Path)
    finalize_parser.add_argument("--preview", action="store_true")

    intelligence_init_parser = subparsers.add_parser("intelligence-init", help="Create the local SQLite knowledge store")
    intelligence_init_parser.add_argument("--database", type=Path, help="SQLite path (defaults to data/social_manager.sqlite3)")

    import_parser = subparsers.add_parser("import-analytics", help="Import a normalized local analytics JSON file into SQLite")
    import_parser.add_argument("--input", required=True, type=Path)
    import_parser.add_argument("--database", type=Path)

    youtube_sync_parser = subparsers.add_parser("sync-youtube", help="Sync the authorized YouTube channel's public metadata and private analytics")
    youtube_sync_parser.add_argument("--start-date", help="Analytics start date (YYYY-MM-DD; default: 90 days ago)")
    youtube_sync_parser.add_argument("--end-date", help="Analytics end date (YYYY-MM-DD; default: today)")
    youtube_sync_parser.add_argument("--max-results", type=int, default=50, help="Most-recent videos to index (maximum 50)")
    youtube_sync_parser.add_argument("--database", type=Path)

    instagram_sync_parser = subparsers.add_parser("sync-instagram", help="Sync the authorized Instagram professional account")
    instagram_sync_parser.add_argument("--account-id", help="Instagram professional account ID (defaults to INSTAGRAM_ACCOUNT_ID)")
    instagram_sync_parser.add_argument("--max-results", type=int, default=50)
    instagram_sync_parser.add_argument("--database", type=Path)

    competitor_sync_parser = subparsers.add_parser("sync-competitor", help="Sync permitted public competitor data; no scraping or private analytics")
    competitor_sync_parser.add_argument("--platform", required=True, choices=("youtube", "instagram"))
    competitor_sync_parser.add_argument("--handle", required=True)
    competitor_sync_parser.add_argument("--max-results", type=int, default=50)
    competitor_sync_parser.add_argument("--instagram-owner-account-id", help="Optional Instagram professional account ID for Meta Business Discovery")
    competitor_sync_parser.add_argument("--database", type=Path)

    competitors_sync_parser = subparsers.add_parser("sync-competitors", help="Sync every competitor defined in a local config JSON")
    competitors_sync_parser.add_argument("--config", required=True, type=Path)
    competitors_sync_parser.add_argument("--max-results", type=int, default=50)
    competitors_sync_parser.add_argument("--instagram-owner-account-id")
    competitors_sync_parser.add_argument("--database", type=Path)

    analysis_parser = subparsers.add_parser("analyze", help="Create local research, ideas, hooks, and a ChatGPT Desktop creative-session prompt")
    analysis_parser.add_argument("--track", choices=("short_form", "long_form", "both"), default="both")
    analysis_parser.add_argument("--out", type=Path, help="Destination directory for analysis artifacts")
    analysis_parser.add_argument("--database", type=Path)

    draft_parser = subparsers.add_parser("register-draft", help="Validate and register a ChatGPT-created storyboard in the local knowledge store")
    draft_parser.add_argument("--storyboard", required=True, type=Path)
    draft_parser.add_argument("--database", type=Path)

    publication_parser = subparsers.add_parser("register-publication", help="Link a production storyboard to a platform post/video for feedback tracking")
    publication_parser.add_argument("--video-id", required=True)
    publication_parser.add_argument("--platform", required=True, choices=("youtube", "instagram"))
    publication_parser.add_argument("--external-id", required=True, help="YouTube video ID or Instagram media ID")
    publication_parser.add_argument("--account-external-id", required=True, help="Local platform account ID that owns the publication")
    publication_parser.add_argument("--url")
    publication_parser.add_argument("--database", type=Path)

    metric_parser = subparsers.add_parser("record-metric", help="Record a manual local feedback metric for an already tracked item")
    metric_parser.add_argument("--platform", required=True, choices=("youtube", "instagram"))
    metric_parser.add_argument("--external-id", required=True)
    metric_parser.add_argument("--metric", required=True)
    metric_parser.add_argument("--value", required=True, type=float)
    metric_parser.add_argument("--collected-on", help="YYYY-MM-DD; defaults to today")
    metric_parser.add_argument("--database", type=Path)

    agency_run_parser = subparsers.add_parser("agency-run", help="Create/resume today's scheduled local agency workflow and prepare valid Desktop handoffs")
    agency_run_parser.add_argument("--date", help="Run date in YYYY-MM-DD (defaults to today)")
    agency_run_parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            storyboard = _validate_path(args.storyboard)
            print(f"VALID: {storyboard['video_id']} ({len(storyboard['scenes'])} scenes)")
            return
        if args.command == "inspect":
            print(json.dumps(inspect_output(args.output, args.preview), indent=2))
            return
        if args.command == "thumbnail":
            print(f"THUMBNAIL: {render_thumbnail_from_package(args.output)}")
            return
        if args.command == "finalize":
            print(json.dumps(finalize_package(args.output, args.preview), indent=2))
            return
        if args.command == "intelligence-init":
            store = _intelligence_store(args.database)
            print(f"INTELLIGENCE DATABASE: {store.database_path}")
            return
        if args.command == "import-analytics":
            result = import_json_file(_intelligence_store(args.database), args.input)
            print(f"IMPORTED: {json.dumps(result)}")
            return
        if args.command == "sync-youtube":
            start, end = _sync_dates(args.start_date, args.end_date)
            result = sync_youtube_owned(_intelligence_store(args.database), start, end, args.max_results)
            print(f"YOUTUBE SYNCED: {json.dumps(result)}")
            return
        if args.command == "sync-instagram":
            result = sync_instagram_owned(_intelligence_store(args.database), args.account_id, args.max_results)
            print(f"INSTAGRAM SYNCED: {json.dumps(result)}")
            return
        if args.command == "sync-competitor":
            store = _intelligence_store(args.database)
            if args.platform == "youtube":
                result = sync_youtube_competitor(store, args.handle, args.max_results)
            else:
                result = sync_instagram_competitor(store, args.handle, args.instagram_owner_account_id, args.max_results)
            print(f"COMPETITOR SYNCED: {json.dumps(result)}")
            return
        if args.command == "sync-competitors":
            store = _intelligence_store(args.database)
            results: list[dict[str, Any]] = []
            for competitor in load_competitor_config(args.config):
                if competitor["platform"] == "youtube":
                    outcome = sync_youtube_competitor(store, competitor["handle"], args.max_results)
                else:
                    outcome = sync_instagram_competitor(store, competitor["handle"], args.instagram_owner_account_id, args.max_results)
                results.append({**competitor, **outcome})
            print(f"COMPETITORS SYNCED: {json.dumps(results)}")
            return
        if args.command == "analyze":
            artifact = write_analysis_artifacts(_intelligence_store(args.database), args.track, args.out)
            print(f"ANALYSIS: {artifact.markdown_path}")
            print(f"CHATGPT PROMPT: {artifact.prompt_path}")
            return
        if args.command == "register-draft":
            video_id = register_draft(_intelligence_store(args.database), args.storyboard)
            print(f"DRAFT REGISTERED: {video_id}")
            return
        if args.command == "register-publication":
            store = _intelligence_store(args.database)
            store.register_publication(args.video_id, args.platform, args.external_id, args.account_external_id, args.url)
            print(f"PUBLICATION REGISTERED: {args.video_id}")
            return
        if args.command == "record-metric":
            if args.collected_on:
                date.fromisoformat(args.collected_on)
            store = _intelligence_store(args.database)
            store.record_metric_by_external_id(args.platform, args.external_id, args.metric, args.value, collected_on=args.collected_on)
            print(f"METRIC RECORDED: {args.metric} for {args.platform}:{args.external_id}")
            return
        if args.command == "agency-run":
            run_date = _parse_date(args.date).isoformat()
            result = _agency_store(args.database).run_scheduled_workflow(run_date)
            print(f"AGENCY RUN: {json.dumps(result)}")
            return
        quality = "preview" if args.command == "preview" else "full"
        output = produce(args.storyboard, _parse_date(args.date), args.overwrite, quality, args.command == "package", args.narration_wav, args.narration_voice_name)
        print(f"SUCCESS: {output}")
    except (StoryboardValidationError, TTSUnavailableError, IntelligenceError, ConnectorError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
