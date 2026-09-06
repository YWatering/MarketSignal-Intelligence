"""Central component versions for reproducible MarketSignal outputs."""

from __future__ import annotations


SKILL_VERSION = "5.0"
DATA_CONTRACT_VERSION = "5.0"
BATCH_CONTRACT_VERSION = "2.0"
SOURCE_ADAPTER_VERSION = "4.0"
FORECAST_ENGINE_VERSION = "5.0"
WORKBOOK_TEMPLATE_VERSION = "5.0"
BASELINE_MODEL_VERSION = "persistence-v1"
MOVING_AVERAGE_MODEL_VERSION = "moving-average-v1"
HISTORICAL_MEAN_MODEL_VERSION = "historical-mean-v1"
EXPONENTIAL_SMOOTHING_MODEL_VERSION = "ewma-v1"
RIDGE_MODEL_VERSION = "ridge-v1"


def component_versions() -> dict[str, str]:
    return {
        "skill": SKILL_VERSION,
        "data_contract": DATA_CONTRACT_VERSION,
        "batch_contract": BATCH_CONTRACT_VERSION,
        "source_adapters": SOURCE_ADAPTER_VERSION,
        "forecast_engine": FORECAST_ENGINE_VERSION,
        "workbook_template": WORKBOOK_TEMPLATE_VERSION,
        "last_close_baseline": BASELINE_MODEL_VERSION,
        "moving_average_baseline": MOVING_AVERAGE_MODEL_VERSION,
        "historical_mean_baseline": HISTORICAL_MEAN_MODEL_VERSION,
        "exponential_smoothing_baseline": EXPONENTIAL_SMOOTHING_MODEL_VERSION,
        "multisignal_ridge": RIDGE_MODEL_VERSION,
    }
