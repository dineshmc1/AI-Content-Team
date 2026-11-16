from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from social_manager.agency.store import AgencyError, AgencyStore
from social_manager.intelligence import LocalKnowledgeStore


class AgencyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AgencyStore(self.root / "live.sqlite3", self.root / "agent-tasks")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_initialization_is_idempotent_and_seeds_agents(self) -> None:
        backup = self.store.initialize()
        self.assertIsNone(backup)
        self.assertEqual(len(self.store.list_agents()), 8)

    def test_existing_intelligence_database_is_backed_up_before_migration(self) -> None:
        database = self.root / "preexisting.sqlite3"
        LocalKnowledgeStore(database).initialize()
        migrated = AgencyStore(database, self.root / "tasks-2")
        backup = migrated.initialize()
        self.assertIsNotNone(backup)
        assert backup is not None
        self.assertTrue(backup.exists())

    def test_desktop_task_requires_matching_valid_output(self) -> None:
        task = self.store.create_task(
            "research-editor", "research", "Verify an AI incident", {"topic": "example"}
        )
        files = self.store.prepare_desktop_handoff(task["id"])
        self.assertTrue(files.instructions_path.exists())
        payload = {"task_id": task["id"], "revision": 1, "claims": [], "sources": []}
        output_path = files.directory / "output" / "output.json"
        output_path.write_text(json.dumps(payload), encoding="utf-8")
        imported = self.store.import_desktop_output(task["id"])
        self.assertEqual(imported["state"], "validating")
        completed = self.store.transition_task(task["id"], "awaiting_approval", "tester")
        self.assertEqual(completed["state"], "awaiting_approval")
        self.assertGreaterEqual(len(self.store.list_events()), 5)

    def test_invalid_transition_and_stale_output_are_rejected(self) -> None:
        task = self.store.create_task("copywriter", "copy", "Draft a hook", {"brief": "x"})
        with self.assertRaisesRegex(AgencyError, "Cannot transition"):
            self.store.transition_task(task["id"], "completed", "tester")
        files = self.store.prepare_desktop_handoff(task["id"])
        (files.directory / "output" / "output.json").write_text(
            json.dumps({"task_id": "old-task", "revision": 1, "script": "x", "metadata": {}}), encoding="utf-8"
        )
        with self.assertRaisesRegex(AgencyError, "stale"):
            self.store.import_desktop_output(task["id"])

    def test_content_family_can_be_scheduled(self) -> None:
        family = self.store.create_content_family("Why validation matters", "inside_the_machine")
        schedule = self.store.schedule_content(family["id"], "instagram", "reel", "2026-11-17T09:00:00+08:00")
        self.assertEqual(schedule["content_title"], "Why validation matters")
        self.assertEqual(len(self.store.list_schedule()), 1)

    def test_daily_run_is_idempotent_and_prepares_only_next_desktop_task(self) -> None:
        family = self.store.create_content_family("Validation gates", "inside_the_machine")
        self.store.schedule_content(family["id"], "instagram", "reel", "2026-11-17T09:00:00+08:00")
        first = self.store.run_scheduled_workflow("2026-11-17")
        second = self.store.run_scheduled_workflow("2026-11-17")
        self.assertEqual(first, second)
        self.assertEqual(len(first["workflow_task_ids"][0]), 8)
        self.assertEqual(len(first["desktop_handoffs"]), 1)
        task = self.store.get_task(first["desktop_handoffs"][0]["task_id"])
        self.assertEqual(task["state"], "waiting_for_desktop")


if __name__ == "__main__":
    unittest.main()
