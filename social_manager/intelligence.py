from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

from .core import ROOT, StoryboardValidationError, load_storyboard


DATA_ROOT = ROOT / "data"
RESEARCH_ROOT = ROOT / "research"
DEFAULT_DATABASE = DATA_ROOT / "social_manager.sqlite3"

PLATFORMS = {"youtube", "instagram"}
TRACKS = {"short_form", "long_form", "both"}
METRICS = {
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "reach",
    "average_view_percentage",
    "average_view_duration_seconds",
    "watch_minutes",
    "subscribers_gained",
    "subscribers_lost",
    "follower_count",
    "media_count",
    "accounts_engaged",
    "total_interactions",
    "profile_views",
}

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in", "is", "it",
    "of", "on", "or", "that", "the", "this", "to", "was", "we", "what", "when", "why", "with", "you",
    "your", "my", "our", "their", "they", "these", "those", "than", "then", "into", "not", "just", "can",
    "will", "would", "should", "about", "after", "before", "here", "really", "video", "videos", "short", "shorts",
    "ai", "actual", "actually", "business", "complete", "demo", "faster", "guide", "make", "playbook", "practical",
    "proof", "proof-led", "screen", "simple", "three", "two", "four", "five", "one", "hour", "thing", "things",
    "expensive", "fast", "reliable", "scratch", "save", "saving",
}


class IntelligenceError(RuntimeError):
    """The local intelligence layer could not complete a requested operation."""


class ClosingSQLiteConnection(sqlite3.Connection):
    """SQLite's stock context manager commits but deliberately does not close.

    Explicit closure matters on Windows: otherwise a completed local analysis can leave the
    database file locked while the next CLI command or test starts.
    """

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool | None:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def date_today() -> str:
    return date.today().isoformat()


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def safe_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise IntelligenceError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise IntelligenceError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or result < 0:
        raise IntelligenceError(f"{field} must be a finite non-negative number")
    return result


@dataclass(frozen=True)
class AnalysisArtifact:
    run_id: str
    json_path: Path
    markdown_path: Path
    prompt_path: Path


