from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def ensure_dir(path: str | Path) -> Path:
    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    return output


def load_json(path: str | Path, default: Any | None = None) -> Any:
    candidate = Path(path)
    if not candidate.exists():
        return {} if default is None else default
    return json.loads(candidate.read_text(encoding="utf-8"))


def save_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    ensure_dir(target.parent)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def bps_to_decimal(value_bps: float) -> float:
    return value_bps / 10_000.0


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def rolling_mean(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return sum(values_list) / len(values_list)


def stddev(values: Iterable[float]) -> float:
    values_list = list(values)
    if len(values_list) < 2:
        return 0.0
    mean = rolling_mean(values_list)
    variance = sum((value - mean) ** 2 for value in values_list) / len(values_list)
    return math.sqrt(variance)


def floor_ts(timestamp: datetime, minutes: int = 1) -> datetime:
    bucket_seconds = minutes * 60
    floored = int(timestamp.timestamp() // bucket_seconds * bucket_seconds)
    return datetime.fromtimestamp(floored, tz=UTC)


def daterange(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    cursor = start
    values: list[datetime] = []
    while cursor < end:
        values.append(cursor)
        cursor += step
    return values
