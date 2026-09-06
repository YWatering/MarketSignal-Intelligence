"""Central component versions for reproducible MarketSignal outputs."""

from __future__ import annotations


SKILL_VERSION = "4.0"
DATA_CONTRACT_VERSION = "4.0"
BATCH_CONTRACT_VERSION = "1.0"
SOURCE_ADAPTER_VERSION = "4.0"
FORECAST_ENGINE_VERSION = "3.1"
WORKBOOK_TEMPLATE_VERSION = "4.0"
BASELINE_MODEL_VERSION = "persistence-v1"
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
        "multisignal_ridge": RIDGE_MODEL_VERSION,
    }
