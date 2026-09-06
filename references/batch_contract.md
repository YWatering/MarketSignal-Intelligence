# Stage-Four Batch Contract

Read this reference for multi-stock, portfolio, industry, theme, or repeatable task requests.

## Scope

The batch runner accepts an explicit list of stocks and applies the existing single-stock pipeline to every item. `industry` and `theme` tasks use user-supplied constituents; the runner does not invent or silently expand membership.

Supported task types:

- `portfolio`: a user-defined stock list.
- `industry`: a user-defined industry basket.
- `theme`: a user-defined topic or theme basket.

## Manifest

The manifest is YAML or JSON and uses batch contract version `1.0`.

```yaml
version: "1.0"
name: example-comparison
task_type: industry
group: example-group
mode: online
start_date: 2024-01-01
end_date: latest
forecast: true
forecast_horizon: 5
cross_validate_prices: true
cross_validation_tolerance_pct: 1.0
output_dir: outputs/example
summary_output: outputs/example_summary.xlsx
log_file: .cache/marketsignal/example.jsonl
state_file: .cache/marketsignal/example-state.json
execution:
  attempts: 2
  retry_delay_seconds: 1
  continue_on_error: true
schedule:
  interval_hours: 24
items:
  - symbol: "600519"
    market: cn_a
    label: example-a
  - symbol: "000858"
    market: cn_a
    label: example-b
```

Rules:

- `version` must equal `1.0`.
- `name`, `task_type`, and at least one item are required.
- A manifest supports at most 50 unique normalized stock identities.
- `mode` is `fixture` or `online`; formal forecasts require `online`.
- `end_date: latest` resolves to the local date when the task starts.
- Output names are stable and do not contain run dates.
- API credentials are supplied through the existing environment variables, not stored in the manifest.

## Failure Recovery

Each stock runs independently. `execution.attempts` controls whole-item retries from 1 to 5. Successful items remain successful when another item fails. A completed single-stock pipeline can retain `warning` when a source or quality check requires attention. With `continue_on_error: true`, the batch produces a `partial` summary instead of discarding completed work.

Structured JSONL logs contain a random run identifier, event sequence, item identity, attempt number, duration, status, and sanitized error. Logs do not store API credentials.

## Repeatable Execution

When `schedule.interval_hours` is configured, the runner stores the last fully completed `pass` or `warning` execution in `state_file`. A later invocation returns `skipped` until the interval has elapsed. `--force` bypasses the interval guard. The repository provides the repeatable task entrypoint; an external scheduler is responsible for invoking it.

## Summary Workbook

The batch workbook contains:

| Sheet | Purpose |
| --- | --- |
| `README` | Task type, mode, constituent policy, completion status, and batch contract version. |
| `主体任务` | Label, group, normalized symbol, market, attempts, per-item output, and isolated error. |
| `横向比较` | Latest close, period return, volatility, financial ratios, news tone, data coverage, selected forecast, forecast interval, source cross-check status, and item status. |
| `版本信息` | Skill, contracts, source adapters, forecast engine, models, and workbook template versions. |

The summary workbook is for cross-sectional review. Detailed prices, financial statements, news, notices, model evaluation, and backtests remain in each stock workbook.

Relative output, log, and state paths are resolved from the repository root so the same manifest behaves consistently when invoked by an external scheduler from another working directory.
