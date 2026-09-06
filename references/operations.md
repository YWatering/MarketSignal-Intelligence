# Stage-Five Operations and Versioning

Read this reference when diagnosing source consistency, retries, logs, scheduled execution, or component compatibility.

## Price Cross-Validation

`--cross-validate-prices` requests a secondary daily-price adapter when one is configured:

- China A shares select an independent alternate from the Eastmoney, Tencent, and Sina routes exposed through AKShare.
- Hong Kong stocks compare the two configured AKShare history routes.
- China B shares and US stocks currently report `not_supported` because no independent secondary adapter is configured.

The comparison aligns cleaned close prices by trading date and reports overlap count, mean and maximum percentage difference, rows outside tolerance, and status. A mismatch produces a warning; it does not replace the primary series or average conflicting values.

## Retry and Isolation

Batch recovery retries the complete single-stock task. This preserves the single-stock transaction boundary: a workbook is considered complete only after all required collection, cleaning, optional forecasting, and export steps succeed. The existing price adapters may also fall back between supported source routes.

Do not retry indefinitely. The manifest limits attempts to five and records each failure before continuing or stopping according to `continue_on_error`.

## Structured Logs

JSONL events use these common fields:

```json
{
  "run_id": "random-run-identifier",
  "sequence": 1,
  "event": "attempt_started",
  "task_name": "example-comparison",
  "symbol": "600519.SH",
  "market": "cn_a",
  "attempt": 1
}
```

File order and `sequence` define event order. Durations use seconds. Errors inherit URL redaction from the single-stock pipeline. Do not place credentials in task labels, paths, or manifest fields.

## Version Registry

`scripts/versioning.py` is the source of truth for:

- Skill version.
- Single-stock data contract version.
- Batch manifest contract version.
- Source adapter version.
- Forecast engine version.
- Workbook template version.
- All candidate baseline model versions and the ridge model version.

Every single-stock and batch workbook contains a `版本信息` sheet. Change a version when its external behavior, schema, model implementation, or output contract changes. Documentation and tests must be updated in the same change.

Version identifiers describe compatibility; they are not creation or generation dates.
