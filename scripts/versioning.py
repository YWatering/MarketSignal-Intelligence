"""Central component versions for reproducible MarketSignal outputs."""

from __future__ import annotations


SKILL_VERSION = "8.0"
DATA_CONTRACT_VERSION = "8.0"
BATCH_CONTRACT_VERSION = "2.0"
ML_PANEL_CONTRACT_VERSION = "1.0"
SINGLE_ASSET_CONTRACT_VERSION = "1.0"
MULTI_MARKET_ML_CONTRACT_VERSION = "2.0"
SOURCE_ADAPTER_VERSION = "7.0"
FORECAST_ENGINE_VERSION = "5.0"
ML_FORECAST_ENGINE_VERSION = "2.0"
SINGLE_ASSET_ENGINE_VERSION = "2.0"
ML_FEATURE_VERSION = "2.0"
MARKET_CALENDAR_VERSION = "1.0"
WORKBOOK_TEMPLATE_VERSION = "8.0"
BASELINE_MODEL_VERSION = "persistence-v1"
MOVING_AVERAGE_MODEL_VERSION = "moving-average-v1"
HISTORICAL_MEAN_MODEL_VERSION = "historical-mean-v1"
EXPONENTIAL_SMOOTHING_MODEL_VERSION = "ewma-v1"
RIDGE_MODEL_VERSION = "ridge-v1"
ZERO_EXCESS_MODEL_VERSION = "zero-excess-v1"
EXCESS_MEAN_MODEL_VERSION = "excess-mean-v1"
PANEL_RIDGE_MODEL_VERSION = "panel-ridge-v1"
ELASTIC_NET_MODEL_VERSION = "elastic-net-v1"
RANDOM_FOREST_MODEL_VERSION = "random-forest-v1"
HIST_GRADIENT_BOOSTING_MODEL_VERSION = "hist-gradient-boosting-v1"


def component_versions() -> dict[str, str]:
    return {
        "skill": SKILL_VERSION,
        "data_contract": DATA_CONTRACT_VERSION,
        "batch_contract": BATCH_CONTRACT_VERSION,
        "ml_panel_contract": ML_PANEL_CONTRACT_VERSION,
        "single_asset_contract": SINGLE_ASSET_CONTRACT_VERSION,
        "multi_market_ml_contract": MULTI_MARKET_ML_CONTRACT_VERSION,
        "source_adapters": SOURCE_ADAPTER_VERSION,
        "forecast_engine": FORECAST_ENGINE_VERSION,
        "ml_forecast_engine": ML_FORECAST_ENGINE_VERSION,
        "single_asset_engine": SINGLE_ASSET_ENGINE_VERSION,
        "ml_features": ML_FEATURE_VERSION,
        "market_calendars": MARKET_CALENDAR_VERSION,
        "workbook_template": WORKBOOK_TEMPLATE_VERSION,
        "last_close_baseline": BASELINE_MODEL_VERSION,
        "moving_average_baseline": MOVING_AVERAGE_MODEL_VERSION,
        "historical_mean_baseline": HISTORICAL_MEAN_MODEL_VERSION,
        "exponential_smoothing_baseline": EXPONENTIAL_SMOOTHING_MODEL_VERSION,
        "multisignal_ridge": RIDGE_MODEL_VERSION,
        "zero_excess": ZERO_EXCESS_MODEL_VERSION,
        "excess_historical_mean": EXCESS_MEAN_MODEL_VERSION,
        "panel_ridge": PANEL_RIDGE_MODEL_VERSION,
        "elastic_net": ELASTIC_NET_MODEL_VERSION,
        "random_forest": RANDOM_FOREST_MODEL_VERSION,
        "hist_gradient_boosting": HIST_GRADIENT_BOOSTING_MODEL_VERSION,
    }
