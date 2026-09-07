---
name: market-signal-intelligence
description: Collect and compare real stock data across China A shares, China B shares, Hong Kong stocks, and US stocks; clean prices, financial statements, notices, and news; validate sources; run leakage-aware price forecasts, single-asset benchmark-relative excess-return forecasts, or A-share panel machine-learning forecasts; export traceable Excel workbooks. Use for stock, portfolio, user-defined industry or theme research, repeatable task manifests, market sentiment, explainable forecasts, or strict out-of-sample model comparison.
metadata:
  short-description: Real-data stock research, comparison, and forecasts to Excel
---

# MarketSignal Intelligence

Use this skill for repeatable market-research workflows involving one or more explicitly identified stocks, their market data, financial statements, notices, and related news.

## Current Stage

Stage seven provides all earlier collection, governance, and forecasting capabilities plus:

- A structured input contract for one stock, one market, and an optional date range.
- Symbol parsing for China A shares (`cn_a`), China B shares (`cn_b`), Hong Kong stocks (`hk`), and US stocks (`us`).
- AKShare adapters for Chinese-market daily prices, stock news, A/B-share financial statements, Hong Kong annual financial statements, and A/B-share notices.
- Twelve Data and Yahoo Finance RSS adapters for US prices and news.
- SEC EDGAR adapters for US ticker-to-CIK resolution, company facts, and recent 10-K, 10-Q, and 8-K filings.
- Fixture mode for deterministic US AAPL demonstrations and tests; Chinese-market online mode uses live market adapters.
- Basic cleaning, deduplication, range filtering, source tracking, local response caching, and traceable Excel export.
- Rule-based Chinese and English news tone classification with matched-keyword evidence.
- Market, financial, news, and notice indicators with calculation methods.
- Last-close, moving-average, historical-mean, exponential-smoothing, and explainable multi-signal ridge candidate models.
- Features derived from cleaned online prices, financial filings, news tone, and notices produced by the same stage-two run.
- Expanding-window one-step-ahead walk-forward validation with price, return, direction, bias, and empirical interval metrics.
- Balanced price-and-direction model selection with fixed improvement gates, multiple robustness windows, volatility slices, explicit selection reasons, and cost sensitivity analysis.
- Future point forecasts, empirical prediction intervals, actual-versus-predicted backtest rows, and standardized feature coefficients for all candidates.
- Versioned manifests for multi-stock portfolios and user-defined industry or theme baskets.
- Per-item retries, failure isolation, structured JSONL logs, and interval-based repeatable execution state.
- Optional primary-versus-secondary price validation for China A shares and Hong Kong stocks.
- Central versions for the Skill, contracts, source adapters, forecast engine, models, and Excel templates.
- Forward-adjusted A-share price collection and explicit benchmark-index history for excess-return labels.
- Versioned multi-stock machine-learning manifests with supplied industry, size, and membership metadata.
- One-day and five-day excess-return targets, outperform probabilities, and same-cutoff cross-sectional ranks.
- Zero-excess, historical-mean, panel Ridge, Elastic Net, Random Forest, and histogram gradient boosting candidates.
- Purged nested chronological validation with a locked outer candidate and an untouched final acceptance holdout.
- Regression, rank, direction, probability-calibration, grouped-spread, turnover, cost, and drawdown evaluation.
- Model states and rejection reasons that allow simple models to remain selected when machine learning fails its gates.
- Global feature importance, model-agnostic local prediction differences, and explicit leakage checks.
- A separate `single_asset` task type for one A-share stock relative to one explicitly supplied benchmark index.
- Single-stock time-series excess-return targets, outperform probability, direction, empirical intervals, and relative-to-benchmark cost evaluation.
- Single-stock validation that retains chronological purging and final-holdout governance while omitting cross-sectional ranks and grouped portfolio tests.

Stage six and seven do not automatically discover industry or theme constituents, reconstruct historical index membership without supplied records, run an always-on scheduler, provide causal claims, guarantee returns, implement advanced sentiment models, or apply exchange-holiday calendars. Do not present a forecast as investment advice or certainty.

## Workflow

1. Decide whether the request is a single-stock task or an explicit multi-stock, portfolio, industry, or theme task. Do not infer basket constituents.
2. For a single stock, read [references/data_contract.md](references/data_contract.md) before changing fields, market routing, or output sheets. For price prediction requests, also read [references/forecasting.md](references/forecasting.md).
3. For multiple stocks or repeatable tasks, read [references/batch_contract.md](references/batch_contract.md). For source consistency, retries, logs, scheduling state, or versions, read [references/operations.md](references/operations.md).
4. Use online mode for current Chinese-market research when AKShare and network access are available. Use online US mode only when the Twelve Data key, SEC User-Agent, and required network access are available.
5. Run `scripts/marketsignal.py` for one stock, `scripts/market_batch.py` for a stage-five manifest, or `scripts/market_ml.py` for a stage-six panel or stage-seven `single_asset` task. Read [references/ml_forecasting.md](references/ml_forecasting.md) before changing machine-learning targets, splits, gates, or sheets.
6. For Chinese markets, inspect source and cache status. Request price cross-validation when the user needs source consistency evidence; unsupported markets must be reported explicitly.
7. For forecasting, use only records returned and cleaned by the current single-stock run. Reject fixture rows, enforce point-in-time availability, keep future external features fixed at the last real data cutoff, and inspect `模型评估`, `模型稳健性`, and `成本敏感性` before interpreting the selected model.
8. Treat missing, invalid, duplicated, out-of-range, unsupported, source-failed, mismatched, or insufficient-history records as quality signals. Keep completed batch items when another item fails.
9. Deliver all workbook paths and summarize per-item status, attempts, source validation, market, currency, data cutoff, row counts, selected model, validation metrics, forecast interval, limitations, and quality warnings.

