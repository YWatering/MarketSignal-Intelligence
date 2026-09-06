"""Operational helpers for retries, logs, scheduling, and source checks."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


T = TypeVar("T")


class JsonlRunLogger:
    """Append stable structured events without document-generation metadata."""

    def __init__(self, path: Path | None, run_id: str | None = None) -> None:
        self.path = path
        self.run_id = run_id or uuid.uuid4().hex
        self.sequence = 0

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        self.sequence += 1
        record = {
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event": event,
            **fields,
        }
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record


def retry_operation(
    operation: Callable[[], T],
    *,
    attempts: int,
    delay_seconds: float = 0.0,
    logger: JsonlRunLogger | None = None,
    context: dict[str, Any] | None = None,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> tuple[T, int]:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    context = context or {}
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        if logger:
            logger.emit("attempt_started", attempt=attempt, **context)
        started = time.monotonic()
        try:
            result = operation()
        except retry_exceptions as exc:
            last_error = exc
            if logger:
                logger.emit(
                    "attempt_failed",
                    attempt=attempt,
                    duration_seconds=round(time.monotonic() - started, 6),
                    error_type=type(exc).__name__,
                    error=str(exc),
                    retrying=attempt < attempts,
                    **context,
                )
            if attempt < attempts and delay_seconds > 0:
                time.sleep(delay_seconds)
            continue
        if logger:
            logger.emit(
                "attempt_succeeded",
                attempt=attempt,
                duration_seconds=round(time.monotonic() - started, 6),
                **context,
            )
        return result, attempt
    assert last_error is not None
    raise last_error


def compare_price_sources(
    primary: Iterable[dict[str, Any]],
    secondary: Iterable[dict[str, Any]],
    *,
    primary_provider: str,
    secondary_provider: str,
    tolerance_pct: float = 1.0,
    minimum_overlap: int = 3,
) -> dict[str, Any]:
    if tolerance_pct <= 0:
        raise ValueError("tolerance_pct must be greater than zero")
    if minimum_overlap < 1:
        raise ValueError("minimum_overlap must be at least 1")
    primary_by_date = {
        str(row.get("date", "")): float(row["close"])
        for row in primary
        if row.get("date") and row.get("close") is not None
    }
    secondary_by_date = {
        str(row.get("date", "")): float(row["close"])
        for row in secondary
        if row.get("date") and row.get("close") is not None
    }
    overlap_dates = sorted(set(primary_by_date) & set(secondary_by_date))
    differences = []
    for row_date in overlap_dates:
        primary_close = primary_by_date[row_date]
        secondary_close = secondary_by_date[row_date]
        denominator = max(abs(primary_close), abs(secondary_close), 1e-12)
        difference = abs(primary_close - secondary_close) / denominator * 100.0
        if math.isfinite(difference):
            differences.append(difference)
    outside_tolerance = sum(value > tolerance_pct for value in differences)
    if len(differences) < minimum_overlap:
        status = "insufficient_overlap"
        notes = f"fewer than {minimum_overlap} overlapping close observations"
    elif outside_tolerance:
        status = "warning"
        notes = "one or more overlapping closes exceeded the configured tolerance"
    else:
        status = "pass"
        notes = "overlapping closes remained within the configured tolerance"
    return {
        "status": status,
        "primary_provider": primary_provider,
        "secondary_provider": secondary_provider,
        "overlap_rows": len(differences),
        "mean_close_difference_pct": sum(differences) / len(differences) if differences else None,
        "max_close_difference_pct": max(differences) if differences else None,
        "outside_tolerance_rows": outside_tolerance,
        "tolerance_pct": tolerance_pct,
        "notes": notes,
    }


def schedule_is_due(
    *,
    interval_hours: float | None,
    last_completion_epoch: float | None,
    now_epoch: float,
) -> bool:
    if interval_hours is None or last_completion_epoch is None:
        return True
    if interval_hours <= 0:
        raise ValueError("interval_hours must be greater than zero")
    return now_epoch - last_completion_epoch >= interval_hours * 3600.0
