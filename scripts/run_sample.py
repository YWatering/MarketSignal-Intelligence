#!/usr/bin/env python3
"""Generate the deterministic compatibility example workbook."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from marketsignal import run_pipeline  # noqa: E402


def main() -> int:
    output = ROOT / "outputs" / "aapl_market_signal.xlsx"
    summary = run_pipeline(
        symbol="AAPL",
        start_date="2026-08-03",
        end_date="2026-08-14",
        mode="fixture",
        output=output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