class LocalKnowledgeStore:
    """A SQLite-backed evidence store. Raw performance remains local to this project."""

    def __init__(self, database_path: Path = DEFAULT_DATABASE) -> None:
        self.database_path = database_path.resolve()

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, factory=ClosingSQLiteConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY,
                    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'instagram')),
                    external_id TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    display_name TEXT,
                    is_owned INTEGER NOT NULL DEFAULT 0 CHECK(is_owned IN (0, 1)),
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, external_id)
                );

                CREATE TABLE IF NOT EXISTS content_items (
                    id INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'instagram')),
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    format TEXT NOT NULL DEFAULT 'unknown',
                    duration_seconds REAL,
                    published_at TEXT,
                    url TEXT,
                    thumbnail_url TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    first_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, external_id)
                );

                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id INTEGER PRIMARY KEY,
                    content_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    collected_on TEXT NOT NULL,
                    source TEXT NOT NULL,
                    UNIQUE(content_id, metric, collected_on)
                );
                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_content_metric_date
                    ON metric_snapshots(content_id, metric, collected_on DESC);

                CREATE TABLE IF NOT EXISTS account_metric_snapshots (
                    id INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    collected_on TEXT NOT NULL,
                    source TEXT NOT NULL,
                    UNIQUE(account_id, metric, collected_on)
                );

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    track TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS idea_candidates (
                    id TEXT PRIMARY KEY,
                    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    track TEXT NOT NULL,
                    title TEXT NOT NULL,
                    angle TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    hooks_json TEXT NOT NULL,
                    score REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed'
                );

                CREATE TABLE IF NOT EXISTS creative_assets (
                    id INTEGER PRIMARY KEY,
                    video_id TEXT NOT NULL UNIQUE,
                    storyboard_path TEXT,
                    output_path TEXT,
                    platform_external_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS content_search USING fts5(
                    title, body, platform UNINDEXED, content_id UNINDEXED
                );
                """
            )

    def start_sync(self, source: str, detail: dict[str, Any] | None = None) -> str:
        sync_id = f"sync-{uuid.uuid4().hex[:12]}"
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sync_runs(id, source, started_at, status, detail_json) VALUES (?, ?, ?, 'running', ?)",
                (sync_id, source, utc_now(), json_dump(detail or {})),
            )
        return sync_id

    def finish_sync(self, sync_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
        if status not in {"success", "failed"}:
            raise IntelligenceError("sync status must be success or failed")
        with self.connect() as connection:
            connection.execute(
                "UPDATE sync_runs SET completed_at = ?, status = ?, detail_json = ? WHERE id = ?",
                (utc_now(), status, json_dump(detail or {}), sync_id),
            )

    def upsert_account(
        self,
        platform: str,
        external_id: str,
        handle: str,
        is_owned: bool,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        if platform not in PLATFORMS:
            raise IntelligenceError(f"Unsupported platform: {platform}")
        if not external_id.strip() or not handle.strip():
            raise IntelligenceError("account external_id and handle are required")
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO accounts(platform, external_id, handle, display_name, is_owned, metadata_json, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, external_id) DO UPDATE SET
                    handle = excluded.handle,
                    display_name = excluded.display_name,
                    is_owned = excluded.is_owned,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (platform, external_id, handle, display_name, int(is_owned), json_dump(metadata or {}), now, now),
            )
            row = connection.execute(
                "SELECT id FROM accounts WHERE platform = ? AND external_id = ?", (platform, external_id)
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def _account_id(self, platform: str, external_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM accounts WHERE platform = ? AND external_id = ?", (platform, external_id)
            ).fetchone()
        if row is None:
            raise IntelligenceError(f"Unknown {platform} account: {external_id}")
        return int(row["id"])

    def upsert_content(self, record: dict[str, Any], source: str, collected_on: str | None = None) -> int:
        platform = str(record.get("platform", ""))
        if platform not in PLATFORMS:
            raise IntelligenceError("content.platform must be youtube or instagram")
        account_external_id = str(record.get("account_external_id", ""))
        external_id = str(record.get("external_id", ""))
        if not account_external_id or not external_id:
            raise IntelligenceError("content.account_external_id and content.external_id are required")
        account_id = self._account_id(platform, account_external_id)
        title = str(record.get("title") or "")
        body = str(record.get("body") or "")
        content_format = str(record.get("format") or "unknown")
        duration_value = record.get("duration_seconds")
        duration = safe_float(duration_value, "content.duration_seconds") if duration_value is not None else None
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO content_items(
                    account_id, platform, external_id, title, body, format, duration_seconds,
                    published_at, url, thumbnail_url, metadata_json, first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, external_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    title = excluded.title,
                    body = excluded.body,
                    format = excluded.format,
                    duration_seconds = excluded.duration_seconds,
                    published_at = excluded.published_at,
                    url = excluded.url,
                    thumbnail_url = excluded.thumbnail_url,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id, platform, external_id, title, body, content_format, duration,
                    record.get("published_at"), record.get("url"), record.get("thumbnail_url"),
                    json_dump(record.get("metadata") or {}), now, now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM content_items WHERE platform = ? AND external_id = ?", (platform, external_id)
            ).fetchone()
            assert row is not None
            content_id = int(row["id"])
            connection.execute("DELETE FROM content_search WHERE content_id = ?", (content_id,))
            connection.execute(
                "INSERT INTO content_search(title, body, platform, content_id) VALUES (?, ?, ?, ?)",
                (title, body, platform, str(content_id)),
            )
        metrics = record.get("metrics") or {}
        if not isinstance(metrics, dict):
            raise IntelligenceError("content.metrics must be an object")
        snapshot_date = collected_on or date_today()
        for metric, value in metrics.items():
            self.record_metric(content_id, str(metric), value, source, snapshot_date)
        return content_id

    def record_metric(self, content_id: int, metric: str, value: Any, source: str, collected_on: str | None = None) -> None:
        if metric not in METRICS:
            raise IntelligenceError(f"Unsupported metric: {metric}")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO metric_snapshots(content_id, metric, value, collected_on, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(content_id, metric, collected_on) DO UPDATE SET value = excluded.value, source = excluded.source
                """,
                (content_id, metric, safe_float(value, f"metric {metric}"), collected_on or date_today(), source),
            )

    def record_metric_by_external_id(
        self, platform: str, external_id: str, metric: str, value: Any, source: str = "manual", collected_on: str | None = None
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM content_items WHERE platform = ? AND external_id = ?", (platform, external_id)
            ).fetchone()
        if row is None:
            raise IntelligenceError(f"Unknown {platform} content item: {external_id}")
        self.record_metric(int(row["id"]), metric, value, source, collected_on)

    def record_account_metric(self, account_id: int, metric: str, value: Any, source: str, collected_on: str | None = None) -> None:
        if metric not in METRICS:
            raise IntelligenceError(f"Unsupported metric: {metric}")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO account_metric_snapshots(account_id, metric, value, collected_on, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, metric, collected_on) DO UPDATE SET value = excluded.value, source = excluded.source
                """,
                (account_id, metric, safe_float(value, f"metric {metric}"), collected_on or date_today(), source),
            )

    def ingest_document(self, payload: dict[str, Any], source: str = "local_import") -> dict[str, int]:
        accounts = payload.get("accounts")
        content = payload.get("content")
        if not isinstance(accounts, list) or not isinstance(content, list):
            raise IntelligenceError("Import JSON must contain accounts and content arrays")
        collected_on = str(payload.get("collected_on") or date_today())
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", collected_on):
            raise IntelligenceError("collected_on must use YYYY-MM-DD")
        for account in accounts:
            if not isinstance(account, dict):
                raise IntelligenceError("Each account must be an object")
            self.upsert_account(
                str(account.get("platform", "")),
                str(account.get("external_id", "")),
                str(account.get("handle", "")),
                bool(account.get("is_owned", False)),
                str(account["display_name"]) if account.get("display_name") else None,
                account.get("metadata") if isinstance(account.get("metadata"), dict) else {},
            )
        for item in content:
            if not isinstance(item, dict):
                raise IntelligenceError("Each content item must be an object")
            self.upsert_content(item, source=source, collected_on=collected_on)
        return {"accounts": len(accounts), "content": len(content)}

    def content_with_latest_metrics(self) -> list[dict[str, Any]]:
        metric_columns = ",\n".join(
            f"MAX(CASE WHEN lm.metric = '{metric}' THEN lm.value END) AS {metric}" for metric in sorted(METRICS)
        )
        query = f"""
            WITH latest_metric_dates AS (
                SELECT content_id, metric, MAX(collected_on) AS collected_on
                FROM metric_snapshots
                GROUP BY content_id, metric
            ), latest_metrics AS (
                SELECT s.content_id, s.metric, s.value
                FROM metric_snapshots s
                JOIN latest_metric_dates d
                  ON d.content_id = s.content_id AND d.metric = s.metric AND d.collected_on = s.collected_on
            )
            SELECT c.id, c.platform, c.external_id, c.title, c.body, c.format, c.duration_seconds,
                   c.published_at, c.url, c.thumbnail_url, c.metadata_json,
                   a.external_id AS account_external_id, a.handle, a.display_name, a.is_owned,
                   {metric_columns}
            FROM content_items c
            JOIN accounts a ON a.id = c.account_id
            LEFT JOIN latest_metrics lm ON lm.content_id = c.id
            GROUP BY c.id
            ORDER BY c.published_at DESC, c.id DESC
        """
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def create_analysis_run(self, track: str, inputs: dict[str, Any], summary: dict[str, Any]) -> str:
        run_id = f"analysis-{uuid.uuid4().hex[:12]}"
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO analysis_runs(id, created_at, track, inputs_json, summary_json) VALUES (?, ?, ?, ?, ?)",
                (run_id, utc_now(), track, json_dump(inputs), json_dump(summary)),
            )
        return run_id

    def save_idea_candidates(self, analysis_run_id: str, ideas: Iterable[dict[str, Any]]) -> None:
        with self.connect() as connection:
            for idea in ideas:
                connection.execute(
                    """
                    INSERT INTO idea_candidates(id, analysis_run_id, track, title, angle, evidence_json, hooks_json, score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        idea["id"], analysis_run_id, idea["track"], idea["title"], idea["angle"],
                        json_dump(idea["evidence"]), json_dump(idea["hooks"]), float(idea["score"]),
                    ),
                )

    def register_storyboard(self, storyboard_path: Path, output_path: Path | None = None) -> str:
        storyboard = load_storyboard(storyboard_path)
        video_id = str(storyboard["video_id"])
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO creative_assets(video_id, storyboard_path, output_path, status, created_at, updated_at)
                VALUES (?, ?, ?, 'draft', ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET storyboard_path = excluded.storyboard_path,
                    output_path = COALESCE(excluded.output_path, creative_assets.output_path), updated_at = excluded.updated_at
                """,
                (video_id, str(storyboard_path.resolve()), str(output_path.resolve()) if output_path else None, now, now),
            )
        return video_id

    def register_publication(self, video_id: str, platform: str, external_id: str, account_external_id: str, url: str | None = None) -> None:
        if platform not in PLATFORMS:
            raise IntelligenceError("publication platform must be youtube or instagram")
        account_id = self._account_id(platform, account_external_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO content_items(account_id, platform, external_id, url, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, external_id) DO UPDATE SET account_id = excluded.account_id,
                    url = COALESCE(excluded.url, content_items.url), updated_at = excluded.updated_at
                """,
                (account_id, platform, external_id, url, utc_now(), utc_now()),
            )
            updated = connection.execute(
                "UPDATE creative_assets SET platform_external_id = ?, status = 'published', updated_at = ? WHERE video_id = ?",
                (external_id, utc_now(), video_id),
            )
            if updated.rowcount != 1:
                raise IntelligenceError(f"Unknown local draft video_id: {video_id}. Register the storyboard before linking a publication.")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def content_track(item: dict[str, Any]) -> str:
    content_format = str(item.get("format") or "").lower()
    duration = item.get("duration_seconds")
    if content_format in {"reel", "reels", "short", "shorts"}:
        return "short_form"
    if item.get("platform") == "instagram" and content_format in {"video", "clip"}:
        return "short_form"
    if duration is not None and float(duration) <= 180:
        return "short_form"
    return "long_form"


