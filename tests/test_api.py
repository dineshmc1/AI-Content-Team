from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

from social_manager.agency.store import AgencyStore
from social_manager.api.app import TaskCreateRequest, create_app, validate_local_origin


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(AgencyStore(root / "agency.sqlite3", root / "tasks"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def endpoint(self, path: str, method: str = "GET"):
        return next(
            route.endpoint
            for route in self.app.router.routes
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
        )

    def test_real_health_agents_and_task_lifecycle(self) -> None:
        health = self.endpoint("/api/health")
        agents = self.endpoint("/api/agents")
        create_task = self.endpoint("/api/tasks", "POST")
        handoff_task = self.endpoint("/api/tasks/{task_id}/desktop-handoff", "POST")
        events_endpoint = self.endpoint("/api/events")
        self.assertEqual(health()["status"], "ok")
        self.assertEqual(len(agents()), 8)
        created = create_task(TaskCreateRequest(
            agent_id="content-strategist", task_type="strategy", title="Rank ideas", inputs={"analysis_run": "local"}
        ))
        task_id = created["id"]
        handoff = handoff_task(task_id)
        self.assertEqual(handoff["task_id"], task_id)
        events = events_endpoint(0, 200)
        self.assertTrue(any(event["task_id"] == task_id for event in events))

    def test_rejects_cross_origin_state_change(self) -> None:
        request = Request({"type": "http", "method": "POST", "headers": [(b"origin", b"https://evil.example")]})
        with self.assertRaises(HTTPException) as context:
            validate_local_origin(request)
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
