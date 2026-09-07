#!/usr/bin/env python3
"""Run stage-six to stage-eight benchmark-relative excess-return workflows."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from openpyxl import Workbook

from marketsignal import (
    SEC_COMPANYFACTS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_TICKERS_URL,
    PipelineError,
    _fetch_json,
    _sec_headers,
    _remove_core_properties,
    _set_number_format,
    _write_table,
    clean_announcements,
    clean_financials,
    clean_news,
    clean_prices,
    fetch_online_china_adjusted_prices,
    fetch_online_china_announcements,
    fetch_online_china_financials,
    fetch_online_china_index_prices,
    fetch_online_china_news,
    fetch_online_news,
    fetch_online_us_adjusted_prices,
    filter_announcements_by_range,
    filter_financials_by_range,
    filter_news_by_range,
    filter_prices_by_range,
    parse_symbol,
    resolve_entity,
    extract_announcement_rows,
    extract_financial_rows,
    _ticker_records,
)
from ml_forecasting import MLForecastError, build_excess_return_panel, run_ml_forecast_analysis
from versioning import (
    ML_PANEL_CONTRACT_VERSION,
    MULTI_MARKET_ML_CONTRACT_VERSION,
    SINGLE_ASSET_CONTRACT_VERSION,
    component_versions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "marketsignal"
ALLOWED_MEMBERSHIP_POLICIES = {"user_supplied_fixed_universe", "historical_constituents"}
ALLOWED_TASK_TYPES = {"panel", "single_asset"}
ALLOWED_BENCHMARK_TYPES = {"market_index", "benchmark_asset"}
SUPPORTED_MARKETS = {"cn_a", "cn_b", "hk", "us"}
MARKET_ALIASES = {
    "a": "cn_a",
    "a股": "cn_a",
    "b": "cn_b",
    "b股": "cn_b",
    "hk": "hk",
    "港股": "hk",
    "us": "us",
    "美股": "us",
}
MARKET_CALENDARS = {
    "cn_a": "CN_A_SHARE",
    "cn_b": "CN_B_SHARE",
    "hk": "HKEX",
    "us": "NYSE_NASDAQ",
}
MARKET_TIMEZONES = {
    "cn_a": "Asia/Shanghai",
    "cn_b": "Asia/Shanghai",
    "hk": "Asia/Hong_Kong",
    "us": "America/New_York",
}


class MLTaskError(RuntimeError):
    """Expected manifest or stage-six task failure."""


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MLTaskError(f"could not read ML manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise MLTaskError("ML manifest must contain a mapping at the top level")
    return payload


def _date_value(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if text == "latest":
        return date.today().isoformat()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise MLTaskError(f"{field} must use YYYY-MM-DD or latest") from exc


def _positive_int(value: Any, field: str, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MLTaskError(f"{field} must be an integer") from exc
    if number < minimum:
        raise MLTaskError(f"{field} must be at least {minimum}")
    return number


def _non_negative_float(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MLTaskError(f"{field} must be numeric") from exc
    if number < 0:
        raise MLTaskError(f"{field} cannot be negative")
    return number


def _market_value(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    market = MARKET_ALIASES.get(normalized, normalized)
    if market not in SUPPORTED_MARKETS:
        raise MLTaskError(f"market must be one of: {', '.join(sorted(SUPPORTED_MARKETS))}")
    return market


def validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    market = _market_value(payload.get("market", "cn_a"))
    version = str(payload.get("version", ""))
    allowed_versions = {
        ML_PANEL_CONTRACT_VERSION,
        SINGLE_ASSET_CONTRACT_VERSION,
        MULTI_MARKET_ML_CONTRACT_VERSION,
    }
    if version not in allowed_versions:
        raise MLTaskError(
            f"ML manifest version must be one of {', '.join(sorted(allowed_versions))}; received {payload.get('version', 'missing')}"
        )
    if market != "cn_a" and version != MULTI_MARKET_ML_CONTRACT_VERSION:
        raise MLTaskError(
            f"market={market} requires ML manifest version {MULTI_MARKET_ML_CONTRACT_VERSION}"
        )
    name = str(payload.get("name", "")).strip()
    if not name:
        raise MLTaskError("ML manifest requires a non-empty name")
    task_type = str(payload.get("task_type", "panel")).strip() or "panel"
    if task_type not in ALLOWED_TASK_TYPES:
        raise MLTaskError(f"task_type must be one of: {', '.join(sorted(ALLOWED_TASK_TYPES))}")
    if str(payload.get("mode", "online")) != "online":
        raise MLTaskError("formal stage-six to stage-eight forecasts require mode=online")
    start_date = _date_value(payload.get("start_date"), "start_date")
    end_date = _date_value(payload.get("end_date", "latest"), "end_date")
    if start_date > end_date:
        raise MLTaskError("start_date cannot be later than end_date")
    benchmark = payload.get("benchmark")
    if not isinstance(benchmark, dict) or not str(benchmark.get("symbol", "")).strip():
        raise MLTaskError("ML manifest requires an explicit benchmark.symbol")
    benchmark_market = _market_value(benchmark.get("market", market))
    if benchmark_market != market:
        raise MLTaskError("benchmark.market must match the task market; cross-market panels are not supported")
    benchmark_type = str(benchmark.get("type", "market_index")).strip() or "market_index"
    if benchmark_type not in ALLOWED_BENCHMARK_TYPES:
        raise MLTaskError(
            f"benchmark.type must be one of: {', '.join(sorted(ALLOWED_BENCHMARK_TYPES))}"
        )
    benchmark_symbol = str(benchmark["symbol"]).strip()
    benchmark_instrument: dict[str, str] | None = None
    if market == "cn_a" and benchmark_type == "market_index":
        if not benchmark_symbol.isdigit() or len(benchmark_symbol) != 6:
            raise MLTaskError("cn_a market_index benchmark.symbol must contain six digits")
        benchmark_currency = "CNY"
    else:
        try:
            benchmark_instrument = parse_symbol(benchmark_symbol, benchmark_market)
        except PipelineError as exc:
            raise MLTaskError(f"benchmark: {exc}") from exc
        benchmark_symbol = benchmark_instrument["symbol"]
        benchmark_currency = benchmark_instrument["currency"]
    horizon_values = payload.get("horizons", [1, 5])
    if not isinstance(horizon_values, list) or not horizon_values:
        raise MLTaskError("horizons must be a non-empty list")
    horizons = sorted({int(value) for value in horizon_values})
    if any(value < 1 or value > 20 for value in horizons):
        raise MLTaskError("horizons must contain trading-step values from 1 to 20")
    membership_policy = str(payload.get("membership_policy", "user_supplied_fixed_universe"))
    if task_type == "single_asset":
        membership_policy = "single_asset"
    if task_type == "panel" and membership_policy not in ALLOWED_MEMBERSHIP_POLICIES:
        raise MLTaskError(
            f"membership_policy must be one of: {', '.join(sorted(ALLOWED_MEMBERSHIP_POLICIES))}"
        )
    if task_type == "single_asset":
        items = [
            {
                "symbol": payload.get("symbol", ""),
                "label": payload.get("label", ""),
                "industry": payload.get("industry", "未分类"),
                "size_bucket": payload.get("size_bucket", "未分类"),
                "membership_source": "single_asset",
            }
        ]
    else:
        items = payload.get("items")
        if not isinstance(items, list) or len(items) < 2:
            raise MLTaskError("ML manifest requires at least two explicitly supplied same-market items")
        if len(items) > 30:
            raise MLTaskError("ML manifest supports at most 30 items per run")
    normalized_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise MLTaskError(f"item {index} must be a mapping")
        try:
            instrument = parse_symbol(str(item.get("symbol", "")), market)
        except PipelineError as exc:
            raise MLTaskError(f"item {index}: {exc}") from exc
        if instrument["symbol"] in seen:
            raise MLTaskError(f"duplicate ML panel item: {instrument['symbol']}")
        seen.add(instrument["symbol"])
        normalized_items.append(
            {
                "instrument": instrument,
                "symbol": instrument["symbol"],
                "market": instrument["market"],
                "currency": instrument["currency"],
                "label": str(item.get("label", instrument["symbol"])).strip() or instrument["symbol"],
                "industry": str(item.get("industry", "未分类")).strip() or "未分类",
                "size_bucket": str(item.get("size_bucket", "未分类")).strip() or "未分类",
                "membership_start": str(item.get("membership_start", "")),
                "membership_end": str(item.get("membership_end", "")),
                "membership_source": str(item.get("membership_source", membership_policy)),
            }
        )
    if task_type == "panel" and len({item["currency"] for item in normalized_items}) > 1:
        raise MLTaskError("panel items must use one common trading currency")
    if normalized_items and normalized_items[0]["currency"] != benchmark_currency:
        raise MLTaskError(
            "benchmark currency must match the stock currency; cross-currency excess returns require an explicit FX layer"
        )
    default_stem = f"{market}_single_excess_return" if task_type == "single_asset" else f"{market}_ml_panel"
    default_output = f"outputs/{default_stem}.xlsx"
    output = Path(str(payload.get("output", default_output)))
    if output.suffix.lower() != ".xlsx":
        raise MLTaskError("output must use the .xlsx extension")
    return {
        "version": version,
        "name": name,
        "task_type": task_type,
        "market": market,
        "calendar": MARKET_CALENDARS[market],
        "timezone": MARKET_TIMEZONES[market],
        "mode": "online",
        "start_date": start_date,
        "end_date": end_date,
        "benchmark": {
            "symbol": benchmark_symbol,
            "label": str(benchmark.get("label", benchmark_symbol)),
            "type": benchmark_type,
            "market": benchmark_market,
            "currency": benchmark_currency,
            "instrument": benchmark_instrument,
        },
        "horizons": horizons,
        "membership_policy": membership_policy,
        "include_context": bool(payload.get("include_context", True)),
        "final_test_dates": _positive_int(payload.get("final_test_dates", 40), "final_test_dates", 10),
        "outer_test_dates": _positive_int(payload.get("outer_test_dates", 20), "outer_test_dates", 10),
        "outer_folds": _positive_int(payload.get("outer_folds", 3), "outer_folds", 2),
        "inner_validation_dates": _positive_int(
            payload.get("inner_validation_dates", 20), "inner_validation_dates", 10
        ),
        "minimum_training_dates": _positive_int(
            payload.get("minimum_training_dates", 120), "minimum_training_dates", 60
        ),
        "transaction_cost_bps": _non_negative_float(
            payload.get("transaction_cost_bps", 10.0), "transaction_cost_bps"
        ),
        "slippage_bps": _non_negative_float(payload.get("slippage_bps", 5.0), "slippage_bps"),
        "output": output,
        "items": normalized_items,
    }


def _collect_context(
    instrument: dict[str, str],
    start_date: str,
    end_date: str,
    cache_dir: Path,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_rows: list[dict[str, Any]] = []
    datasets: dict[str, list[dict[str, Any]]] = {"financials": [], "news": [], "announcements": []}
    collectors = (
        ("financials", lambda: fetch_online_china_financials(instrument, cache_dir, refresh_cache)),
        ("news", lambda: fetch_online_china_news(instrument, cache_dir, refresh_cache)),
        (
            "announcements",
            lambda: fetch_online_china_announcements(
                instrument, start_date, end_date, cache_dir, refresh_cache
            ),
        ),
    )
    for dataset, collector in collectors:
        try:
            result = collector()
            raw = result[0]
            location = result[2] if dataset == "financials" else result[1]
            cache_status = result[3] if dataset == "financials" else result[2]
            provider = result[4] if dataset == "financials" else result[3]
            if dataset == "financials":
                cleaned, quality = clean_financials(raw)
                cleaned, out_of_range = filter_financials_by_range(cleaned, start_date, end_date)
            elif dataset == "news":
                cleaned, quality = clean_news(raw)
                cleaned, out_of_range = filter_news_by_range(cleaned, start_date, end_date)
            else:
                cleaned, quality = clean_announcements(raw)
                cleaned, out_of_range = filter_announcements_by_range(cleaned, start_date, end_date)
            datasets[dataset] = cleaned
            source_rows.append(
                {
                    "symbol": instrument["symbol"],
                    "market": instrument["market"],
                    "currency": instrument["currency"],
                    "dataset": dataset,
                    "provider": provider,
                    "status": "pass" if cleaned else "warning",
                    "cache_status": cache_status,
                    "source_location": location,
                    "raw_rows": quality["raw_rows"],
                    "clean_rows": quality["clean_rows"],
                    "output_rows": len(cleaned),
                    "notes": f"out_of_range={out_of_range}",
                }
            )
        except PipelineError as exc:
            source_rows.append(
                {
                    "symbol": instrument["symbol"],
                    "market": instrument["market"],
                    "currency": instrument["currency"],
                    "dataset": dataset,
                    "provider": "AKShare",
                    "status": "warning",
                    "cache_status": "error",
                    "source_location": "AKShare",
                    "raw_rows": 0,
                    "clean_rows": 0,
                    "output_rows": 0,
                    "notes": str(exc),
                }
            )
    return datasets["financials"], datasets["news"], datasets["announcements"], source_rows


def _collect_us_context(
    instrument: dict[str, str],
    start_date: str,
    end_date: str,
    cache_dir: Path,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect SEC and Yahoo context while preserving filing availability dates."""
    sec_user_agent = os.environ.get("MARKETSIGNAL_SEC_USER_AGENT", "")
    headers = _sec_headers(sec_user_agent)
    timeout = 30
    source_rows: list[dict[str, Any]] = []
    datasets: dict[str, list[dict[str, Any]]] = {"financials": [], "news": [], "announcements": []}

    def record_source(
        dataset: str,
        provider: str,
        location: str,
        cache_status: str,
        raw_count: int,
        clean_count: int,
        output_count: int,
        notes: str,
    ) -> None:
        source_rows.append(
            {
                "symbol": instrument["symbol"],
                "market": instrument["market"],
                "currency": instrument["currency"],
                "dataset": dataset,
                "provider": provider,
                "status": "pass" if output_count else "warning",
                "cache_status": cache_status,
                "source_location": location,
                "raw_rows": raw_count,
                "clean_rows": clean_count,
                "output_rows": output_count,
                "notes": notes,
            }
        )

    try:
        raw_news, location, cache_status = fetch_online_news(
            instrument["provider_symbol"],
            timeout,
            cache_dir,
            refresh_cache,
            market=instrument["market"],
            currency=instrument["currency"],
        )
        cleaned, quality = clean_news(raw_news)
        cleaned, out_of_range = filter_news_by_range(cleaned, start_date, end_date)
        datasets["news"] = cleaned
        record_source(
            "news",
            "Yahoo Finance RSS",
            location,
            cache_status,
            quality["raw_rows"],
            quality["clean_rows"],
            len(cleaned),
            f"out_of_range={out_of_range}",
        )
    except PipelineError as exc:
        record_source("news", "Yahoo Finance RSS", "Yahoo Finance RSS", "error", 0, 0, 0, str(exc))

    try:
        ticker_payload, ticker_cache = _fetch_json(
            SEC_TICKERS_URL,
            timeout,
            headers=headers,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
            is_sec_request=True,
        )
        ticker_match = next(
            (
                record
                for record in _ticker_records(ticker_payload)
                if str(record.get("ticker", "")).upper() == instrument["provider_symbol"].upper()
            ),
            None,
        )
        if not ticker_match:
            raise PipelineError(f"SEC ticker mapping did not find symbol: {instrument['symbol']}")
        cik = str(ticker_match.get("cik_str", ticker_match.get("cik", ""))).zfill(10)
        submissions_url = f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json"
        submissions, submissions_cache = _fetch_json(
            submissions_url,
            timeout,
            headers=headers,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
            is_sec_request=True,
        )
        entity = resolve_entity(
            instrument["provider_symbol"],
            ticker_payload,
            submissions,
            "SEC EDGAR",
            instrument=instrument,
        )
        facts_url = f"{SEC_COMPANYFACTS_URL}/CIK{entity['cik']}.json"
        facts, facts_cache = _fetch_json(
            facts_url,
            timeout,
            headers=headers,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
            is_sec_request=True,
        )
        raw_financials = extract_financial_rows(facts, "SEC Company Facts", instrument=instrument)
        cleaned_financials, quality = clean_financials(raw_financials)
        cleaned_financials, out_of_range = filter_financials_by_range(cleaned_financials, start_date, end_date)
        datasets["financials"] = cleaned_financials
        record_source(
            "financials",
            "SEC Company Facts",
            facts_url,
            "hit" if ticker_cache == submissions_cache == facts_cache == "hit" else "miss",
            quality["raw_rows"],
            quality["clean_rows"],
            len(cleaned_financials),
            f"entity={entity['company_name']}; cik={entity['cik']}; out_of_range={out_of_range}",
        )
        raw_announcements = extract_announcement_rows(
            submissions,
            entity["cik"],
            "SEC EDGAR submissions",
            limit=40,
            instrument=instrument,
        )
        cleaned_announcements, quality = clean_announcements(raw_announcements)
        cleaned_announcements, out_of_range = filter_announcements_by_range(
            cleaned_announcements, start_date, end_date
        )
        datasets["announcements"] = cleaned_announcements
        record_source(
            "announcements",
            "SEC EDGAR submissions",
            submissions_url,
            submissions_cache,
            quality["raw_rows"],
            quality["clean_rows"],
            len(cleaned_announcements),
            f"entity={entity['company_name']}; out_of_range={out_of_range}",
        )
    except PipelineError as exc:
        record_source("financials", "SEC EDGAR", SEC_COMPANYFACTS_URL, "error", 0, 0, 0, str(exc))
        record_source("announcements", "SEC EDGAR", SEC_SUBMISSIONS_URL, "error", 0, 0, 0, str(exc))

    return datasets["financials"], datasets["news"], datasets["announcements"], source_rows


