# Existing-system audit — Build AI With Dinesh 2.0

Audit date: 2026-11-16  
Workspace: `D:\Dinesh\AI SM Manager`

## Architecture summary

The repository is a local Windows-first Python production CLI with a separate
Remotion/React renderer in `remotion/`. The Python package is the current
orchestration and validation boundary:

- `social_manager.cli` registers the video, intelligence, connector, and
  publication commands.
- `social_manager.core` owns JSON storyboard loading, Python guardrails,
  Zod delegation, narration-duration timing, manifests, paths, and subprocess
  execution.
- `social_manager.tts` invokes a local Windows SAPI PowerShell helper and
  records the actually selected voice.
- `social_manager.intelligence` owns the existing local SQLite knowledge
  store, WAL/busy-timeout configuration, analytics imports, ranking, ideas,
  creative brief generation, and ChatGPT Desktop prompt artifacts.
- `social_manager.connectors` calls the official YouTube Data/Analytics and
  Meta Graph APIs; it does not scrape competitor data.
- `remotion/src` validates the same storyboard contract with Zod and renders
  the registered video and thumbnail compositions.

The existing database schema is created idempotently by
`LocalKnowledgeStore.initialize()`. It currently contains `accounts`,
`content_items`, metric snapshot tables, sync/analysis runs, idea candidates,
creative assets, and an FTS5 content index. It does not yet have durable
agency tasks, approvals, content-family, artifact, or migration tables.

## Existing capabilities

- Strict compatible Python and Zod storyboard validation for four series.
- Remotion short and long-form compositions, scene registry, branded
components, thumbnail stills, local WAV narration, and estimated subtitles.
- Rendering/preview/package commands with no implicit overwrite, bounded
concurrency, FFprobe/decode inspection, representative frames, and manifests.
- Official authorized analytics syncs plus local JSON import and permitted
public competitor observations.
- Local performance analysis that creates evidence, hooks, ideas, a creative
brief, and a ChatGPT Desktop handoff prompt without an OpenAI API key.
- Existing content/production data is kept under `data/`, `research/`,
`output/`, `.work/`, and `remotion/public/jobs/`; these are gitignored and
must remain untouched.

## Baseline and dependencies

- Python: 3.12.10.
- Node: 26.13.0; npm: 11.20.0.
- Remotion project: 4.0.543, React 19.3.1, TypeScript 5.9.3, Zod 4.6.7.
- FastAPI, Uvicorn, Pydantic, and HTTPX are already available in the active
  Python environment, but are not declared in `pyproject.toml` yet.
- Baseline Python test command: `python -m unittest discover -s tests -v`.
  Result: 11/11 passed.
- Baseline Remotion command: `cd remotion; npm run lint`.
  Result: ESLint and TypeScript passed.

## Current limitations and architectural weaknesses

1. There is no local REST API, dashboard, event stream, durable job queue,
   approval workflow, or controlled background production manager.
2. The intelligence schema is initialized directly in code, so it has no
   version ledger or transactional migration/backup protocol.
3. The default knowledge database is `data/social_manager.sqlite3`, while the
   operational commands shown in earlier use may target `data/live.sqlite3`.
   The agency must use one explicit configured database rather than silently
   joining or replacing them.
4. The present ChatGPT Desktop workflow is a generated prompt file only. It
   needs a versioned task exchange folder, import validation, and an honest
   waiting state; unattended Desktop control must not be implied.
5. The current storyboard contract lacks explicit character, dialogue,
   continuity, factual-claim, and multi-format family data. Existing `2.0`
   storyboards must remain valid.
6. Captions are scene-relative estimated word reveal text, not a persisted
   caption JSON artifact. Later production work must retain that honest label
   until word timings are actually verified.
7. Competitor ingestion supports permitted public observations only. Instagram
   discovery remains constrained by Meta eligibility and cannot become a
   general public-account scraper.
8. The root Git repository currently tracks `remotion` as a dirty nested Git
   repository. It has pre-existing uncommitted renderer files and lockfile
   changes. The upgrade must not reset, replace, or commit those changes.

## Integration approach

Add an `agency` domain beside—not inside—the existing production and
intelligence modules. It will share a configured `LocalKnowledgeStore`
database, add versioned schema migrations, and expose FastAPI routes that call
small service/repository functions. The dashboard will be a thin Next.js
client of those routes. It will never duplicate scheduling or task state logic.

Existing CLI code remains the render authority. The API production service will
allowlist existing `python -m social_manager` subcommands and launch them in a
controlled subprocess, recording log paths and process state. A single
orchestrator will make deterministic local checks and then create Desktop task
folders when creative reasoning requires a human-operated ChatGPT Desktop
session. It will not claim those tasks are running until validated output is
imported.

## Migration and compatibility risks

- Back up the selected SQLite database before any schema migration and keep
  the backup outside the active database transaction.
- New tables use `CREATE TABLE IF NOT EXISTS` only through a numbered
  migration ledger; never alter or drop existing intelligence tables.
- Existing commands retain their option names and defaults. The agency database
  selection is explicit through `--database` or local configuration.
- Existing storyboard `specification_version: "2.0"` stays valid. Enriched
  fields are optional and are parsed only by the new schema layer.
- API file operations are restricted to project-approved roots; no endpoint
  executes arbitrary shell text or returns secrets.

## Compatibility commitments

The upgrade will preserve existing CLI commands, existing local SQLite data,
storyboard validation, Remotion compositions, SAPI fallback behavior, output
package conventions, no-overwrite safeguards, analytics connector behavior,
and passing baseline tests. Existing generated data and the dirty Remotion
worktree are out of scope for cleanup.
