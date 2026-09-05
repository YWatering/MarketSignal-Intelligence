---
name: market-signal-intelligence
description: Collect and analyze single-stock data across China A shares, China B shares, Hong Kong stocks, and US stocks; clean prices, financial statements, notices, and news; export traceable Excel workbooks with basic indicators. Use when users request Chinese or US single-stock research, financial data, market sentiment, Excel reports, or price-analysis workflows.
metadata:
  short-description: Multi-market stock research and financial analysis to Excel
---

# MarketSignal Intelligence

Use this skill for repeatable market-research workflows involving one stock, its market data, financial statements, notices, and related news.

## Current Stage

Stage two provides:

- A structured input contract for one stock, one market, and an optional date range.
- Symbol parsing for China A shares (`cn_a`), China B shares (`cn_b`), Hong Kong stocks (`hk`), and US stocks (`us`).
- AKShare adapters for Chinese-market daily prices, stock news, A/B-share financial statements, Hong Kong annual financial statements, and A/B-share notices.
- Twelve Data and Yahoo Finance RSS adapters for US prices and news.
- SEC EDGAR adapters for US ticker-to-CIK resolution, company facts, and recent 10-K, 10-Q, and 8-K filings.
- Fixture mode for deterministic US AAPL demonstrations and tests; Chinese-market online mode uses live market adapters.
- Basic cleaning, deduplication, range filtering, source tracking, local response caching, and traceable Excel export.
- Rule-based Chinese and English news tone classification with matched-keyword evidence.
- Market, financial, news, and notice indicators with calculation methods.

Stage two does not provide price forecasting, advanced sentiment analysis, multi-stock comparison, or scheduled research. Do not invent unavailable capabilities or produce a forecast when the required implementation is not available.

## Workflow

1. Parse the requested symbol, market, date range, data mode, and output path. Infer the market only when the symbol format is unambiguous; otherwise ask for `cn_a`, `cn_b`, `hk`, or `us`.
2. Read [references/data_contract.md](references/data_contract.md) before changing fields, market routing, or output sheets.
3. Use online mode for current Chinese-market research when AKShare and network access are available. Use online US mode only when the Twelve Data key, SEC User-Agent, and required network access are available.
4. Run `scripts/marketsignal.py` with the normalized symbol and explicit `--market` when useful. Preserve market, currency, source URLs, provider names, and data-mode information.
5. For Chinese markets, use AKShare adapters and inspect the returned source and cache status. For US markets, provide `MARKETSIGNAL_SEC_USER_AGENT` or `--sec-user-agent` with an application name and contact email, and provide `MARKETSIGNAL_TWELVE_DATA_API_KEY` for Twelve Data.
6. Treat missing, invalid, duplicated, out-of-range, unsupported, or source-failed records as quality signals. Do not silently replace unavailable data with guesses.
7. Deliver the workbook path and summarize market, currency, row counts, source status, cache status, limitations, and quality warnings.

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
- Preserve business dates such as trading dates, publication times, report periods, filing dates, and requested analysis ranges.
- Do not add document creation dates, update dates, generation dates, or similar metadata labels.
- Do not describe an analysis result as investment advice, a guaranteed return, or a certain price movement.
