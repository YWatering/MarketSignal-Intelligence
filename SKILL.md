---
name: market-signal-intelligence
description: Collect and analyze stock market, financial news, and public sentiment data; clean and export traceable Excel workbooks and provide cautious market-analysis support. Use when users request multi-source market research, stock data collection, Excel reports, or price-analysis workflows.
metadata:
  short-description: Market, news, and stock analysis to Excel
---

# MarketSignal Intelligence

Use this skill for repeatable market-research workflows involving a stock, company, industry, keyword, or theme.

## Current Stage

Stage one provides:

- A structured input contract for a single stock and date range.
- Online adapters for Twelve Data daily prices and Yahoo Finance RSS news.
- Fixture mode for deterministic, offline demonstrations and tests.
- Basic cleaning, deduplication, validation, and traceable Excel export.

Price forecasting, financial statements, advanced sentiment analysis, multi-stock comparison, and scheduled research are planned for later stages. Do not invent those capabilities or produce a forecast when the required implementation is not available.

## Workflow

1. Parse the requested symbol, date range, data mode, and output path. If the stock or range is ambiguous, ask for the missing value.
2. Read [references/data_contract.md](references/data_contract.md) before changing fields or output sheets.
3. Use online mode for current research only when the required API key and network access are available. Use fixture mode for repeatable examples, tests, or offline work.
4. Run `scripts/marketsignal.py`, preserve source URLs and data-mode information, and inspect the returned quality summary.
5. Treat missing, invalid, duplicated, or source-failed records as quality signals. Do not silently replace unavailable data with guesses.
6. Deliver the workbook path and summarize row counts, source status, limitations, and any quality warnings.

## Commands

Install the project dependencies into a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Generate the deterministic stage-one example:

```bash
.venv/bin/python scripts/run_sample.py
```

Run an online single-stock collection after setting a Twelve Data key:

```bash
export MARKETSIGNAL_TWELVE_DATA_API_KEY="your-key"
.venv/bin/python scripts/marketsignal.py --symbol AAPL --start-date 2026-01-01 --end-date 2026-03-31 --mode online --output outputs/aapl_market_signal.xlsx
```

Run the validation suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Output Rules

- Keep raw and cleaned records logically separate in the processing flow.
- Include source, data range, row counts, limitations, and quality checks in the workbook.
- Preserve dates that are part of the business data, such as trading dates, publication times, and requested analysis ranges.
- Do not add document creation dates, update dates, generation dates, or similar metadata labels.
- Do not describe an analysis result as investment advice, a guaranteed return, or a certain price movement.
