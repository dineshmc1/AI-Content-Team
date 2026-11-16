# Build AI With Dinesh 2.0 — implementation plan

## Module boundaries

`social_manager.agency` will be the durable domain layer: migrations,
repositories, task state transitions, agent definitions, Desktop task exchange,
approvals, schedules, artifacts, and event publication. `social_manager.api`
will contain localhost-only FastAPI schemas and routes. `social_manager.worker`
will safely manage permitted background CLI processes. Existing `core`, `tts`,
`intelligence`, and `connectors` remain their respective validation, narration,
analysis, and platform boundaries.

The new `dashboard/` Next.js App Router application will be a typed API client.
It will render real empty/loading/error states when data is unavailable. The
office is a lazy-loaded visual client of the same events and task records; it
will not own workflow state.

## Data and migration strategy

1. Select one local database path from settings (defaulting compatibly to the
   current intelligence database only when the user has not configured one).
2. Make a timestamped copy in `data/backups/` before applying any new migration.
3. Create an `agency_schema_migrations` ledger and apply migrations in one
   transaction per version.
4. Add additive, indexed tables for agents, tasks, task events, agent runs,
   content families/versions/dependencies, production jobs/artifacts, approval
   requests/decisions, schedules, settings, research briefs, experiments, story
   canon, and asset records. Reuse existing accounts/content/creative assets.
5. Cover migration and backup behavior with a temporary SQLite test; never run
   destructive changes against `data/live.sqlite3` during automated tests.

## Task state machine

Allowed durable states are `queued`, `ready`, `running_local`,
`waiting_for_desktop`, `awaiting_import`, `validating`, `needs_revision`,
`awaiting_approval`, `completed`, `failed`, and `canceled`. A transition has a
reason, timestamp, actor, optional artifact hash, and event record. Completion
requires an output validator; approvals are invalidated if their approved
artifact hash changes. Dependency resolution is idempotent and never repeats a
completed valid task merely because an agency run restarts.

## Desktop integration

Desktop mode is the default. It creates a folder at
`workspace/agent-tasks/<task-id>/` with versioned JSON request/manifest/schema,
human-readable instructions/context, output directory, and validation report.
The UI can copy the prompt and open the folder. Import validates the task ID,
revision, file hashes, and declared output schema before state advancement.
There is no claim of automated ChatGPT Desktop execution. An optional Codex CLI
adapter will remain disabled until explicitly configured and capability-checked.

## Rendering integration

The production manager wraps existing CLI functions/subcommands instead of
copying renderer logic. It records stdout/stderr to a job-local log, tracks the
subprocess and expected artifact paths, and exposes retry only when it is safe.
The later story layer extends—not replaces—the `2.0` contract and creates
schema-driven carousel/thumbnail/character components. Remotion remains the
authoritative renderer; all cinematic enhancements follow the installed
Remotion timing, caption, and markup guidance.

## Delivery sequence and acceptance gates

1. **Foundation (current):** audit, migration engine, task/event domain,
   FastAPI health/status/events, developer diagnostics, and tests.
2. **Dashboard:** Next.js pages, real API-backed overview/calendar/pipeline,
   production actions, analytics, approvals, and build tests.
3. **Agency workflow:** eight typed agent definitions, dependency planning,
   task exchange/import/validation, quality/approval logic, and tests.
4. **Media:** backwards-compatible story extensions, local reusable SVG rigs,
   carousel/metadata/QA additions, and actual smoke artifacts.
5. **Office:** PixiJS or a justified lightweight canvas implementation, backed
   solely by task events with reduced-motion and low-power modes.
6. **Autopilot:** idempotent daily planner and `agency.ps1`; it performs
   deterministic work and queues honest Desktop dependencies.

Each stage is complete only after relevant Python tests, API tests, frontend
build/tests, and applicable production smoke tests pass. Completion reports
will contain actual commands/results, not projected ones.

## Primary risks and mitigations

- **Desktop AI is not an unattended local API:** preserve visible waiting and
  import states; do not invent automation.
- **User's live data and tokens:** back up before migration, never read or log
  `.env`, and restrict API paths/commands.
- **Local renderer load:** use existing bounded rendering, one controlled job
  manager, and lightweight/lazy office rendering.
- **Platform variability:** cache official API responses, preserve connector
  errors, and keep standard tests credential-free.
- **Dirty nested Remotion repository:** make minimal additive edits only and
  leave existing uncommitted work intact.

## Initial acceptance criteria

The foundation must prove that a database backup is created, migrations are
idempotent, invalid state transitions fail, a valid Desktop task can be emitted
and imported, the API is localhost-only, event replay works, and existing tests
remain green. The dashboard and later phase reports will be created only once
their stated acceptance criteria are actually met.
