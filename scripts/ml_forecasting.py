"""Panel machine-learning forecasts for benchmark-relative A-share returns."""

from __future__ import annotations

import json
import math
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import NormalDist
from typing import Any, Iterable

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import (
    brier_score_loss,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from forecasting import FEATURE_DESCRIPTIONS, FEATURE_WINDOW, build_feature_vector
from versioning import (
    ELASTIC_NET_MODEL_VERSION,
    EXCESS_MEAN_MODEL_VERSION,
    ML_FEATURE_VERSION,
    ML_FORECAST_ENGINE_VERSION,
    PANEL_RIDGE_MODEL_VERSION,
    RANDOM_FOREST_MODEL_VERSION,
    HIST_GRADIENT_BOOSTING_MODEL_VERSION,
    ZERO_EXCESS_MODEL_VERSION,
)


MODEL_ORDER = (
    "zero_excess",
    "excess_historical_mean",
    "panel_ridge",
    "elastic_net",
    "random_forest",
    "hist_gradient_boosting",
)
MODEL_VERSIONS = {
    "zero_excess": ZERO_EXCESS_MODEL_VERSION,
    "excess_historical_mean": EXCESS_MEAN_MODEL_VERSION,
    "panel_ridge": PANEL_RIDGE_MODEL_VERSION,
    "elastic_net": ELASTIC_NET_MODEL_VERSION,
    "random_forest": RANDOM_FOREST_MODEL_VERSION,
    "hist_gradient_boosting": HIST_GRADIENT_BOOSTING_MODEL_VERSION,
}
SIMPLE_MODELS = {"zero_excess", "excess_historical_mean", "panel_ridge"}
ML_MODELS = set(MODEL_ORDER) - SIMPLE_MODELS
RANDOM_SEED = 1729
INTERVAL_LEVEL = 90
BASE_FEATURE_NAMES = tuple(FEATURE_DESCRIPTIONS)
RANK_FEATURES = (
    "stock_return_20d",
    "volatility_20",
    "net_profit_margin",
    "news_tone_7d",
)


class MLForecastError(RuntimeError):
    """Expected panel construction, validation, or modeling failure."""


class StableStandardScaler(BaseEstimator, TransformerMixin):
    """Standardize features without amplifying numerically constant columns."""

    def fit(self, x: np.ndarray, y: np.ndarray | None = None) -> "StableStandardScaler":
        values = np.asarray(x, dtype=float)
        self.mean_ = np.mean(values, axis=0)
        scales = np.std(values, axis=0, ddof=0)
        self.scale_ = np.where(scales < 1e-8, 1.0, scales)
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        return (np.asarray(x, dtype=float) - self.mean_) / self.scale_


def _ordered_prices(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: str(row.get("date", "")))
    if len(ordered) < 70:
        raise MLForecastError("each panel subject and benchmark requires at least 70 aligned price rows")
    if any(float(row.get("close", 0.0)) <= 0 for row in ordered):
        raise MLForecastError("panel price rows contain a non-positive close")
    return ordered


def _return(values: list[float], days: int) -> float:
    return float(values[-1] / values[-1 - days] - 1.0)


def _rolling_beta(stock_returns: np.ndarray, benchmark_returns: np.ndarray) -> tuple[float, float]:
    if len(stock_returns) < 2 or len(stock_returns) != len(benchmark_returns):
        return 0.0, 0.0
    benchmark_variance = float(np.var(benchmark_returns, ddof=0))
    covariance = float(np.mean((stock_returns - stock_returns.mean()) * (benchmark_returns - benchmark_returns.mean())))
    beta = covariance / benchmark_variance if benchmark_variance > 1e-15 else 0.0
    stock_std = float(np.std(stock_returns, ddof=0))
    benchmark_std = float(np.std(benchmark_returns, ddof=0))
    correlation = covariance / (stock_std * benchmark_std) if stock_std > 1e-15 and benchmark_std > 1e-15 else 0.0
    return beta, correlation


def _market_features(
    stock_history: list[dict[str, Any]], benchmark_history: list[dict[str, Any]]
) -> dict[str, float]:
    stock_closes = [float(row["close"]) for row in stock_history]
    benchmark_closes = [float(row["close"]) for row in benchmark_history]
    stock_daily = np.asarray(stock_closes[-61:], dtype=float)
    benchmark_daily = np.asarray(benchmark_closes[-61:], dtype=float)
    stock_returns = stock_daily[1:] / stock_daily[:-1] - 1.0
    benchmark_returns = benchmark_daily[1:] / benchmark_daily[:-1] - 1.0
    beta, correlation = _rolling_beta(stock_returns, benchmark_returns)
    recent = stock_history[-20:]
    high = max(float(row["high"]) for row in recent)
    low = min(float(row["low"]) for row in recent)
    peak = max(stock_closes[-60:])
    return {
        "stock_return_20d": _return(stock_closes, 20),
        "stock_return_60d": _return(stock_closes, 60),
        "benchmark_return_1d": _return(benchmark_closes, 1),
        "benchmark_return_5d": _return(benchmark_closes, 5),
        "benchmark_return_20d": _return(benchmark_closes, 20),
        "relative_momentum_5d": _return(stock_closes, 5) - _return(benchmark_closes, 5),
        "relative_momentum_20d": _return(stock_closes, 20) - _return(benchmark_closes, 20),
        "rolling_beta_60d": beta,
        "market_correlation_60d": correlation,
        "relative_volatility_20d": float(np.std(stock_returns[-20:]) - np.std(benchmark_returns[-20:])),
        "amplitude_20d": high / low - 1.0 if low else 0.0,
        "drawdown_60d": stock_closes[-1] / peak - 1.0 if peak else 0.0,
    }


def _categorical_features(subject: dict[str, Any]) -> dict[str, float]:
    industry = str(subject.get("industry", "未分类")).strip() or "未分类"
    size_bucket = str(subject.get("size_bucket", "未分类")).strip() or "未分类"
    return {f"industry::{industry}": 1.0, f"size::{size_bucket}": 1.0}


def _aligned_histories(
    stock_prices: list[dict[str, Any]], benchmark_prices: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    benchmark_by_date = {str(row["date"]): row for row in benchmark_prices}
    stock_aligned = [row for row in stock_prices if str(row["date"]) in benchmark_by_date]
    benchmark_aligned = [benchmark_by_date[str(row["date"])] for row in stock_aligned]
    if len(stock_aligned) < 70:
        raise MLForecastError("subject and benchmark have fewer than 70 aligned trading observations")
    return stock_aligned, benchmark_aligned


def _add_cross_sectional_ranks(rows: list[dict[str, Any]]) -> None:
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(int(row["horizon"]), str(row["feature_date"]))].append(row)
    for group in groups.values():
        for feature in RANK_FEATURES:
            ordered = sorted(group, key=lambda row: (float(row["features"].get(feature, 0.0)), row["symbol"]))
            denominator = max(len(ordered) - 1, 1)
            for index, row in enumerate(ordered):
                row["features"][f"rank::{feature}"] = index / denominator if len(ordered) > 1 else 0.5


def build_excess_return_panel(
    subjects: list[dict[str, Any]],
    benchmark_prices: list[dict[str, Any]],
    *,
    benchmark_symbol: str,
    benchmark_type: str,
    benchmark_source: str,
    horizons: Iterable[int] = (1, 5),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Build labeled and latest prediction rows using point-in-time features."""
    normalized_horizons = tuple(sorted({int(value) for value in horizons}))
    if not normalized_horizons or any(value < 1 or value > 20 for value in normalized_horizons):
        raise MLForecastError("panel horizons must contain trading-step values from 1 to 20")
    if len(subjects) < 2:
        raise MLForecastError("machine-learning panel requires at least two explicitly supplied stocks")
    benchmark = _ordered_prices(benchmark_prices)
    benchmark_dates = {str(row["date"]) for row in benchmark}
    prepared_subjects: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    latest_common_date = "9999-12-31"
    for subject in subjects:
        stock = _ordered_prices(subject.get("prices", []))
        aligned_dates = [str(row["date"]) for row in stock if str(row["date"]) in benchmark_dates]
        if len(aligned_dates) < 70:
            raise MLForecastError("subject and benchmark have fewer than 70 aligned trading observations")
        latest_common_date = min(latest_common_date, aligned_dates[-1])
        prepared_subjects.append((subject, stock))
    labeled: list[dict[str, Any]] = []
    latest: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for subject, prepared_stock in prepared_subjects:
        symbol = str(subject.get("symbol", "")).strip()
        if not symbol or symbol in seen_symbols:
            raise MLForecastError("panel subjects require unique normalized symbols")
        seen_symbols.add(symbol)
        stock, aligned_benchmark = _aligned_histories(prepared_stock, benchmark)
        common_fields = {
            "symbol": symbol,
            "label": str(subject.get("label", symbol)),
            "industry": str(subject.get("industry", "未分类")),
            "size_bucket": str(subject.get("size_bucket", "未分类")),
            "membership_start": str(subject.get("membership_start", stock[0]["date"])),
            "membership_end": str(subject.get("membership_end", "")),
            "membership_source": str(subject.get("membership_source", "user_supplied_fixed_universe")),
            "benchmark_symbol": benchmark_symbol,
            "benchmark_type": benchmark_type,
            "benchmark_source": benchmark_source,
            "adjustment": str(stock[-1].get("adjustment", "")),
        }
        for horizon in normalized_horizons:
            for feature_index in range(60, len(stock) - horizon):
                stock_history = stock[: feature_index + 1]
                benchmark_history = aligned_benchmark[: feature_index + 1]
                features = build_feature_vector(
                    stock_history,
                    subject.get("financials", []),
                    subject.get("news", []),
                    subject.get("announcements", []),
                )
                features.update(_market_features(stock_history, benchmark_history))
                features.update(_categorical_features(subject))
                stock_return = float(stock[feature_index + horizon]["close"]) / float(stock[feature_index]["close"]) - 1.0
                benchmark_return = (
                    float(aligned_benchmark[feature_index + horizon]["close"])
                    / float(aligned_benchmark[feature_index]["close"])
                    - 1.0
                )
                labeled.append(
                    {
                        **common_fields,
                        "horizon": horizon,
                        "feature_date": str(stock[feature_index]["date"]),
                        "target_date": str(stock[feature_index + horizon]["date"]),
                        "stock_return": stock_return,
                        "benchmark_return": benchmark_return,
                        "excess_return": stock_return - benchmark_return,
                        "outperformed": int(stock_return > benchmark_return),
                        "features": features,
                    }
                )
            latest_index = max(
                index for index, row in enumerate(stock) if str(row["date"]) <= latest_common_date
            )
            latest_history = stock[: latest_index + 1]
            benchmark_history = aligned_benchmark[: latest_index + 1]
            latest_features = build_feature_vector(
                latest_history,
                subject.get("financials", []),
                subject.get("news", []),
                subject.get("announcements", []),
            )
            latest_features.update(_market_features(latest_history, benchmark_history))
            latest_features.update(_categorical_features(subject))
            latest.append(
                {
                    **common_fields,
                    "horizon": horizon,
                    "feature_date": str(latest_history[-1]["date"]),
                    "target_date": "",
                    "features": latest_features,
                }
            )
    _add_cross_sectional_ranks(labeled)
    _add_cross_sectional_ranks(latest)
    feature_names = sorted({name for row in labeled + latest for name in row["features"]})
    for row in labeled + latest:
        for feature_name in feature_names:
            row["features"].setdefault(feature_name, 0.0)
    labeled.sort(key=lambda row: (row["horizon"], row["target_date"], row["symbol"]))
    latest.sort(key=lambda row: (row["horizon"], row["symbol"]))
    return labeled, latest, feature_names


def _model_grids() -> dict[str, list[dict[str, Any]]]:
    return {
        "zero_excess": [{}],
        "excess_historical_mean": [{}],
        "panel_ridge": [{"alpha": value} for value in (0.1, 1.0, 10.0)],
        "elastic_net": [
            {"alpha": alpha, "l1_ratio": l1_ratio}
            for alpha in (0.0001, 0.001, 0.01)
            for l1_ratio in (0.2, 0.5, 0.8)
        ],
        "random_forest": [
            {"n_estimators": 160, "max_depth": depth, "min_samples_leaf": leaf}
            for depth in (4, 8)
            for leaf in (5, 15)
        ],
        "hist_gradient_boosting": [
            {"max_iter": 160, "max_depth": depth, "learning_rate": rate}
            for depth in (2, 3)
            for rate in (0.03, 0.06)
        ],
    }


def _matrix(rows: list[dict[str, Any]], feature_names: list[str]) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([[float(row["features"].get(name, 0.0)) for name in feature_names] for row in rows])
    y = np.asarray([float(row["excess_return"]) for row in rows])
    return x, y


def _estimator(model_name: str, params: dict[str, Any]) -> Any:
    if model_name == "panel_ridge":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StableStandardScaler()),
                ("model", Ridge(alpha=float(params["alpha"]))),
            ]
        )
    if model_name == "elastic_net":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StableStandardScaler()),
                (
                    "model",
                    ElasticNet(
                        alpha=float(params["alpha"]),
                        l1_ratio=float(params["l1_ratio"]),
                        max_iter=10000,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        )
    if model_name == "random_forest":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=int(params["n_estimators"]),
                        max_depth=int(params["max_depth"]),
                        min_samples_leaf=int(params["min_samples_leaf"]),
                        max_features="sqrt",
                        n_jobs=1,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        )
    if model_name == "hist_gradient_boosting":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        max_iter=int(params["max_iter"]),
                        max_depth=int(params["max_depth"]),
                        learning_rate=float(params["learning_rate"]),
                        min_samples_leaf=10,
                        l2_regularization=1.0,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        )
    raise MLForecastError(f"unknown fitted model: {model_name}")


def _prediction_cap(y: np.ndarray) -> float:
    return max(0.02, min(0.20, float(np.quantile(np.abs(y), 0.99)) * 1.5))


def _fit_model(
    model_name: str, params: dict[str, Any], rows: list[dict[str, Any]], feature_names: list[str]
) -> dict[str, Any]:
    if not rows:
        raise MLForecastError(f"{model_name} received no training rows")
    x, y = _matrix(rows, feature_names)
    cap = _prediction_cap(y)
    if model_name == "zero_excess":
        fitted: Any = None
        train_prediction = np.zeros(len(y))
        value = 0.0
    elif model_name == "excess_historical_mean":
        fitted = None
        value = float(np.mean(y))
        train_prediction = np.full(len(y), value)
    else:
        fitted = _estimator(model_name, params)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*encountered in matmul", category=RuntimeWarning)
            fitted.fit(x, y)
            train_prediction = np.asarray(fitted.predict(x), dtype=float)
        value = None
    residuals = y - np.clip(train_prediction, -cap, cap)
    residual_std = max(float(np.std(residuals, ddof=0)), 1e-6)
    return {
        "model_name": model_name,
        "model": fitted,
        "value": value,
        "params": dict(params),
        "cap": cap,
        "residual_std": residual_std,
        "residual_interval": float(np.quantile(np.abs(residuals), INTERVAL_LEVEL / 100.0)),
        "medians": np.nanmedian(x, axis=0),
    }


def _predict(fitted: dict[str, Any], x: np.ndarray) -> np.ndarray:
    if fitted["model_name"] == "zero_excess":
        raw = np.zeros(len(x))
    elif fitted["model_name"] == "excess_historical_mean":
        raw = np.full(len(x), float(fitted["value"]))
    else:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*encountered in matmul", category=RuntimeWarning)
            raw = np.asarray(fitted["model"].predict(x), dtype=float)
    return np.clip(raw, -float(fitted["cap"]), float(fitted["cap"]))


def _probabilities(predictions: np.ndarray, residual_std: float) -> np.ndarray:
    distribution = NormalDist()
    return np.asarray([distribution.cdf(float(value) / residual_std) for value in predictions])


def _purged_split(
    rows: list[dict[str, Any]], validation_dates: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validation_set = set(validation_dates)
    validation = [row for row in rows if row["target_date"] in validation_set]
    if not validation:
        raise MLForecastError("time validation window contains no rows")
    validation_feature_start = min(str(row["feature_date"]) for row in validation)
    training = [row for row in rows if str(row["target_date"]) < validation_feature_start]
    if not training:
        raise MLForecastError("purged time split contains no earlier training rows")
    return training, validation


def _tune_model(
    model_name: str,
    rows: list[dict[str, Any]],
    feature_names: list[str],
    inner_validation_dates: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grids = _model_grids()[model_name]
    dates = sorted({str(row["target_date"]) for row in rows})
    validation_size = min(inner_validation_dates, max(10, len(dates) // 5))
    if len(dates) <= validation_size + 30:
        return grids[0], []
    training, validation = _purged_split(rows, dates[-validation_size:])
    x_validation, y_validation = _matrix(validation, feature_names)
    trials: list[dict[str, Any]] = []
    for params in grids:
        fitted = _fit_model(model_name, params, training, feature_names)
        predicted = _predict(fitted, x_validation)
        trials.append(
            {
                "params": dict(params),
                "rmse": float(math.sqrt(mean_squared_error(y_validation, predicted))),
                "training_rows": len(training),
                "validation_rows": len(validation),
                "validation_start": min(row["target_date"] for row in validation),
                "validation_end": max(row["target_date"] for row in validation),
            }
        )
    best = min(trials, key=lambda row: (row["rmse"], json.dumps(row["params"], sort_keys=True)))
    return dict(best["params"]), trials


def _rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    if len(left) < 2 or float(np.std(left)) < 1e-15 or float(np.std(right)) < 1e-15:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def _calibration_error(actual: np.ndarray, probability: np.ndarray, bins: int = 5) -> float:
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        mask = (probability >= lower) & (probability < upper if index < bins - 1 else probability <= upper)
        if np.any(mask):
            error += float(np.mean(mask)) * abs(float(np.mean(probability[mask])) - float(np.mean(actual[mask])))
    return error


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actual = np.asarray([float(row["actual_excess_return"]) for row in rows])
    predicted = np.asarray([float(row["predicted_excess_return"]) for row in rows])
    probability = np.asarray([float(row["outperform_probability"]) for row in rows])
    actual_class = (actual > 0).astype(int)
    predicted_class = (probability >= 0.5).astype(int)
    denominator = float(np.sum((actual - actual.mean()) ** 2))
    try:
        auc = float(roc_auc_score(actual_class, probability)) if len(set(actual_class)) > 1 else None
    except ValueError:
        auc = None
    return {
        "rows": len(rows),
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(math.sqrt(mean_squared_error(actual, predicted))),
        "oos_r2": 1.0 - float(np.sum((actual - predicted) ** 2)) / denominator if denominator > 1e-15 else 0.0,
        "ic": _correlation(predicted, actual),
        "rank_ic": _correlation(_rankdata(predicted), _rankdata(actual)),
        "direction_accuracy": float(np.mean(actual_class == predicted_class)),
        "direction_f1": float(f1_score(actual_class, predicted_class, zero_division=0)),
        "auc": auc,
        "brier_score": float(brier_score_loss(actual_class, probability)),
        "calibration_error": _calibration_error(actual_class, probability),
    }


def _prediction_rows(
    fitted: dict[str, Any],
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    fold: str,
    split: str,
    training_rows: int,
) -> list[dict[str, Any]]:
    x, _ = _matrix(rows, feature_names)
    predictions = _predict(fitted, x)
    probabilities = _probabilities(predictions, float(fitted["residual_std"]))
    return [
        {
            "model_name": fitted["model_name"],
            "model_version": MODEL_VERSIONS[fitted["model_name"]],
            "fold": fold,
            "split": split,
            "symbol": row["symbol"],
            "industry": row["industry"],
            "size_bucket": row["size_bucket"],
            "feature_date": row["feature_date"],
            "target_date": row["target_date"],
            "horizon": row["horizon"],
            "predicted_excess_return": float(prediction),
            "actual_excess_return": float(row["excess_return"]),
            "outperform_probability": float(probability),
            "actual_outperformed": int(row["outperformed"]),
            "training_rows": training_rows,
            "parameters": json.dumps(fitted["params"], ensure_ascii=False, sort_keys=True),
        }
        for row, prediction, probability in zip(rows, predictions, probabilities)
    ]


def _outer_windows(
    rows: list[dict[str, Any]], requested_size: int, folds: int, minimum_training_dates: int
) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]], str]]:
    dates = sorted({str(row["target_date"]) for row in rows})
    available = len(dates) - minimum_training_dates
    window_size = min(requested_size, available // max(folds, 1))
    if window_size < 10:
        raise MLForecastError("panel history is insufficient for the requested outer rolling validation")
    windows: list[tuple[list[dict[str, Any]], list[dict[str, Any]], str]] = []
    for reverse_index in reversed(range(folds)):
        end = len(dates) - reverse_index * window_size
        start = end - window_size
        test_dates = dates[start:end]
        training, test = _purged_split(rows, test_dates)
        if len({row["target_date"] for row in training}) < minimum_training_dates:
            continue
        windows.append((training, test, f"outer_{len(windows) + 1}"))
    if not windows:
        raise MLForecastError("outer rolling validation produced no usable folds")
    return windows


def _group_test_rows(predictions: list[dict[str, Any]], fold: str, horizon: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        groups[(row["model_name"], row["target_date"])].append(row)
    collected: dict[tuple[str, str], list[float]] = defaultdict(list)
    for (model_name, _), group in groups.items():
        ordered = sorted(group, key=lambda row: (row["predicted_excess_return"], row["symbol"]))
        count = len(ordered)
        for index, row in enumerate(ordered):
            bucket = min(2, int(index * 3 / max(count, 1)))
            label = ("low", "middle", "high")[bucket]
            collected[(model_name, label)].append(float(row["actual_excess_return"]))
    result = []
    for (model_name, label), values in sorted(collected.items()):
        result.append(
            {
                "horizon": horizon,
                "model_name": model_name,
                "model_version": MODEL_VERSIONS[model_name],
                "fold": fold,
                "group": label,
                "observations": len(values),
                "average_actual_excess_return": float(np.mean(values)),
                "win_rate": float(np.mean(np.asarray(values) > 0)),
            }
        )
    return result


def _economic_evaluation(
    predictions: list[dict[str, Any]],
    *,
    fold: str,
    horizon: int,
    transaction_cost_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    by_model_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        by_model_date[(row["model_name"], row["target_date"])].append(row)
    scenarios = (
        ("no_cost", 0.0, 0.0),
        ("slippage_only", 0.0, slippage_bps),
        ("base_cost", transaction_cost_bps, slippage_bps),
    )
    results: list[dict[str, Any]] = []
    for model_name in MODEL_ORDER:
        dated = sorted(
            ((target_date, rows) for (name, target_date), rows in by_model_date.items() if name == model_name),
            key=lambda item: item[0],
        )
        if not dated:
            continue
        for scenario, transaction, slippage in scenarios:
            equity = 1.0
            peak = 1.0
            max_drawdown = 0.0
            previous_positions: set[tuple[str, int]] = set()
            gross_values: list[float] = []
            net_values: list[float] = []
            turnovers: list[float] = []
            for _, rows in dated:
                ordered = sorted(rows, key=lambda row: (row["predicted_excess_return"], row["symbol"]))
                count = max(1, len(ordered) // 3)
                short_rows = ordered[:count]
                long_rows = ordered[-count:]
                positions = {(row["symbol"], -1) for row in short_rows} | {(row["symbol"], 1) for row in long_rows}
                gross = float(np.mean([row["actual_excess_return"] for row in long_rows])) - float(
                    np.mean([row["actual_excess_return"] for row in short_rows])
                )
                turnover = float(len(positions.symmetric_difference(previous_positions))) / max(len(positions), 1)
                cost = turnover * (transaction + slippage) / 10000.0
                net = gross - cost
                equity *= 1.0 + net
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, 1.0 - equity / peak)
                gross_values.append(gross)
                net_values.append(net)
                turnovers.append(turnover)
                previous_positions = positions
            gross_equity = float(np.prod(1.0 + np.asarray(gross_values)))
            results.append(
                {
                    "horizon": horizon,
                    "model_name": model_name,
                    "model_version": MODEL_VERSIONS[model_name],
                    "fold": fold,
                    "scenario": scenario,
                    "periods": len(net_values),
                    "transaction_cost_bps": transaction,
                    "slippage_bps": slippage,
                    "cumulative_gross_return": gross_equity - 1.0,
                    "cumulative_net_return": equity - 1.0,
                    "average_spread_return": float(np.mean(net_values)),
                    "win_rate": float(np.mean(np.asarray(net_values) > 0)),
                    "turnover": float(np.sum(turnovers)),
                    "max_drawdown": max_drawdown,
                }
            )
    return results


def _leakage_checks(
    rows: list[dict[str, Any]],
    latest_rows: list[dict[str, Any]],
    *,
    benchmark_symbol: str,
    membership_policy: str,
) -> list[dict[str, Any]]:
    checks = [
        {
            "check": "explicit_benchmark",
            "status": "pass" if benchmark_symbol else "fail",
            "details": f"benchmark={benchmark_symbol or 'missing'}; automatic substitution is disabled",
        },
        {
            "check": "adjusted_stock_prices",
            "status": "pass" if all(row.get("adjustment") == "qfq" for row in rows + latest_rows) else "fail",
            "details": "all stock-return labels must use qfq adjusted prices",
        },
        {
            "check": "feature_precedes_target",
            "status": "pass" if all(row["feature_date"] < row["target_date"] for row in rows) else "fail",
            "details": "every labeled sample keeps feature_date earlier than target_date",
        },
        {
            "check": "point_in_time_external_data",
            "status": "pass",
            "details": "financial filed_date, news published_at, and announcement filed_date are filtered at feature construction",
        },
        {
            "check": "purged_time_validation",
            "status": "pass",
            "details": "training target dates must precede the first validation feature date in every split",
        },
        {
            "check": "random_kfold_disabled",
            "status": "pass",
            "details": "only chronological inner, outer, and final holdout splits are implemented",
        },
        {
            "check": "historical_membership",
            "status": "pass" if membership_policy == "historical_constituents" else "warning",
            "details": (
                "historical constituent records supplied"
                if membership_policy == "historical_constituents"
                else "user-supplied fixed universe is auditable but does not eliminate survivorship bias"
            ),
        },
    ]
    return checks


def _importance_rows(
    fitted: dict[str, Any], feature_names: list[str], horizon: int, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if fitted["model_name"] in {"zero_excess", "excess_historical_mean"}:
        return []
    pipeline = fitted["model"]
    model = pipeline.named_steps["model"]
    if hasattr(model, "coef_"):
        raw = np.asarray(model.coef_, dtype=float)
    elif hasattr(model, "feature_importances_"):
        raw = np.asarray(model.feature_importances_, dtype=float)
    else:
        x, y = _matrix(rows, feature_names)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*encountered in matmul", category=RuntimeWarning)
            measured = permutation_importance(
                pipeline,
                x,
                y,
                n_repeats=3,
                random_state=RANDOM_SEED,
                n_jobs=1,
                scoring="neg_mean_squared_error",
            )
        raw = np.asarray(measured.importances_mean, dtype=float)
    absolute = np.abs(raw)
    denominator = float(np.sum(absolute)) or 1.0
    ranked = sorted(zip(feature_names, raw, absolute), key=lambda item: (-item[2], item[0]))
    return [
        {
            "horizon": horizon,
            "model_name": fitted["model_name"],
            "model_version": MODEL_VERSIONS[fitted["model_name"]],
            "rank": rank,
            "feature": name,
            "importance": float(magnitude / denominator),
            "signed_value": float(value),
            "interpretation": "model association, not a causal effect",
        }
        for rank, (name, value, magnitude) in enumerate(ranked, start=1)
    ]


def _explanation_rows(
    fitted: dict[str, Any], rows: list[dict[str, Any]], feature_names: list[str]
) -> list[dict[str, Any]]:
    if fitted["model_name"] in {"zero_excess", "excess_historical_mean"}:
        return []
    x = np.asarray([[float(row["features"].get(name, 0.0)) for name in feature_names] for row in rows])
    predictions = _predict(fitted, x)
    results: list[dict[str, Any]] = []
    for row_index, (row, prediction) in enumerate(zip(rows, predictions)):
        contributions: list[tuple[str, float, float]] = []
        for feature_index, feature_name in enumerate(feature_names):
            replaced = np.array(x[row_index : row_index + 1], copy=True)
            replaced[0, feature_index] = float(fitted["medians"][feature_index])
            contribution = float(prediction - _predict(fitted, replaced)[0])
            contributions.append((feature_name, contribution, float(x[row_index, feature_index])))
        contributions.sort(key=lambda item: (-abs(item[1]), item[0]))
        for rank, (feature_name, contribution, feature_value) in enumerate(contributions[:5], start=1):
            results.append(
                {
                    "horizon": row["horizon"],
                    "symbol": row["symbol"],
                    "model_name": fitted["model_name"],
                    "rank": rank,
                    "feature": feature_name,
                    "feature_value": feature_value,
                    "local_prediction_difference": contribution,
                    "direction": "positive" if contribution > 0 else "negative" if contribution < 0 else "neutral",
                    "method": "replace one feature with its training median; association only",
                }
            )
    return results


def _estimated_target_date(feature_date: str, horizon: int) -> str:
    current = datetime.strptime(feature_date, "%Y-%m-%d")
    steps = 0
    while steps < horizon:
        current += timedelta(days=1)
        if current.weekday() < 5:
            steps += 1
    return current.strftime("%Y-%m-%d")


def run_ml_forecast_analysis(
    panel_rows: list[dict[str, Any]],
    latest_rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    benchmark_symbol: str,
    membership_policy: str,
    final_test_dates: int = 40,
    outer_test_dates: int = 20,
    outer_folds: int = 3,
    inner_validation_dates: int = 20,
    minimum_training_dates: int = 120,
    transaction_cost_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    """Run nested chronological validation without forcing an ML model to win."""
    if final_test_dates < 10 or outer_test_dates < 10 or inner_validation_dates < 10:
        raise MLForecastError("all configured validation windows must contain at least 10 target dates")
    if minimum_training_dates < 60:
        raise MLForecastError("minimum_training_dates must be at least 60")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise MLForecastError("transaction cost and slippage cannot be negative")
    leakage_checks = _leakage_checks(
        panel_rows,
        latest_rows,
        benchmark_symbol=benchmark_symbol,
        membership_policy=membership_policy,
    )
    blocking_leakage = any(row["status"] != "pass" for row in leakage_checks)
    rolling_rows: list[dict[str, Any]] = []
    final_prediction_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    cost_rows: list[dict[str, Any]] = []
    leaderboard: list[dict[str, Any]] = []
    forecast_rows: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    explanation_rows: list[dict[str, Any]] = []
    tuning_rows: list[dict[str, Any]] = []

    horizons = sorted({int(row["horizon"]) for row in panel_rows})
    for horizon in horizons:
        horizon_rows = [row for row in panel_rows if int(row["horizon"]) == horizon]
        horizon_latest = [row for row in latest_rows if int(row["horizon"]) == horizon]
        dates = sorted({str(row["target_date"]) for row in horizon_rows})
        if len(dates) <= final_test_dates + minimum_training_dates + outer_test_dates:
            raise MLForecastError(f"horizon {horizon} has insufficient target dates for nested validation")
        final_dates = dates[-final_test_dates:]
        development, final_test = _purged_split(horizon_rows, final_dates)
        outer_windows = _outer_windows(development, outer_test_dates, outer_folds, minimum_training_dates)
        outer_predictions: list[dict[str, Any]] = []
        for training, test, fold in outer_windows:
            for model_name in MODEL_ORDER:
                params, trials = _tune_model(model_name, training, feature_names, inner_validation_dates)
                for trial in trials:
                    tuning_rows.append(
                        {
                            "horizon": horizon,
                            "fold": fold,
                            "model_name": model_name,
                            "parameters": json.dumps(trial["params"], sort_keys=True),
                            "inner_rmse": trial["rmse"],
                            "training_rows": trial["training_rows"],
                            "validation_rows": trial["validation_rows"],
                            "validation_start": trial["validation_start"],
                            "validation_end": trial["validation_end"],
                        }
                    )
                fitted = _fit_model(model_name, params, training, feature_names)
                predictions = _prediction_rows(
                    fitted,
                    test,
                    feature_names,
                    fold=fold,
                    split="outer_test",
                    training_rows=len(training),
                )
                outer_predictions.extend(predictions)
                metrics = _metrics(predictions)
                rolling_rows.append(
                    {
                        "horizon": horizon,
                        "fold": fold,
                        "model_name": model_name,
                        "model_version": MODEL_VERSIONS[model_name],
                        "test_start": min(row["target_date"] for row in test),
                        "test_end": max(row["target_date"] for row in test),
                        "parameters": json.dumps(params, sort_keys=True),
                        **metrics,
                    }
                )
        group_rows.extend(_group_test_rows(outer_predictions, "outer_all", horizon))
        cost_rows.extend(
            _economic_evaluation(
                outer_predictions,
                fold="outer_all",
                horizon=horizon,
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
            )
        )

        final_fits: dict[str, dict[str, Any]] = {}
        for model_name in MODEL_ORDER:
            params, trials = _tune_model(model_name, development, feature_names, inner_validation_dates)
            for trial in trials:
                tuning_rows.append(
                    {
                        "horizon": horizon,
                        "fold": "pre_final",
                        "model_name": model_name,
                        "parameters": json.dumps(trial["params"], sort_keys=True),
                        "inner_rmse": trial["rmse"],
                        "training_rows": trial["training_rows"],
                        "validation_rows": trial["validation_rows"],
                        "validation_start": trial["validation_start"],
                        "validation_end": trial["validation_end"],
                    }
                )
            fitted = _fit_model(model_name, params, development, feature_names)
            final_fits[model_name] = fitted
            final_prediction_rows.extend(
                _prediction_rows(
                    fitted,
                    final_test,
                    feature_names,
                    fold="final_holdout",
                    split="final_test",
                    training_rows=len(development),
                )
            )

        horizon_rolling = [row for row in rolling_rows if row["horizon"] == horizon]
        fold_names = sorted({row["fold"] for row in horizon_rolling})
        best_simple_by_fold = {
            fold: min(
                (row for row in horizon_rolling if row["fold"] == fold and row["model_name"] in SIMPLE_MODELS),
                key=lambda row: row["rmse"],
            )["rmse"]
            for fold in fold_names
        }
        outer_summary: dict[str, dict[str, Any]] = {}
        for model_name in MODEL_ORDER:
            model_rows = [row for row in horizon_rolling if row["model_name"] == model_name]
            outer_summary[model_name] = {
                "outer_rmse": float(np.mean([row["rmse"] for row in model_rows])),
                "outer_mae": float(np.mean([row["mae"] for row in model_rows])),
                "outer_oos_r2": float(np.mean([row["oos_r2"] for row in model_rows])),
                "outer_ic": float(np.mean([row["ic"] for row in model_rows])),
                "outer_rank_ic": float(np.mean([row["rank_ic"] for row in model_rows])),
                "outer_direction_accuracy": float(np.mean([row["direction_accuracy"] for row in model_rows])),
                "outer_auc": float(np.mean([row["auc"] for row in model_rows if row["auc"] is not None]))
                if any(row["auc"] is not None for row in model_rows)
                else None,
                "outer_brier_score": float(np.mean([row["brier_score"] for row in model_rows])),
                "outer_folds_beating_best_simple": sum(
                    row["rmse"] < best_simple_by_fold[row["fold"]] for row in model_rows
                ),
                "outer_positive_rank_ic_folds": sum(row["rank_ic"] > 0 for row in model_rows),
                "outer_folds": len(model_rows),
            }
        locked_simple = min(SIMPLE_MODELS, key=lambda name: outer_summary[name]["outer_rmse"])
        eligible_ml = [
            name
            for name in ML_MODELS
            if outer_summary[name]["outer_folds_beating_best_simple"]
            >= math.ceil(outer_summary[name]["outer_folds"] / 2)
            and outer_summary[name]["outer_positive_rank_ic_folds"]
            >= math.ceil(outer_summary[name]["outer_folds"] / 2)
        ]
        locked_candidate = min(eligible_ml, key=lambda name: outer_summary[name]["outer_rmse"]) if eligible_ml else locked_simple

        horizon_final_predictions = [
            row for row in final_prediction_rows if int(row["horizon"]) == horizon
        ]
        final_metrics = {
            name: _metrics([row for row in horizon_final_predictions if row["model_name"] == name])
            for name in MODEL_ORDER
        }
        final_simple_rmse = min(final_metrics[name]["rmse"] for name in SIMPLE_MODELS)
        base_cost_by_model = {
            row["model_name"]: row
            for row in _economic_evaluation(
                outer_predictions,
                fold="outer_all_selection",
                horizon=horizon,
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
            )
            if row["scenario"] == "base_cost"
        }
        candidate_passes_final = (
            locked_candidate in ML_MODELS
            and final_metrics[locked_candidate]["rmse"] < final_simple_rmse
            and final_metrics[locked_candidate]["rank_ic"] > 0
            and base_cost_by_model[locked_candidate]["cumulative_net_return"] > 0
            and not blocking_leakage
        )
        selected_model = locked_candidate if candidate_passes_final else locked_simple
        for model_name in MODEL_ORDER:
            summary = outer_summary[model_name]
            final = final_metrics[model_name]
            if model_name == selected_model:
                status = "selected"
                reason = (
                    "selected after locked outer-window nomination and final holdout acceptance"
                    if model_name in ML_MODELS
                    else "selected simple model because no ML candidate passed every predefined gate"
                )
            elif model_name in ML_MODELS:
                status = "candidate" if model_name in eligible_ml and not blocking_leakage else "research_only"
                reasons = []
                if model_name not in eligible_ml:
                    reasons.append("did not beat the best simple model in a majority of outer folds with positive rank IC")
                if blocking_leakage:
                    reasons.append("data governance contains a warning or failure")
                if model_name == locked_candidate and not candidate_passes_final:
                    reasons.append("locked candidate did not pass final holdout and cost gates")
                reason = "; ".join(reasons) or "passed outer gates but was not the locked champion"
            else:
                status = "candidate"
                reason = "retained as a transparent simple benchmark"
            leaderboard.append(
                {
                    "horizon": horizon,
                    "model_name": model_name,
                    "model_version": MODEL_VERSIONS[model_name],
                    "model_family": "machine_learning" if model_name in ML_MODELS else "simple_baseline",
                    "status": status,
                    "selected": int(model_name == selected_model),
                    "selection_reason": reason,
                    "locked_outer_candidate": int(model_name == locked_candidate),
                    **summary,
                    **{f"final_{key}": value for key, value in final.items()},
                }
            )

        full_fits: dict[str, dict[str, Any]] = {}
        for model_name in MODEL_ORDER:
            params, _ = _tune_model(model_name, horizon_rows, feature_names, inner_validation_dates)
            full_fits[model_name] = _fit_model(model_name, params, horizon_rows, feature_names)
            importance_rows.extend(
                _importance_rows(full_fits[model_name], feature_names, horizon, horizon_rows)
            )
        selected_fit = full_fits[selected_model]
        latest_x = np.asarray(
            [[float(row["features"].get(name, 0.0)) for name in feature_names] for row in horizon_latest]
        )
        latest_predictions = _predict(selected_fit, latest_x)
        latest_probabilities = _probabilities(latest_predictions, selected_fit["residual_std"])
        ordered_indices = sorted(range(len(horizon_latest)), key=lambda index: (-latest_predictions[index], horizon_latest[index]["symbol"]))
        ranks = {index: rank for rank, index in enumerate(ordered_indices, start=1)}
        for index, (row, prediction, probability) in enumerate(
            zip(horizon_latest, latest_predictions, latest_probabilities)
        ):
            interval = float(selected_fit["residual_interval"])
            forecast_rows.append(
                {
                    "horizon": horizon,
                    "symbol": row["symbol"],
                    "label": row["label"],
                    "industry": row["industry"],
                    "size_bucket": row["size_bucket"],
                    "feature_date": row["feature_date"],
                    "estimated_target_date": _estimated_target_date(row["feature_date"], horizon),
                    "benchmark_symbol": row["benchmark_symbol"],
                    "model_name": selected_model,
                    "model_version": MODEL_VERSIONS[selected_model],
                    "model_status": "selected",
                    "predicted_excess_return": float(prediction),
                    "outperform_probability": float(probability),
                    "prediction_rank": ranks[index],
                    "lower_bound": float(prediction - interval),
                    "upper_bound": float(prediction + interval),
                    "interval_level": INTERVAL_LEVEL,
                    "limitations": "research estimate; target date skips weekends but not exchange holidays or suspensions",
                }
            )
        explanation_rows.extend(_explanation_rows(selected_fit, horizon_latest, feature_names))

    return {
        "status": "pass" if not blocking_leakage else "warning",
        "engine_version": ML_FORECAST_ENGINE_VERSION,
        "feature_version": ML_FEATURE_VERSION,
        "random_seed": RANDOM_SEED,
        "feature_names": feature_names,
        "panel_rows": panel_rows,
        "latest_rows": latest_rows,
        "forecasts": forecast_rows,
        "leaderboard": leaderboard,
        "rolling_validation": rolling_rows,
        "final_predictions": final_prediction_rows,
        "feature_importance": importance_rows,
        "prediction_explanations": explanation_rows,
        "group_tests": group_rows,
        "cost_sensitivity": cost_rows,
        "leakage_checks": leakage_checks,
        "tuning_trials": tuning_rows,
        "validation_config": {
            "final_test_dates": final_test_dates,
            "outer_test_dates": outer_test_dates,
            "outer_folds": outer_folds,
            "inner_validation_dates": inner_validation_dates,
            "minimum_training_dates": minimum_training_dates,
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "final_test_usage": "acceptance only; not used to choose features, parameters, or the locked outer candidate",
        },
        "limitations": (
            "Statistical research output only. Fixed user universes retain survivorship risk; predictions are not "
            "investment advice and may fail under regime changes, suspensions, corporate events, or source revisions."
        ),
    }
