---
name: market-signal-intelligence
description: Collect and analyze single-stock market, SEC financial, filing, and news data; clean and export traceable Excel workbooks with basic indicators. Use when users request single-stock market research, SEC-backed financial reports, Excel reports, or price-analysis workflows.
metadata:
  short-description: Market, news, and stock analysis to Excel
---

# MarketSignal Intelligence

Use this skill for repeatable market-research workflows involving a stock, company, industry, keyword, or theme.

## Current Stage

Stage two provides:

- A structured input contract for a single stock and date range.
- Online adapters for Twelve Data daily prices and Yahoo Finance RSS news.
- SEC EDGAR adapters for ticker-to-CIK resolution, company facts, and recent 10-K, 10-Q, and 8-K filings.
- Fixture mode for deterministic, offline demonstrations and tests.
- Basic cleaning, deduplication, range filtering, source tracking, local response caching, and traceable Excel export.
- Rule-based news tone classification with matched-keyword evidence.
- Market, financial, news, and filing indicators with calculation methods.

Stage two does not provide price forecasting, advanced sentiment analysis, multi-stock comparison, or scheduled research. Financial data is limited to configured US-GAAP concepts and SEC filing forms. Do not invent unavailable capabilities or produce a forecast when the required implementation is not available.

## Workflow

1. Parse the requested symbol, date range, data mode, and output path. If the stock or range is ambiguous, ask for the missing value.
2. Read [references/data_contract.md](references/data_contract.md) before changing fields or output sheets.
3. Use online mode for current research only when the required API key and network access are available. Use fixture mode for repeatable examples, tests, or offline work.
4. Run `scripts/marketsignal.py`, preserve source URLs and data-mode information, and inspect the returned quality summary.
5. For online mode, provide `MARKETSIGNAL_SEC_USER_AGENT` or `--sec-user-agent` with an application name and contact email, and provide `MARKETSIGNAL_TWELVE_DATA_API_KEY` for Twelve Data.
6. Treat missing, invalid, duplicated, out-of-range, or source-failed records as quality signals. Do not silently replace unavailable data with guesses.
7. Deliver the workbook path and summarize row counts, source status, cache status, limitations, and any quality warnings.

## Commands

Install the project dependencies into a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Generate the deterministic stage-two example:

```bash
.venv/bin/python scripts/run_sample.py
```

Run an online single-stock collection after setting a Twelve Data key:

```bash
export MARKETSIGNAL_TWELVE_DATA_API_KEY="your-key"
export MARKETSIGNAL_SEC_USER_AGENT="MarketSignal Intelligence contact@example.com"
.venv/bin/python scripts/marketsignal.py --symbol AAPL --start-date YYYY-MM-DD --end-date YYYY-MM-DD --mode online --output outputs/aapl_market_signal.xlsx
```

Run the validation suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Output Rules

- Keep raw and cleaned records logically separate in the processing flow.
- Include source, data range, row counts, limitations, and quality checks in the workbook.
- Include entity mapping, financial-reporting period, filing date, accession number, calculation method, and source location where applicable.
- Preserve dates that are part of the business data, such as trading dates, publication times, and requested analysis ranges.
- Do not add document creation dates, update dates, generation dates, or similar metadata labels.
- Do not describe an analysis result as investment advice, a guaranteed return, or a certain price movement.