def percentile(value: float, values: list[float]) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    below_or_equal = sum(candidate <= value for candidate in ranked)
    return round(100 * below_or_equal / len(ranked), 2)


def hook_family(text: str) -> str:
    text = text.strip().lower()
    if "?" in text or text.startswith(("why", "what", "how", "can", "should", "are", "is")):
        return "question"
    if re.search(r"\b\d+[\s:-]", text):
        return "numbered_specificity"
    if text.startswith(("stop", "never", "nobody", "most", "the truth", "you don't")):
        return "contrarian"
    if text.startswith(("how to", "build", "make", "learn", "use")):
        return "how_to"
    if any(term in text for term in ("mistake", "wrong", "failed", "broke", "problem")):
        return "failure_or_risk"
    return "direct_claim"


def first_hook(item: dict[str, Any]) -> str:
    candidate = str(item.get("title") or item.get("body") or "").strip()
    first_line = re.split(r"[\n.!]", candidate, maxsplit=1)[0].strip()
    return first_line[:180]


def theme_tokens(item: dict[str, Any]) -> list[str]:
    text = f"{item.get('title', '')} {item.get('body', '')}".lower()
    tokens: list[str] = []
    for raw_token in re.findall(r"[a-z][a-z0-9-]{2,}", text):
        token = raw_token
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 4:
            token = token[:-1]
        if token not in STOP_WORDS:
            tokens.append(token)
    return tokens


