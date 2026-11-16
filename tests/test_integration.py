import json
import unittest
import wave
from pathlib import Path

from social_manager.core import ROOT, validate_with_zod, wav_duration_seconds


class IntegrationTests(unittest.TestCase):
    def test_example_storyboard_is_accepted_by_zod(self) -> None:
        storyboard = validate_with_zod(ROOT / "examples" / "ai-system-visualization.json")
        self.assertEqual(storyboard["video_id"], "ai-system-visualization")
        self.assertEqual(len(storyboard["scenes"]), 3)

    def test_wav_duration_detection(self) -> None:
        temp_root = ROOT / ".test-artifacts"
        temp_root.mkdir(exist_ok=True)
        path = temp_root / "duration.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x00" * 12000)
        self.assertAlmostEqual(wav_duration_seconds(path), 1.5)
        path.unlink()
        temp_root.rmdir()

    def test_rendered_sample_manifests_record_success_when_available(self) -> None:
        manifests = list((ROOT / "output").glob("*/*/manifest.json"))
        if not manifests:
            self.skipTest("No rendered package is present; use the production smoke command.")
        statuses = [json.loads(manifest.read_text(encoding="utf-8"))["status"] for manifest in manifests]
        self.assertIn("success", statuses)


if __name__ == "__main__":
    unittest.main()
