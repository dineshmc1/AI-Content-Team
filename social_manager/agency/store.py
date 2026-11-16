from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..core import ROOT
from ..intelligence import DEFAULT_DATABASE, LocalKnowledgeStore, utc_now


TASK_STATES = frozenset(
    {
        "queued",
        "ready",
        "running_local",
        "waiting_for_desktop",
        "awaiting_import",
        "validating",
        "needs_revision",
        "awaiting_approval",
        "completed",
        "failed",
        "canceled",
    }
)

TERMINAL_TASK_STATES = frozenset({"completed", "failed", "canceled"})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"ready", "canceled", "failed"}),
    "ready": frozenset({"running_local", "waiting_for_desktop", "canceled", "failed"}),
    "running_local": frozenset({"validating", "awaiting_approval", "completed", "failed", "canceled"}),
    "waiting_for_desktop": frozenset({"awaiting_import", "canceled", "failed"}),
    "awaiting_import": frozenset({"validating", "needs_revision", "canceled", "failed"}),
    "validating": frozenset({"needs_revision", "awaiting_approval", "completed", "failed", "canceled"}),
    "needs_revision": frozenset({"ready", "waiting_for_desktop", "canceled", "failed"}),
    "awaiting_approval": frozenset({"completed", "needs_revision", "canceled", "failed"}),
    "completed": frozenset(),
    "failed": frozenset({"ready", "canceled"}),
    "canceled": frozenset(),
}


