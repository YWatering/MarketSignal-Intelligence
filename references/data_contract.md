# Stage-One Data Contract

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

The stage-one implementation handles one symbol per run. It does not yet calculate financial statements, advanced sentiment scores, or price forecasts.

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

## Normalized News Record

```json
{
  "published_at": "source-provided publication time",
  "title": "headline",
  "source": "publisher",
  "url": "https://example.com/article",
  "summary": "optional summary",
  "sentiment": "unclassified"
}
```

News records are deduplicated by URL when available, otherwise by title and publication time, and limited to the requested date range when publication time is available. Stage one preserves the source text and does not infer sentiment.

## Workbook Contract

The generated workbook contains these sheets:

| Sheet | Purpose |
| --- | --- |
| `README` | Request parameters, source status, limitations, and row counts. |
| `行情数据` | Cleaned daily price records. |
| `新闻舆情` | Cleaned news records and source links. |
| `数据质量` | Raw counts, cleaned counts, duplicate counts, invalid counts, out-of-range counts, and status. |

Every source-dependent sheet must retain source information. The workbook must not include document creation, update, or generation date labels.

## Error Behavior

- Invalid symbols, dates, output paths, or source payloads fail with a concise error and non-zero exit status.
- Missing online credentials fail before a network request.
- A failed source is reported as an error; it is never silently represented as a successful empty result.
- Fixture mode remains available for offline validation and reproducible examples.