## Commands

Install project dependencies into a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run a Chinese A-share report:

```bash
.venv/bin/python scripts/marketsignal.py --symbol 600519 --market cn_a --mode online --output outputs/maotai_market_signal.xlsx
```

Run a real-data Chinese A-share forecast:

```bash
.venv/bin/python scripts/marketsignal.py --symbol 600519 --market cn_a --start-date 2024-01-01 --end-date YYYY-MM-DD --mode online --forecast --forecast-horizon 5 --output outputs/maotai_forecast.xlsx
```

Run a versioned multi-stock or industry task:

```bash
.venv/bin/python scripts/market_batch.py --manifest examples/china_liquor.yaml --dry-run
.venv/bin/python scripts/market_batch.py --manifest examples/china_liquor.yaml --force
```

Run the stage-six A-share excess-return panel:

```bash
.venv/bin/python scripts/market_ml.py --manifest examples/china_a_ml_panel.yaml --validate-only
.venv/bin/python scripts/market_ml.py --manifest examples/china_a_ml_panel.yaml
```

Run the stage-seven single-asset excess-return task:

```bash
.venv/bin/python scripts/market_ml.py --manifest examples/china_maotai_single_excess_return.yaml --validate-only
.venv/bin/python scripts/market_ml.py --manifest examples/china_maotai_single_excess_return.yaml
```

Run a Hong Kong stock report:

```bash
.venv/bin/python scripts/marketsignal.py --symbol 00700.HK --market hk --mode online --output outputs/tencent_market_signal.xlsx
```

Run the deterministic US example:

```bash
.venv/bin/python scripts/run_sample.py
```

Run the validation suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Data Routing

| Market | Prices | News | Financials | Notices |
| --- | --- | --- | --- | --- |
| `cn_a` | AKShare A-share history with Tencent fallback | AKShare stock news | AKShare profit, balance-sheet, and cash-flow reports | AKShare individual notices |
| `cn_b` | AKShare B-share daily history | AKShare stock news | AKShare profit, balance-sheet, and cash-flow reports | AKShare individual notices when usable rows are returned |
| `hk` | AKShare Hong Kong history with daily fallback | AKShare stock news | AKShare annual Hong Kong reports and company profile | Not configured in this stage |
| `us` | Twelve Data | Yahoo Finance RSS | SEC Company Facts | SEC submissions |

## Output Rules

- Keep raw and cleaned records logically separate in the processing flow.
- Include source, market, currency, data range, row counts, limitations, and quality checks in the workbook.
- Include entity mapping, financial-reporting period, filing or notice date, accession number when available, calculation method, and source location where applicable.
- For forecasts, include data cutoff, training and validation ranges, all candidate models, baseline comparison, model version, validation method, error metrics, selection thresholds and reasons, robustness windows, cost assumptions, interval method, backtest details, and feature coefficients.
- For multi-stock tasks, require an explicit versioned manifest and retain every normalized constituent, attempt count, output path, and isolated failure.
- For stage-six tasks, require forward-adjusted stock prices, one explicit benchmark, one common prediction cutoff, purged nested time validation, and a final holdout used only for acceptance.
- For stage-seven `single_asset` tasks, require one explicit stock, one explicit benchmark, common stock/index dates, point-in-time features, purged nested time validation, and a final holdout used only for acceptance.
- For stage-seven tasks, do not create cross-sectional ranks, high-minus-low groups, or grouped-spread results; evaluate the predicted direction as a single-stock relative-to-benchmark research signal.
- Do not promote a machine-learning model when fixed-universe survivorship risk or another leakage check remains unresolved.
- When cross-validation is requested, compare aligned closes without replacing the primary series; report unsupported adapters or mismatches.
- Include the central component versions in every workbook and keep output schemas compatible with the declared contract version.
- Never run a formal forecast from fixture mode or fixture rows. Fail clearly when clean online price history is below the configured minimum.
- Prevent look-ahead by using only prices through the feature date and only financial, news, and notice records available on or before that date.
- Preserve business dates such as trading dates, publication times, report periods, filing dates, and requested analysis ranges.
- Do not add document creation dates, update dates, generation dates, or similar metadata labels.
- Do not describe an analysis result as investment advice, a guaranteed return, or a certain price movement.