AGENT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "agent_id": "content-strategist",
        "name": "Content Strategist",
        "role": "strategy",
        "description": "Ranks evidence-backed topics and plans content families.",
        "input_schema": {"type": "object", "required": ["analysis_run"]},
        "output_schema": {"required": ["recommendations"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["local_intelligence"],
    },
    {
        "agent_id": "research-editor",
        "name": "Research Editor",
        "role": "research",
        "description": "Builds sourced evidence packs and identifies verification risk.",
        "input_schema": {"type": "object", "required": ["topic"]},
        "output_schema": {"required": ["claims", "sources"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["desktop_research"],
    },
    {
        "agent_id": "copywriter",
        "name": "Copywriter",
        "role": "copy",
        "description": "Writes approved, evidence-aware hooks, scripts, and metadata.",
        "input_schema": {"type": "object", "required": ["brief"]},
        "output_schema": {"required": ["script", "metadata"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["desktop_writing"],
    },
    {
        "agent_id": "story-director",
        "name": "Story Director",
        "role": "story",
        "description": "Turns approved scripts into compatible storyboard plans.",
        "input_schema": {"type": "object", "required": ["script"]},
        "output_schema": {"required": ["storyboard"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["storyboard_validation"],
    },
    {
        "agent_id": "graphic-designer",
        "name": "Graphic Designer",
        "role": "design",
        "description": "Prepares brand-consistent visual and carousel specifications.",
        "input_schema": {"type": "object", "required": ["storyboard"]},
        "output_schema": {"required": ["design_specification"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["local_assets"],
    },
    {
        "agent_id": "video-producer",
        "name": "Video Producer",
        "role": "production",
        "description": "Validates, renders, inspects, and packages media locally.",
        "input_schema": {"type": "object", "required": ["storyboard_path"]},
        "output_schema": {"required": ["package_path"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["remotion", "windows_sapi", "ffprobe"],
    },
    {
        "agent_id": "social-seo-manager",
        "name": "Social Media & SEO Manager",
        "role": "publishing",
        "description": "Prepares platform-specific metadata and manual-export packages.",
        "input_schema": {"type": "object", "required": ["content_family"]},
        "output_schema": {"required": ["publication_metadata"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["local_metadata"],
    },
    {
        "agent_id": "performance-analyst",
        "name": "Performance Analyst",
        "role": "analytics",
        "description": "Separates observed performance from testable hypotheses.",
        "input_schema": {"type": "object", "required": ["metric_snapshot"]},
        "output_schema": {"required": ["observations", "hypotheses"]},
        "prompt_template_version": "1.0",
        "allowed_tools": ["local_intelligence"],
    },
)


class AgencyError(RuntimeError):
    """A safe, user-actionable agency workflow error."""


@dataclass(frozen=True)
class DesktopTaskFiles:
    task_id: str
    directory: Path
    request_path: Path
    instructions_path: Path
    output_schema_path: Path


class AgencyStore:
    """SQLite-backed agency state that extends, but never rewrites, intelligence data."""

    def __init__(self, database_path: Path = DEFAULT_DATABASE, workspace_root: Path | None = None) -> None:
        self.database_path = database_path.resolve()
        self.workspace_root = (workspace_root or ROOT / "workspace" / "agent-tasks").resolve()
        self.knowledge = LocalKnowledgeStore(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        return self.knowledge.connect()

    def _backup_database(self) -> Path | None:
        if not self.database_path.exists() or self.database_path.stat().st_size == 0:
            return None
        backups = self.database_path.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        destination = backups / f"{self.database_path.stem}-before-agency-{utc_now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}.sqlite3"
        source = sqlite3.connect(self.database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    def initialize(self) -> Path | None:
        """Create current intelligence tables and apply additive agency migration 1."""
        self.knowledge.initialize()
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS agency_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {int(row["version"]) for row in connection.execute("SELECT version FROM agency_schema_migrations").fetchall()}
        backup_path = self._backup_database() if ({1, 2, 3} - applied) else None
        if 1 not in applied:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS agents (
                        agent_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        role TEXT NOT NULL,
                        description TEXT NOT NULL,
                        input_schema_json TEXT NOT NULL,
                        output_schema_json TEXT NOT NULL,
                        prompt_template_version TEXT NOT NULL,
                        allowed_tools_json TEXT NOT NULL,
                        execution_mode TEXT NOT NULL DEFAULT 'desktop_handoff',
                        current_state TEXT NOT NULL DEFAULT 'idle',
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS content_families (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        series TEXT NOT NULL,
                        status TEXT NOT NULL,
                        source_idea_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS agent_tasks (
                        id TEXT PRIMARY KEY,
                        agent_id TEXT NOT NULL REFERENCES agents(agent_id),
                        content_family_id TEXT REFERENCES content_families(id),
                        task_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        state TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 1,
                        execution_mode TEXT NOT NULL,
                        input_json TEXT NOT NULL,
                        output_schema_json TEXT NOT NULL,
                        output_json TEXT,
                        dependencies_json TEXT NOT NULL DEFAULT '[]',
                        workspace_path TEXT,
                        output_hash TEXT,
                        error_detail TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_agent_tasks_state_updated ON agent_tasks(state, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_state ON agent_tasks(agent_id, state);
                    CREATE TABLE IF NOT EXISTS task_events (
                        id INTEGER PRIMARY KEY,
                        task_id TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                        event_type TEXT NOT NULL,
                        previous_state TEXT,
                        state TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        detail_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_task_events_task_created ON task_events(task_id, created_at DESC);
                    CREATE TABLE IF NOT EXISTS production_jobs (
                        id TEXT PRIMARY KEY,
                        task_id TEXT REFERENCES agent_tasks(id),
                        operation TEXT NOT NULL,
                        state TEXT NOT NULL,
                        command_json TEXT NOT NULL,
                        log_path TEXT,
                        process_id INTEGER,
                        started_at TEXT,
                        finished_at TEXT,
                        error_detail TEXT
                    );
                    CREATE TABLE IF NOT EXISTS production_artifacts (
                        id TEXT PRIMARY KEY,
                        task_id TEXT REFERENCES agent_tasks(id),
                        content_family_id TEXT REFERENCES content_families(id),
                        kind TEXT NOT NULL,
                        path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        UNIQUE(path, sha256)
                    );
                    CREATE TABLE IF NOT EXISTS approval_requests (
                        id TEXT PRIMARY KEY,
                        task_id TEXT REFERENCES agent_tasks(id),
                        artifact_id TEXT REFERENCES production_artifacts(id),
                        state TEXT NOT NULL,
                        requested_at TEXT NOT NULL,
                        decided_at TEXT,
                        decision_comment TEXT
                    );
                    CREATE TABLE IF NOT EXISTS application_settings (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO agency_schema_migrations(version, applied_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                    """
                )
        if 2 not in applied:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS content_versions (
                        id TEXT PRIMARY KEY,
                        content_family_id TEXT NOT NULL REFERENCES content_families(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        body_path TEXT,
                        body_hash TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(content_family_id, kind, version)
                    );
                    CREATE TABLE IF NOT EXISTS publication_schedule (
                        id TEXT PRIMARY KEY,
                        content_family_id TEXT NOT NULL REFERENCES content_families(id) ON DELETE CASCADE,
                        platform TEXT NOT NULL CHECK(platform IN ('instagram', 'youtube')),
                        format TEXT NOT NULL,
                        scheduled_for TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_publication_schedule_date ON publication_schedule(scheduled_for, status);
                    CREATE TABLE IF NOT EXISTS approval_decisions (
                        id TEXT PRIMARY KEY,
                        approval_request_id TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
                        decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected', 'revision_requested')),
                        actor TEXT NOT NULL,
                        comment TEXT,
                        artifact_hash TEXT,
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO agency_schema_migrations(version, applied_at) VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                    """
                )
        if 3 not in applied:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS agency_runs (
                        id TEXT PRIMARY KEY,
                        run_date TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        summary_json TEXT NOT NULL DEFAULT '{}',
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        UNIQUE(run_date, mode)
                    );
                    INSERT INTO agency_schema_migrations(version, applied_at) VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                    """
                )
        self.seed_agents()
        return backup_path

    def seed_agents(self) -> None:
        now = utc_now()
        with self._connect() as connection:
            for definition in AGENT_DEFINITIONS:
                connection.execute(
                    """
                    INSERT INTO agents(agent_id, name, role, description, input_schema_json, output_schema_json,
                        prompt_template_version, allowed_tools_json, execution_mode, current_state, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'desktop_handoff', 'idle', ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        name=excluded.name, role=excluded.role, description=excluded.description,
                        input_schema_json=excluded.input_schema_json, output_schema_json=excluded.output_schema_json,
                        prompt_template_version=excluded.prompt_template_version,
                        allowed_tools_json=excluded.allowed_tools_json, updated_at=excluded.updated_at
                    """,
                    (
                        definition["agent_id"], definition["name"], definition["role"], definition["description"],
                        json.dumps(definition["input_schema"], sort_keys=True), json.dumps(definition["output_schema"], sort_keys=True),
                        definition["prompt_template_version"], json.dumps(definition["allowed_tools"]), now,
                    ),
                )

    def list_agents(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, (
                  SELECT t.id FROM agent_tasks t
                  WHERE t.agent_id = a.agent_id AND t.state NOT IN ('completed', 'failed', 'canceled')
                  ORDER BY t.updated_at DESC LIMIT 1
                ) AS active_task_id
                FROM agents a ORDER BY a.name
                """
            ).fetchall()
        return [self._agent_row(row) for row in rows]

    def create_content_family(self, title: str, series: str, status: str = "planned", source_idea_id: str | None = None) -> dict[str, Any]:
        if not title.strip() or not series.strip():
            raise AgencyError("Content title and series are required")
        if status not in {"planned", "researching", "scripting", "designing", "rendering", "awaiting_approval", "ready", "published", "failed", "canceled"}:
            raise AgencyError("Unsupported content status")
        family_id = f"content-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO content_families(id, title, series, status, source_idea_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (family_id, title.strip(), series.strip(), status, source_idea_id, now, now),
            )
        return self.get_content_family(family_id)

    def get_content_family(self, family_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM content_families WHERE id = ?", (family_id,)).fetchone()
        if row is None:
            raise AgencyError(f"Unknown content family: {family_id}")
        return dict(row)

    def list_content_families(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM content_families ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [dict(row) for row in rows]

    def schedule_content(self, content_family_id: str, platform: str, content_format: str, scheduled_for: str) -> dict[str, Any]:
        if platform not in {"instagram", "youtube"}:
            raise AgencyError("Publication platform must be instagram or youtube")
        if not content_format.strip() or len(content_format) > 80:
            raise AgencyError("Publication format is required")
        if not scheduled_for or len(scheduled_for) > 40:
            raise AgencyError("A valid local publication timestamp is required")
        self.get_content_family(content_family_id)
        schedule_id = f"schedule-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO publication_schedule(id, content_family_id, platform, format, scheduled_for, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'planned', ?, ?)",
                (schedule_id, content_family_id, platform, content_format.strip(), scheduled_for, now, now),
            )
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT ps.*, cf.title AS content_title, cf.series AS content_series
                   FROM publication_schedule ps JOIN content_families cf ON cf.id = ps.content_family_id
                   WHERE ps.id = ?""", (schedule_id,)
            ).fetchone()
        if row is None:
            raise AgencyError(f"Unknown publication schedule: {schedule_id}")
        return dict(row)

    def list_schedule(self, start: str | None = None, end: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if start:
            clauses.append("ps.scheduled_for >= ?")
            params.append(start)
        if end:
            clauses.append("ps.scheduled_for <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT ps.*, cf.title AS content_title, cf.series AS content_series
                    FROM publication_schedule ps JOIN content_families cf ON cf.id = ps.content_family_id
                    {where} ORDER BY ps.scheduled_for ASC LIMIT ?""", params
            ).fetchall()
        return [dict(row) for row in rows]

    def _existing_task(self, content_family_id: str, task_type: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM agent_tasks WHERE content_family_id = ? AND task_type = ? ORDER BY created_at ASC LIMIT 1",
                (content_family_id, task_type),
            ).fetchone()
        return str(row["id"]) if row else None

    def plan_content_family_workflow(self, content_family_id: str) -> list[dict[str, Any]]:
        """Create the stable workflow graph once; later runs reuse all prior task IDs."""
        family = self.get_content_family(content_family_id)
        steps = (
            ("content-strategist", "strategy", "Create an evidence-backed angle", {"analysis_run": "local knowledge store"}),
            ("research-editor", "research", "Build a sourced research pack", {"topic": family["title"]}),
            ("copywriter", "copy", "Write script, hook, and platform metadata", {"brief": family["title"]}),
            ("story-director", "storyboard", "Create a validated storyboard plan", {"script": family["title"]}),
            ("graphic-designer", "design", "Create visual and carousel specifications", {"storyboard": family["title"]}),
            ("video-producer", "production", "Validate and produce approved media", {"storyboard_path": "awaiting approved storyboard"}),
            ("social-seo-manager", "metadata", "Prepare platform publishing package", {"content_family": content_family_id}),
            ("performance-analyst", "performance_review", "Record post-publication observations", {"metric_snapshot": "awaiting publication"}),
        )
        task_ids: list[str] = []
        created: list[dict[str, Any]] = []
        for agent_id, task_type, title, inputs in steps:
            existing = self._existing_task(content_family_id, task_type)
            if existing:
                task_ids.append(existing)
                created.append(self.get_task(existing))
                continue
            dependencies = task_ids[-1:] if task_ids else []
            task = self.create_task(agent_id, task_type, title, inputs, dependencies, content_family_id)
            task_ids.append(task["id"])
            created.append(task)
        return created

    def advance_ready_tasks(self) -> list[dict[str, Any]]:
        """Promote only queued tasks whose recorded dependencies actually completed."""
        promoted: list[dict[str, Any]] = []
        for task in self.list_tasks(states=["queued"], limit=500):
            dependencies = task["dependencies"]
            if all(self.get_task(str(dependency))["state"] == "completed" for dependency in dependencies):
                promoted.append(self.transition_task(task["id"], "ready", "orchestrator", {"reason": "dependencies_completed"}))
        return promoted

    def run_scheduled_workflow(self, run_date: str) -> dict[str, Any]:
        """Perform local deterministic planning and emit the next legitimate Desktop handoff."""
        if len(run_date) != 10:
            raise AgencyError("run_date must use YYYY-MM-DD")
        run_id = f"agency-run-{run_date}"
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agency_runs WHERE run_date = ? AND mode = 'daily'", (run_date,)).fetchone()
            if row and str(row["status"]) == "completed":
                return json.loads(str(row["summary_json"]))
            if not row:
                connection.execute(
                    "INSERT INTO agency_runs(id, run_date, mode, status, started_at) VALUES (?, ?, 'daily', 'running', ?)",
                    (run_id, run_date, utc_now()),
                )
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT content_family_id FROM publication_schedule WHERE substr(scheduled_for, 1, 10) = ? AND status = 'planned'",
                (run_date,),
            ).fetchall()
        families = [str(row["content_family_id"]) for row in rows]
        workflows = [self.plan_content_family_workflow(family_id) for family_id in families]
        promoted = self.advance_ready_tasks()
        handoffs: list[dict[str, str]] = []
        for task in self.list_tasks(states=["ready"], limit=500):
            if task["execution_mode"] == "desktop_handoff":
                files = self.prepare_desktop_handoff(task["id"])
                handoffs.append({"task_id": task["id"], "directory": str(files.directory)})
        summary = {
            "run_id": run_id,
            "run_date": run_date,
            "scheduled_content_families": families,
            "workflow_task_ids": [[task["id"] for task in workflow] for workflow in workflows],
            "promoted_task_ids": [task["id"] for task in promoted],
            "desktop_handoffs": handoffs,
            "platform_sync": "not_run_by_autopilot; use explicit authorized sync commands",
            "status": "completed",
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE agency_runs SET status = 'completed', completed_at = ?, summary_json = ? WHERE run_date = ? AND mode = 'daily'",
                (utc_now(), json.dumps(summary, sort_keys=True), run_date),
            )
        return summary

    def _agent_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("input_schema_json", "output_schema_json", "allowed_tools_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        return result

    def _task_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for key in ("input_json", "output_schema_json", "dependencies_json", "output_json"):
            if result.get(key) is not None:
                result[key.removesuffix("_json")] = json.loads(result.pop(key))
            else:
                result.pop(key, None)
        return result

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise AgencyError(f"Unknown task: {task_id}")
        return self._task_row(row)

    def list_tasks(self, states: Iterable[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        selected = tuple(states or ())
        if any(state not in TASK_STATES for state in selected):
            raise AgencyError("Unknown task state filter")
        clause = ""
        values: list[Any] = []
        if selected:
            clause = f"WHERE state IN ({','.join('?' for _ in selected)})"
            values.extend(selected)
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM agent_tasks {clause} ORDER BY updated_at DESC LIMIT ?", values
            ).fetchall()
        return [self._task_row(row) for row in rows]

    def _require_agent(self, agent_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if row is None:
            raise AgencyError(f"Unknown agent: {agent_id}")
        return self._agent_row(row)

    def _append_event(
        self, connection: sqlite3.Connection, task_id: str, event_type: str, previous_state: str | None,
        state: str, actor: str, detail: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO task_events(task_id, event_type, previous_state, state, actor, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, event_type, previous_state, state, actor, json.dumps(detail or {}, sort_keys=True), utc_now()),
        )

    def create_task(
        self, agent_id: str, task_type: str, title: str, inputs: dict[str, Any],
        dependencies: list[str] | None = None, content_family_id: str | None = None,
        execution_mode: str = "desktop_handoff",
    ) -> dict[str, Any]:
        if not task_type.strip() or not title.strip() or not isinstance(inputs, dict):
            raise AgencyError("task_type, title, and object inputs are required")
        if execution_mode not in {"desktop_handoff", "local"}:
            raise AgencyError("Unsupported execution mode")
        agent = self._require_agent(agent_id)
        dependencies = dependencies or []
        if len(set(dependencies)) != len(dependencies):
            raise AgencyError("Task dependencies must be unique")
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        with self._connect() as connection:
            known = {str(row["id"]) for row in connection.execute(
                f"SELECT id FROM agent_tasks WHERE id IN ({','.join('?' for _ in dependencies)})", dependencies
            ).fetchall()} if dependencies else set()
            missing = set(dependencies) - known
            if missing:
                raise AgencyError(f"Unknown task dependency: {sorted(missing)[0]}")
            connection.execute(
                """
                INSERT INTO agent_tasks(id, agent_id, content_family_id, task_type, title, state, revision,
                    execution_mode, input_json, output_schema_json, dependencies_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', 1, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, agent_id, content_family_id, task_type, title, execution_mode, json.dumps(inputs, sort_keys=True),
                 json.dumps(agent["output_schema"], sort_keys=True), json.dumps(dependencies), now, now),
            )
            self._append_event(connection, task_id, "created", None, "queued", "system", {"dependencies": dependencies})
        return self.get_task(task_id)

    def transition_task(self, task_id: str, target_state: str, actor: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        if target_state not in TASK_STATES:
            raise AgencyError(f"Unknown task state: {target_state}")
        with self._connect() as connection:
            row = connection.execute("SELECT state FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise AgencyError(f"Unknown task: {task_id}")
            current = str(row["state"])
            if target_state not in ALLOWED_TRANSITIONS[current]:
                raise AgencyError(f"Cannot transition task {task_id} from {current} to {target_state}")
            complete = utc_now() if target_state == "completed" else None
            connection.execute(
                "UPDATE agent_tasks SET state = ?, updated_at = ?, completed_at = COALESCE(?, completed_at) WHERE id = ?",
                (target_state, utc_now(), complete, task_id),
            )
            self._append_event(connection, task_id, "state_transition", current, target_state, actor, detail)
        return self.get_task(task_id)

    def prepare_desktop_handoff(self, task_id: str) -> DesktopTaskFiles:
        task = self.get_task(task_id)
        if task["execution_mode"] != "desktop_handoff":
            raise AgencyError("Only Desktop-handoff tasks can create a Desktop exchange folder")
        if task["state"] == "queued":
            self.transition_task(task_id, "ready", "system")
            task = self.get_task(task_id)
        if task["state"] == "ready":
            self.transition_task(task_id, "waiting_for_desktop", "system")
            task = self.get_task(task_id)
        if task["state"] not in {"waiting_for_desktop", "awaiting_import"}:
            raise AgencyError(f"Task {task_id} is not ready for Desktop handoff")
        directory = (self.workspace_root / task_id).resolve()
        if self.workspace_root not in directory.parents:
            raise AgencyError("Invalid Desktop task path")
        directory.mkdir(parents=True, exist_ok=True)
        output_directory = directory / "output"
        output_directory.mkdir(exist_ok=True)
        request = {
            "task_id": task_id,
            "revision": task["revision"],
            "agent_id": task["agent_id"],
            "task_type": task["task_type"],
            "title": task["title"],
            "inputs": task["input"],
            "expected_output": "output/output.json",
            "approval_required": True,
        }
        (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        (directory / "expected-output-schema.json").write_text(json.dumps(task["output_schema"], indent=2) + "\n", encoding="utf-8")
        (directory / "instructions.md").write_text(
            f"# {task['title']}\n\n"
            "Use the supplied local context. Do not render, publish, install software, or modify files outside this task folder. "
            "Write a JSON object to `output/output.json` containing matching `task_id` and `revision`, plus every required deliverable. "
            "This task remains waiting until the local application validates and imports that file.\n",
            encoding="utf-8",
        )
        manifest = {"task_id": task_id, "revision": task["revision"], "state": task["state"], "created_at": utc_now()}
        (directory / "task-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        with self._connect() as connection:
            connection.execute("UPDATE agent_tasks SET workspace_path = ?, updated_at = ? WHERE id = ?", (str(directory), utc_now(), task_id))
            self._append_event(connection, task_id, "desktop_handoff_prepared", task["state"], task["state"], "system", {"directory": str(directory)})
        return DesktopTaskFiles(task_id, directory, directory / "request.json", directory / "instructions.md", directory / "expected-output-schema.json")

    def import_desktop_output(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["state"] == "waiting_for_desktop":
            self.transition_task(task_id, "awaiting_import", "system")
            task = self.get_task(task_id)
        if task["state"] != "awaiting_import":
            raise AgencyError("Task is not waiting for Desktop output")
        output_path = Path(str(task.get("workspace_path") or "")) / "output" / "output.json"
        if not output_path.exists() or not output_path.is_file():
            raise AgencyError(f"Desktop output is missing: {output_path}")
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AgencyError("Desktop output must be valid JSON") from exc
        if not isinstance(payload, dict) or payload.get("task_id") != task_id or payload.get("revision") != task["revision"]:
            raise AgencyError("Desktop output has a stale or incorrect task ID/revision")
        required = task["output_schema"].get("required", [])
        if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
            raise AgencyError("Task output schema is invalid")
        missing = [name for name in required if name not in payload]
        if missing:
            raise AgencyError(f"Desktop output is missing required fields: {', '.join(missing)}")
        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        self.transition_task(task_id, "validating", "system", {"output_sha256": digest})
        with self._connect() as connection:
            connection.execute(
                "UPDATE agent_tasks SET output_json = ?, output_hash = ?, updated_at = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True), digest, utc_now(), task_id),
            )
            self._append_event(connection, task_id, "desktop_output_imported", "validating", "validating", "system", {"output_sha256": digest})
        return self.get_task(task_id)

    def list_events(self, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE id > ? ORDER BY id ASC LIMIT ?", (max(after_id, 0), limit)
            ).fetchall()
        result = []
        for row in rows:
            event = dict(row)
            event["detail"] = json.loads(event.pop("detail_json"))
            result.append(event)
        return result

    def overview(self) -> dict[str, Any]:
        with self._connect() as connection:
            status_counts = {row["state"]: row["count"] for row in connection.execute(
                "SELECT state, COUNT(*) AS count FROM agent_tasks GROUP BY state"
            ).fetchall()}
            latest_sync = connection.execute(
                "SELECT source, completed_at, status FROM sync_runs ORDER BY COALESCE(completed_at, started_at) DESC LIMIT 1"
            ).fetchone()
        return {
            "tasks_by_state": status_counts,
            "active_tasks": sum(count for state, count in status_counts.items() if state not in TERMINAL_TASK_STATES),
            "pending_approvals": status_counts.get("awaiting_approval", 0),
            "waiting_for_desktop": status_counts.get("waiting_for_desktop", 0) + status_counts.get("awaiting_import", 0),
            "last_platform_sync": dict(latest_sync) if latest_sync else None,
        }
