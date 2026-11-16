import json
import tempfile
import unittest
from pathlib import Path

from social_manager.core import ROOT
from social_manager.intelligence import LocalKnowledgeStore, analyze_local_knowledge, import_json_file, write_analysis_artifacts


class LocalIntelligenceTests(unittest.TestCase):
    def test_local_import_creates_short_and_long_form_evidence(self) -> None:
        artifacts_root = ROOT / ".intelligence-test-artifacts"
        artifacts_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts_root) as temporary:
            root = Path(temporary)
            store = LocalKnowledgeStore(root / "knowledge.sqlite3")
            store.initialize()
            result = import_json_file(store, ROOT / "examples" / "analytics-sample.json")
            analysis = analyze_local_knowledge(store, "both")
            self.assertEqual(result, {"accounts": 4, "content": 7})
            self.assertEqual(analysis["content_count"], 7)
            self.assertEqual({idea["track"] for idea in analysis["ideas"]}, {"short_form", "long_form"})
            self.assertTrue(all("proof-led" not in idea["title"].lower() for idea in analysis["ideas"]))

    def test_analysis_artifacts_are_local_and_include_a_chatgpt_prompt(self) -> None:
        artifacts_root = ROOT / ".intelligence-test-artifacts"
        artifacts_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts_root) as temporary:
            root = Path(temporary)
            store = LocalKnowledgeStore(root / "knowledge.sqlite3")
            store.initialize()
            import_json_file(store, ROOT / "examples" / "analytics-sample.json")
            artifact = write_analysis_artifacts(store, "both", root / "brief")
            analysis = json.loads(artifact.json_path.read_text(encoding="utf-8"))
            prompt = artifact.prompt_path.read_text(encoding="utf-8")
            self.assertEqual(analysis["tracks"], ["short_form", "long_form"])
            self.assertIn("Do not render or publish without explicit approval", prompt)
            self.assertTrue(artifact.markdown_path.exists())


if __name__ == "__main__":
    unittest.main()
