import json
import unittest
from datetime import date
from pathlib import Path

from social_manager.core import StoryboardValidationError, manifest_template, production_directory, resolve_scene_timing, slugify, validate_storyboard_data, write_json


def valid_storyboard() -> dict:
    return {
        "video_id": "test-video", "series": "inside_the_machine", "title": "Test Video", "platform": "youtube_shorts", "aspect_ratio": "9:16", "fps": 30, "narration_voice": "david", "narration_script": "A concise test narration.", "scenes": [{"scene_id": "scene-1", "scene_type": "architecture", "headline": "TEST", "duration_seconds": 2}], "thumbnail_details": {"variant": "technical", "headline": "TEST"}, "metadata": {"caption": "Useful context.", "cta": "Follow."},
    }


class StoryboardTests(unittest.TestCase):
    def test_schema_accepts_valid_storyboard(self) -> None:
        validate_storyboard_data(valid_storyboard())

    def test_schema_rejects_factual_detective_without_sources(self) -> None:
        story = valid_storyboard() | {"series": "ai_detective", "fictional": False}
        with self.assertRaisesRegex(StoryboardValidationError, "source_references"):
            validate_storyboard_data(story)

    def test_duration_follows_measured_audio(self) -> None:
        resolved = resolve_scene_timing(valid_storyboard(), 8.25)
        self.assertAlmostEqual(sum(scene["duration_seconds"] for scene in resolved["scenes"]), 9.0, places=2)
        self.assertEqual(resolved["caption_timing"], "estimated")

    def test_output_path_handling(self) -> None:
        self.assertEqual(slugify("A: System / Test!"), "a-system-test")
        self.assertTrue(str(production_directory(valid_storyboard(), date(2026, 11, 14))).endswith("output\\2026-11-14\\test-video"))

    def test_caption_limit(self) -> None:
        story = valid_storyboard()
        story["metadata"]["caption"] = "x" * 2201
        with self.assertRaisesRegex(StoryboardValidationError, "2200"):
            validate_storyboard_data(story)

    def test_manifest_generation_and_json_output(self) -> None:
        directory = Path.cwd() / ".test-artifacts"
        directory.mkdir(exist_ok=True)
        manifest = manifest_template(valid_storyboard(), directory, "job-test")
        destination = directory / "manifest.json"
        write_json(destination, manifest)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))["job_id"], "job-test")
        destination.unlink()
        directory.rmdir()


if __name__ == "__main__":
    unittest.main()