def score_content(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    views = [float(item.get("views") or 0) for item in items]
    engagements = []
    watch = [float(item.get("average_view_percentage") or 0) for item in items if item.get("average_view_percentage") is not None]
    for item in items:
        viewed = float(item.get("views") or 0)
        weighted = (
            float(item.get("likes") or 0)
            + 2 * float(item.get("comments") or 0)
            + 3 * float(item.get("shares") or 0)
            + 3 * float(item.get("saves") or 0)
        )
        item["engagement_rate"] = weighted / viewed if viewed else 0.0
        engagements.append(item["engagement_rate"])
        timestamp = _parse_timestamp(item.get("published_at"))
        age_days = max(1.0, (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds() / 86400) if timestamp else 30.0
        item["view_velocity"] = viewed / age_days
    velocities = [float(item["view_velocity"]) for item in items]
    for item in items:
        view_score = percentile(math.log1p(float(item.get("view_velocity") or 0)), [math.log1p(v) for v in velocities])
        engagement_score = percentile(float(item["engagement_rate"]), engagements)
        watch_score = percentile(float(item.get("average_view_percentage") or 0), watch) if watch else 50.0
        item["performance_score"] = round((0.5 * view_score) + (0.3 * engagement_score) + (0.2 * watch_score), 2)
        item["hook"] = first_hook(item)
        item["hook_family"] = hook_family(item["hook"])
    return sorted(items, key=lambda item: (float(item["performance_score"]), float(item.get("views") or 0)), reverse=True)


def _ideas_for_track(track: str, scored: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    relevant = [item for item in scored if content_track(item) == track]
    if not relevant:
        return []
    high_performers = relevant[:max(3, math.ceil(len(relevant) * 0.3))]
    competitor_terms: Counter[str] = Counter()
    owned_terms: Counter[str] = Counter()
    term_strength: Counter[str] = Counter()
    evidence_by_term: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in high_performers:
        terms = set(theme_tokens(item))
        for term in terms:
            if int(item["is_owned"]):
                owned_terms[term] += 1
            else:
                competitor_terms[term] += 1
            term_strength[term] += float(item["performance_score"])
            evidence_by_term[term].append(item)
    ranked_terms = sorted(
        set(competitor_terms) | set(owned_terms),
        key=lambda term: (
            competitor_terms[term] * 2 - owned_terms[term],
            term_strength[term],
            competitor_terms[term] + owned_terms[term],
            len(term),
            term,
        ),
        reverse=True,
    )
    fallback = [token for item in high_performers for token in theme_tokens(item)]
    terms: list[str] = []
    used_primary_sources: set[str] = set()
    for index, term in enumerate(ranked_terms):
        evidence_items = evidence_by_term.get(term, [])
        primary_source = str(evidence_items[0].get("external_id") or "") if evidence_items else ""
        remaining_primary_sources = {
            str((evidence_by_term.get(candidate) or [{}])[0].get("external_id") or "")
            for candidate in ranked_terms[index + 1:]
        }
        if primary_source in used_primary_sources and any(source and source not in used_primary_sources for source in remaining_primary_sources):
            continue
        terms.append(term)
        if primary_source:
            used_primary_sources.add(primary_source)
        if len(terms) == limit:
            break
    if not terms:
        terms = list(dict.fromkeys(fallback))[:limit]
    ideas: list[dict[str, Any]] = []
    for index, term in enumerate(terms, start=1):
        evidence_items = evidence_by_term.get(term, high_performers[:2])[:3]
        average_score = sum(float(item["performance_score"]) for item in evidence_items) / len(evidence_items)
        gap = competitor_terms[term] > owned_terms[term]
        if track == "short_form":
            title = f"The {term} shortcut people miss"
            angle = f"A 30–60 second evidence-led explanation of {term}, designed to test a {'competitor gap' if gap else 'validated audience theme'} with one concrete payoff."
            hooks = [
                f"Stop scrolling—this is the {term} shortcut that changes the result.",
                f"Most people get {term} wrong for one simple reason.",
                f"I tested the common {term} advice. Here is what actually held up.",
            ]
        else:
            title = f"The practical {term} playbook"
            angle = f"An 8–12 minute explainer that turns {term} into a clear framework, examples, trade-offs, and an actionable checklist."
            hooks = [
                f"If you are still unclear on {term}, start with this framework.",
                f"The usual advice about {term} leaves out the part that determines the outcome.",
                f"I mapped the full {term} workflow so you can skip the expensive mistakes.",
            ]
        evidence = [
            {
                "platform": item["platform"], "account": item["handle"], "owned": bool(item["is_owned"]),
                "title": item["title"], "url": item["url"], "performance_score": item["performance_score"],
                "views": item.get("views") or 0, "hook": item["hook"],
            }
            for item in evidence_items
        ]
        ideas.append(
            {
                "id": f"idea-{track}-{index}-{uuid.uuid4().hex[:6]}", "track": track, "title": title,
                "angle": angle, "evidence": evidence, "hooks": hooks, "score": round(average_score, 2),
            }
        )
    return ideas


def analyze_local_knowledge(store: LocalKnowledgeStore, track: str = "both") -> dict[str, Any]:
    if track not in TRACKS:
        raise IntelligenceError("track must be short_form, long_form, or both")
    items = store.content_with_latest_metrics()
    if not items:
        raise IntelligenceError("No local content is available. Import a local export or run an authorized sync first.")
    by_track: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_track[content_track(item)].append(item)
    scored = sorted(
        [scored_item for group in by_track.values() for scored_item in score_content(group)],
        key=lambda item: (float(item["performance_score"]), float(item.get("views") or 0)),
        reverse=True,
    )
    tracks = ["short_form", "long_form"] if track == "both" else [track]
    ideas = [idea for item_track in tracks for idea in _ideas_for_track(item_track, scored)]
    top_performers = [
        {
            "track": content_track(item), "platform": item["platform"], "account": item["handle"],
            "owned": bool(item["is_owned"]), "title": item["title"], "url": item["url"],
            "format": item["format"], "views": item.get("views") or 0,
            "engagement_rate": round(float(item["engagement_rate"]), 4),
            "average_view_percentage": item.get("average_view_percentage"),
            "performance_score": item["performance_score"], "hook": item["hook"], "hook_family": item["hook_family"],
        }
        for item in scored[:20]
    ]
    hook_counts = Counter(item["hook_family"] for item in scored[:max(3, math.ceil(len(scored) * 0.3))])
    return {
        "generated_at": utc_now(), "database": str(store.database_path), "content_count": len(items),
        "owned_content_count": sum(bool(item["is_owned"]) for item in items),
        "competitor_content_count": sum(not bool(item["is_owned"]) for item in items),
        "tracks": tracks, "top_performers": top_performers, "hook_patterns": dict(hook_counts),
        "ideas": ideas,
        "methodology": {
            "performance_score": "50% relative view velocity, 30% relative weighted engagement rate, 20% relative average view percentage when available.",
            "limits": "Recommendations are locally computed hypotheses from accessible data, not guarantees. Competitor private analytics are not included.",
        },
    }


def markdown_brief(analysis: dict[str, Any]) -> str:
    lines = [
        "# Local Social Intelligence Brief", "",
        f"Generated: {analysis['generated_at']}",
        f"Evidence: {analysis['content_count']} items ({analysis['owned_content_count']} owned, {analysis['competitor_content_count']} competitor/public).", "",
        "## Guardrails", "",
        "- Treat every recommendation as a testable hypothesis, not a performance guarantee.",
        "- Use only cited evidence below; do not claim access to competitor private analytics.",
        "- Preserve factual accuracy and do not fabricate sources, results, or personal experience.", "",
        "## Observed hook patterns", "",
    ]
    for family, count in sorted(analysis["hook_patterns"].items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- `{family}`: {count} high-performing observed examples")
    lines.extend(["", "## Highest-scoring observed content", ""])
    for item in analysis["top_performers"][:10]:
        owner = "owned" if item["owned"] else "competitor/public"
        lines.append(
            f"- **{item['title'] or '(untitled)'}** — {item['platform']} / @{item['account']} ({owner}); "
            f"score {item['performance_score']}, views {item['views']}, hook: “{item['hook']}”"
        )
        if item.get("url"):
            lines.append(f"  - Evidence: {item['url']}")
    lines.extend(["", "## Proposed test-ready ideas", ""])
    for idea in analysis["ideas"]:
        lines.extend([f"### {idea['title']} ({idea['track']}, score {idea['score']})", "", idea["angle"], "", "Hook variants:"])
        lines.extend(f"- {hook}" for hook in idea["hooks"])
        lines.append("Evidence:")
        for item in idea["evidence"]:
            source = f" — {item['url']}" if item.get("url") else ""
            lines.append(f"- @{item['account']}: {item['title']} (score {item['performance_score']}, {item['views']} views){source}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def creative_prompt(analysis_path: Path, track: str) -> str:
    return f'''# ChatGPT Desktop Creative Session — Local-Only Workflow

Read `{analysis_path.resolve()}` before proposing anything. It contains the local evidence base.

Act as a creative director for both short-form Reels/YouTube Shorts and 16:9 YouTube. Use the evidence, but do not claim that any idea is guaranteed to perform. Do not invent competitor metrics, sources, testimonials, or technical facts.

1. Choose one `short_form` and one `long_form` idea from the brief, or clearly explain an evidence-based alternative.
2. For each, select the strongest hook variant and provide two A/B alternatives.
3. Write a complete, truthful narration script appropriate to the format: 30–60 seconds for short-form; 8–12 minutes for long-form.
4. Create a production-ready storyboard JSON matching the existing `specification_version: "2.0"` contract. Use `instagram_reels` or `youtube_shorts` with `9:16` for short-form, and `youtube` with `16:9` for long-form.
5. Save the two storyboard files under `examples/drafts/`. Then run `python -m social_manager validate --storyboard <path>` for each. Fix any validation errors before reporting completion.
6. Do not render or publish without explicit approval. Register validated drafts locally with `python -m social_manager register-draft --storyboard <path>`.

The goal is an evidence-backed creative hypothesis, a clean script, and a renderable storyboard—not a promise of virality.
'''


def write_analysis_artifacts(store: LocalKnowledgeStore, track: str = "both", output_dir: Path | None = None) -> AnalysisArtifact:
    analysis = analyze_local_knowledge(store, track)
    run_id = store.create_analysis_run(track, {"database": str(store.database_path)}, analysis)
    for idea in analysis["ideas"]:
        idea["analysis_run_id"] = run_id
    store.save_idea_candidates(run_id, analysis["ideas"])
    destination = (output_dir or RESEARCH_ROOT / date_today() / run_id).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "analysis.json"
    markdown_path = destination / "creative_brief.md"
    prompt_path = destination / "chatgpt-desktop-prompt.md"
    json_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_brief(analysis), encoding="utf-8")
    prompt_path.write_text(creative_prompt(json_path, track), encoding="utf-8")
    return AnalysisArtifact(run_id, json_path, markdown_path, prompt_path)


def import_json_file(store: LocalKnowledgeStore, input_path: Path) -> dict[str, int]:
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IntelligenceError(f"Import file is missing: {input_path}") from exc
    except json.JSONDecodeError as exc:
        raise IntelligenceError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise IntelligenceError("Import JSON root must be an object")
    return store.ingest_document(payload, source=f"file:{input_path.name}")


def register_draft(store: LocalKnowledgeStore, storyboard_path: Path) -> str:
    try:
        return store.register_storyboard(storyboard_path)
    except StoryboardValidationError as exc:
        raise IntelligenceError(f"Cannot register an invalid storyboard: {exc}") from exc
