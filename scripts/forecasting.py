"""Leakage-aware, multi-model price forecasting from cleaned stage-two data."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

import numpy as np

from versioning import (
    BASELINE_MODEL_VERSION,
    EXPONENTIAL_SMOOTHING_MODEL_VERSION,
    HISTORICAL_MEAN_MODEL_VERSION,
    MOVING_AVERAGE_MODEL_VERSION,
    RIDGE_MODEL_VERSION,
)


DATE_FORMAT = "%Y-%m-%d"
FEATURE_WINDOW = 21
MIN_TRAINING_SAMPLES = 40
INTERVAL_LEVEL = 90
DEFAULT_ROBUSTNESS_WINDOWS = (20, 40, 80)
DEFAULT_PRICE_WEIGHT = 0.65
DEFAULT_DIRECTION_WEIGHT = 0.35
DEFAULT_MIN_PRICE_IMPROVEMENT_PCT = 2.0
DEFAULT_MIN_DIRECTION_IMPROVEMENT_POINTS = 5.0
DEFAULT_MAX_PRICE_DETERIORATION_PCT = 1.0
DEFAULT_MAX_DIRECTION_DETERIORATION_POINTS = 5.0
DEFAULT_TRANSACTION_COST_BPS = 10.0
DEFAULT_SLIPPAGE_BPS = 5.0

BASELINE_MODEL = "last_close_baseline"
BASELINE_VERSION = BASELINE_MODEL_VERSION
MOVING_AVERAGE_MODEL = "moving_average_baseline"
MOVING_AVERAGE_VERSION = MOVING_AVERAGE_MODEL_VERSION
HISTORICAL_MEAN_MODEL = "historical_mean_baseline"
HISTORICAL_MEAN_VERSION = HISTORICAL_MEAN_MODEL_VERSION
EXPONENTIAL_SMOOTHING_MODEL = "exponential_smoothing_baseline"
EXPONENTIAL_SMOOTHING_VERSION = EXPONENTIAL_SMOOTHING_MODEL_VERSION
RIDGE_MODEL = "multisignal_ridge"
RIDGE_VERSION = RIDGE_MODEL_VERSION
MODEL_NAMES = (
    BASELINE_MODEL,
    MOVING_AVERAGE_MODEL,
    HISTORICAL_MEAN_MODEL,
    EXPONENTIAL_SMOOTHING_MODEL,
    RIDGE_MODEL,
)
MODEL_VERSIONS = {
    BASELINE_MODEL: BASELINE_VERSION,
    MOVING_AVERAGE_MODEL: MOVING_AVERAGE_VERSION,
    HISTORICAL_MEAN_MODEL: HISTORICAL_MEAN_VERSION,
    EXPONENTIAL_SMOOTHING_MODEL: EXPONENTIAL_SMOOTHING_VERSION,
    RIDGE_MODEL: RIDGE_VERSION,
}

FEATURE_DESCRIPTIONS = {
    "return_1d": "Previous one-trading-day close return.",
    "return_5d": "Previous five-trading-day close return.",
    "ma_gap_5": "Close divided by the trailing five-day moving average minus one.",
    "ma_gap_20": "Close divided by the trailing twenty-day moving average minus one.",
    "ema_gap_10": "Close divided by the trailing ten-day exponential moving average minus one.",
    "volatility_20": "Standard deviation of trailing close-to-close returns.",
    "volume_ratio_20": "Current volume divided by trailing twenty-day average volume minus one.",
    "news_tone_7d": "Positive minus negative stage-two news items available in the trailing seven calendar days.",
    "news_count_7d": "Stage-two news item count available in the trailing seven calendar days.",
    "announcement_count_30d": "Stage-two notice or filing count available in the trailing thirty calendar days.",
    "net_profit_margin": "Latest available same-period net income divided by revenue.",
    "current_ratio": "Latest available same-period current assets divided by current liabilities.",
    "operating_cash_flow_margin": "Latest available same-period operating cash flow divided by revenue.",
    "revenue_growth": "Latest available revenue change against a comparable-duration prior period.",
    "fundamental_coverage": "Share of configured fundamental ratios available at the feature cutoff.",
}
FEATURE_NAMES = tuple(FEATURE_DESCRIPTIONS)


class ForecastError(RuntimeError):
    """Expected forecast validation or modeling failure."""


def _parse_business_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], DATE_FORMAT)
    except ValueError:
        try:
            return parsedate_to_datetime(text).replace(tzinfo=None)
        except (TypeError, ValueError, IndexError, OverflowError):
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
            except (TypeError, ValueError, OverflowError):
                return None


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _direction(value: float) -> int:
    if value > 1e-12:
        return 1
    if value < -1e-12:
        return -1
    return 0


def _latest_metric_rows(financials: Iterable[dict[str, Any]], cutoff: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in financials:
        filed_date = str(row.get("filed_date", ""))
        if not filed_date or filed_date > cutoff:
            continue
        grouped.setdefault(str(row.get("metric", "")), []).append(row)
    for rows in grouped.values():
        rows.sort(
            key=lambda item: (
                str(item.get("period_end", "")),
                str(item.get("period_start", "")),
                str(item.get("filed_date", "")),
            ),
            reverse=True,
        )
    return grouped


def _matching_period_ratio(
    grouped: dict[str, list[dict[str, Any]]], numerator_metric: str, denominator_metric: str
) -> float | None:
    denominators = {
        (str(row.get("period_start", "")), str(row.get("period_end", ""))): row
        for row in grouped.get(denominator_metric, [])
    }
    for numerator in grouped.get(numerator_metric, []):
        key = (str(numerator.get("period_start", "")), str(numerator.get("period_end", "")))
        denominator = denominators.get(key)
        if denominator:
            return _safe_ratio(float(numerator["value"]), float(denominator["value"]))
    return None


def _current_ratio(grouped: dict[str, list[dict[str, Any]]]) -> float | None:
    liabilities = {
        str(row.get("period_end", "")): row for row in grouped.get("current_liabilities", [])
    }
    for assets in grouped.get("current_assets", []):
        liability = liabilities.get(str(assets.get("period_end", "")))
        if liability:
            return _safe_ratio(float(assets["value"]), float(liability["value"]))
    return None


def _revenue_growth(grouped: dict[str, list[dict[str, Any]]]) -> float | None:
    revenues = grouped.get("revenue", [])
    if len(revenues) < 2:
        return None
    latest = revenues[0]
    latest_start = _parse_business_date(latest.get("period_start"))
    latest_end = _parse_business_date(latest.get("period_end"))
    if not latest_start or not latest_end:
        return None
    latest_duration = (latest_end - latest_start).days
    for prior in revenues[1:]:
        prior_start = _parse_business_date(prior.get("period_start"))
        prior_end = _parse_business_date(prior.get("period_end"))
        if not prior_start or not prior_end:
            continue
        if abs((prior_end - prior_start).days - latest_duration) <= 7:
            return _safe_ratio(float(latest["value"]) - float(prior["value"]), float(prior["value"]))
    return None


def _fundamental_features(financials: list[dict[str, Any]], cutoff: str) -> dict[str, float]:
    grouped = _latest_metric_rows(financials, cutoff)
    values = {
        "net_profit_margin": _matching_period_ratio(grouped, "net_income", "revenue"),
        "current_ratio": _current_ratio(grouped),
        "operating_cash_flow_margin": _matching_period_ratio(grouped, "operating_cash_flow", "revenue"),
        "revenue_growth": _revenue_growth(grouped),
    }
    coverage = sum(value is not None for value in values.values()) / len(values)
    return {
        "net_profit_margin": _clip(values["net_profit_margin"] or 0.0, -5.0, 5.0),
        "current_ratio": _clip(values["current_ratio"] or 0.0, 0.0, 20.0),
        "operating_cash_flow_margin": _clip(values["operating_cash_flow_margin"] or 0.0, -5.0, 5.0),
        "revenue_growth": _clip(values["revenue_growth"] or 0.0, -5.0, 5.0),
        "fundamental_coverage": coverage,
    }


def _dated_rows(
    rows: Iterable[dict[str, Any]], field: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        row_date = _parse_business_date(row.get(field))
        if row_date and start <= row_date <= end:
            selected.append(row)
    return selected


def _ema(values: np.ndarray, span: int) -> float:
    alpha = 2.0 / (span + 1.0)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1.0 - alpha) * result
    return result


def build_feature_vector(
    price_history: list[dict[str, Any]],
    financials: list[dict[str, Any]],
    news: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    *,
    information_cutoff: str | None = None,
) -> dict[str, float]:
    if len(price_history) < FEATURE_WINDOW:
        raise ForecastError(f"at least {FEATURE_WINDOW} price rows are required to build forecast features")
    ordered = sorted(price_history, key=lambda row: str(row["date"]))
    closes = np.asarray([float(row["close"]) for row in ordered], dtype=float)
    volumes = np.asarray([float(row["volume"]) for row in ordered], dtype=float)
    if np.any(~np.isfinite(closes)) or np.any(closes <= 0):
        raise ForecastError("forecast price history contains invalid close values")
    if np.any(~np.isfinite(volumes)) or np.any(volumes < 0):
        raise ForecastError("forecast price history contains invalid volume values")
    feature_date = str(ordered[-1]["date"])
    cutoff = min(feature_date, information_cutoff) if information_cutoff else feature_date
    cutoff_date = _parse_business_date(cutoff)
    if not cutoff_date:
        raise ForecastError(f"forecast information cutoff is invalid: {cutoff}")
    returns = closes[1:] / closes[:-1] - 1.0
    average_volume = float(np.mean(volumes[-20:]))
    news_rows = _dated_rows(news, "published_at", cutoff_date - timedelta(days=6), cutoff_date)
    announcement_rows = _dated_rows(
        announcements, "filed_date", cutoff_date - timedelta(days=29), cutoff_date
    )
    tone_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    ema_10 = _ema(closes[-10:], 10)
    feature_values = {
        "return_1d": float(returns[-1]),
        "return_5d": float(closes[-1] / closes[-6] - 1.0),
        "ma_gap_5": float(closes[-1] / np.mean(closes[-5:]) - 1.0),
        "ma_gap_20": float(closes[-1] / np.mean(closes[-20:]) - 1.0),
        "ema_gap_10": float(closes[-1] / ema_10 - 1.0) if ema_10 else 0.0,
        "volatility_20": float(np.std(returns[-20:], ddof=0)),
        "volume_ratio_20": float(volumes[-1] / average_volume - 1.0) if average_volume else 0.0,
        "news_tone_7d": sum(tone_map.get(str(row.get("sentiment", "neutral")), 0.0) for row in news_rows),
        "news_count_7d": float(len(news_rows)),
        "announcement_count_30d": float(len(announcement_rows)),
        **_fundamental_features(financials, cutoff),
    }
    return {name: float(feature_values[name]) for name in FEATURE_NAMES}


def build_training_samples(
    prices: list[dict[str, Any]],
    financials: list[dict[str, Any]],
    news: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(prices, key=lambda row: str(row["date"]))
    samples: list[dict[str, Any]] = []
    for target_index in range(FEATURE_WINDOW, len(ordered)):
        history = ordered[:target_index]
        target = ordered[target_index]
        previous_close = float(history[-1]["close"])
        target_close = float(target["close"])
        samples.append(
            {
                "feature_date": str(history[-1]["date"]),
                "target_date": str(target["date"]),
                "previous_close": previous_close,
                "actual_close": target_close,
                "actual_return": target_close / previous_close - 1.0,
                "features": build_feature_vector(history, financials, news, announcements),
            }
        )
    return samples


def _fit_ridge(samples: list[dict[str, Any]], alpha: float) -> dict[str, Any]:
    if not samples:
        raise ForecastError("ridge model received no training samples")
    x = np.asarray([[sample["features"][name] for name in FEATURE_NAMES] for sample in samples], dtype=float)
    y = np.asarray([float(sample["actual_return"]) for sample in samples], dtype=float)
    if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)):
        raise ForecastError("ridge model received non-finite training values")
    means = np.mean(x, axis=0)
    scales = np.std(x, axis=0, ddof=0)
    scales = np.where(scales < 1e-12, 1.0, scales)
    standardized = (x - means) / scales
    design = np.column_stack((np.ones(len(standardized)), standardized))
    regularization = np.eye(design.shape[1], dtype=float) * math.sqrt(alpha)
    regularization[0, 0] = 0.0
    augmented_design = np.vstack((design, regularization))
    augmented_target = np.concatenate((y, np.zeros(design.shape[1], dtype=float)))
    try:
        weights = np.linalg.lstsq(augmented_design, augmented_target, rcond=None)[0]
    except np.linalg.LinAlgError:
        weights = np.linalg.pinv(augmented_design) @ augmented_target
    if np.any(~np.isfinite(weights)):
        raise ForecastError("ridge model fitting produced non-finite coefficients")
    return {
        "intercept": float(weights[0]),
        "coefficients": np.asarray(weights[1:], dtype=float),
        "means": means,
        "scales": scales,
    }


def _predict_ridge(model: dict[str, Any], features: dict[str, float]) -> float:
    vector = np.asarray([features[name] for name in FEATURE_NAMES], dtype=float)
    standardized = (vector - model["means"]) / model["scales"]
    result = float(model["intercept"] + standardized @ model["coefficients"])
    if not math.isfinite(result):
        raise ForecastError("ridge model prediction produced a non-finite return")
    return result


def _return_cap(samples: list[dict[str, Any]]) -> float:
    returns = np.asarray([abs(float(sample["actual_return"])) for sample in samples], dtype=float)
    if not len(returns):
        return 0.05
    return _clip(float(np.quantile(returns, 0.99)) * 1.5, 0.02, 0.20)


def _fit_model(model_name: str, samples: list[dict[str, Any]], ridge_alpha: float) -> dict[str, Any] | None:
    if model_name == RIDGE_MODEL:
        return _fit_ridge(samples, ridge_alpha)
    if model_name == HISTORICAL_MEAN_MODEL:
        if not samples:
            raise ForecastError("historical mean model received no training samples")
        return {"mean_return": float(np.mean([sample["actual_return"] for sample in samples]))}
    if model_name in MODEL_NAMES:
        return None
    raise ForecastError(f"unknown forecast model: {model_name}")


def _predict_model(model_name: str, model: dict[str, Any] | None, sample: dict[str, Any]) -> float:
    features = sample["features"]
    if model_name == BASELINE_MODEL:
        return 0.0
    if model_name == MOVING_AVERAGE_MODEL:
        value = _safe_ratio(1.0, 1.0 + features["ma_gap_5"])
        return float(value - 1.0) if value is not None else 0.0
    if model_name == HISTORICAL_MEAN_MODEL:
        if not model:
            raise ForecastError("historical mean model state is missing")
        return float(model["mean_return"])
    if model_name == EXPONENTIAL_SMOOTHING_MODEL:
        value = _safe_ratio(1.0, 1.0 + features["ema_gap_10"])
        return float(value - 1.0) if value is not None else 0.0
    if model_name == RIDGE_MODEL:
        if not model:
            raise ForecastError("ridge model state is missing")
        return _predict_ridge(model, features)
    raise ForecastError(f"unknown forecast model: {model_name}")


def _prediction_row(
    model_name: str,
    sample: dict[str, Any],
    predicted_return: float,
    training_samples: int,
) -> dict[str, Any]:
    previous_close = float(sample["previous_close"])
    actual_close = float(sample["actual_close"])
    predicted_close = previous_close * (1.0 + predicted_return)
    error = predicted_close - actual_close
    actual_return = float(sample["actual_return"])
    predicted_direction = _direction(predicted_return)
    actual_direction = _direction(actual_return)
    return {
        "model_name": model_name,
        "model_version": MODEL_VERSIONS[model_name],
        "feature_date": sample["feature_date"],
        "target_date": sample["target_date"],
        "previous_close": previous_close,
        "predicted_return": predicted_return,
        "actual_return": actual_return,
        "predicted_close": predicted_close,
        "actual_close": actual_close,
        "error": error,
        "absolute_error": abs(error),
        "predicted_direction": predicted_direction,
        "actual_direction": actual_direction,
        "direction_correct": int(predicted_direction == actual_direction),
        "signal": predicted_direction,
        "training_samples": training_samples,
    }


def _walk_forward_backtest(
    samples: list[dict[str, Any]],
    validation_size: int,
    ridge_alpha: float,
) -> list[dict[str, Any]]:
    if validation_size < 1:
        raise ForecastError("validation window must contain at least one sample")
    validation_start = len(samples) - validation_size
    if validation_start < MIN_TRAINING_SAMPLES:
        raise ForecastError(
            f"forecast requires at least {MIN_TRAINING_SAMPLES} pre-validation training samples after feature construction"
        )
    rows: list[dict[str, Any]] = []
    for index in range(validation_start, len(samples)):
        training = samples[:index]
        target = samples[index]
        cap = _return_cap(training)
        for model_name in MODEL_NAMES:
            model = _fit_model(model_name, training, ridge_alpha)
            predicted_return = _clip(_predict_model(model_name, model, target), -cap, cap)
            rows.append(_prediction_row(model_name, target, predicted_return, len(training)))
    return rows


def _classification_metrics(rows: list[dict[str, Any]]) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    f1_values: list[float] = []
    for label in (-1, 0, 1):
        true_positive = sum(
            row["predicted_direction"] == label and row["actual_direction"] == label for row in rows
        )
        false_positive = sum(
            row["predicted_direction"] == label and row["actual_direction"] != label for row in rows
        )
        false_negative = sum(
            row["predicted_direction"] != label and row["actual_direction"] == label for row in rows
        )
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1_values.append(2.0 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return float(np.mean(f1_values)), float(np.mean([row["direction_correct"] for row in rows]) * 100.0)


def _evaluate_backtest(model_name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ForecastError(f"model {model_name} received no backtest rows")
    errors = np.asarray([float(row["error"]) for row in rows], dtype=float)
    actuals = np.asarray([float(row["actual_close"]) for row in rows], dtype=float)
    return_errors = np.asarray(
        [float(row["predicted_return"]) - float(row["actual_return"]) for row in rows], dtype=float
    )
    absolute_errors = np.abs(errors)
    interval_absolute_error = float(np.quantile(absolute_errors, INTERVAL_LEVEL / 100.0))
    direction_f1, direction_accuracy = _classification_metrics(rows)
    return {
        "model_name": model_name,
        "model_version": MODEL_VERSIONS[model_name],
        "validation_method": "expanding-window one-step-ahead walk-forward",
        "validation_rows": len(rows),
        "mae": float(np.mean(absolute_errors)),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "mape": float(np.mean(np.abs(errors / actuals)) * 100.0),
        "return_mae": float(np.mean(np.abs(return_errors)) * 100.0),
        "return_bias": float(np.mean(return_errors) * 100.0),
        "price_bias": float(np.mean(errors)),
        "direction_accuracy": direction_accuracy,
        "direction_f1": direction_f1 * 100.0,
        "interval_absolute_error": interval_absolute_error,
        "interval_coverage": float(np.mean(absolute_errors <= interval_absolute_error) * 100.0),
        "interval_width": interval_absolute_error * 2.0,
    }


def _robustness_rows(
    samples: list[dict[str, Any]],
    windows: tuple[int, ...],
    ridge_alpha: float,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = {}
    for requested_window in windows:
        validation_size = min(requested_window, len(samples) - MIN_TRAINING_SAMPLES)
        if validation_size < 10:
            continue
        backtest = _walk_forward_backtest(samples, validation_size, ridge_alpha)
        grouped[validation_size] = []
        for model_name in MODEL_NAMES:
            model_rows = [row for row in backtest if row["model_name"] == model_name]
            evaluation = _evaluate_backtest(model_name, model_rows)
            evaluation.update({"validation_window": validation_size, "regime": "all", "robustness_status": "measured"})
            rows.append(evaluation)
            grouped[validation_size].append(evaluation)
        volatility_values = np.asarray(
            [float(samples[-validation_size + index]["features"]["volatility_20"]) for index in range(validation_size)],
            dtype=float,
        )
        volatility_median = float(np.median(volatility_values)) if len(volatility_values) else 0.0
        for regime, predicate in (
            ("low_volatility", lambda value: value <= volatility_median),
            ("high_volatility", lambda value: value > volatility_median),
        ):
            selected_dates = {
                samples[-validation_size + index]["target_date"]
                for index, value in enumerate(volatility_values)
                if predicate(value)
            }
            if len(selected_dates) < 5:
                continue
            for model_name in MODEL_NAMES:
                regime_rows = [
                    row for row in backtest if row["model_name"] == model_name and row["target_date"] in selected_dates
                ]
                if len(regime_rows) < 5:
                    continue
                evaluation = _evaluate_backtest(model_name, regime_rows)
                evaluation.update({"validation_window": validation_size, "regime": regime, "robustness_status": "measured"})
                rows.append(evaluation)
    if not grouped:
        raise ForecastError("forecast robustness validation produced no usable windows")
    return rows, grouped


def _add_selection_fields(
    evaluations: list[dict[str, Any]],
    robustness_by_window: dict[int, list[dict[str, Any]]],
    *,
    price_weight: float,
    direction_weight: float,
    minimum_price_improvement_pct: float,
    minimum_direction_improvement_points: float,
    max_price_deterioration_pct: float,
    max_direction_deterioration_points: float,
) -> str:
    baseline = next(row for row in evaluations if row["model_name"] == BASELINE_MODEL)
    weight_total = price_weight + direction_weight
    if weight_total <= 0:
        raise ForecastError("price and direction selection weights must have a positive sum")
    price_weight /= weight_total
    direction_weight /= weight_total
    baseline_rmse = max(float(baseline["rmse"]), 1e-12)
    robustness_status: dict[str, tuple[bool, int, int]] = {}
    for model_name in MODEL_NAMES:
        windows_passed = 0
        windows_evaluated = 0
        for window_rows in robustness_by_window.values():
            baseline_window = next(row for row in window_rows if row["model_name"] == BASELINE_MODEL)
            model_window = next(row for row in window_rows if row["model_name"] == model_name)
            windows_evaluated += 1
            price_ok = model_window["rmse"] <= baseline_window["rmse"] * (1.0 + max_price_deterioration_pct / 100.0)
            direction_ok = model_window["direction_accuracy"] >= baseline_window["direction_accuracy"] - max_direction_deterioration_points
            if price_ok and direction_ok:
                windows_passed += 1
        robustness_status[model_name] = (windows_evaluated > 0 and windows_passed == windows_evaluated, windows_passed, windows_evaluated)
    for row in evaluations:
        price_improvement = (baseline_rmse - float(row["rmse"])) / baseline_rmse * 100.0
        direction_improvement = float(row["direction_accuracy"]) - float(baseline["direction_accuracy"])
        price_score = _clip(1.0 + price_improvement / 100.0, 0.0, 2.0)
        direction_score = float(row["direction_accuracy"]) / 100.0
        row["price_rmse_improvement_pct"] = price_improvement
        row["direction_improvement_points"] = direction_improvement
        row["selection_score"] = price_weight * price_score + direction_weight * direction_score
        stable, passed, evaluated = robustness_status[row["model_name"]]
        row["robustness_passed"] = stable
        row["robustness_windows_passed"] = passed
        row["robustness_windows_evaluated"] = evaluated
        row["eligible_for_selection"] = False
        row["selection_reason"] = ""
        if row["model_name"] == BASELINE_MODEL:
            row["eligible_for_selection"] = True
            row["selection_reason"] = "always eligible as the minimum performance hurdle"
            continue
        price_not_materially_worse = price_improvement >= -max_price_deterioration_pct
        direction_not_materially_worse = direction_improvement >= -max_direction_deterioration_points
        row["eligible_for_selection"] = bool(stable and price_not_materially_worse and direction_not_materially_worse)
        if row["model_name"] == RIDGE_MODEL:
            meets_improvement = (
                price_improvement >= minimum_price_improvement_pct
                or direction_improvement >= minimum_direction_improvement_points
            )
            row["eligible_for_selection"] = bool(row["eligible_for_selection"] and meets_improvement)
            if not stable:
                row["selection_reason"] = "rejected: failed at least one robustness window"
            elif not meets_improvement:
                row["selection_reason"] = "rejected: did not meet the configured improvement threshold"
            elif not price_not_materially_worse or not direction_not_materially_worse:
                row["selection_reason"] = "rejected: materially worse than the baseline on one objective"
            else:
                row["selection_reason"] = "eligible: passed robustness and improvement thresholds"
        elif row["eligible_for_selection"]:
            row["selection_reason"] = "eligible: simple baseline passed robustness and did not materially trail"
        else:
            row["selection_reason"] = "rejected: did not pass robustness or relative-performance checks"
    eligible = [row for row in evaluations if row["eligible_for_selection"]]
    selected = max(eligible, key=lambda row: (row["selection_score"], -row["rmse"], -row["mae"]))
    for row in evaluations:
        row["selected"] = row["model_name"] == selected["model_name"]
        if row["selected"]:
            row["selection_reason"] = f"selected: highest eligible balanced score ({row['selection_score']:.4f})"
    return selected["model_name"]


def _cost_evaluation(
    model_name: str,
    rows: list[dict[str, Any]],
    *,
    scenario: str,
    transaction_cost_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    previous_signal = 0
    gross_returns: list[float] = []
    net_returns: list[float] = []
    turnovers: list[float] = []
    costs: list[float] = []
    active_outcomes: list[float] = []
    for row in rows:
        signal = int(row["signal"])
        actual_return = float(row["actual_return"])
        turnover = abs(signal - previous_signal)
        cost_return = turnover * (transaction_cost_bps + slippage_bps) / 10000.0
        gross_return = signal * actual_return
        net_return = gross_return - cost_return
        equity *= 1.0 + net_return
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1.0 - equity / peak)
        if signal:
            active_outcomes.append(net_return)
        gross_returns.append(gross_return)
        net_returns.append(net_return)
        turnovers.append(float(turnover))
        costs.append(cost_return)
        previous_signal = signal
    gross_equity = float(np.prod(1.0 + np.asarray(gross_returns))) if gross_returns else 1.0
    return {
        "model_name": model_name,
        "model_version": MODEL_VERSIONS[model_name],
        "scenario": scenario,
        "validation_rows": len(rows),
        "transaction_cost_bps": transaction_cost_bps,
        "slippage_bps": slippage_bps,
        "cumulative_gross_return_pct": (gross_equity - 1.0) * 100.0,
        "cumulative_net_return_pct": (equity - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown * 100.0,
        "win_rate_pct": float(np.mean(np.asarray(active_outcomes) > 0) * 100.0) if active_outcomes else 0.0,
        "turnover": float(np.sum(turnovers)),
        "trade_count": int(sum(turnover > 0 for turnover in turnovers)),
        "total_cost_pct": float(np.sum(costs) * 100.0),
    }


def _next_business_dates(last_date: str, horizon: int) -> list[str]:
    current = datetime.strptime(last_date, DATE_FORMAT)
    dates: list[str] = []
    while len(dates) < horizon:
        current += timedelta(days=1)
        if current.weekday() < 5:
            dates.append(current.strftime(DATE_FORMAT))
    return dates


def _future_rows(
    model_name: str,
    model: dict[str, Any] | None,
    prices: list[dict[str, Any]],
    financials: list[dict[str, Any]],
    news: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    horizon: int,
    residual_interval: float,
    return_cap: float,
    market: str,
    currency: str,
) -> list[dict[str, Any]]:
    history = [dict(row) for row in sorted(prices, key=lambda row: str(row["date"]))]
    data_cutoff = str(history[-1]["date"])
    future_dates = _next_business_dates(data_cutoff, horizon)
    future: list[dict[str, Any]] = []
    for step, forecast_date in enumerate(future_dates, start=1):
        previous_close = float(history[-1]["close"])
        features = build_feature_vector(history, financials, news, announcements, information_cutoff=data_cutoff)
        predicted_return = _clip(_predict_model(model_name, model, {"features": features}), -return_cap, return_cap)
        predicted_close = previous_close * (1.0 + predicted_return)
        interval_width = residual_interval * math.sqrt(step)
        future.append(
            {
                "model_name": model_name,
                "model_version": MODEL_VERSIONS[model_name],
                "forecast_step": step,
                "estimated_trading_date": forecast_date,
                "data_cutoff": data_cutoff,
                "previous_close": previous_close,
                "predicted_return": predicted_return,
                "predicted_direction": _direction(predicted_return),
                "predicted_close": predicted_close,
                "lower_bound": max(0.0, predicted_close - interval_width),
                "upper_bound": predicted_close + interval_width,
                "interval_level": INTERVAL_LEVEL,
                "interval_method": "walk-forward absolute-error quantile scaled by square root of horizon",
                "input_status": "actual stage-two data" if step == 1 else "recursive price path; external inputs fixed at data cutoff",
                "market": market,
                "currency": currency,
            }
        )
        recent_volumes = [float(row["volume"]) for row in history[-20:]]
        estimated_volume = float(np.mean(recent_volumes)) if recent_volumes else 0.0
        history.append(
            {
                "date": forecast_date,
                "open": predicted_close,
                "high": predicted_close,
                "low": predicted_close,
                "close": predicted_close,
                "volume": estimated_volume,
                "source": "recursive model input",
                "market": market,
                "currency": currency,
            }
        )
    return future


def _normalise_windows(value: Iterable[int] | None) -> tuple[int, ...]:
    windows = tuple(sorted({int(item) for item in (value or DEFAULT_ROBUSTNESS_WINDOWS)}))
    if not windows or any(window < 10 for window in windows):
        raise ForecastError("robustness validation windows must be at least 10")
    return windows


def run_forecast_analysis(
    prices: list[dict[str, Any]],
    financials: list[dict[str, Any]],
    news: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    *,
    horizon: int = 5,
    minimum_history: int = 120,
    validation_points: int = 40,
    ridge_alpha: float = 1.0,
    robustness_windows: Iterable[int] | None = DEFAULT_ROBUSTNESS_WINDOWS,
    price_weight: float = DEFAULT_PRICE_WEIGHT,
    direction_weight: float = DEFAULT_DIRECTION_WEIGHT,
    minimum_price_improvement_pct: float = DEFAULT_MIN_PRICE_IMPROVEMENT_PCT,
    minimum_direction_improvement_points: float = DEFAULT_MIN_DIRECTION_IMPROVEMENT_POINTS,
    max_price_deterioration_pct: float = DEFAULT_MAX_PRICE_DETERIORATION_PCT,
    max_direction_deterioration_points: float = DEFAULT_MAX_DIRECTION_DETERIORATION_POINTS,
    transaction_cost_bps: float = DEFAULT_TRANSACTION_COST_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    market: str,
    currency: str,
) -> dict[str, Any]:
    if horizon < 1 or horizon > 20:
        raise ForecastError("forecast horizon must be between 1 and 20 trading steps")
    if minimum_history < 80:
        raise ForecastError("forecast minimum history must be at least 80 price rows")
    if validation_points < 10:
        raise ForecastError("forecast validation points must be at least 10")
    if ridge_alpha <= 0:
        raise ForecastError("ridge alpha must be greater than zero")
    if price_weight < 0 or direction_weight < 0 or price_weight + direction_weight <= 0:
        raise ForecastError("price and direction selection weights must be non-negative with a positive sum")
    if any(value < 0 for value in (minimum_price_improvement_pct, minimum_direction_improvement_points, max_price_deterioration_pct, max_direction_deterioration_points)):
        raise ForecastError("forecast improvement and deterioration thresholds cannot be negative")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise ForecastError("transaction cost and slippage cannot be negative")
    ordered_prices = sorted(prices, key=lambda row: str(row["date"]))
    if len(ordered_prices) < minimum_history:
        raise ForecastError(
            f"forecast requires at least {minimum_history} clean online price rows; received {len(ordered_prices)}. Use an earlier start date."
        )
    datasets = {"price": ordered_prices, "financial": financials, "news": news, "announcement": announcements}
    for dataset_name, rows in datasets.items():
        if any(str(row.get("source", "")).startswith("fixture") for row in rows):
            raise ForecastError(f"forecast requires real stage-two online data and cannot use fixture {dataset_name} rows")
    samples = build_training_samples(ordered_prices, financials, news, announcements)
    normalised_windows = _normalise_windows(robustness_windows)
    validation_size = min(validation_points, len(samples) - MIN_TRAINING_SAMPLES)
    if validation_size < 10:
        raise ForecastError("forecast validation window must leave at least 40 pre-validation training samples")
    backtest_rows = _walk_forward_backtest(samples, validation_size, ridge_alpha)
    grouped_backtest = {model_name: [row for row in backtest_rows if row["model_name"] == model_name] for model_name in MODEL_NAMES}
    evaluations = [_evaluate_backtest(model_name, grouped_backtest[model_name]) for model_name in MODEL_NAMES]
    robust_rows, robustness_by_window = _robustness_rows(samples, normalised_windows, ridge_alpha)
    selected_model = _add_selection_fields(
        evaluations,
        robustness_by_window,
        price_weight=price_weight,
        direction_weight=direction_weight,
        minimum_price_improvement_pct=minimum_price_improvement_pct,
        minimum_direction_improvement_points=minimum_direction_improvement_points,
        max_price_deterioration_pct=max_price_deterioration_pct,
        max_direction_deterioration_points=max_direction_deterioration_points,
    )
    validation_start_index = len(samples) - validation_size
    for evaluation in evaluations:
        evaluation.update(
            {
                "training_start": samples[0]["feature_date"],
                "training_end": samples[validation_start_index - 1]["target_date"],
                "validation_start": samples[validation_start_index]["target_date"],
                "validation_end": samples[-1]["target_date"],
                "ridge_alpha": ridge_alpha if evaluation["model_name"] == RIDGE_MODEL else "not applicable",
                "price_weight": price_weight,
                "direction_weight": direction_weight,
                "minimum_price_improvement_pct": minimum_price_improvement_pct,
                "minimum_direction_improvement_points": minimum_direction_improvement_points,
                "max_price_deterioration_pct": max_price_deterioration_pct,
                "max_direction_deterioration_points": max_direction_deterioration_points,
                "leakage_controls": "price features end at feature_date; financials require filed_date <= feature_date; news and notices require publication date <= feature_date; scaling and model fitting use earlier samples only",
            }
        )
    final_models = {model_name: _fit_model(model_name, samples, ridge_alpha) for model_name in MODEL_NAMES}
    cap = _return_cap(samples)
    evaluation_by_model = {row["model_name"]: row for row in evaluations}
    forecasts: list[dict[str, Any]] = []
    for model_name in MODEL_NAMES:
        forecasts.extend(
            _future_rows(
                model_name,
                final_models[model_name],
                ordered_prices,
                financials,
                news,
                announcements,
                horizon,
                evaluation_by_model[model_name]["interval_absolute_error"],
                cap,
                market,
                currency,
            )
        )
    for row in forecasts:
        row["selected"] = row["model_name"] == selected_model
    latest_features = build_feature_vector(ordered_prices, financials, news, announcements)
    final_ridge = final_models[RIDGE_MODEL]
    if not final_ridge:
        raise ForecastError("final ridge model is missing")
    coefficient_pairs = list(zip(FEATURE_NAMES, final_ridge["coefficients"], strict=True))
    coefficient_pairs.sort(key=lambda item: abs(float(item[1])), reverse=True)
    contributions = []
    for rank, (feature, coefficient) in enumerate(coefficient_pairs, start=1):
        coefficient_value = float(coefficient) * 100.0
        contributions.append(
            {
                "model_name": RIDGE_MODEL,
                "model_version": RIDGE_VERSION,
                "rank": rank,
                "feature": feature,
                "coefficient_pct_return_per_std": coefficient_value,
                "direction": "positive" if coefficient_value > 0 else "negative" if coefficient_value < 0 else "neutral",
                "latest_feature_value": latest_features[feature],
                "description": FEATURE_DESCRIPTIONS[feature],
            }
        )
    cost_scenarios = [("no_cost", 0.0, 0.0), ("slippage_only", 0.0, slippage_bps), ("base_cost", transaction_cost_bps, slippage_bps)]
    cost_evaluations = [
        _cost_evaluation(
            model_name,
            grouped_backtest[model_name],
            scenario=scenario,
            transaction_cost_bps=scenario_transaction_cost,
            slippage_bps=scenario_slippage,
        )
        for scenario, scenario_transaction_cost, scenario_slippage in cost_scenarios
        for model_name in MODEL_NAMES
    ]
    selected_evaluation = evaluation_by_model[selected_model]
    return {
        "status": "pass",
        "data_cutoff": ordered_prices[-1]["date"],
        "training_price_rows": len(ordered_prices),
        "training_samples": len(samples),
        "validation_samples": validation_size,
        "forecast_horizon": horizon,
        "selected_model": selected_model,
        "selected_model_version": selected_evaluation["model_version"],
        "model_names": MODEL_NAMES,
        "robustness_windows": normalised_windows,
        "selection_config": {
            "price_weight": price_weight,
            "direction_weight": direction_weight,
            "minimum_price_improvement_pct": minimum_price_improvement_pct,
            "minimum_direction_improvement_points": minimum_direction_improvement_points,
            "max_price_deterioration_pct": max_price_deterioration_pct,
            "max_direction_deterioration_points": max_direction_deterioration_points,
        },
        "cost_config": {
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
        },
        "forecasts": forecasts,
        "evaluations": evaluations,
        "backtest": backtest_rows,
        "robustness": robust_rows,
        "cost_evaluations": cost_evaluations,
        "feature_contributions": contributions,
        "data_profile": {
            "price_rows": len(ordered_prices),
            "financial_rows": len(financials),
            "news_rows": len(news),
            "announcement_rows": len(announcements),
            "price_start": ordered_prices[0]["date"],
            "price_end": ordered_prices[-1]["date"],
        },
        "limitations": "Forecasts are research estimates; future news and filings are unknown; recursive horizons compound error; estimated trading dates exclude weekends but do not apply exchange holiday calendars; cost scenarios are simplified and do not guarantee executable returns.",
    }
