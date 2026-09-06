---
name: market-signal-intelligence
description: Collect real online single-stock data across China A shares, China B shares, Hong Kong stocks, and US stocks; clean prices, financial statements, notices, and news; run leakage-aware backtesting and price forecasts; export traceable Excel workbooks. Use when users request Chinese or US single-stock research, financial data, market sentiment, Excel reports, or explainable price forecasts.
metadata:
  short-description: Real-data stock research, backtesting, and forecasts to Excel
---

# MarketSignal Intelligence

Use this skill for repeatable market-research workflows involving one stock, its market data, financial statements, notices, and related news.

## Current Stage

Stage three provides all stage-two collection and governance capabilities plus:

- A structured input contract for one stock, one market, and an optional date range.
- Symbol parsing for China A shares (`cn_a`), China B shares (`cn_b`), Hong Kong stocks (`hk`), and US stocks (`us`).
- AKShare adapters for Chinese-market daily prices, stock news, A/B-share financial statements, Hong Kong annual financial statements, and A/B-share notices.
- Twelve Data and Yahoo Finance RSS adapters for US prices and news.
- SEC EDGAR adapters for US ticker-to-CIK resolution, company facts, and recent 10-K, 10-Q, and 8-K filings.
- Fixture mode for deterministic US AAPL demonstrations and tests; Chinese-market online mode uses live market adapters.
- Basic cleaning, deduplication, range filtering, source tracking, local response caching, and traceable Excel export.
- Rule-based Chinese and English news tone classification with matched-keyword evidence.
- Market, financial, news, and notice indicators with calculation methods.
- A last-close persistence baseline and an explainable multi-signal ridge regression model.
- Features derived from cleaned online prices, financial filings, news tone, and notices produced by the same stage-two run.
- Expanding-window one-step-ahead walk-forward validation with MAE, RMSE, MAPE, return MAE, and direction accuracy.
- Model selection by validation RMSE, future point forecasts, empirical prediction intervals, actual-versus-predicted backtest rows, and standardized feature coefficients.

Stage three does not provide causal claims, guaranteed returns, advanced sentiment models, multi-stock comparison, exchange-holiday calendars, or scheduled research. Do not present a forecast as investment advice or certainty.

## Workflow

1. Parse the requested symbol, market, date range, data mode, and output path. Infer the market only when the symbol format is unambiguous; otherwise ask for `cn_a`, `cn_b`, `hk`, or `us`.
2. Read [references/data_contract.md](references/data_contract.md) before changing fields, market routing, or output sheets. For prediction requests, also read [references/forecasting.md](references/forecasting.md).
3. Use online mode for current Chinese-market research when AKShare and network access are available. Use online US mode only when the Twelve Data key, SEC User-Agent, and required network access are available.
4. Run `scripts/marketsignal.py` with the normalized symbol and explicit `--market` when useful. Add `--forecast` only in online mode and use a date range long enough to provide at least the configured minimum history.
5. For Chinese markets, use AKShare adapters and inspect the returned source and cache status. For US markets, provide `MARKETSIGNAL_SEC_USER_AGENT` or `--sec-user-agent` with an application name and contact email, and provide `MARKETSIGNAL_TWELVE_DATA_API_KEY` for Twelve Data.
6. For forecasting, use only records returned and cleaned by the current stage-two run. Reject fixture rows, enforce point-in-time availability, and keep future external features fixed at the last real data cutoff.
7. Treat missing, invalid, duplicated, out-of-range, unsupported, source-failed, or insufficient-history records as quality signals. Do not silently replace unavailable data with guesses.
8. Deliver the workbook path and summarize market, currency, data cutoff, row counts, selected model, validation metrics, forecast interval, source status, limitations, and quality warnings.

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
- For forecasts, include data cutoff, training and validation ranges, baseline comparison, model version, validation method, error metrics, interval method, backtest details, and feature coefficients.
- Never run a formal forecast from fixture mode or fixture rows. Fail clearly when clean online price history is below the configured minimum.
- Prevent look-ahead by using only prices through the feature date and only financial, news, and notice records available on or before that date.
- Preserve business dates such as trading dates, publication times, report periods, filing dates, and requested analysis ranges.
- Do not add document creation dates, update dates, generation dates, or similar metadata labels.
- Do not describe an analysis result as investment advice, a guaranteed return, or a certain price movement.
