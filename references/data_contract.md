# Stage-Two Data Contract

## Request

The command-line interface accepts:

| Field | Required | Description |
| --- | --- | --- |
| `symbol` | yes | One stock code. Supports China A shares, China B shares, Hong Kong stocks, and US stocks. |
| `market` | no | `cn_a`, `cn_b`, `hk`, or `us`; aliases such as `A股`, `B股`, `港股`, and `美股` are accepted by the Python API. |
| `start_date` | no | Inclusive analysis start in `YYYY-MM-DD` format. |
| `end_date` | no | Inclusive analysis end in `YYYY-MM-DD` format. |
| `mode` | yes | `fixture` for deterministic local US AAPL data or `online` for market adapters. |
| `output` | yes | `.xlsx` output path. |
| `twelve_data_api_key` | US online | Twelve Data key, or `MARKETSIGNAL_TWELVE_DATA_API_KEY`. |
| `sec_user_agent` | US online | Application name plus contact email, or `MARKETSIGNAL_SEC_USER_AGENT`. |
| `cache_dir` | no | Local response cache directory. |
| `refresh_cache` | no | Ignore existing cached responses for this run. |

The implementation handles one symbol per run. Market is inferred from common formats when possible:

| Input | Normalized symbol | Market | Currency |
| --- | --- | --- | --- |
| `600519` or `600519.SH` | `600519.SH` | `cn_a` | CNY |
| `900901` | `900901.SH` | `cn_b` | USD |
| `200002.SZ` | `200002.SZ` | `cn_b` | HKD |
| `00700` or `00700.HK` | `00700.HK` | `hk` | HKD |
| `AAPL` or `NASDAQ:AAPL` | `AAPL` | `us` | USD |

Conflicting market and exchange suffixes fail early. The pipeline does not provide price forecasts or advanced sentiment analysis.

## Normalized Price Record

```json
{
  "date": "YYYY-MM-DD",
  "open": 0.0,
  "high": 0.0,
  "low": 0.0,
  "close": 0.0,
  "volume": 0,
  "source": "source-name",
  "market": "cn_a",
  "currency": "CNY"
}
```

Records are sorted by date, deduplicated by date, limited to the requested date range, and rejected when required numeric fields are invalid or when `high < low`.

## Entity Record

```json
{
  "symbol": "600519.SH",
  "provider_symbol": "600519",
  "market": "cn_a",
  "market_name": "中国A股",
  "company_name": "公司名称",
  "cik": "",
  "exchange": "SSE",
  "sic": "",
  "sic_description": "",
  "currency": "CNY",
  "source": "AKShare"
}
```

For US stocks, CIK, SIC, and SIC description come from SEC EDGAR when available. For Chinese markets, the entity name and exchange come from AKShare or the normalized instrument; CIK and SIC fields remain empty.

## Normalized News Record

```json
{
  "published_at": "source-provided publication time",
  "title": "headline",
  "source": "publisher",
  "url": "https://example.com/article",
  "summary": "optional summary",
  "sentiment": "neutral",
  "sentiment_basis": "matched keyword or no configured keyword matched",
  "market": "cn_a",
  "currency": "CNY"
}
```

News records are deduplicated by URL when available, otherwise by title and publication time, and limited to the requested date range when publication time is available. The source text is preserved alongside the stage-two rule-based tone label.

Stage two applies a small, transparent Chinese and English keyword rule to title and summary text. It stores the classification and matched keywords; this is not a general sentiment model.

## Financial Record

```json
{
  "period_start": "YYYY-MM-DD",
  "period_end": "YYYY-MM-DD",
  "filed_date": "YYYY-MM-DD",
  "fiscal_year": "2026",
  "fiscal_period": "中报",
  "form": "中报",
  "metric": "revenue",
  "metric_label": "营业收入",
  "value": 0.0,
  "unit": "CNY",
  "accession_number": "",
  "source": "AKShare",
  "market": "cn_a",
  "currency": "CNY"
}
```

The common metric set is:

| Metric | Chinese-market source fields | US source fields |
| --- | --- | --- |
| `revenue` | 营业总收入、营业收入、营业额、营运收入等 | RevenueFromContractWithCustomerExcludingAssessedTax or SalesRevenueNet |
| `net_income` | 归属于股东的净利润、股东应占溢利、除税后溢利等 | NetIncomeLoss |
| `assets` | 资产总额或资产总值 | Assets |
| `liabilities` | 负债总额或负债总值 | Liabilities |
| `current_assets` | 流动资产 | AssetsCurrent |
| `current_liabilities` | 流动负债 | LiabilitiesCurrent |
| `cash_and_equivalents` | 货币资金或现金及现金等价物 | CashAndCashEquivalentsAtCarryingValue |
| `operating_cash_flow` | 经营活动产生的现金流量净额 | NetCashProvidedByUsedInOperatingActivities |

A 股和 B 股报告保留年报、中报、一季报、三季报等报告口径；港股当前使用年度报告接口；美股保留 10-K 和 10-Q。财务记录保留报告期起止和申报或公告日期。提供分析范围时，以 filed date 作为可用性边界，避免市场观察区间较短时错误丢弃此前已可获得的财务信息。

## Announcement Record

Announcement rows include filing or notice date, report date when available, form, title, accession number or notice URL, source, market, and currency.

- US rows include SEC 10-K, 10-Q, and 8-K forms and SEC archive URLs.
- China A rows use `公告` as the normalized form; B rows use the same adapter when usable rows are returned and otherwise report the limitation explicitly.
- Hong Kong notice collection is not configured in this stage; the source record explicitly reports that limitation.

## Workbook Contract

The generated workbook contains these sheets:

| Sheet | Purpose |
| --- | --- |
| `README` | Request parameters, normalized symbol, market, currency, entity summary, source status, limitations, and row counts. |
| `主体信息` | Symbol, provider symbol, company name, market, currency, exchange, CIK when available, SIC when available, and source. |
| `行情数据` | Cleaned daily price records with market and currency. |
| `财务数据` | Cleaned financial statements with reporting period, filing or notice date, metric, unit, market, currency, and source. |
| `新闻舆情` | Cleaned news records, links, Chinese or English rule-based tone, and evidence. |
| `公告数据` | Cleaned China notices or US SEC filing history. |
| `指标分析` | Derived market, financial, news, and notice indicators with methods. |
| `数据来源` | Provider, market, currency, mode, cache status, source location, raw, clean, and output row counts, and notes. |
| `数据质量` | Raw counts, cleaned counts, output counts, duplicate counts, invalid counts, out-of-range counts, and status. |

Every source-dependent sheet must retain source information. The workbook must not include document creation, update, or generation date labels.

## Error Behavior

- Invalid symbols, market conflicts, dates, output paths, or source payloads fail with a concise error and non-zero exit status.
- Chinese online mode fails clearly when AKShare is not installed or an adapter fails; it never silently returns guessed data.
- US online mode fails before a network request when required Twelve Data credentials or SEC User-Agent contact information is missing.
- A failed source is reported as an error; an unsupported dataset, such as Hong Kong notices in this stage, is reported explicitly in the source record.
- Fixture mode remains available for offline validation and reproducible US AAPL examples.
