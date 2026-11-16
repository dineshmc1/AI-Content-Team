from __future__ import annotations

import json
import re
import subprocess
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REMOTION_DIR = ROOT / "remotion"
OUTPUT_ROOT = ROOT / "output"

SCENE_TYPES = {
    "hook", "architecture", "data_flow", "terminal", "permission_boundary",
    "evidence_board", "incident_timeline", "security_alert", "human_review",
    "lesson", "city_environment", "company_dashboard", "board_meeting",
    "leaderboard", "chapter", "code_demo", "comparison", "outro",
}
SERIES = {"ai_detective", "inside_the_machine", "ai_city", "long_form_youtube"}


class StoryboardValidationError(ValueError):
    """A storyboard is not a renderable production specification."""


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise StoryboardValidationError("title must contain at least one letter or number")
    return slug


def load_storyboard(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoryboardValidationError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise StoryboardValidationError("Storyboard root must be a JSON object")
    validate_storyboard_data(data)
    return data


def _required(data: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> Any:
    value = data.get(key)
    if not isinstance(value, kind) or (isinstance(value, str) and not value.strip()):
        name = getattr(kind, "__name__", "valid value")
        raise StoryboardValidationError(f"{key}: expected {name}")
    return value


def validate_storyboard_data(data: dict[str, Any]) -> None:
    for key in ("video_id", "title", "narration_voice", "narration_script"):
        _required(data, key, str)
    if data.get("specification_version", "2.0") != "2.0":
        raise StoryboardValidationError("specification_version must be '2.0'")
    if data.get("series") not in SERIES:
        raise StoryboardValidationError(f"series must be one of {sorted(SERIES)}")
    if data.get("platform") not in {"instagram_reels", "youtube_shorts", "youtube"}:
        raise StoryboardValidationError("platform must be instagram_reels, youtube_shorts, or youtube")
    if data.get("aspect_ratio") not in {"9:16", "16:9"}:
        raise StoryboardValidationError("aspect_ratio must be 9:16 or 16:9")
    if data.get("fps") != 30:
        raise StoryboardValidationError("fps must be 30")
    if data.get("aspect_ratio") == "16:9" and data.get("platform") != "youtube":
        raise StoryboardValidationError("16:9 productions must use platform 'youtube'")
    scenes = _required(data, "scenes", list)
    if not scenes:
        raise StoryboardValidationError("scenes must contain at least one scene")
    seen: set[str] = set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise StoryboardValidationError(f"scenes[{index}] must be an object")
        scene_id = _required(scene, "scene_id", str)
        if scene_id in seen:
            raise StoryboardValidationError(f"scenes[{index}].scene_id must be unique: {scene_id}")
        seen.add(scene_id)
        if scene.get("scene_type") not in SCENE_TYPES:
            raise StoryboardValidationError(f"scenes[{index}].scene_type is unsupported")
        _required(scene, "headline", str)
        duration = scene.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise StoryboardValidationError(f"scenes[{index}].duration_seconds must be positive")
    metadata = _required(data, "metadata", dict)
    caption = _required(metadata, "caption", str)
    if len(caption) > 2200:
        raise StoryboardValidationError("metadata.caption exceeds Instagram's 2200 character limit")
    _required(metadata, "cta", str)
    thumbnail = _required(data, "thumbnail_details", dict)
    if thumbnail.get("variant") not in {"detective", "technical", "city"}:
        raise StoryboardValidationError("thumbnail_details.variant must be detective, technical, or city")
    _required(thumbnail, "headline", str)
    if data.get("series") == "ai_detective" and not data.get("fictional", False) and not data.get("source_references"):
        raise StoryboardValidationError("AI Detective factual stories require source_references; set fictional true for simulations")


def validate_with_zod(path: Path) -> dict[str, Any]:
    command = ["node", str(REMOTION_DIR / "scripts" / "validate-storyboard.mjs"), str(path.resolve())]
    result = run_command(command, ROOT)
    if result.returncode != 0:
        raise StoryboardValidationError(result.stderr.strip() or result.stdout.strip())
    try:
        return json.loads(result.stdout)["storyboard"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise StoryboardValidationError("Zod validation did not return a valid normalized storyboard") from exc


def run_command(args: list[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    return CommandResult(args=args, returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def resolve_scene_timing(storyboard: dict[str, Any], audio_seconds: float, ending_seconds: float = 0.75) -> dict[str, Any]:
    resolved = json.loads(json.dumps(storyboard))
    weights = [float(scene["duration_seconds"]) for scene in resolved["scenes"]]
    total_weight = sum(weights)
    if total_weight <= 0:
        raise StoryboardValidationError("scene duration weights must sum to a positive number")
    narrated_seconds = max(0.1, audio_seconds)
    for scene, weight in zip(resolved["scenes"], weights, strict=True):
        scene["duration_seconds"] = round(narrated_seconds * (weight / total_weight), 3)
    resolved["scenes"][-1]["duration_seconds"] = round(resolved["scenes"][-1]["duration_seconds"] + ending_seconds, 3)
    resolved["audio_duration_seconds"] = round(audio_seconds, 3)
    resolved["caption_timing"] = "estimated"
    return resolved


def production_directory(storyboard: dict[str, Any], production_date: date) -> Path:
    return OUTPUT_ROOT / production_date.isoformat() / slugify(str(storyboard["title"]))


def manifest_template(storyboard: dict[str, Any], output_dir: Path, job_id: str) -> dict[str, Any]:
    resolution = "1080x1920" if storyboard["aspect_ratio"] == "9:16" else "1920x1080"
    return {
        "job_id": job_id,
        "video_title": storyboard["title"],
        "series": storyboard["series"],
        "input_specification_version": storyboard.get("specification_version", "2.0"),
        "status": "started",
        "validation_status": "pending",
        "narration_status": "pending",
        "asset_status": "pending",
        "resolution": resolution,
        "fps": storyboard["fps"],
        "duration_seconds": None,
        "caption_timing": storyboard.get("caption_timing", "estimated"),
        "output_directory": str(output_dir),
        "outputs": {},
        "errors": [],
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
