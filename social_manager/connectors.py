from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .intelligence import IntelligenceError, LocalKnowledgeStore, date_today


YOUTUBE_DATA_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2"
META_GRAPH_API = "https://graph.facebook.com/v27.0"


class ConnectorError(IntelligenceError):
    """An authorized platform call failed without exposing its secret material."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConnectorError(f"{name} is not set. Put the token in your local shell or credential loader; never commit it.")
    return value


def _get_json(url: str, params: dict[str, Any], access_token: str) -> dict[str, Any]:
    clean = {key: value for key, value in params.items() if value is not None}
    full_url = f"{url}?{urlencode(clean, doseq=True)}" if clean else url
    request = Request(full_url, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS API host URLs only
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise ConnectorError(f"Platform request failed ({exc.code}): {body}") from exc
    except (URLError, TimeoutError) as exc:
        raise ConnectorError(f"Platform request could not be completed: {exc.reason if isinstance(exc, URLError) else 'timeout'}") from exc
    except json.JSONDecodeError as exc:
        raise ConnectorError("Platform response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ConnectorError("Platform response had an unexpected shape")
    return payload


def _youtube_duration_seconds(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _youtube_metric_name(name: str) -> str | None:
    mapping = {
        "views": "views",
        "likes": "likes",
        "comments": "comments",
        "shares": "shares",
        "estimatedMinutesWatched": "watch_minutes",
        "averageViewDuration": "average_view_duration_seconds",
        "averageViewPercentage": "average_view_percentage",
        "subscribersGained": "subscribers_gained",
        "subscribersLost": "subscribers_lost",
    }
    return mapping.get(name)


def _youtube_metrics_by_video(access_token: str, start_date: str, end_date: str) -> dict[str, dict[str, float]]:
    payload = _get_json(
        f"{YOUTUBE_ANALYTICS_API}/reports",
        {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": "video",
            "sort": "-views",
            "maxResults": 200,
            "metrics": "views,likes,comments,shares,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,subscribersLost",
        },
        access_token,
    )
    headers = [str(column.get("name")) for column in payload.get("columnHeaders", []) if isinstance(column, dict)]
    rows = payload.get("rows") or []
    metrics: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, list) or not row or len(row) != len(headers):
            continue
        values = dict(zip(headers, row, strict=True))
        video_id = str(values.pop("video", ""))
        if not video_id:
            continue
        normalized: dict[str, float] = {}
        for name, value in values.items():
            mapped = _youtube_metric_name(name)
            if mapped and isinstance(value, (int, float)):
                normalized[mapped] = float(value)
        metrics[video_id] = normalized
    return metrics


def _youtube_channel(access_token: str, channel_id: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"part": "snippet,statistics,contentDetails"}
    if channel_id:
        params["id"] = channel_id
    else:
        params["mine"] = "true"
    payload = _get_json(f"{YOUTUBE_DATA_API}/channels", params, access_token)
    items = payload.get("items") or []
    if not items or not isinstance(items[0], dict):
        raise ConnectorError("No YouTube channel was returned. Check the OAuth scope and selected channel.")
    return items[0]


def _youtube_uploads(access_token: str, playlist_id: str, max_results: int = 50) -> list[dict[str, Any]]:
    payload = _get_json(
        f"{YOUTUBE_DATA_API}/playlistItems",
        {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": min(max_results, 50)},
        access_token,
    )
    items = payload.get("items") or []
    return [item for item in items if isinstance(item, dict)]


def _youtube_videos(access_token: str, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not video_ids:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(video_ids), 50):
        payload = _get_json(
            f"{YOUTUBE_DATA_API}/videos",
            {"part": "snippet,contentDetails,statistics", "id": ",".join(video_ids[start:start + 50])},
            access_token,
        )
        for item in payload.get("items") or []:
            if isinstance(item, dict) and item.get("id"):
                result[str(item["id"])] = item
    return result


def _youtube_content_record(channel_id: str, item: dict[str, Any], metrics: dict[str, float]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    duration = _youtube_duration_seconds(str(details.get("duration") or ""))
    thumbnail = next(iter((snippet.get("thumbnails") or {}).values()), {}) if isinstance(snippet.get("thumbnails"), dict) else {}
    merged_metrics = dict(metrics)
    for raw, mapped in (("viewCount", "views"), ("likeCount", "likes"), ("commentCount", "comments")):
        if mapped not in merged_metrics and statistics.get(raw) is not None:
            try:
                merged_metrics[mapped] = float(statistics[raw])
            except (TypeError, ValueError):
                pass
    return {
        "platform": "youtube", "account_external_id": channel_id, "external_id": str(item["id"]),
        "title": str(snippet.get("title") or ""), "body": str(snippet.get("description") or ""),
        "format": "short" if duration is not None and duration <= 180 else "video",
        "duration_seconds": duration, "published_at": snippet.get("publishedAt"),
        "url": f"https://www.youtube.com/watch?v={item['id']}",
        "thumbnail_url": thumbnail.get("url") if isinstance(thumbnail, dict) else None,
        "metadata": {"youtube_statistics": statistics}, "metrics": merged_metrics,
    }


def sync_youtube_owned(store: LocalKnowledgeStore, start_date: str, end_date: str, max_results: int = 50) -> dict[str, int]:
    """Sync the currently authorized YouTube channel. Requires yt-analytics.readonly and youtube.readonly OAuth scopes."""
    token = _required_env("YOUTUBE_ACCESS_TOKEN")
    sync_id = store.start_sync("youtube_owned", {"start_date": start_date, "end_date": end_date})
    try:
        channel = _youtube_channel(token)
        channel_id = str(channel["id"])
        snippet = channel.get("snippet") if isinstance(channel.get("snippet"), dict) else {}
        statistics = channel.get("statistics") if isinstance(channel.get("statistics"), dict) else {}
        account_id = store.upsert_account(
            "youtube", channel_id, str(snippet.get("customUrl") or channel_id), True,
            str(snippet.get("title") or ""), {"description": snippet.get("description", "")},
        )
        for raw, metric in (("subscriberCount", "follower_count"), ("videoCount", "media_count")):
            if statistics.get(raw) is not None:
                store.record_account_metric(account_id, metric, statistics[raw], "youtube_data")
        uploads_id = str(((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or "")
        if not uploads_id:
            raise ConnectorError("The authorized YouTube channel did not expose an uploads playlist.")
        videos = _youtube_videos(
            token,
            [str(((entry.get("contentDetails") or {}).get("videoId") or "")) for entry in _youtube_uploads(token, uploads_id, max_results)],
        )
        analytic_metrics = _youtube_metrics_by_video(token, start_date, end_date)
        for video in videos.values():
            store.upsert_content(_youtube_content_record(channel_id, video, analytic_metrics.get(str(video["id"]), {})), "youtube_owned")
        result = {"accounts": 1, "content": len(videos)}
        store.finish_sync(sync_id, "success", result)
        return result
    except Exception as exc:
        store.finish_sync(sync_id, "failed", {"error": str(exc)})
        raise


def _find_youtube_channel(access_token: str, handle: str) -> dict[str, Any]:
    cleaned = handle.removeprefix("@").strip()
    payload = _get_json(
        f"{YOUTUBE_DATA_API}/search",
        {"part": "snippet", "q": cleaned, "type": "channel", "maxResults": 5},
        access_token,
    )
    candidates = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    if not candidates:
        raise ConnectorError(f"No public YouTube channel found for '{handle}'.")
    channel_id = str((candidates[0].get("id") or {}).get("channelId") or "")
    if not channel_id:
        raise ConnectorError(f"YouTube returned no channel ID for '{handle}'.")
    return _youtube_channel(access_token, channel_id)


def sync_youtube_competitor(store: LocalKnowledgeStore, handle: str, max_results: int = 50) -> dict[str, int]:
    """Sync permitted public metadata and counts only; never competitor private analytics."""
    token = _required_env("YOUTUBE_ACCESS_TOKEN")
    sync_id = store.start_sync("youtube_competitor", {"handle": handle})
    try:
        channel = _find_youtube_channel(token, handle)
        channel_id = str(channel["id"])
        snippet = channel.get("snippet") if isinstance(channel.get("snippet"), dict) else {}
        stats = channel.get("statistics") if isinstance(channel.get("statistics"), dict) else {}
        account_id = store.upsert_account(
            "youtube", channel_id, str(snippet.get("customUrl") or handle), False, str(snippet.get("title") or ""),
            {"description": snippet.get("description", "")},
        )
        for raw, metric in (("subscriberCount", "follower_count"), ("videoCount", "media_count")):
            if stats.get(raw) is not None:
                store.record_account_metric(account_id, metric, stats[raw], "youtube_data_public")
        uploads_id = str(((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or "")
        videos = _youtube_videos(
            token,
            [str(((entry.get("contentDetails") or {}).get("videoId") or "")) for entry in _youtube_uploads(token, uploads_id, max_results)],
        )
        for video in videos.values():
            store.upsert_content(_youtube_content_record(channel_id, video, {}), "youtube_data_public")
        result = {"accounts": 1, "content": len(videos)}
        store.finish_sync(sync_id, "success", result)
        return result
    except Exception as exc:
        store.finish_sync(sync_id, "failed", {"error": str(exc)})
        raise


def _instagram_base_record(account_id: str, media: dict[str, Any], metrics: dict[str, float] | None = None) -> dict[str, Any]:
    media_type = str(media.get("media_product_type") or media.get("media_type") or "unknown").lower()
    normalized_format = "reel" if media_type == "reels" else media_type
    merged = metrics or {}
    for field, metric in (("like_count", "likes"), ("comments_count", "comments")):
        if media.get(field) is not None:
            try:
                merged.setdefault(metric, float(media[field]))
            except (TypeError, ValueError):
                pass
    return {
        "platform": "instagram", "account_external_id": account_id, "external_id": str(media["id"]),
        "title": str(media.get("caption") or "")[:140], "body": str(media.get("caption") or ""),
        "format": normalized_format, "published_at": media.get("timestamp"), "url": media.get("permalink"),
        "thumbnail_url": media.get("thumbnail_url") or media.get("media_url"),
        "metadata": {"media_type": media.get("media_type"), "media_product_type": media.get("media_product_type")},
        "metrics": merged,
    }


def _instagram_media(access_token: str, account_id: str, max_results: int = 50) -> list[dict[str, Any]]:
    payload = _get_json(
        f"{META_GRAPH_API}/{account_id}/media",
        {
            "fields": "id,caption,comments_count,like_count,media_type,media_product_type,permalink,timestamp,thumbnail_url,media_url",
            "limit": min(max_results, 100),
        },
        access_token,
    )
    return [item for item in payload.get("data") or [] if isinstance(item, dict)]


def _instagram_account_metrics(access_token: str, account_id: str) -> dict[str, float]:
    payload = _get_json(
        f"{META_GRAPH_API}/{account_id}/insights",
        {"metric": "reach,accounts_engaged,total_interactions,profile_views", "period": "day", "since": date_today(), "until": date_today()},
        access_token,
    )
    result: dict[str, float] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        values = item.get("values") or []
        if values and isinstance(values[-1], dict) and isinstance(values[-1].get("value"), (int, float)):
            result[str(item.get("name"))] = float(values[-1]["value"])
    return result


def sync_instagram_owned(store: LocalKnowledgeStore, account_id: str | None = None, max_results: int = 50) -> dict[str, int]:
    """Sync the authorized Instagram professional account; individual metric availability varies by media type and Meta API version."""
    token = _required_env("META_ACCESS_TOKEN")
    ig_account_id = account_id or _required_env("INSTAGRAM_ACCOUNT_ID")
    sync_id = store.start_sync("instagram_owned", {"account_id": ig_account_id})
    try:
        account = _get_json(
            f"{META_GRAPH_API}/{ig_account_id}", {"fields": "id,username,name,biography,followers_count,media_count"}, token
        )
        handle = str(account.get("username") or ig_account_id)
        local_account_id = store.upsert_account(
            "instagram", str(account.get("id") or ig_account_id), handle, True, str(account.get("name") or ""),
            {"biography": account.get("biography", "")},
        )
        for raw, metric in (("followers_count", "follower_count"), ("media_count", "media_count")):
            if account.get(raw) is not None:
                store.record_account_metric(local_account_id, metric, account[raw], "instagram_graph")
        # Account insight availability varies by app configuration. Keep content import useful even when one insight endpoint is unavailable.
        try:
            for metric, value in _instagram_account_metrics(token, ig_account_id).items():
                store.record_account_metric(local_account_id, metric, value, "instagram_graph")
        except ConnectorError:
            pass
        media = _instagram_media(token, ig_account_id, max_results)
        for item in media:
            store.upsert_content(_instagram_base_record(str(account.get("id") or ig_account_id), item), "instagram_graph")
        result = {"accounts": 1, "content": len(media)}
        store.finish_sync(sync_id, "success", result)
        return result
    except Exception as exc:
        store.finish_sync(sync_id, "failed", {"error": str(exc)})
        raise


def sync_instagram_competitor(store: LocalKnowledgeStore, handle: str, owner_account_id: str | None = None, max_results: int = 50) -> dict[str, int]:
    """Use Meta's permitted Business Discovery data; no scraping and no private competitor insights."""
    token = _required_env("META_ACCESS_TOKEN")
    ig_owner_id = owner_account_id or _required_env("INSTAGRAM_ACCOUNT_ID")
    clean_handle = handle.removeprefix("@").strip()
    sync_id = store.start_sync("instagram_competitor", {"handle": clean_handle})
    try:
        fields = (
            f"business_discovery.username({clean_handle}){{id,username,name,biography,followers_count,media_count,"
            f"media.limit({min(max_results, 100)}){{id,caption,comments_count,like_count,media_type,media_product_type,permalink,timestamp,thumbnail_url}}}}"
        )
        payload = _get_json(f"{META_GRAPH_API}/{ig_owner_id}", {"fields": fields}, token)
        account = payload.get("business_discovery")
        if not isinstance(account, dict) or not account.get("id"):
            raise ConnectorError(f"Meta Business Discovery returned no eligible public professional account for '@{clean_handle}'.")
        competitor_id = str(account["id"])
        local_account_id = store.upsert_account(
            "instagram", competitor_id, str(account.get("username") or clean_handle), False, str(account.get("name") or ""),
            {"biography": account.get("biography", "")},
        )
        for raw, metric in (("followers_count", "follower_count"), ("media_count", "media_count")):
            if account.get(raw) is not None:
                store.record_account_metric(local_account_id, metric, account[raw], "instagram_business_discovery")
        media = account.get("media") if isinstance(account.get("media"), dict) else {}
        entries = [item for item in media.get("data") or [] if isinstance(item, dict)]
        for item in entries:
            store.upsert_content(_instagram_base_record(competitor_id, item), "instagram_business_discovery")
        result = {"accounts": 1, "content": len(entries)}
        store.finish_sync(sync_id, "success", result)
        return result
    except Exception as exc:
        store.finish_sync(sync_id, "failed", {"error": str(exc)})
        raise


def load_competitor_config(path: Path) -> list[dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConnectorError(f"Competitor config is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"Invalid competitor config JSON: {exc.msg}") from exc
    entries = raw.get("competitors") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ConnectorError("Competitor config must contain a competitors array")
    result: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("platform") not in {"youtube", "instagram"} or not isinstance(entry.get("handle"), str):
            raise ConnectorError("Each competitor needs platform (youtube/instagram) and handle")
        result.append({"platform": str(entry["platform"]), "handle": str(entry["handle"])})
    return result
