# Stage-Two Data Contract

## Request

The command-line interface accepts:

| Field | Required | Description |
| --- | --- | --- |
| `symbol` | yes | Stock symbol accepted by the selected price source. |
| `start_date` | no | Inclusive analysis start in `YYYY-MM-DD` format. |
| `end_date` | no | Inclusive analysis end in `YYYY-MM-DD` format. |
| `mode` | yes | `fixture` for deterministic local data or `online` for source adapters. |
| `output` | yes | `.xlsx` output path. |
| `twelve_data_api_key` | online | Twelve Data key, or `MARKETSIGNAL_TWELVE_DATA_API_KEY`. |
| `sec_user_agent` | online | Application name plus contact email, or `MARKETSIGNAL_SEC_USER_AGENT`. |
| `cache_dir` | no | Local response cache directory. |
| `refresh_cache` | no | Ignore existing cached responses for this run. |

The stage-two implementation handles one symbol per run. It includes configured US SEC company facts, recent US SEC filings, basic derived indicators, and transparent rule-based news tone labels. It does not yet provide price forecasts or advanced sentiment analysis.

## Normalized Price Record

```json
{
  "date": "YYYY-MM-DD",
  "open": 0.0,
  "high": 0.0,
  "low": 0.0,
  "close": 0.0,
  "volume": 0,
  "source": "source-name"
}
```

Records are sorted by date, deduplicated by date, limited to the requested date range, and rejected when required numeric fields are invalid or when `high < low`.

## Entity Record

```json
{
  "symbol": "AAPL",
  "company_name": "Company name",
  "cik": "0000000000",
  "exchange": "exchange",
  "sic": "sic code",
  "sic_description": "industry description",
  "source": "SEC EDGAR"
}
```

The entity record is resolved from the SEC ticker-to-CIK mapping and the company's submissions metadata.

## Normalized News Record

```json
{
  "published_at": "source-provided publication time",
  "title": "headline",
  "source": "publisher",
  "url": "https://example.com/article",
  "summary": "optional summary",
  "sentiment": "neutral",
  "sentiment_basis": "matched keyword or no configured keyword matched"
}
```

News records are deduplicated by URL when available, otherwise by title and publication time, and limited to the requested date range when publication time is available. The source text is preserved alongside the stage-two rule-based tone label.

Stage two applies a small, transparent English keyword rule to title and summary text. It stores the classification and matched keywords; this is not a general sentiment model.

## Financial Record

```json
{
  "period_start": "YYYY-MM-DD or empty for point-in-time facts",
  "period_end": "YYYY-MM-DD",
  "filed_date": "YYYY-MM-DD",
  "form": "10-Q",
  "metric": "revenue",
  "metric_label": "Revenue",
  "value": 0.0,
  "unit": "USD",
  "accession_number": "0000000000-00-000000",
  "source": "SEC companyfacts"
}
```

Financial rows retain period start, period end, and filing availability date. Period start distinguishes quarter-only, year-to-date, and annual values that share an end date. When a market analysis range is provided, reports filed after `end_date` are excluded; reports filed before the market window remain available as the latest known fundamental information.

## Announcement Record

Announcement rows include filing date, report date, form, title, accession number, SEC archive URL, and source. Stage two includes `10-K`, `10-Q`, and `8-K` forms.

## Workbook Contract

The generated workbook contains these sheets:

| Sheet | Purpose |
| --- | --- |
| `README` | Request parameters, entity summary, source status, limitations, and row counts. |
| `主体信息` | Symbol, company name, CIK, exchange, SIC, and source. |
| `行情数据` | Cleaned daily price records. |
| `财务数据` | Cleaned SEC company facts with period, filing, metric, unit, and accession number. |
| `新闻舆情` | Cleaned news records, links, rule-based tone, and evidence. |
| `公告数据` | Cleaned SEC 10-K, 10-Q, and 8-K filing history. |
| `指标分析` | Derived market, financial, news, and filing indicators with methods. |
| `数据来源` | Provider, mode, cache status, source location, raw, clean, and output row counts, and notes. |
| `数据质量` | Raw counts, cleaned counts, output counts, duplicate counts, invalid counts, out-of-range counts, and status. |

Every source-dependent sheet must retain source information. The workbook must not include document creation, update, or generation date labels.

## Error Behavior

- Invalid symbols, dates, output paths, or source payloads fail with a concise error and non-zero exit status.
- Missing online credentials fail before a network request.
- SEC online requests require a user agent containing a contact email.
- A failed source is reported as an error; it is never silently represented as a successful empty result.
- Fixture mode remains available for offline validation and reproducible examples.