def _collect_context_for_market(
    instrument: dict[str, str],
    start_date: str,
    end_date: str,
    cache_dir: Path,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if instrument["market"] == "us":
        return _collect_us_context(instrument, start_date, end_date, cache_dir, refresh_cache)
    return _collect_context(instrument, start_date, end_date, cache_dir, refresh_cache)


def _sheet_from_dicts(workbook: Workbook, title: str, rows: list[dict[str, Any]], headers: list[str]) -> Any:
    sheet = workbook.create_sheet(title)
    _write_table(sheet, headers, [[row.get(header, "") for header in headers] for row in rows])
    return sheet


def build_ml_workbook(
    output: Path,
    config: dict[str, Any],
    result: dict[str, Any],
    source_rows: list[dict[str, Any]],
) -> None:
    single_asset = config.get("task_type", "panel") == "single_asset"
    market = config.get("market", result.get("market", "cn_a"))
    items = config.get("items") or [{}]
    currency = items[0].get("currency", "CNY")
    calendar = config.get("calendar", result.get("calendar", "CN_A_SHARE"))
    timezone = config.get("timezone", "Asia/Shanghai")
    benchmark = config.get("benchmark", {})
    benchmark_symbol = benchmark.get("symbol", result.get("benchmark_symbol", ""))
    benchmark_type = benchmark.get("type", "market_index")
    benchmark_market = benchmark.get("market", result.get("benchmark_market", market))
    benchmark_currency = benchmark.get("currency", result.get("benchmark_currency", currency))
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "MarketSignal Intelligence"
    workbook.properties.title = "MarketSignal Intelligence ML excess-return report"

    readme_rows = [
        [
            "task_name",
            config["name"],
            "Versioned single-stock excess-return task"
            if single_asset
            else "Versioned multi-stock panel task",
        ],
        ["task_type", config.get("task_type", "panel"), "single_asset uses one stock relative to the explicit benchmark"],
        ["market", market, "Market-isolated task; cross-market panels are disabled"],
        ["currency", currency, "Local return currency; no implicit FX conversion"],
        ["calendar", calendar, "Exchange calendar used for estimated target dates"],
        ["timezone", timezone, "Market-local reporting timezone"],
        ["benchmark_symbol", benchmark_symbol, benchmark.get("label", benchmark_symbol)],
        ["benchmark_type", benchmark_type, "Explicit benchmark; no automatic substitution"],
        ["benchmark_market", benchmark_market, "Must match task market"],
        ["benchmark_currency", benchmark_currency, "Must match stock currency"],
        ["analysis_start", config["start_date"], "Business data range"],
        ["analysis_end", config["end_date"], "Business data range"],
        ["horizons", ", ".join(map(str, config["horizons"])), "Trading-step excess-return targets"],
        ["subjects", len(config["items"]), "Explicitly supplied stocks"],
        ["membership_policy", config["membership_policy"], "Controls survivorship-bias status"],
        ["pipeline_status", result["status"], "Review 数据泄漏检查 and 模型排行榜"],
        ["panel_rows", len(result["panel_rows"]), "Labeled stock-date-horizon observations"],
        ["feature_count", len(result["feature_names"]), "Point-in-time features; cross-sectional ranks only in panel mode"],
        ["random_seed", result["random_seed"], "Fixed for repeatable fitted models"],
        ["limitations", result["limitations"], "Research output; not investment advice"],
    ]
    readme_rows.extend(
        [[f"version_{component}", version, "Central component version registry"] for component, version in component_versions().items()]
    )
    readme = workbook.create_sheet("README")
    _write_table(readme, ["field", "value", "notes"], readme_rows)

    panel_headers = (
        [
            "symbol",
            "label",
            "market",
            "currency",
            "calendar",
            "timezone",
            "horizon",
            "feature_date",
            "target_date",
            "benchmark_symbol",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "stock_return",
            "benchmark_return",
            "excess_return",
            "outperformed",
            "adjustment",
            *result["feature_names"],
        ]
        if single_asset
        else [
            "symbol",
            "label",
            "industry",
            "size_bucket",
            "market",
            "currency",
            "calendar",
            "timezone",
            "horizon",
            "feature_date",
            "target_date",
            "benchmark_symbol",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "stock_return",
            "benchmark_return",
            "excess_return",
            "outperformed",
            "membership_source",
            "adjustment",
            *result["feature_names"],
        ]
    )
    panel_sheet = workbook.create_sheet("单股样本" if single_asset else "面板样本")
    panel_values = []
    for row in result["panel_rows"]:
        flat = {**row, **row["features"]}
        panel_values.append([flat.get(header, "") for header in panel_headers])
    _write_table(panel_sheet, panel_headers, panel_values)
    _set_number_format(
        panel_sheet,
        {"stock_return", "benchmark_return", "excess_return", *result["feature_names"]},
        "0.000000",
    )

    forecast_headers = (
        [
            "horizon",
            "symbol",
            "label",
            "feature_date",
            "estimated_target_date",
            "benchmark_symbol",
            "market",
            "currency",
            "calendar",
            "timezone",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "model_name",
            "model_version",
            "model_status",
            "predicted_excess_return",
            "outperform_probability",
            "predicted_direction",
            "lower_bound",
            "upper_bound",
            "interval_level",
            "limitations",
        ]
        if single_asset
        else [
            "horizon",
            "symbol",
            "label",
            "industry",
            "size_bucket",
            "feature_date",
            "estimated_target_date",
            "benchmark_symbol",
            "market",
            "currency",
            "calendar",
            "timezone",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "model_name",
            "model_version",
            "model_status",
            "predicted_excess_return",
            "outperform_probability",
            "predicted_direction",
            "prediction_rank",
            "lower_bound",
            "upper_bound",
            "interval_level",
            "limitations",
        ]
    )
    forecast_sheet = _sheet_from_dicts(
        workbook,
        "单股超额收益预测" if single_asset else "机器学习预测",
        result["forecasts"],
        forecast_headers,
    )
    _set_number_format(
        forecast_sheet,
        {"predicted_excess_return", "outperform_probability", "lower_bound", "upper_bound"},
        "0.0000%",
    )

    leaderboard_headers = [
        "horizon",
        "model_name",
        "model_version",
        "model_family",
        "status",
        "selected",
        "locked_outer_candidate",
        "selection_reason",
        "outer_rmse",
        "outer_mae",
        "outer_oos_r2",
        "outer_ic",
        "outer_rank_ic",
        "outer_direction_accuracy",
        "outer_auc",
        "outer_brier_score",
        "outer_folds_beating_best_simple",
        "outer_positive_rank_ic_folds",
        "outer_folds",
        "final_rows",
        "final_rmse",
        "final_mae",
        "final_oos_r2",
        "final_ic",
        "final_rank_ic",
        "final_direction_accuracy",
        "final_direction_f1",
        "final_auc",
        "final_brier_score",
        "final_calibration_error",
    ]
    leaderboard_sheet = _sheet_from_dicts(workbook, "模型排行榜", result["leaderboard"], leaderboard_headers)
    _set_number_format(
        leaderboard_sheet,
        {
            "outer_rmse",
            "outer_mae",
            "outer_oos_r2",
            "outer_ic",
            "outer_rank_ic",
            "outer_direction_accuracy",
            "outer_auc",
            "outer_brier_score",
            "final_rmse",
            "final_mae",
            "final_oos_r2",
            "final_ic",
            "final_rank_ic",
            "final_direction_accuracy",
            "final_direction_f1",
            "final_auc",
            "final_brier_score",
            "final_calibration_error",
        },
        "0.000000",
    )

    rolling_headers = [
        "horizon",
        "fold",
        "model_name",
        "model_version",
        "test_start",
        "test_end",
        "parameters",
        "rows",
        "mae",
        "rmse",
        "oos_r2",
        "ic",
        "rank_ic",
        "direction_accuracy",
        "direction_f1",
        "auc",
        "brier_score",
        "calibration_error",
    ]
    rolling_sheet = _sheet_from_dicts(workbook, "滚动验证", result["rolling_validation"], rolling_headers)
    _set_number_format(
        rolling_sheet,
        {"mae", "rmse", "oos_r2", "ic", "rank_ic", "direction_accuracy", "direction_f1", "auc", "brier_score", "calibration_error"},
        "0.000000",
    )

    importance_headers = [
        "horizon",
        "model_name",
        "model_version",
        "rank",
        "feature",
        "importance",
        "signed_value",
        "interpretation",
    ]
    importance_sheet = _sheet_from_dicts(workbook, "特征重要性", result["feature_importance"], importance_headers)
    _set_number_format(importance_sheet, {"importance", "signed_value"}, "0.000000")

    explanation_headers = [
        "horizon",
        "symbol",
        "model_name",
        "rank",
        "feature",
        "feature_value",
        "local_prediction_difference",
        "direction",
        "method",
    ]
    explanation_sheet = _sheet_from_dicts(
        workbook, "预测解释", result["prediction_explanations"], explanation_headers
    )
    _set_number_format(explanation_sheet, {"feature_value", "local_prediction_difference"}, "0.000000")

    if not single_asset:
        group_headers = [
            "horizon",
            "model_name",
            "model_version",
            "fold",
            "group",
            "observations",
            "average_actual_excess_return",
            "win_rate",
        ]
        group_sheet = _sheet_from_dicts(workbook, "分组检验", result["group_tests"], group_headers)
        _set_number_format(group_sheet, {"average_actual_excess_return", "win_rate"}, "0.0000%")

    cost_headers = [
        "horizon",
        "model_name",
        "model_version",
        "fold",
        "scenario",
        *(["strategy_type"] if single_asset else []),
        "periods",
        "transaction_cost_bps",
        "slippage_bps",
        "cumulative_gross_return",
        "cumulative_net_return",
        "average_spread_return",
        "win_rate",
        "turnover",
        "max_drawdown",
    ]
    cost_sheet = _sheet_from_dicts(workbook, "成本敏感性", result["cost_sensitivity"], cost_headers)
    _set_number_format(
        cost_sheet,
        {"cumulative_gross_return", "cumulative_net_return", "average_spread_return", "win_rate", "max_drawdown"},
        "0.0000%",
    )

    leakage_sheet = _sheet_from_dicts(
        workbook, "数据泄漏检查", result["leakage_checks"], ["check", "status", "details"]
    )
    final_headers = (
        [
            "model_name",
            "model_version",
            "fold",
            "split",
            "symbol",
            "market",
            "currency",
            "calendar",
            "timezone",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "feature_date",
            "target_date",
            "horizon",
            "predicted_excess_return",
            "actual_excess_return",
            "outperform_probability",
            "actual_outperformed",
            "training_rows",
            "parameters",
        ]
        if single_asset
        else [
            "model_name",
            "model_version",
            "fold",
            "split",
            "symbol",
            "industry",
            "size_bucket",
            "market",
            "currency",
            "calendar",
            "timezone",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "feature_date",
            "target_date",
            "horizon",
            "predicted_excess_return",
            "actual_excess_return",
            "outperform_probability",
            "actual_outperformed",
            "training_rows",
            "parameters",
        ]
    )
    final_sheet = _sheet_from_dicts(workbook, "最终测试明细", result["final_predictions"], final_headers)
    _set_number_format(
        final_sheet,
        {"predicted_excess_return", "actual_excess_return", "outperform_probability"},
        "0.0000%",
    )

    tuning_headers = [
        "horizon",
        "fold",
        "model_name",
        "parameters",
        "inner_rmse",
        "training_rows",
        "validation_rows",
        "validation_start",
        "validation_end",
    ]
    tuning_sheet = _sheet_from_dicts(workbook, "参数搜索", result["tuning_trials"], tuning_headers)
    _set_number_format(tuning_sheet, {"inner_rmse"}, "0.000000")

    source_headers = [
        "symbol",
        "market",
        "currency",
        "calendar",
        "timezone",
        "dataset",
        "provider",
        "status",
        "cache_status",
        "source_location",
        "raw_rows",
        "clean_rows",
        "output_rows",
        "adjustment_method",
        "notes",
    ]
    _sheet_from_dicts(workbook, "数据来源", source_rows, source_headers)

    subject_price_sources = [row for row in source_rows if row.get("dataset") == "adjusted_prices"]
    benchmark_price_sources = [row for row in source_rows if row.get("dataset") == "benchmark_prices"]
    context_rows = [row for row in source_rows if row.get("dataset") in {"financials", "news", "announcements"}]
    adapter_rows = [
        {
            "market": market,
            "currency": currency,
            "calendar": calendar,
            "timezone": timezone,
            "benchmark_symbol": benchmark_symbol,
            "benchmark_market": benchmark_market,
            "benchmark_currency": benchmark_currency,
            "benchmark_adjustment": result.get("benchmark_adjustment", ""),
            "stock_adjustment": ", ".join(
                sorted({str(row.get("adjustment_method", "")) for row in subject_price_sources})
            ),
            "price_sources": ", ".join(sorted({str(row.get("provider", "")) for row in subject_price_sources})),
            "context_datasets": len(context_rows),
            "status": "pass" if subject_price_sources and benchmark_price_sources else "fail",
            "notes": "same-market, same-currency task; context warnings remain visible in 数据来源",
        }
    ]
    _sheet_from_dicts(
        workbook,
        "市场适配检查",
        adapter_rows,
        [
            "market",
            "currency",
            "calendar",
            "timezone",
            "benchmark_symbol",
            "benchmark_market",
            "benchmark_currency",
            "benchmark_adjustment",
            "stock_adjustment",
            "price_sources",
            "context_datasets",
            "status",
            "notes",
        ],
    )

    version_rows = [
        {"component": component, "version": version, "notes": "central version registry"}
        for component, version in component_versions().items()
    ]
    version_rows.extend(
        [
            {"component": "random_seed", "version": str(result["random_seed"]), "notes": "model reproducibility"},
            {
                "component": "final_test_usage",
                "version": "acceptance_only",
                "notes": result["validation_config"]["final_test_usage"],
            },
        ]
    )
    _sheet_from_dicts(workbook, "模型版本", version_rows, ["component", "version", "notes"])

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    _remove_core_properties(output)


def run_ml_task(
    manifest: Path,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh_cache: bool = False,
) -> dict[str, Any]:
    config = validate_manifest(_read_manifest(manifest))
    benchmark = config["benchmark"]
    benchmark_adjustment = "index"
    if config["market"] == "cn_a" and benchmark["type"] == "market_index":
        benchmark_raw, benchmark_location, benchmark_cache, benchmark_provider = fetch_online_china_index_prices(
            benchmark["symbol"],
            config["start_date"],
            config["end_date"],
            cache_dir,
            refresh_cache,
        )
    elif benchmark.get("instrument", {}).get("market") == "us":
        api_key = os.environ.get("MARKETSIGNAL_TWELVE_DATA_API_KEY", "")
        if not api_key:
            raise MLTaskError("US market ML tasks require MARKETSIGNAL_TWELVE_DATA_API_KEY")
        benchmark_raw, benchmark_location, benchmark_cache, benchmark_provider = fetch_online_us_adjusted_prices(
            benchmark["instrument"]["provider_symbol"],
            config["start_date"],
            config["end_date"],
            api_key,
            30,
            cache_dir,
            refresh_cache,
            currency=benchmark["currency"],
        )
        benchmark_adjustment = "adjusted_all"
    else:
        benchmark_raw, benchmark_location, benchmark_cache, benchmark_provider = fetch_online_china_adjusted_prices(
            benchmark["instrument"],
            config["start_date"],
            config["end_date"],
            cache_dir,
            refresh_cache,
        )
        benchmark_adjustment = "qfq"
    benchmark_prices, benchmark_quality = clean_prices(benchmark_raw)
    benchmark_prices, benchmark_out_of_range = filter_prices_by_range(
        benchmark_prices, config["start_date"], config["end_date"]
    )
    for row in benchmark_prices:
        row["adjustment"] = benchmark_adjustment
    source_rows = [
        {
            "symbol": config["benchmark"]["symbol"],
            "market": config["benchmark"]["market"],
            "currency": config["benchmark"]["currency"],
            "dataset": "benchmark_prices",
            "provider": benchmark_provider,
            "status": "pass" if benchmark_prices else "fail",
            "cache_status": benchmark_cache,
            "source_location": benchmark_location,
            "raw_rows": benchmark_quality["raw_rows"],
            "clean_rows": benchmark_quality["clean_rows"],
            "output_rows": len(benchmark_prices),
            "notes": f"out_of_range={benchmark_out_of_range}; adjustment={benchmark_adjustment}; explicit benchmark",
        }
    ]
    if not benchmark_prices:
        raise MLTaskError("benchmark source returned no clean rows in the requested range")

    subjects: list[dict[str, Any]] = []
    for item in config["items"]:
        if config["market"] == "us":
            api_key = os.environ.get("MARKETSIGNAL_TWELVE_DATA_API_KEY", "")
            if not api_key:
                raise MLTaskError("US market ML tasks require MARKETSIGNAL_TWELVE_DATA_API_KEY")
            raw_prices, price_location, price_cache, price_provider = fetch_online_us_adjusted_prices(
                item["instrument"]["provider_symbol"],
                config["start_date"],
                config["end_date"],
                api_key,
                30,
                cache_dir,
                refresh_cache,
                currency=item["currency"],
            )
            stock_adjustment = "adjusted_all"
        else:
            raw_prices, price_location, price_cache, price_provider = fetch_online_china_adjusted_prices(
                item["instrument"],
                config["start_date"],
                config["end_date"],
                cache_dir,
                refresh_cache,
            )
            stock_adjustment = "qfq"
        prices, quality = clean_prices(raw_prices)
        prices, out_of_range = filter_prices_by_range(prices, config["start_date"], config["end_date"])
        for row in prices:
            row["adjustment"] = stock_adjustment
        if not prices:
            raise MLTaskError(f"{item['symbol']} returned no clean adjusted price rows")
        source_rows.append(
            {
                "symbol": item["symbol"],
                "market": item["market"],
                "currency": item["currency"],
                "dataset": "adjusted_prices",
                "provider": price_provider,
                "status": "pass",
                "cache_status": price_cache,
                "source_location": price_location,
                "raw_rows": quality["raw_rows"],
                "clean_rows": quality["clean_rows"],
                "output_rows": len(prices),
                "notes": f"adjustment={stock_adjustment}; out_of_range={out_of_range}",
            }
        )
        financials: list[dict[str, Any]] = []
        news: list[dict[str, Any]] = []
        announcements: list[dict[str, Any]] = []
        if config["include_context"]:
            financials, news, announcements, context_sources = _collect_context_for_market(
                item["instrument"],
                config["start_date"],
                config["end_date"],
                cache_dir,
                refresh_cache,
            )
            source_rows.extend(context_sources)
        subjects.append(
            {
                **{key: value for key, value in item.items() if key != "instrument"},
                "prices": prices,
                "financials": financials,
                "news": news,
                "announcements": announcements,
            }
        )

    single_asset = config["task_type"] == "single_asset"
    panel_rows, latest_rows, feature_names = build_excess_return_panel(
        subjects,
        benchmark_prices,
        benchmark_symbol=config["benchmark"]["symbol"],
        benchmark_type=config["benchmark"]["type"],
        benchmark_source=benchmark_provider,
        horizons=config["horizons"],
        single_asset=single_asset,
        market=config["market"],
        calendar=config["calendar"],
        timezone=config["timezone"],
        benchmark_market=benchmark["market"],
        benchmark_currency=benchmark["currency"],
        benchmark_calendar=config["calendar"],
        benchmark_timezone=config["timezone"],
    )
    result = run_ml_forecast_analysis(
        panel_rows,
        latest_rows,
        feature_names,
        benchmark_symbol=config["benchmark"]["symbol"],
        membership_policy=config["membership_policy"],
        final_test_dates=config["final_test_dates"],
        outer_test_dates=config["outer_test_dates"],
        outer_folds=config["outer_folds"],
        inner_validation_dates=config["inner_validation_dates"],
        minimum_training_dates=config["minimum_training_dates"],
        transaction_cost_bps=config["transaction_cost_bps"],
        slippage_bps=config["slippage_bps"],
        single_asset=single_asset,
        market=config["market"],
        calendar=config["calendar"],
        benchmark_market=benchmark["market"],
        benchmark_currency=benchmark["currency"],
        benchmark_adjustment=benchmark_adjustment,
    )
    for source_row in source_rows:
        source_row.setdefault("market", config["market"])
        source_row.setdefault("currency", config["items"][0]["currency"])
        source_row.setdefault("calendar", config["calendar"])
        source_row.setdefault("timezone", config["timezone"])
        source_row.setdefault("adjustment_method", "")
    for source_row in source_rows:
        if source_row.get("dataset") == "benchmark_prices":
            source_row["adjustment_method"] = benchmark_adjustment
        elif source_row.get("dataset") == "adjusted_prices":
            source_row["adjustment_method"] = "adjusted_all" if config["market"] == "us" else "qfq"
    output = config["output"]
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    build_ml_workbook(output, config, result, source_rows)
    return {
        "status": result["status"],
        "task_type": config["task_type"],
        "output": str(output),
        "subjects": len(subjects),
        "benchmark_symbol": config["benchmark"]["symbol"],
        "market": config["market"],
        "currency": config["items"][0]["currency"],
        "benchmark_currency": benchmark["currency"],
        "panel_rows": len(panel_rows),
        "features": len(feature_names),
        "forecasts": len(result["forecasts"]),
        "selected_models": {
            str(row["horizon"]): row["model_name"]
            for row in result["leaderboard"]
            if row["selected"]
        },
        "leakage_status": {row["check"]: row["status"] for row in result["leakage_checks"]},
        "component_versions": component_versions(),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train and validate multi-market panel or single-asset excess-return models"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.validate_only:
            config = validate_manifest(_read_manifest(args.manifest))
            summary = {
                "status": "valid",
                "name": config["name"],
                "subjects": len(config["items"]),
                "benchmark_symbol": config["benchmark"]["symbol"],
                "horizons": config["horizons"],
            }
        else:
            summary = run_ml_task(
                args.manifest,
                cache_dir=args.cache_dir,
                refresh_cache=args.refresh_cache,
            )
    except (MLTaskError, MLForecastError, PipelineError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
