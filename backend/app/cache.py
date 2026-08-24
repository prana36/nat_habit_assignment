import json
from datetime import datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings


def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def task_cache_key(user_id: int, params: dict[str, Any]) -> str:
    encoded = json.dumps(params, sort_keys=True, default=str)
    return f"tasks:{user_id}:{encoded}"


def encode_task(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def get_cached_tasks(key: str) -> dict[str, Any] | None:
    if not get_settings().cache_enabled:
        return None
    try:
        raw = get_redis().get(key)
        return json.loads(raw) if raw else None
    except RedisError:
        return None


def set_cached_tasks(key: str, payload: dict[str, Any]) -> None:
    if not get_settings().cache_enabled:
        return
    try:
        get_redis().setex(key, get_settings().cache_ttl_seconds, json.dumps(payload, default=encode_task))
    except RedisError:
        return


def invalidate_task_cache(user_id: int) -> None:
    if not get_settings().cache_enabled:
        return
    try:
        redis = get_redis()
        keys = list(redis.scan_iter(f"tasks:{user_id}:*"))
        if keys:
            redis.delete(*keys)
    except RedisError:
        return
