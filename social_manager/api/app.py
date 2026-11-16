from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agency.store import AgencyError, AgencyStore, TASK_STATES
from ..intelligence import DEFAULT_DATABASE


class TaskCreateRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=100)
    task_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=250)
    inputs: dict[str, Any]
    dependencies: list[str] = Field(default_factory=list, max_length=50)
    content_family_id: str | None = Field(default=None, max_length=100)
    execution_mode: Literal["desktop_handoff", "local"] = "desktop_handoff"


class TaskTransitionRequest(BaseModel):
    state: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ContentFamilyRequest(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    series: str = Field(min_length=1, max_length=100)


class ScheduleRequest(BaseModel):
    content_family_id: str = Field(min_length=1, max_length=100)
    platform: Literal["instagram", "youtube"]
    format: str = Field(min_length=1, max_length=80)
    scheduled_for: str = Field(min_length=1, max_length=40)


def _store_from_environment() -> AgencyStore:
    raw_path = os.environ.get("SOCIAL_MANAGER_DATABASE")
    return AgencyStore(Path(raw_path) if raw_path else DEFAULT_DATABASE)


def validate_local_origin(request: Request) -> None:
    """Reject browser-originated mutations from sites other than the local dashboard."""
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin not in {None, "http://localhost:3000", "http://127.0.0.1:3000"}:
        raise HTTPException(status_code=403, detail="Cross-origin state changes are not permitted")


def create_app(store: AgencyStore | None = None, initialize: bool = True) -> FastAPI:
    agency = store or _store_from_environment()
    if initialize:
        agency.initialize()
    app = FastAPI(title="Build AI With Dinesh Local Agency", version="2.0.0")
    app.state.agency_store = agency
    if not initialize:
        @app.on_event("startup")
        async def initialize_on_startup() -> None:
            agency.initialize()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    def service() -> AgencyStore:
        return app.state.agency_store

    def handled(callable_: Any) -> Any:
        try:
            return callable_()
        except AgencyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "database": str(service().database_path), "mode": "desktop_handoff"}

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        return service().overview()

    @app.get("/api/agents")
    def agents() -> list[dict[str, Any]]:
        return service().list_agents()

    @app.get("/api/content")
    def content(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service().knowledge.content_with_latest_metrics()[:limit]

    @app.get("/api/content-families")
    def content_families(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return service().list_content_families(limit)

    @app.post("/api/content-families")
    def create_content_family(request: ContentFamilyRequest, _: None = Depends(validate_local_origin)) -> dict[str, Any]:
        return handled(lambda: service().create_content_family(request.title, request.series))

    @app.get("/api/calendar")
    def calendar(start: str | None = None, end: str | None = None, limit: int = Query(default=200, ge=1, le=500)) -> list[dict[str, Any]]:
        return handled(lambda: service().list_schedule(start, end, limit))

    @app.post("/api/calendar")
    def schedule_content(request: ScheduleRequest, _: None = Depends(validate_local_origin)) -> dict[str, Any]:
        return handled(lambda: service().schedule_content(request.content_family_id, request.platform, request.format, request.scheduled_for))

    @app.post("/api/autopilot/run")
    def run_autopilot(_: None = Depends(validate_local_origin)) -> dict[str, Any]:
        return handled(lambda: service().run_scheduled_workflow(date.today().isoformat()))

    @app.get("/api/tasks")
    def tasks(state: list[str] = Query(default=[]), limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return handled(lambda: service().list_tasks(state, limit))

    @app.post("/api/tasks")
    def create_task(request: TaskCreateRequest, _: None = Depends(validate_local_origin)) -> dict[str, Any]:
        return handled(lambda: service().create_task(**request.model_dump()))

    @app.post("/api/tasks/{task_id}/transition")
    def transition_task(task_id: str, request: TaskTransitionRequest, _: None = Depends(validate_local_origin)) -> dict[str, Any]:
        return handled(lambda: service().transition_task(task_id, request.state, "dashboard", request.detail))

    @app.post("/api/tasks/{task_id}/desktop-handoff")
    def desktop_handoff(task_id: str, _: None = Depends(validate_local_origin)) -> dict[str, str]:
        files = handled(lambda: service().prepare_desktop_handoff(task_id))
        return {"task_id": files.task_id, "directory": str(files.directory), "instructions_path": str(files.instructions_path)}

    @app.post("/api/tasks/{task_id}/import-output")
    def import_output(task_id: str, _: None = Depends(validate_local_origin)) -> dict[str, Any]:
        return handled(lambda: service().import_desktop_output(task_id))

    @app.get("/api/events")
    def events(after_id: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)) -> list[dict[str, Any]]:
        return service().list_events(after_id, limit)

    @app.get("/api/events/stream")
    async def event_stream(after_id: int = Query(default=0, ge=0)) -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            current = after_id
            while True:
                events = service().list_events(current, 100)
                for event in events:
                    current = event["id"]
                    yield f"id: {current}\nevent: task\ndata: {json.dumps(event)}\n\n"
                yield ": keepalive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return app


app = create_app(initialize=False)
