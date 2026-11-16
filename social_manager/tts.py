from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .core import wav_duration_seconds


class TTSUnavailableError(RuntimeError):
    """No configured local TTS provider can synthesize narration."""


@dataclass(frozen=True)
class TTSResult:
    path: Path
    provider: str
    voice_id: str
    duration_seconds: float


def _generate_windows_sapi(text: str, voice_id: str, output_path: Path, speed: int) -> TTSResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("windows_sapi.ps1")
    sapi_rate = round((speed - 180) / 12)
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), text, str(output_path.resolve()), voice_id, str(sapi_rate)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise TTSUnavailableError(f"Windows SAPI synthesis failed: {completed.stderr.strip() or completed.stdout.strip()}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise TTSUnavailableError("Windows SAPI completed without creating narration audio")
    try:
        selected = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise TTSUnavailableError("Windows SAPI did not return its selected voice metadata") from exc
    resolved_name = str(selected["voice_name"])
    suffix = " (fallback; requested male voice unavailable)" if selected.get("fallback_voice") else ""
    return TTSResult(output_path, "windows_sapi", f"{resolved_name}{suffix}", wav_duration_seconds(output_path))


def generate_narration(text: str, voice_id: str, output_path: Path, settings: dict[str, object] | None = None) -> TTSResult:
    """Generate a WAV with a locally installed voice; Kokoro remains optional."""
    if not text.strip():
        raise ValueError("Narration text must not be empty")
    config = settings or {}
    provider = str(config.get("provider", "auto"))
    speed = int(config.get("speed", 180))
    if provider not in {"auto", "windows_sapi", "kokoro"}:
        raise TTSUnavailableError(f"Unsupported TTS provider: {provider}")
    if provider == "kokoro":
        raise TTSUnavailableError("Kokoro is not configured in this Python 3.12 environment; use provider=windows_sapi or provide a tested local Kokoro adapter.")
    return _generate_windows_sapi(text, voice_id, output_path, speed)
