"""Leakage-aware price forecasting from cleaned stage-two market data."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

import numpy as np


DATE_FORMAT = "%Y-%m-%d"
FEATURE_WINDOW = 21
MIN_TRAINING_SAMPLES = 40
BASELINE_MODEL = "last_close_baseline"
BASELINE_VERSION = "persistence-v1"
RIDGE_MODEL = "multisignal_ridge"
RIDGE_VERSION = "ridge-v1"
INTERVAL_LEVEL = 90

FEATURE_DESCRIPTIONS = {
    "return_1d": "Previous one-trading-day close return.",
    "return_5d": "Previous five-trading-day close return.",
    "ma_gap_5": "Close divided by the trailing five-day moving average minus one.",
    "ma_gap_20": "Close divided by the trailing twenty-day moving average minus one.",
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
    feature_values = {
        "return_1d": float(returns[-1]),
        "return_5d": float(closes[-1] / closes[-6] - 1.0),
        "ma_gap_5": float(closes[-1] / np.mean(closes[-5:]) - 1.0),
        "ma_gap_20": float(closes[-1] / np.mean(closes[-20:]) - 1.0),
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
    return float(model["intercept"] + standardized @ model["coefficients"])


def _return_cap(samples: list[dict[str, Any]]) -> float:
    returns = np.asarray([abs(float(sample["actual_return"])) for sample in samples], dtype=float)
    if not len(returns):
        return 0.05
    return _clip(float(np.quantile(returns, 0.99)) * 1.5, 0.02, 0.20)


def _prediction_row(
    model_name: str,
    model_version: str,
    sample: dict[str, Any],
    predicted_return: float,
    training_samples: int,
) -> dict[str, Any]:
    previous_close = float(sample["previous_close"])
    actual_close = float(sample["actual_close"])
    predicted_close = previous_close * (1.0 + predicted_return)
    error = predicted_close - actual_close
    actual_return = float(sample["actual_return"])
    return {
        "model_name": model_name,
        "model_version": model_version,
        "feature_date": sample["feature_date"],
        "target_date": sample["target_date"],
        "previous_close": previous_close,
        "predicted_return": predicted_return,
        "actual_return": actual_return,
        "predicted_close": predicted_close,
        "actual_close": actual_close,
        "error": error,
        "absolute_error": abs(error),
        "direction_correct": int(_direction(predicted_return) == _direction(actual_return)),
        "training_samples": training_samples,
    }


def _evaluate_backtest(model_name: str, model_version: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = np.asarray([float(row["error"]) for row in rows], dtype=float)
    actuals = np.asarray([float(row["actual_close"]) for row in rows], dtype=float)
    return_errors = np.asarray(
        [float(row["predicted_return"]) - float(row["actual_return"]) for row in rows], dtype=float
    )
    absolute_errors = np.abs(errors)
    return {
        "model_name": model_name,
        "model_version": model_version,
        "validation_method": "expanding-window one-step-ahead walk-forward",
        "validation_rows": len(rows),
        "mae": float(np.mean(absolute_errors)),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "mape": float(np.mean(np.abs(errors / actuals)) * 100.0),
        "return_mae": float(np.mean(np.abs(return_errors)) * 100.0),
        "direction_accuracy": float(np.mean([row["direction_correct"] for row in rows]) * 100.0),
        "interval_absolute_error": float(np.quantile(absolute_errors, INTERVAL_LEVEL / 100.0)),
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
    model_version: str,
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
        if model is None:
            predicted_return = 0.0
        else:
            features = build_feature_vector(
                history,
                financials,
                news,
                announcements,
                information_cutoff=data_cutoff,
            )
            predicted_return = _clip(_predict_ridge(model, features), -return_cap, return_cap)
        predicted_close = previous_close * (1.0 + predicted_return)
        interval_width = residual_interval * math.sqrt(step)
        future.append(
            {
                "model_name": model_name,
                "model_version": model_version,
                "forecast_step": step,
                "estimated_trading_date": forecast_date,
                "data_cutoff": data_cutoff,
                "previous_close": previous_close,
                "predicted_return": predicted_return,
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
    ordered_prices = sorted(prices, key=lambda row: str(row["date"]))
    if len(ordered_prices) < minimum_history:
        raise ForecastError(
            f"forecast requires at least {minimum_history} clean online price rows; received {len(ordered_prices)}. Use an earlier start date."
        )
    datasets = {
        "price": ordered_prices,
        "financial": financials,
        "news": news,
        "announcement": announcements,
    }
    for dataset_name, rows in datasets.items():
        if any(str(row.get("source", "")).startswith("fixture") for row in rows):
            raise ForecastError(
                f"forecast requires real stage-two online data and cannot use fixture {dataset_name} rows"
            )
    samples = build_training_samples(ordered_prices, financials, news, announcements)
    validation_size = min(validation_points, len(samples) - MIN_TRAINING_SAMPLES)
    validation_start = len(samples) - validation_size
    if validation_start < MIN_TRAINING_SAMPLES:
        raise ForecastError(
            f"forecast requires at least {MIN_TRAINING_SAMPLES} pre-validation training samples after feature construction"
        )
    backtest_rows: list[dict[str, Any]] = []
    for index in range(validation_start, len(samples)):
        training = samples[:index]
        target = samples[index]
        cap = _return_cap(training)
        backtest_rows.append(
            _prediction_row(BASELINE_MODEL, BASELINE_VERSION, target, 0.0, len(training))
        )
        ridge = _fit_ridge(training, ridge_alpha)
        ridge_return = _clip(_predict_ridge(ridge, target["features"]), -cap, cap)
        backtest_rows.append(
            _prediction_row(RIDGE_MODEL, RIDGE_VERSION, target, ridge_return, len(training))
        )
    grouped_backtest = {
        model_name: [row for row in backtest_rows if row["model_name"] == model_name]
        for model_name in (BASELINE_MODEL, RIDGE_MODEL)
    }
    evaluations = [
        _evaluate_backtest(BASELINE_MODEL, BASELINE_VERSION, grouped_backtest[BASELINE_MODEL]),
        _evaluate_backtest(RIDGE_MODEL, RIDGE_VERSION, grouped_backtest[RIDGE_MODEL]),
    ]
    selected_model = min(evaluations, key=lambda row: (row["rmse"], row["mae"]))["model_name"]
    for evaluation in evaluations:
        evaluation["selected"] = evaluation["model_name"] == selected_model
        evaluation["training_start"] = samples[0]["feature_date"]
        evaluation["training_end"] = samples[validation_start - 1]["target_date"]
        evaluation["validation_start"] = samples[validation_start]["target_date"]
        evaluation["validation_end"] = samples[-1]["target_date"]
        evaluation["ridge_alpha"] = ridge_alpha if evaluation["model_name"] == RIDGE_MODEL else "not applicable"
        evaluation["leakage_controls"] = (
            "price features end at feature_date; financials require filed_date <= feature_date; "
            "news and notices require publication date <= feature_date; scaling and model fitting use earlier samples only"
        )
    final_ridge = _fit_ridge(samples, ridge_alpha)
    cap = _return_cap(samples)
    evaluation_by_model = {row["model_name"]: row for row in evaluations}
    forecasts = _future_rows(
        BASELINE_MODEL,
        BASELINE_VERSION,
        None,
        ordered_prices,
        financials,
        news,
        announcements,
        horizon,
        evaluation_by_model[BASELINE_MODEL]["interval_absolute_error"],
        cap,
        market,
        currency,
    )
    forecasts.extend(
        _future_rows(
            RIDGE_MODEL,
            RIDGE_VERSION,
            final_ridge,
            ordered_prices,
            financials,
            news,
            announcements,
            horizon,
            evaluation_by_model[RIDGE_MODEL]["interval_absolute_error"],
            cap,
            market,
            currency,
        )
    )
    for row in forecasts:
        row["selected"] = row["model_name"] == selected_model
    latest_features = build_feature_vector(ordered_prices, financials, news, announcements)
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
    return {
        "status": "pass",
        "data_cutoff": ordered_prices[-1]["date"],
        "training_price_rows": len(ordered_prices),
        "training_samples": len(samples),
        "validation_samples": validation_size,
        "forecast_horizon": horizon,
        "selected_model": selected_model,
        "selected_model_version": evaluation_by_model[selected_model]["model_version"],
        "forecasts": forecasts,
        "evaluations": evaluations,
        "backtest": backtest_rows,
        "feature_contributions": contributions,
        "data_profile": {
            "price_rows": len(ordered_prices),
            "financial_rows": len(financials),
            "news_rows": len(news),
            "announcement_rows": len(announcements),
            "price_start": ordered_prices[0]["date"],
            "price_end": ordered_prices[-1]["date"],
        },
        "limitations": (
            "Forecasts are research estimates, future market news and filings are unknown, recursive horizons compound error, "
            "and estimated trading dates exclude weekends but do not apply exchange holiday calendars."
        ),
    }
