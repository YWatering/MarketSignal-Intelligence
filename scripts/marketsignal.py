#!/usr/bin/env python3
"""MarketSignal Intelligence multi-market collection, governance, analysis, and Excel export."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import html
import io
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "fixtures"
DEFAULT_PRICES_FIXTURE = FIXTURES_DIR / "aapl_prices.json"
DEFAULT_NEWS_FIXTURE = FIXTURES_DIR / "aapl_news.xml"
DEFAULT_ENTITY_FIXTURE = FIXTURES_DIR / "aapl_entity.json"
DEFAULT_FINANCIALS_FIXTURE = FIXTURES_DIR / "aapl_financials.json"
DEFAULT_SUBMISSIONS_FIXTURE = FIXTURES_DIR / "aapl_submissions.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "marketsignal"

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
YAHOO_NEWS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"

USER_AGENT = "MarketSignal-Intelligence/0.2"
DATE_FORMAT = "%Y-%m-%d"
SEC_FORMS = {"10-K", "10-Q", "8-K"}
CHINA_MARKETS = {"cn_a", "cn_b", "hk"}
FINANCIAL_FORMS = SEC_FORMS | {"年报", "中报", "一季报", "三季报", "年度", "半年报", "季度", "报告期"}
ANNOUNCEMENT_FORMS = SEC_FORMS | {"公告", "公告通知"}
MARKET_ALIASES = {
    "a": "cn_a",
    "a股": "cn_a",
    "cn_a": "cn_a",
    "b": "cn_b",
    "b股": "cn_b",
    "cn_b": "cn_b",
    "hk": "hk",
    "港股": "hk",
    "hongkong": "hk",
    "us": "us",
    "美股": "us",
}
MARKET_NAMES = {
    "cn_a": "中国A股",
    "cn_b": "中国B股",
    "hk": "中国香港股票",
    "us": "美国股票",
}
MARKET_CURRENCIES = {"cn_a": "CNY", "hk": "HKD", "us": "USD"}
AKSHARE_DOC_URL = "https://akshare.akfamily.xyz/"

FINANCIAL_CONCEPTS = [
    ("revenue", "Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]),
    ("net_income", "Net income", ["NetIncomeLoss"]),
    ("assets", "Total assets", ["Assets"]),
    ("liabilities", "Total liabilities", ["Liabilities"]),
    ("current_assets", "Current assets", ["AssetsCurrent"]),
    ("current_liabilities", "Current liabilities", ["LiabilitiesCurrent"]),
    ("cash_and_equivalents", "Cash and cash equivalents", ["CashAndCashEquivalentsAtCarryingValue"]),
    ("operating_cash_flow", "Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities"]),
]

POSITIVE_TERMS = {
    "beat",
    "demand",
    "gain",
    "growth",
    "launch",
    "profit",
    "record",
    "strong",
    "surge",
    "upgrade",
}
NEGATIVE_TERMS = {
    "cut",
    "decline",
    "downgrade",
    "drop",
    "fall",
    "investigation",
    "lawsuit",
    "loss",
    "miss",
    "risk",
    "warning",
    "weak",
}
POSITIVE_CN_TERMS = {
    "增长",
    "上涨",
    "盈利",
    "利好",
    "回购",
    "创新高",
    "强劲",
    "增持",
    "改善",
    "突破",
    "超预期",
    "扩张",
    "创纪录",
}
NEGATIVE_CN_TERMS = {
    "下降",
    "下跌",
    "亏损",
    "利空",
    "减持",
    "风险",
    "诉讼",
    "调查",
    "预警",
    "下滑",
    "不及预期",
    "处罚",
    "暴跌",
    "违约",
}


class PipelineError(RuntimeError):
    """Expected user-facing pipeline failure."""


def _normalize_market(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    market = MARKET_ALIASES.get(normalized)
    if not market:
        raise PipelineError(f"unsupported market: {value}; use cn_a, cn_b, hk, or us")
    return market


def _china_exchange_for_code(code: str, suffix: str | None = None) -> str:
    if suffix in {"SH", "SS"} or code.startswith(("5", "6", "9")):
        return "SH"
    if suffix == "BJ" or code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def parse_symbol(symbol: str, market: str | None = None) -> dict[str, str]:
    raw = str(symbol or "").strip().upper()
    if not raw:
        raise PipelineError("symbol must be a non-empty value")
    selected_market = _normalize_market(market)
    exchange_hint: str | None = None
    if ":" in raw:
        prefix, raw = raw.split(":", 1)
        prefix_market = {"A": "cn_a", "B": "cn_b", "HK": "hk", "NYSE": "us", "NASDAQ": "us"}.get(prefix)
        if selected_market and prefix_market and selected_market != prefix_market:
            raise PipelineError(f"symbol prefix {prefix} conflicts with market {selected_market}")
        selected_market = selected_market or prefix_market
    suffix_match = re.fullmatch(r"(.+)\.(SH|SS|SZ|BJ|HK|US)", raw)
    if suffix_match:
        raw, suffix = suffix_match.groups()
        suffix_market = "hk" if suffix == "HK" else "us" if suffix == "US" else None
        if selected_market and suffix_market and selected_market != suffix_market:
            raise PipelineError(f"symbol suffix .{suffix} conflicts with market {selected_market}")
        selected_market = selected_market or suffix_market
        exchange_hint = "SH" if suffix == "SS" else suffix
    prefix_match = re.fullmatch(r"(SH|SZ|BJ|HK)([A-Z0-9.]+)", raw)
    if prefix_match:
        prefix, raw = prefix_match.groups()
        selected_market = selected_market or ("hk" if prefix == "HK" else "cn_b" if raw.startswith(("900", "200")) else "cn_a")
        exchange_hint = prefix
    if selected_market == "hk" or (selected_market is None and raw.isdigit() and 4 <= len(raw) <= 5):
        if not raw.isdigit() or len(raw) > 5 or exchange_hint not in {None, "HK"}:
            raise PipelineError("Hong Kong symbols must be one to five digits, such as 00700 or 00700.HK")
        code = raw.zfill(5)
        return {
            "symbol": f"{code}.HK",
            "code": code,
            "provider_symbol": code,
            "financial_symbol": code,
            "market": "hk",
            "market_name": MARKET_NAMES["hk"],
            "exchange": "HKEX",
            "exchange_prefix": "HK",
            "currency": MARKET_CURRENCIES["hk"],
        }
    if selected_market in {"cn_a", "cn_b"} or (selected_market is None and raw.isdigit() and len(raw) == 6):
        if not raw.isdigit() or len(raw) != 6:
            raise PipelineError("Chinese A/B-share symbols must be six digits, such as 600519 or 900901")
        inferred_exchange = _china_exchange_for_code(raw)
        exchange = exchange_hint or inferred_exchange
        if exchange not in {"SH", "SZ", "BJ"}:
            raise PipelineError("Chinese A/B-share symbols must use .SH, .SZ, or .BJ exchange suffixes")
        if exchange != inferred_exchange:
            raise PipelineError(f"symbol {raw} does not match exchange {exchange}")
        inferred_market = "cn_b" if raw.startswith(("900", "200")) else "cn_a"
        selected_market = selected_market or inferred_market
        if selected_market == "cn_b" and not raw.startswith(("900", "200")):
            raise PipelineError("B-share symbols must start with 900 (Shanghai) or 200 (Shenzhen)")
        if selected_market == "cn_a" and raw.startswith(("900", "200")):
            raise PipelineError("900xxx and 200xxx symbols are B shares; use market cn_b")
        currency = "USD" if selected_market == "cn_b" and exchange == "SH" else "HKD" if selected_market == "cn_b" else MARKET_CURRENCIES[selected_market]
        return {
            "symbol": f"{raw}.{exchange}",
            "code": raw,
            "provider_symbol": raw,
            "financial_symbol": f"{exchange}{raw}",
            "market": selected_market,
            "market_name": MARKET_NAMES[selected_market],
            "exchange": {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[exchange],
            "exchange_prefix": exchange,
            "currency": currency,
        }
    if selected_market not in {None, "us"}:
        raise PipelineError(f"symbol {symbol} is not valid for market {selected_market}")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", raw):
        raise PipelineError("US symbols must contain letters, numbers, dots, or hyphens")
    return {
        "symbol": raw,
        "code": raw,
        "provider_symbol": raw,
        "financial_symbol": raw,
        "market": "us",
        "market_name": MARKET_NAMES["us"],
        "exchange": "US market",
        "exchange_prefix": "US",
        "currency": MARKET_CURRENCIES["us"],
    }


def _parse_date(value: str, field_name: str) -> str:
    try:
        parsed = datetime.strptime(value, DATE_FORMAT)
    except ValueError as exc:
        raise PipelineError(f"{field_name} must use YYYY-MM-DD format: {value}") from exc
    return parsed.strftime(DATE_FORMAT)


def _validate_request(
    symbol: str,
    start_date: str | None,
    end_date: str | None,
    mode: str,
    output: Path,
    announcement_limit: int,
) -> None:
    if not symbol or len(symbol) > 32 or any(char.isspace() for char in symbol):
        raise PipelineError("symbol must be a non-empty value of at most 32 characters without spaces")
    if mode not in {"fixture", "online"}:
        raise PipelineError("mode must be fixture or online")
    if start_date:
        _parse_date(start_date, "start_date")
    if end_date:
        _parse_date(end_date, "end_date")
    if start_date and end_date and start_date > end_date:
        raise PipelineError("start_date cannot be later than end_date")
    if output.suffix.lower() != ".xlsx":
        raise PipelineError("output must use the .xlsx extension")
    if announcement_limit < 1:
        raise PipelineError("announcement_limit must be at least 1")


def _ssl_context() -> ssl.SSLContext:
    configured_cafile = os.environ.get("SSL_CERT_FILE")
    candidates = [configured_cafile, "/etc/ssl/cert.pem", "/etc/openssl@3/cert.pem"]
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    safe_query = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        safe_query.append((key, "redacted" if key.lower() in {"apikey", "api_key", "token"} else value))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(safe_query), parsed.fragment)
    )


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.payload"


def _fetch_text(
    url: str,
    timeout: int,
    *,
    headers: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    is_sec_request: bool = False,
) -> tuple[str, str]:
    cache_file = _cache_path(cache_dir, url) if cache_dir else None
    if cache_file and cache_file.exists() and not refresh_cache:
        return cache_file.read_text(encoding="utf-8"), "hit"

    request_headers = {
        "Accept": "application/json, application/rss+xml, application/xml, text/xml",
        "User-Agent": USER_AGENT,
    }
    if headers:
        request_headers.update(headers)
    if is_sec_request:
        time.sleep(0.12)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            raw_content = response.read()
            encoding = response.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                raw_content = gzip.decompress(raw_content)
            elif encoding == "deflate":
                raw_content = zlib.decompress(raw_content)
            content = raw_content.decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PipelineError(f"source request failed: {_redact_url(url)} ({reason})") from exc
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(content, encoding="utf-8")
    return content, "miss" if cache_file else "disabled"


def _fetch_json(
    url: str,
    timeout: int,
    *,
    headers: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
    is_sec_request: bool = False,
) -> tuple[dict[str, Any], str]:
    content, cache_status = _fetch_text(
        url,
        timeout,
        headers=headers,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
        is_sec_request=is_sec_request,
    )
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"source returned invalid JSON: {_redact_url(url)}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"source returned an unexpected JSON shape: {_redact_url(url)}")
    return payload, cache_status


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str:
    for child in list(element):
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _child_element(element: ET.Element, name: str) -> ET.Element | None:
    for child in list(element):
        if _local_name(child.tag) == name:
            return child
    return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"fixture not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"fixture is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"fixture has an unexpected JSON shape: {path}")
    return payload


def _load_xml(path: Path) -> ET.Element:
    try:
        return ET.fromstring(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"fixture not found: {path}") from exc
    except ET.ParseError as exc:
        raise PipelineError(f"fixture is not valid XML: {path}") from exc


def _fixture_location(path: Path) -> str:
    try:
        return f"fixture:{path.resolve().relative_to(PROJECT_ROOT)}"
    except ValueError:
        return f"fixture:{path}"


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(math.isnan(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).strip()


def _date_value(value: Any) -> str:
    text = _text_value(value)
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else text


def _number_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if bool(math.isnan(value)):
            return None
    except (TypeError, ValueError):
        pass
    text = _text_value(value).replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "N/A", "nan", "None"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pick_value(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row and _number_value(row[name]) is not None:
            return row[name]
    return None


def _dataframe_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    try:
        records = frame.to_dict(orient="records")
    except (AttributeError, TypeError):
        return []
    return [row for row in records if isinstance(row, dict)]


def _normalize_price_rows(
    payload: dict[str, Any], source: str, market: str = "us", currency: str = "USD"
) -> list[dict[str, Any]]:
    values = payload.get("values")
    if not isinstance(values, list):
        raise PipelineError("price source payload does not contain a values list")
    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            rows.append({})
            continue
        rows.append(
            {
                "date": value.get("datetime", value.get("date", "")),
                "open": value.get("open"),
                "high": value.get("high"),
                "low": value.get("low"),
                "close": value.get("close"),
                "volume": value.get("volume"),
                "source": source,
                "market": market,
                "currency": currency,
            }
        )
    return rows


def _normalize_dataframe_price_rows(
    frame: Any, source: str, market: str, currency: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in _dataframe_records(frame):
        rows.append(
            {
                "date": _date_value(record.get("date", record.get("日期", record.get("交易日期", "")))),
                "open": _number_value(_pick_value(record, ("open", "开盘", "开盘价"))),
                "high": _number_value(_pick_value(record, ("high", "最高", "最高价"))),
                "low": _number_value(_pick_value(record, ("low", "最低", "最低价"))),
                "close": _number_value(_pick_value(record, ("close", "收盘", "收盘价"))),
                "volume": _number_value(_pick_value(record, ("volume", "成交量"))),
                "source": source,
                "market": market,
                "currency": currency,
            }
        )
    return rows


def _classify_news_tone(title: str, summary: str) -> tuple[str, str]:
    text = f"{title} {summary}".lower()
    tokens = set(re.findall(r"[a-z]+", text))
    positive_hits = sorted(tokens & POSITIVE_TERMS)
    negative_hits = sorted(tokens & NEGATIVE_TERMS)
    positive_cn_hits = sorted(term for term in POSITIVE_CN_TERMS if term in text)
    negative_cn_hits = sorted(term for term in NEGATIVE_CN_TERMS if term in text)
    positive_hits.extend(positive_cn_hits)
    negative_hits.extend(negative_cn_hits)
    if len(positive_hits) > len(negative_hits):
        return "positive", ", ".join(positive_hits)
    if len(negative_hits) > len(positive_hits):
        return "negative", ", ".join(negative_hits)
    if positive_hits or negative_hits:
        return "neutral", ", ".join(positive_hits + negative_hits)
    return "neutral", "no configured keyword matched"


def _normalize_news_rows(root: ET.Element, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        source_element = _child_element(item, "source")
        publisher = (source_element.text or "").strip() if source_element is not None else source
        title = html.unescape(_child_text(item, "title"))
        summary = html.unescape(_child_text(item, "description"))
        sentiment, sentiment_basis = _classify_news_tone(title, summary)
        rows.append(
            {
                "published_at": _child_text(item, "pubDate"),
                "title": title,
                "source": publisher or source,
                "url": _child_text(item, "link"),
                "summary": summary,
                "sentiment": sentiment,
                "sentiment_basis": sentiment_basis,
            }
        )
    return rows


def _normalize_dataframe_news_rows(frame: Any, source: str, market: str, currency: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in _dataframe_records(frame):
        title = _text_value(record.get("新闻标题", record.get("title", record.get("标题", ""))))
        summary = _text_value(record.get("新闻内容", record.get("summary", record.get("内容", ""))))
        sentiment, sentiment_basis = _classify_news_tone(title, summary)
        rows.append(
            {
                "published_at": _text_value(record.get("发布时间", record.get("published_at", record.get("date", "")))),
                "title": title,
                "source": _text_value(record.get("文章来源", record.get("source", ""))) or source,
                "url": _text_value(record.get("新闻链接", record.get("url", ""))),
                "summary": summary,
                "sentiment": sentiment,
                "sentiment_basis": sentiment_basis,
                "market": market,
                "currency": currency,
            }
        )
    return rows


def clean_prices(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_rows = list(rows)
    cleaned: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    duplicate_count = 0
    invalid_count = 0
    for row in source_rows:
        try:
            date = _parse_date(_date_value(row.get("date", "")), "price date")
            values = {}
            for field in ("open", "high", "low", "close"):
                number = _number_value(row.get(field))
                if number is None:
                    raise ValueError(f"invalid {field}")
                values[field] = number
            volume_value = _number_value(row.get("volume"))
            if volume_value is None:
                raise ValueError("invalid volume")
            volume = int(volume_value)
            if values["high"] < values["low"] or volume < 0:
                raise ValueError("invalid price range or volume")
        except (KeyError, TypeError, ValueError, PipelineError):
            invalid_count += 1
            continue
        if date in seen_dates:
            duplicate_count += 1
            continue
        seen_dates.add(date)
        cleaned.append(
            {
                "date": date,
                **values,
                "volume": volume,
                "source": row.get("source", "unknown"),
                "market": row.get("market", "us"),
                "currency": row.get("currency", "USD"),
            }
        )
    cleaned.sort(key=lambda item: item["date"])
    return cleaned, {
        "raw_rows": len(source_rows),
        "clean_rows": len(cleaned),
        "duplicates_removed": duplicate_count,
        "invalid_rows": invalid_count,
    }


def clean_news(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_rows = list(rows)
    cleaned: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    duplicate_count = 0
    invalid_count = 0
    for row in source_rows:
        title = str(row.get("title", "")).strip()
        published_at = str(row.get("published_at", "")).strip()
        url = str(row.get("url", "")).strip()
        if not title:
            invalid_count += 1
            continue
        key = (url, title, published_at)
        if key in seen_keys:
            duplicate_count += 1
            continue
        seen_keys.add(key)
        summary = str(row.get("summary", "")).strip()
        sentiment, sentiment_basis = _classify_news_tone(title, summary)
        cleaned.append(
            {
                "published_at": published_at,
                "title": title,
                "source": str(row.get("source", "unknown")).strip() or "unknown",
                "url": url,
                "summary": summary,
                "sentiment": sentiment,
                "sentiment_basis": sentiment_basis,
                "market": row.get("market", "us"),
                "currency": row.get("currency", "USD"),
            }
        )
    cleaned.sort(key=lambda item: item["published_at"], reverse=True)
    return cleaned, {
        "raw_rows": len(source_rows),
        "clean_rows": len(cleaned),
        "duplicates_removed": duplicate_count,
        "invalid_rows": invalid_count,
    }


def clean_financials(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_rows = list(rows)
    candidates: list[dict[str, Any]] = []
    invalid_count = 0
    for row in source_rows:
        try:
            period_end = _parse_date(_date_value(row.get("period_end", "")), "financial period_end")
            period_start_value = _date_value(row.get("period_start", ""))
            period_start = _parse_date(period_start_value, "financial period_start") if period_start_value else ""
            filed_date = _parse_date(_date_value(row.get("filed_date", "")), "financial filed_date")
            value = _number_value(row["value"])
            if value is None:
                raise ValueError("invalid financial value")
            metric = str(row["metric"]).strip()
            form = str(row["form"]).strip()
            if not metric or form not in FINANCIAL_FORMS:
                raise ValueError("invalid metric or form")
        except (KeyError, TypeError, ValueError, PipelineError):
            invalid_count += 1
            continue
        candidates.append(
            {
                "period_start": period_start,
                "period_end": period_end,
                "filed_date": filed_date,
                "fiscal_year": row.get("fiscal_year", ""),
                "fiscal_period": str(row.get("fiscal_period", "")).strip(),
                "form": form,
                "metric": metric,
                "metric_label": str(row.get("metric_label", metric)).strip(),
                "value": value,
                "unit": str(row.get("unit", "")).strip(),
                "accession_number": str(row.get("accession_number", "")).strip(),
                "source": str(row.get("source", "unknown")).strip() or "unknown",
                "market": str(row.get("market", "us")).strip() or "us",
                "currency": str(row.get("currency", "USD")).strip() or "USD",
            }
        )
    candidates.sort(
        key=lambda item: (
            item["metric"],
            item["period_end"],
            item["period_start"],
            item["form"],
            item["filed_date"],
        ),
        reverse=True,
    )
    cleaned: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    duplicate_count = 0
    for row in candidates:
        key = (row["metric"], row["period_start"], row["period_end"], row["form"])
        if key in seen_keys:
            duplicate_count += 1
            continue
        seen_keys.add(key)
        cleaned.append(row)
    cleaned.sort(key=lambda item: (item["period_end"], item["metric"]))
    return cleaned, {
        "raw_rows": len(source_rows),
        "clean_rows": len(cleaned),
        "duplicates_removed": duplicate_count,
        "invalid_rows": invalid_count,
    }


def clean_announcements(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_rows = list(rows)
    cleaned: list[dict[str, Any]] = []
    seen_accessions: set[str] = set()
    duplicate_count = 0
    invalid_count = 0
    for row in source_rows:
        try:
            filed_date = _parse_date(_date_value(row.get("filed_date", "")), "announcement filed_date")
            form = str(row.get("form", "")).strip()
            accession_number = str(row.get("accession_number", "")).strip()
            if form not in ANNOUNCEMENT_FORMS or not accession_number:
                raise ValueError("invalid form or accession number")
        except (TypeError, ValueError, PipelineError):
            invalid_count += 1
            continue
        if accession_number in seen_accessions:
            duplicate_count += 1
            continue
        seen_accessions.add(accession_number)
        report_date = _date_value(row.get("report_date", ""))
        if report_date:
            try:
                report_date = _parse_date(report_date, "announcement report_date")
            except PipelineError:
                report_date = ""
        cleaned.append(
            {
                "filed_date": filed_date,
                "report_date": report_date,
                "form": form,
                "title": str(row.get("title", "")).strip() or f"{form} filing",
                "accession_number": accession_number,
                "url": str(row.get("url", "")).strip(),
                "source": str(row.get("source", "unknown")).strip() or "unknown",
                "market": str(row.get("market", "us")).strip() or "us",
                "currency": str(row.get("currency", "USD")).strip() or "USD",
            }
        )
    cleaned.sort(key=lambda item: item["filed_date"], reverse=True)
    return cleaned, {
        "raw_rows": len(source_rows),
        "clean_rows": len(cleaned),
        "duplicates_removed": duplicate_count,
        "invalid_rows": invalid_count,
    }


def _filter_rows_by_date(
    rows: Iterable[dict[str, Any]],
    field_name: str,
    start_date: str | None,
    end_date: str | None,
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    excluded_count = 0
    for row in rows:
        date = row.get(field_name, "")
        if not date or (start_date and date < start_date) or (end_date and date > end_date):
            excluded_count += 1
            continue
        filtered.append(row)
    return filtered, excluded_count


def filter_prices_by_range(
    rows: Iterable[dict[str, Any]], start_date: str | None, end_date: str | None
) -> tuple[list[dict[str, Any]], int]:
    return _filter_rows_by_date(rows, "date", start_date, end_date)


def filter_news_by_range(
    rows: Iterable[dict[str, Any]], start_date: str | None, end_date: str | None
) -> tuple[list[dict[str, Any]], int]:
    if not start_date and not end_date:
        return list(rows), 0
    normalized: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        try:
            publication_text = _text_value(copy.get("published_at", ""))
            try:
                publication_date = parsedate_to_datetime(publication_text).date()
            except (TypeError, ValueError, IndexError):
                publication_date = datetime.fromisoformat(publication_text.replace("Z", "+00:00")).date()
            copy["_publication_date"] = publication_date.strftime(DATE_FORMAT)
        except (TypeError, ValueError, IndexError, OverflowError):
            copy["_publication_date"] = ""
        normalized.append(copy)
    filtered, excluded_count = _filter_rows_by_date(normalized, "_publication_date", start_date, end_date)
    for row in filtered:
        row.pop("_publication_date", None)
    return filtered, excluded_count


def filter_financials_by_range(
    rows: Iterable[dict[str, Any]], start_date: str | None, end_date: str | None
) -> tuple[list[dict[str, Any]], int]:
    # Financial reporting periods predate the market observation window. Use filing date
    # as the availability boundary so the latest report available by end_date is retained.
    filtered: list[dict[str, Any]] = []
    excluded_count = 0
    for row in rows:
        filed_date = row.get("filed_date", "")
        if not filed_date or (end_date and filed_date > end_date):
            excluded_count += 1
            continue
        filtered.append(row)
    return filtered, excluded_count


def filter_announcements_by_range(
    rows: Iterable[dict[str, Any]], start_date: str | None, end_date: str | None
) -> tuple[list[dict[str, Any]], int]:
    return _filter_rows_by_date(rows, "filed_date", start_date, end_date)


def _sec_headers(sec_user_agent: str) -> dict[str, str]:
    if not sec_user_agent or "@" not in sec_user_agent or "\n" in sec_user_agent or "\r" in sec_user_agent:
        raise PipelineError(
            "online mode requires a SEC user agent with a contact email via --sec-user-agent or MARKETSIGNAL_SEC_USER_AGENT"
        )
    return {"User-Agent": sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def _akshare_module() -> Any:
    try:
        import akshare as ak
    except ImportError as exc:
        raise PipelineError("Chinese-market online mode requires akshare; install requirements.txt first") from exc
    return ak


def _akshare_cache_path(cache_dir: Path | None, key: str) -> Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return cache_dir / f"akshare-{digest}.json"


def _fetch_akshare_table(
    function_name: str,
    cache_key: str,
    loader: Any,
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[Any, str]:
    cache_file = _akshare_cache_path(cache_dir, cache_key)
    if cache_file and cache_file.exists() and not refresh_cache:
        try:
            import pandas as pd

            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            return pd.DataFrame(payload["records"], columns=payload["columns"]), "hit"
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PipelineError(f"AKShare cache is invalid for {function_name}") from exc
    try:
        # Several AKShare endpoints use progress output; keep the CLI summary readable.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            frame = loader()
    except Exception as exc:
        raise PipelineError(f"AKShare {function_name} failed: {exc}") from exc
    if frame is None:
        try:
            import pandas as pd

            frame = pd.DataFrame()
        except ImportError as exc:
            raise PipelineError("Chinese-market online mode requires pandas through akshare") from exc
    if cache_file:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "columns": [str(column) for column in frame.columns],
                "records": json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False)),
            }
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            raise PipelineError(f"could not cache AKShare {function_name} response") from exc
    return frame, "miss" if cache_file else "disabled"


def _combine_cache_status(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if not values:
        return "disabled"
    if all(value == "hit" for value in values):
        return "hit"
    if all(value == "disabled" for value in values):
        return "disabled"
    if any(value == "miss" for value in values):
        return "miss"
    return "partial"


def _date_argument(value: str | None, default: str) -> str:
    return (value or default).replace("-", "")


def _akshare_price_frame(
    instrument: dict[str, str],
    start_date: str | None,
    end_date: str | None,
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[Any, str, str]:
    ak = _akshare_module()
    start = _date_argument(start_date, "1970-01-01")
    end = _date_argument(end_date, "2050-01-01")
    market = instrument["market"]
    code = instrument["code"]
    if market == "cn_a":
        candidates = [
            ("stock_zh_a_hist", lambda: ak.stock_zh_a_hist(code, "daily", start, end, "", 20)),
            (
                "stock_zh_a_hist_tx",
                lambda: ak.stock_zh_a_hist_tx(
                    f"{instrument['exchange_prefix'].lower()}{code}", start, end, ""
                ),
            ),
        ]
    elif market == "cn_b":
        candidates = [
            (
                "stock_zh_b_daily",
                lambda: ak.stock_zh_b_daily(
                    f"{instrument['exchange_prefix'].lower()}{code}", start, end, ""
                ),
            )
        ]
    else:
        candidates = [
            ("stock_hk_hist", lambda: ak.stock_hk_hist(code, "daily", start, end, "")),
            ("stock_hk_daily", lambda: ak.stock_hk_daily(code, "")),
        ]
    errors: list[str] = []
    for function_name, loader in candidates:
        try:
            frame, cache_status = _fetch_akshare_table(
                function_name,
                f"price:{market}:{instrument['provider_symbol']}:{start}:{end}:{function_name}",
                loader,
                cache_dir,
                refresh_cache,
            )
            if len(frame.index) == 0 and function_name != candidates[-1][0]:
                errors.append(f"{function_name} returned no rows")
                continue
            return frame, cache_status, function_name
        except PipelineError as exc:
            errors.append(str(exc))
    raise PipelineError("; ".join(errors) or f"AKShare has no price adapter for {market}")


def fetch_online_china_prices(
    instrument: dict[str, str],
    start_date: str | None,
    end_date: str | None,
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], str, str, str]:
    frame, cache_status, function_name = _akshare_price_frame(
        instrument, start_date, end_date, cache_dir, refresh_cache
    )
    return (
        _normalize_dataframe_price_rows(frame, f"AKShare {function_name}", instrument["market"], instrument["currency"]),
        AKSHARE_DOC_URL,
        cache_status,
        f"AKShare {function_name}",
    )


def fetch_online_china_news(
    instrument: dict[str, str],
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], str, str, str]:
    ak = _akshare_module()
    function_name = "stock_news_em"
    candidates = list(dict.fromkeys((instrument["code"], instrument["symbol"])))
    errors: list[str] = []
    for news_symbol in candidates:
        try:
            frame, cache_status = _fetch_akshare_table(
                function_name,
                f"news:{instrument['market']}:{news_symbol}",
                lambda news_symbol=news_symbol: ak.stock_news_em(symbol=news_symbol),
                cache_dir,
                refresh_cache,
            )
            return (
                _normalize_dataframe_news_rows(frame, f"AKShare {function_name}", instrument["market"], instrument["currency"]),
                AKSHARE_DOC_URL,
                cache_status,
                f"AKShare {function_name} ({news_symbol})",
            )
        except PipelineError as exc:
            errors.append(str(exc))
    raise PipelineError("; ".join(errors) or f"AKShare has no news adapter for {instrument['symbol']}")


def _china_period_start(period_end: str, report_type: str) -> str:
    if not period_end:
        return ""
    year = period_end[:4]
    return f"{year}-01-01"


def _normalize_china_report_type(value: Any) -> str:
    report_type = _text_value(value)
    return {
        "001": "年度",
        "002": "一季报",
        "003": "中报",
        "004": "三季报",
        "005": "年报",
    }.get(report_type, report_type)


def _china_financial_row(
    record: dict[str, Any],
    metric: str,
    metric_label: str,
    value: Any,
    instrument: dict[str, str],
    source: str,
    hong_kong: bool,
) -> dict[str, Any] | None:
    period_end = _date_value(record.get("REPORT_DATE", record.get("报告日期", record.get("period_end", ""))))
    if not period_end:
        return None
    period_start = _date_value(record.get("START_DATE", record.get("开始日期", record.get("period_start", ""))))
    report_type = _normalize_china_report_type(
        record.get("REPORT_TYPE", record.get("DATE_TYPE_CODE", record.get("报告类型", "")))
    )
    if not period_start:
        period_start = _china_period_start(period_end, report_type)
    filed_date = _date_value(record.get("NOTICE_DATE", record.get("UPDATE_DATE", record.get("公告日期", ""))))
    if not filed_date:
        filed_date = period_end
    form = report_type or ("年度" if hong_kong else "报告期")
    number = _number_value(value)
    if number is None:
        return None
    currency = _text_value(record.get("CURRENCY", record.get("货币", ""))) or instrument["currency"]
    return {
        "period_start": period_start,
        "period_end": period_end,
        "filed_date": filed_date,
        "fiscal_year": period_end[:4],
        "fiscal_period": report_type,
        "form": form,
        "metric": metric,
        "metric_label": metric_label,
        "value": number,
        "unit": currency,
        "accession_number": "",
        "source": source,
        "market": instrument["market"],
        "currency": currency,
    }


def _append_china_metric(
    rows: list[dict[str, Any]],
    record: dict[str, Any],
    instrument: dict[str, str],
    source: str,
    hong_kong: bool,
    metric: str,
    metric_label: str,
    aliases: Iterable[str],
) -> None:
    value = None
    if hong_kong:
        item_name = _text_value(record.get("STD_ITEM_NAME", ""))
        if any(alias in item_name for alias in aliases):
            value = record.get("AMOUNT")
    else:
        value = _pick_value(record, aliases)
    row = _china_financial_row(record, metric, metric_label, value, instrument, source, hong_kong)
    if row:
        rows.append(row)


def extract_china_financial_rows(
    tables: dict[str, Any], instrument: dict[str, str], source_prefix: str = "AKShare"
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    hong_kong = instrument["market"] == "hk"
    metrics = {
        "profit": [
            ("revenue", "营业收入", ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "营业总收入", "营业额", "营运收入", "经营收入总额")),
            ("net_income", "净利润", ("PARENT_NETPROFIT", "NETPROFIT", "股东应占溢利", "股东应占利润", "除税后溢利")),
        ],
        "balance": [
            ("assets", "总资产", ("TOTAL_ASSETS", "ASSET_BALANCE", "资产总额", "资产总值")),
            ("liabilities", "总负债", ("TOTAL_LIABILITIES", "LIAB_BALANCE", "负债总额", "负债总值")),
            ("current_assets", "流动资产", ("TOTAL_CURRENT_ASSETS", "CURRENT_ASSET_BALANCE", "流动资产总额", "流动资产")),
            ("current_liabilities", "流动负债", ("TOTAL_CURRENT_LIAB", "CURRENT_LIAB_BALANCE", "流动负债总额", "流动负债")),
            ("cash_and_equivalents", "现金及现金等价物", ("MONETARYFUNDS", "现金及现金等价物", "现金和现金等价物")),
        ],
        "cash": [
            ("operating_cash_flow", "经营活动现金流", ("NETCASH_OPERATE", "经营活动产生的现金流量净额", "经营现金流", "经营活动现金净额")),
        ],
    }
    for table_name, table in tables.items():
        source = f"{source_prefix} {table_name}"
        for record in _dataframe_records(table):
            for metric, label, aliases in metrics.get(table_name, []):
                _append_china_metric(rows, record, instrument, source, hong_kong, metric, label, aliases)
    return rows


def _entity_name_from_tables(tables: dict[str, Any]) -> str:
    for table in tables.values():
        for record in _dataframe_records(table):
            for key in ("SECURITY_NAME_ABBR", "证券简称", "SECURITY_NAME", "名称"):
                name = _text_value(record.get(key, ""))
                if name:
                    return name
    return "unknown"


def _entity_name_from_profile(frame: Any) -> str:
    for record in _dataframe_records(frame):
        for key in ("公司名称", "证券简称", "公司简称"):
            name = _text_value(record.get(key, ""))
            if name:
                return name
    return ""


def resolve_china_entity(instrument: dict[str, str], tables: dict[str, Any], profile: Any = None) -> dict[str, str]:
    return {
        "symbol": instrument["symbol"],
        "provider_symbol": instrument["provider_symbol"],
        "market": instrument["market"],
        "market_name": instrument["market_name"],
        "company_name": _entity_name_from_profile(profile) or _entity_name_from_tables(tables),
        "cik": "",
        "exchange": instrument["exchange"],
        "sic": "",
        "sic_description": "",
        "currency": instrument["currency"],
        "source": "AKShare",
    }


def fetch_online_china_financials(
    instrument: dict[str, str],
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], dict[str, str], str, str, str, str]:
    ak = _akshare_module()
    statuses: list[str] = []
    tables: dict[str, Any] = {}
    if instrument["market"] in {"cn_a", "cn_b"}:
        symbol = instrument["financial_symbol"]
        specs = {
            "profit": "stock_profit_sheet_by_report_em",
            "balance": "stock_balance_sheet_by_report_em",
            "cash": "stock_cash_flow_sheet_by_report_em",
        }
        for table_name, function_name in specs.items():
            frame, status = _fetch_akshare_table(
                function_name,
                f"financial:{instrument['market']}:{instrument['provider_symbol']}:{table_name}",
                lambda function_name=function_name: getattr(ak, function_name)(symbol=symbol),
                cache_dir,
                refresh_cache,
            )
            tables[table_name] = frame
            statuses.append(status)
        profile = None
        entity = resolve_china_entity(instrument, tables)
        source_prefix = "AKShare"
    else:
        specs = {"profit": "利润表", "balance": "资产负债表", "cash": "现金流量表"}
        for table_name, statement_name in specs.items():
            frame, status = _fetch_akshare_table(
                "stock_financial_hk_report_em",
                f"financial:{instrument['market']}:{instrument['provider_symbol']}:{table_name}",
                lambda statement_name=statement_name: ak.stock_financial_hk_report_em(
                    stock=instrument["code"], symbol=statement_name, indicator="年度"
                ),
                cache_dir,
                refresh_cache,
            )
            tables[table_name] = frame
            statuses.append(status)
        profile, profile_status = _fetch_akshare_table(
            "stock_hk_security_profile_em",
            f"entity:{instrument['market']}:{instrument['provider_symbol']}",
            lambda: ak.stock_hk_security_profile_em(symbol=instrument["code"]),
            cache_dir,
            refresh_cache,
        )
        statuses.append(profile_status)
        entity = resolve_china_entity(instrument, tables, profile)
        source_prefix = "AKShare"
    return (
        extract_china_financial_rows(tables, instrument, source_prefix),
        entity,
        AKSHARE_DOC_URL,
        _combine_cache_status(statuses),
        "AKShare stock financial statement adapters",
        "annual/report-period Chinese-market financial statements",
    )


def fetch_online_china_announcements(
    instrument: dict[str, str],
    start_date: str | None,
    end_date: str | None,
    cache_dir: Path | None,
    refresh_cache: bool,
) -> tuple[list[dict[str, Any]], str, str, str]:
    if instrument["market"] == "hk":
        return [], AKSHARE_DOC_URL, "not_supported", "No dedicated HK notice adapter in this stage"
    ak = _akshare_module()
    function_name = "stock_individual_notice_report"
    try:
        frame, cache_status = _fetch_akshare_table(
            function_name,
            f"announcements:{instrument['market']}:{instrument['provider_symbol']}:{start_date}:{end_date}",
            lambda: ak.stock_individual_notice_report(
                security=instrument["code"],
                symbol="全部",
                begin_date=_date_argument(start_date, "1970-01-01"),
                end_date=_date_argument(end_date, "2050-01-01"),
            ),
            cache_dir,
            refresh_cache,
        )
    except PipelineError as exc:
        if instrument["market"] == "cn_b":
            return [], AKSHARE_DOC_URL, "not_supported", f"B-share notice endpoint returned no usable rows: {exc}"
        raise
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(_dataframe_records(frame)):
        filed_date = _date_value(record.get("公告日期", record.get("date", "")))
        title = _text_value(record.get("公告标题", record.get("title", "")))
        url = _text_value(record.get("网址", record.get("url", "")))
        if not filed_date or not title:
            continue
        rows.append(
            {
                "filed_date": filed_date,
                "report_date": filed_date,
                "form": "公告",
                "title": title,
                "accession_number": url or f"{instrument['symbol']}:{filed_date}:{index}",
                "url": url,
                "source": f"AKShare {function_name}",
                "market": instrument["market"],
                "currency": instrument["currency"],
            }
        )
    return rows, AKSHARE_DOC_URL, cache_status, f"AKShare {function_name}"


def fetch_online_prices(
    symbol: str,
    start_date: str | None,
    end_date: str | None,
    api_key: str,
    timeout: int,
    cache_dir: Path,
    refresh_cache: bool,
    market: str = "us",
    currency: str = "USD",
) -> tuple[list[dict[str, Any]], str, str]:
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": "5000",
        "apikey": api_key,
        "timezone": "Exchange",
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    url = f"{TWELVE_DATA_URL}?{urllib.parse.urlencode(params)}"
    payload, cache_status = _fetch_json(url, timeout, cache_dir=cache_dir, refresh_cache=refresh_cache)
    if payload.get("status") == "error" or "code" in payload:
        message = payload.get("message", "price source rejected the request")
        raise PipelineError(f"Twelve Data error: {message}")
    return _normalize_price_rows(payload, "Twelve Data", market, currency), _redact_url(url), cache_status


def fetch_online_news(
    symbol: str,
    timeout: int,
    cache_dir: Path,
    refresh_cache: bool,
    market: str = "us",
    currency: str = "USD",
) -> tuple[list[dict[str, Any]], str, str]:
    params = {"s": symbol, "region": "US", "lang": "en-US"}
    url = f"{YAHOO_NEWS_URL}?{urllib.parse.urlencode(params)}"
    content, cache_status = _fetch_text(url, timeout, cache_dir=cache_dir, refresh_cache=refresh_cache)
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise PipelineError("Yahoo Finance returned invalid RSS/XML") from exc
    rows = _normalize_news_rows(root, "Yahoo Finance RSS")
    for row in rows:
        row["market"] = market
        row["currency"] = currency
    return rows, url, cache_status


def _ticker_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("data"), list):
        return [record for record in payload["data"] if isinstance(record, dict)]
    return [record for record in payload.values() if isinstance(record, dict)]


def resolve_entity(
    symbol: str,
    ticker_payload: dict[str, Any],
    submissions_payload: dict[str, Any],
    source: str,
    instrument: dict[str, str] | None = None,
) -> dict[str, Any]:
    instrument = instrument or parse_symbol(symbol, "us")
    match = next(
        (
            record
            for record in _ticker_records(ticker_payload)
            if str(record.get("ticker", "")).upper() == symbol.upper()
        ),
        None,
    )
    if not match:
        raise PipelineError(f"SEC ticker mapping did not find symbol: {symbol}")
    cik = str(match.get("cik_str", match.get("cik", ""))).zfill(10)
    if not cik.isdigit() or cik == "0000000000":
        raise PipelineError(f"SEC ticker mapping returned an invalid CIK for symbol: {symbol}")
    exchanges = submissions_payload.get("exchanges", [])
    if not isinstance(exchanges, list):
        exchanges = []
    return {
        "symbol": instrument["symbol"],
        "provider_symbol": instrument["provider_symbol"],
        "market": instrument["market"],
        "market_name": instrument["market_name"],
        "company_name": str(submissions_payload.get("name") or match.get("title") or "unknown"),
        "cik": cik,
        "exchange": ", ".join(str(item) for item in exchanges if item) or instrument["exchange"],
        "sic": str(submissions_payload.get("sic", "")),
        "sic_description": str(submissions_payload.get("sicDescription", "")),
        "currency": instrument["currency"],
        "source": source,
    }


def extract_financial_rows(
    payload: dict[str, Any],
    source: str,
    per_metric_limit: int = 20,
    instrument: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    facts = payload.get("facts", {})
    us_gaap = facts.get("us-gaap", {}) if isinstance(facts, dict) else {}
    if not isinstance(us_gaap, dict):
        raise PipelineError("SEC companyfacts payload does not contain us-gaap facts")
    rows: list[dict[str, Any]] = []
    for metric, metric_label, candidates in FINANCIAL_CONCEPTS:
        concept = next(
            (us_gaap.get(candidate) for candidate in candidates if isinstance(us_gaap.get(candidate), dict)),
            None,
        )
        if not concept:
            continue
        units = concept.get("units", {})
        if not isinstance(units, dict):
            continue
        preferred_unit = "USD" if isinstance(units.get("USD"), list) else next(
            (unit for unit, values in units.items() if isinstance(values, list)), None
        )
        if not preferred_unit:
            continue
        records = [record for record in units[preferred_unit] if isinstance(record, dict)]
        records = [record for record in records if str(record.get("form", "")) in {"10-K", "10-Q"}]
        records.sort(key=lambda item: (str(item.get("end", "")), str(item.get("filed", ""))), reverse=True)
        for record in records[:per_metric_limit]:
            rows.append(
                {
                    "period_start": record.get("start", ""),
                    "period_end": record.get("end", ""),
                    "filed_date": record.get("filed", ""),
                    "fiscal_year": record.get("fy", ""),
                    "fiscal_period": record.get("fp", ""),
                    "form": record.get("form", ""),
                    "metric": metric,
                    "metric_label": metric_label,
                    "value": record.get("val"),
                    "unit": preferred_unit,
                    "accession_number": record.get("accn", ""),
                    "source": source,
                    "market": instrument["market"] if instrument else "us",
                    "currency": preferred_unit if instrument is None else instrument["currency"],
                }
            )
    return rows


def extract_announcement_rows(
    submissions_payload: dict[str, Any],
    cik: str,
    source: str,
    limit: int,
    instrument: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    recent = submissions_payload.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict):
        raise PipelineError("SEC submissions payload does not contain recent filings")
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    forms = recent.get("form", [])
    documents = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])
    if not all(
        isinstance(value, list)
        for value in (accessions, filing_dates, report_dates, forms, documents, descriptions)
    ):
        raise PipelineError("SEC submissions payload has an invalid recent filings shape")
    rows: list[dict[str, Any]] = []
    for index, accession in enumerate(accessions):
        form = str(forms[index]) if index < len(forms) else ""
        if form not in SEC_FORMS:
            continue
        filing_date = filing_dates[index] if index < len(filing_dates) else ""
        report_date = report_dates[index] if index < len(report_dates) else ""
        document = documents[index] if index < len(documents) else ""
        description = descriptions[index] if index < len(descriptions) else ""
        compact_accession = str(accession).replace("-", "")
        document_url = ""
        if document:
            document_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact_accession}/{document}"
        rows.append(
            {
                "filed_date": filing_date,
                "report_date": report_date,
                "form": form,
                "title": description or f"{form} filing",
                "accession_number": accession,
                "url": document_url,
                "source": source,
                "market": instrument["market"] if instrument else "us",
                "currency": instrument["currency"] if instrument else "USD",
            }
        )
    return rows[:limit]


def _source_record(
    dataset: str,
    provider: str,
    mode: str,
    location: str,
    cache_status: str,
    quality: dict[str, int] | None,
    notes: str,
    market: str = "us",
    currency: str = "USD",
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "provider": provider,
        "mode": mode,
        "status": "loaded" if quality is None or quality.get("clean_rows", 0) > 0 else "warning",
        "cache_status": cache_status,
        "source_location": location,
        "raw_rows": quality.get("raw_rows", 1) if quality else 1,
        "clean_rows": quality.get("clean_rows", 1) if quality else 1,
        "output_rows": quality.get("output_rows", 1) if quality else 1,
        "notes": notes,
        "market": market,
        "currency": currency,
    }


def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def build_indicators(
    prices: list[dict[str, Any]],
    financials: list[dict[str, Any]],
    news: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    indicators: list[dict[str, Any]] = []

    def add(category: str, indicator: str, value: float | int | str | None, unit: str, method: str, evidence: str) -> None:
        if value is not None:
            indicators.append(
                {
                    "category": category,
                    "indicator": indicator,
                    "value": value,
                    "unit": unit,
                    "method": method,
                    "evidence": evidence,
                }
            )

    if prices:
        closes = [row["close"] for row in prices]
        volumes = [row["volume"] for row in prices]
        add("market", "latest_close", closes[-1], "price", "last clean close", prices[-1]["date"])
        period_return = _safe_divide(closes[-1] - closes[0], closes[0])
        add("market", "period_return", period_return * 100 if period_return is not None else None, "%", "(last close / first close - 1) * 100", f"{prices[0]['date']} to {prices[-1]['date']}")
        add("market", "average_close", sum(closes) / len(closes), "price", "mean of clean closes", f"{len(closes)} records")
        add("market", "average_volume", sum(volumes) / len(volumes), "shares", "mean of clean volumes", f"{len(volumes)} records")
        returns = [((closes[index] / closes[index - 1]) - 1) * 100 for index in range(1, len(closes)) if closes[index - 1]]
        if returns:
            average_return = sum(returns) / len(returns)
            variance = sum((value - average_return) ** 2 for value in returns) / len(returns)
            add("market", "close_return_volatility", variance**0.5, "%", "population standard deviation of close-to-close returns", f"{len(returns)} return observations")

    sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0}
    for row in news:
        sentiment_counts[row["sentiment"]] = sentiment_counts.get(row["sentiment"], 0) + 1
    for sentiment in ("positive", "negative", "neutral"):
        add("news", f"{sentiment}_news_count", sentiment_counts.get(sentiment, 0), "articles", "rule-based keyword classification", "title and summary retained in 新闻舆情")
    add("news", "news_tone_balance", sentiment_counts.get("positive", 0) - sentiment_counts.get("negative", 0), "articles", "positive count minus negative count", "rule-based keyword classification")
    add("announcements", "filing_count", len(announcements), "filings", "count of clean market notices and filings", "clean announcement and filing rows")

    latest_by_metric: dict[str, dict[str, Any]] = {}
    ordered_financials = sorted(
        financials,
        key=lambda item: (item["period_end"], item["period_start"], item["filed_date"]),
        reverse=True,
    )
    for row in ordered_financials:
        latest_by_metric.setdefault(row["metric"], row)

    def matching_period(numerator_metric: str, denominator_metric: str) -> tuple[float | None, str]:
        denominators = {
            (row["period_start"], row["period_end"]): row
            for row in financials
            if row["metric"] == denominator_metric
        }
        numerator_rows = sorted(
            (row for row in financials if row["metric"] == numerator_metric),
            key=lambda item: (item["period_end"], item["period_start"], item["filed_date"]),
            reverse=True,
        )
        for numerator_row in numerator_rows:
            period = (numerator_row["period_start"], numerator_row["period_end"])
            denominator_row = denominators.get(period)
            if denominator_row:
                return _safe_divide(numerator_row["value"], denominator_row["value"]), f"{period[0]} to {period[1]}"
        return None, "no matching reporting period"

    margin, margin_evidence = matching_period("net_income", "revenue")
    add("financial", "net_profit_margin", margin * 100 if margin is not None else None, "%", "net income / revenue * 100 for the same reporting period", margin_evidence)
    current_assets = latest_by_metric.get("current_assets")
    current_liabilities = latest_by_metric.get("current_liabilities")
    if current_assets and current_liabilities and current_assets["period_end"] == current_liabilities["period_end"]:
        add("financial", "current_ratio", _safe_divide(current_assets["value"], current_liabilities["value"]), "ratio", "current assets / current liabilities at the same period end", current_assets["period_end"])
    cash_margin, cash_evidence = matching_period("operating_cash_flow", "revenue")
    add("financial", "operating_cash_flow_margin", cash_margin * 100 if cash_margin is not None else None, "%", "operating cash flow / revenue * 100 for the same reporting period", cash_evidence)
    revenues = sorted(
        (row for row in financials if row["metric"] == "revenue"),
        key=lambda item: (item["period_end"], item["period_start"]),
        reverse=True,
    )
    if len(revenues) >= 2 and revenues[0]["period_start"]:
        latest_duration = (
            datetime.strptime(revenues[0]["period_end"], DATE_FORMAT)
            - datetime.strptime(revenues[0]["period_start"], DATE_FORMAT)
        ).days
        comparable = None
        for row in revenues[1:]:
            if not row["period_start"]:
                continue
            duration = (
                datetime.strptime(row["period_end"], DATE_FORMAT)
                - datetime.strptime(row["period_start"], DATE_FORMAT)
            ).days
            if abs(duration - latest_duration) <= 7:
                comparable = row
                break
        if comparable:
            change = _safe_divide(revenues[0]["value"] - comparable["value"], comparable["value"])
            add("financial", "revenue_period_change", change * 100 if change is not None else None, "%", "(latest revenue / prior comparable-duration revenue - 1) * 100", f"{comparable['period_start']} to {comparable['period_end']} compared with {revenues[0]['period_start']} to {revenues[0]['period_end']}")
    return indicators


def _write_table(sheet: Any, headers: list[str], rows: list[list[Any]]) -> None:
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column_cells in sheet.columns:
        column_index = column_cells[0].column
        longest = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[get_column_letter(column_index)].width = min(max(longest + 2, 12), 52)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _add_hyperlinks(sheet: Any, header_name: str) -> None:
    headers = [cell.value for cell in sheet[1]]
    try:
        column_index = headers.index(header_name) + 1
    except ValueError:
        return
    for row in range(2, sheet.max_row + 1):
        cell = sheet.cell(row=row, column=column_index)
        if isinstance(cell.value, str) and cell.value.startswith(("https://", "http://")):
            cell.hyperlink = cell.value
            cell.style = "Hyperlink"


def _set_number_format(sheet: Any, header_names: set[str], number_format: str) -> None:
    headers = [cell.value for cell in sheet[1]]
    for index, header in enumerate(headers, start=1):
        if header in header_names:
            for row in range(2, sheet.max_row + 1):
                sheet.cell(row=row, column=index).number_format = number_format


def _remove_core_properties(output: Path) -> None:
    """Strip OOXML core properties so the workbook has no generated time metadata."""
    temporary_output = output.with_suffix(".stripped.xlsx")
    with zipfile.ZipFile(output, "r") as source, zipfile.ZipFile(
        temporary_output, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for entry in source.infolist():
            if entry.filename == "docProps/core.xml":
                continue
            content = source.read(entry.filename)
            if entry.filename == "[Content_Types].xml":
                root = ET.fromstring(content)
                for child in list(root):
                    if child.attrib.get("PartName") == "/docProps/core.xml":
                        root.remove(child)
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            elif entry.filename == "_rels/.rels":
                root = ET.fromstring(content)
                for child in list(root):
                    if child.attrib.get("Type", "").endswith("/metadata/core-properties"):
                        root.remove(child)
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(entry, content)
    temporary_output.replace(output)


def build_workbook(
    output: Path,
    symbol: str,
    start_date: str | None,
    end_date: str | None,
    mode: str,
    entity: dict[str, Any],
    prices: list[dict[str, Any]],
    financials: list[dict[str, Any]],
    news: list[dict[str, Any]],
    announcements: list[dict[str, Any]],
    indicators: list[dict[str, Any]],
    qualities: dict[str, dict[str, int]],
    source_records: list[dict[str, Any]],
    pipeline_status: str,
) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "MarketSignal Intelligence"
    workbook.properties.title = "MarketSignal Intelligence market research report"

    readme_rows = [
        ["symbol", symbol, "Single-stock multi-market run"],
        ["company_name", entity["company_name"], "Resolved from the selected market data source"],
        ["provider_symbol", entity["provider_symbol"], "Symbol passed to the selected data source"],
        ["market", entity["market"], entity["market_name"]],
        ["cik", entity["cik"], "SEC central index key when available"],
        ["exchange", entity["exchange"], "Resolved from the selected market data source"],
        ["currency", entity["currency"], "Reporting currency from market or source"],
        ["start_date", start_date or "not specified", "Inclusive market/news/filing boundary"],
        ["end_date", end_date or "not specified", "Inclusive market/news/filing boundary"],
        ["mode", mode, "fixture is deterministic; online uses source adapters"],
        ["pipeline_status", pipeline_status, "Review 数据质量 and 数据来源 for details"],
        ["price_rows", len(prices), "Rows after cleaning and range filtering"],
        ["financial_rows", len(financials), "Rows after cleaning and availability filtering"],
        ["news_rows", len(news), "Rows after cleaning and range filtering"],
        ["announcement_rows", len(announcements), "Rows after cleaning and range filtering"],
        ["indicator_rows", len(indicators), "Derived market, financial, news, and filing indicators"],
        ["forecast_status", "not implemented in stage two", "Forecasting is planned for a later stage"],
        ["limitations", "Rule-based news tone is not investment advice", "Financial coverage varies by market adapter"],
    ]
    readme = workbook.create_sheet("README")
    _write_table(readme, ["field", "value", "notes"], readme_rows)

    entity_sheet = workbook.create_sheet("主体信息")
    _write_table(
        entity_sheet,
        ["symbol", "company_name", "provider_symbol", "market", "market_name", "cik", "exchange", "sic", "sic_description", "currency", "source"],
        [[entity[key] for key in ["symbol", "company_name", "provider_symbol", "market", "market_name", "cik", "exchange", "sic", "sic_description", "currency", "source"]]],
    )

    price_sheet = workbook.create_sheet("行情数据")
    _write_table(
        price_sheet,
        ["date", "open", "high", "low", "close", "volume", "source", "market", "currency"],
        [[row[key] for key in ["date", "open", "high", "low", "close", "volume", "source", "market", "currency"]] for row in prices],
    )
    _set_number_format(price_sheet, {"open", "high", "low", "close"}, "0.0000")
    _set_number_format(price_sheet, {"volume"}, "#,##0")

    financial_sheet = workbook.create_sheet("财务数据")
    _write_table(
        financial_sheet,
        ["period_start", "period_end", "filed_date", "fiscal_year", "fiscal_period", "form", "metric", "metric_label", "value", "unit", "accession_number", "source", "market", "currency"],
        [[row[key] for key in ["period_start", "period_end", "filed_date", "fiscal_year", "fiscal_period", "form", "metric", "metric_label", "value", "unit", "accession_number", "source", "market", "currency"]] for row in financials],
    )
    _set_number_format(financial_sheet, {"value"}, "#,##0.00")

    news_sheet = workbook.create_sheet("新闻舆情")
    _write_table(
        news_sheet,
        ["published_at", "title", "source", "url", "summary", "sentiment", "sentiment_basis", "market", "currency"],
        [[row[key] for key in ["published_at", "title", "source", "url", "summary", "sentiment", "sentiment_basis", "market", "currency"]] for row in news],
    )
    _add_hyperlinks(news_sheet, "url")

    announcement_sheet = workbook.create_sheet("公告数据")
    _write_table(
        announcement_sheet,
        ["filed_date", "report_date", "form", "title", "accession_number", "url", "source", "market", "currency"],
        [[row[key] for key in ["filed_date", "report_date", "form", "title", "accession_number", "url", "source", "market", "currency"]] for row in announcements],
    )
    _add_hyperlinks(announcement_sheet, "url")

    indicator_sheet = workbook.create_sheet("指标分析")
    _write_table(
        indicator_sheet,
        ["category", "indicator", "value", "unit", "method", "evidence"],
        [[row[key] for key in ["category", "indicator", "value", "unit", "method", "evidence"]] for row in indicators],
    )
    _set_number_format(indicator_sheet, {"value"}, "0.0000")

    source_sheet = workbook.create_sheet("数据来源")
    _write_table(
        source_sheet,
        ["dataset", "provider", "mode", "status", "cache_status", "source_location", "raw_rows", "clean_rows", "output_rows", "notes", "market", "currency"],
        [[record[key] for key in ["dataset", "provider", "mode", "status", "cache_status", "source_location", "raw_rows", "clean_rows", "output_rows", "notes", "market", "currency"]] for record in source_records],
    )
    _add_hyperlinks(source_sheet, "source_location")

    quality_rows: list[list[Any]] = []
    for dataset in ("prices", "financials", "news", "announcements"):
        quality = qualities[dataset]
        for metric in ("raw_rows", "clean_rows", "output_rows", "duplicates_removed", "invalid_rows", "out_of_range"):
            quality_rows.append([dataset, metric, quality[metric], "processing quality metric"])
    quality_rows.append(["pipeline", "status", pipeline_status, "warning means one or more required datasets have no clean rows"])
    quality_sheet = workbook.create_sheet("数据质量")
    _write_table(quality_sheet, ["dataset", "metric", "value", "notes"], quality_rows)

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    _remove_core_properties(output)


def run_pipeline(
    *,
    symbol: str,
    start_date: str | None,
    end_date: str | None,
    mode: str,
    output: Path,
    market: str | None = None,
    prices_fixture: Path = DEFAULT_PRICES_FIXTURE,
    news_fixture: Path = DEFAULT_NEWS_FIXTURE,
    entity_fixture: Path = DEFAULT_ENTITY_FIXTURE,
    financials_fixture: Path = DEFAULT_FINANCIALS_FIXTURE,
    submissions_fixture: Path = DEFAULT_SUBMISSIONS_FIXTURE,
    api_key: str | None = None,
    sec_user_agent: str | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh_cache: bool = False,
    announcement_limit: int = 40,
    timeout: int = 20,
) -> dict[str, Any]:
    _validate_request(symbol, start_date, end_date, mode, output, announcement_limit)
    instrument = parse_symbol(symbol, market)
    normalized_symbol = instrument["symbol"]
    source_records: list[dict[str, Any]] = []

    if mode == "fixture":
        if instrument["market"] != "us":
            raise PipelineError("fixture mode currently contains the US AAPL sample; use online mode for Chinese markets")
        price_payload = _load_json(prices_fixture)
        news_root = _load_xml(news_fixture)
        ticker_payload = _load_json(entity_fixture)
        financials_payload = _load_json(financials_fixture)
        submissions_payload = _load_json(submissions_fixture)
        price_location = _fixture_location(prices_fixture)
        news_location = _fixture_location(news_fixture)
        entity_location = _fixture_location(entity_fixture)
        financial_location = _fixture_location(financials_fixture)
        announcement_location = _fixture_location(submissions_fixture)
        cache_status = "not_applicable"
        price_cache = news_cache = entity_cache = financial_cache = announcement_cache = cache_status
        raw_prices = _normalize_price_rows(price_payload, "fixture:prices", instrument["market"], instrument["currency"])
        raw_news = _normalize_news_rows(news_root, "fixture:news")
        for row in raw_news:
            row["market"] = instrument["market"]
            row["currency"] = instrument["currency"]
        entity = resolve_entity(
            instrument["provider_symbol"], ticker_payload, submissions_payload, "fixture:SEC entity data", instrument
        )
        raw_financials = extract_financial_rows(
            financials_payload, "fixture:SEC companyfacts", instrument=instrument
        )
        raw_announcements = extract_announcement_rows(
            submissions_payload,
            entity["cik"],
            "fixture:SEC submissions",
            announcement_limit,
            instrument,
        )
        price_provider = news_provider = entity_provider = financial_provider = announcement_provider = "fixture"
        financial_notes = "configured fixture financial concepts"
    else:
        if instrument["market"] in CHINA_MARKETS:
            raw_prices, price_location, price_cache, price_provider = fetch_online_china_prices(
                instrument, start_date, end_date, cache_dir, refresh_cache
            )
            raw_news, news_location, news_cache, news_provider = fetch_online_china_news(
                instrument, cache_dir, refresh_cache
            )
            (
                raw_financials,
                entity,
                financial_location,
                financial_cache,
                financial_provider,
                financial_notes,
            ) = fetch_online_china_financials(instrument, cache_dir, refresh_cache)
            raw_announcements, announcement_location, announcement_cache, announcement_provider = fetch_online_china_announcements(
                instrument, start_date, end_date, cache_dir, refresh_cache
            )
            entity_location = financial_location
            entity_cache = "derived"
            entity_provider = "AKShare"
        else:
            key = api_key or os.environ.get("MARKETSIGNAL_TWELVE_DATA_API_KEY")
            if not key:
                raise PipelineError("online mode requires --twelve-data-api-key or MARKETSIGNAL_TWELVE_DATA_API_KEY")
            sec_identity = sec_user_agent or os.environ.get("MARKETSIGNAL_SEC_USER_AGENT")
            sec_headers = _sec_headers(sec_identity or "")
            raw_prices, price_location, price_cache = fetch_online_prices(
                instrument["provider_symbol"],
                start_date,
                end_date,
                key,
                timeout,
                cache_dir,
                refresh_cache,
                instrument["market"],
                instrument["currency"],
            )
            raw_news, news_location, news_cache = fetch_online_news(
                instrument["provider_symbol"],
                timeout,
                cache_dir,
                refresh_cache,
                instrument["market"],
                instrument["currency"],
            )
            ticker_payload, entity_cache = _fetch_json(
                SEC_TICKERS_URL,
                timeout,
                headers=sec_headers,
                cache_dir=cache_dir,
                refresh_cache=refresh_cache,
                is_sec_request=True,
            )
            match = next(
                (
                    record
                    for record in _ticker_records(ticker_payload)
                    if str(record.get("ticker", "")).upper() == instrument["provider_symbol"]
                ),
                None,
            )
            if not match:
                raise PipelineError(f"SEC ticker mapping did not find symbol: {instrument['provider_symbol']}")
            cik = str(match.get("cik_str", match.get("cik", ""))).zfill(10)
            submissions_url = f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json"
            submissions_payload, announcement_cache = _fetch_json(
                submissions_url,
                timeout,
                headers=sec_headers,
                cache_dir=cache_dir,
                refresh_cache=refresh_cache,
                is_sec_request=True,
            )
            entity = resolve_entity(
                instrument["provider_symbol"], ticker_payload, submissions_payload, "SEC EDGAR", instrument
            )
            facts_url = f"{SEC_COMPANYFACTS_URL}/CIK{entity['cik']}.json"
            financials_payload, financial_cache = _fetch_json(
                facts_url,
                timeout,
                headers=sec_headers,
                cache_dir=cache_dir,
                refresh_cache=refresh_cache,
                is_sec_request=True,
            )
            raw_financials = extract_financial_rows(
                financials_payload, "SEC companyfacts", instrument=instrument
            )
            raw_announcements = extract_announcement_rows(
                submissions_payload,
                entity["cik"],
                "SEC submissions",
                announcement_limit,
                instrument,
            )
            entity_location = SEC_TICKERS_URL
            financial_location = facts_url
            announcement_location = submissions_url
            price_provider = "Twelve Data"
            news_provider = "Yahoo Finance RSS"
            entity_provider = "SEC EDGAR"
            financial_provider = "SEC companyfacts"
            announcement_provider = "SEC submissions"
            financial_notes = "configured US-GAAP concepts from 10-K and 10-Q filings"

    prices, price_quality = clean_prices(raw_prices)
    financials, financial_quality = clean_financials(raw_financials)
    news, news_quality = clean_news(raw_news)
    announcements, announcement_quality = clean_announcements(raw_announcements)
    prices, price_quality["out_of_range"] = filter_prices_by_range(prices, start_date, end_date)
    financials, financial_quality["out_of_range"] = filter_financials_by_range(financials, start_date, end_date)
    news, news_quality["out_of_range"] = filter_news_by_range(news, start_date, end_date)
    announcements, announcement_quality["out_of_range"] = filter_announcements_by_range(announcements, start_date, end_date)
    price_quality["output_rows"] = len(prices)
    financial_quality["output_rows"] = len(financials)
    news_quality["output_rows"] = len(news)
    announcement_quality["output_rows"] = len(announcements)

    source_records.extend(
        [
            _source_record("prices", price_provider, mode, price_location, price_cache, price_quality, "daily OHLCV data", instrument["market"], instrument["currency"]),
            _source_record("news", news_provider, mode, news_location, news_cache, news_quality, "news headlines and source text", instrument["market"], instrument["currency"]),
            _source_record("entity", entity_provider, mode, entity_location, entity_cache, None, "symbol-to-entity mapping and entity attributes", instrument["market"], instrument["currency"]),
            _source_record("financials", financial_provider, mode, financial_location, financial_cache, financial_quality, financial_notes, instrument["market"], instrument["currency"]),
            _source_record("announcements", announcement_provider, mode, announcement_location, announcement_cache, announcement_quality, "market filing and notice history", instrument["market"], instrument["currency"]),
        ]
    )

    required_sets = {"prices": prices, "financials": financials, "news": news}
    pipeline_status = "pass" if all(required_sets.values()) else "warning"
    indicators = build_indicators(prices, financials, news, announcements)
    qualities = {
        "prices": price_quality,
        "financials": financial_quality,
        "news": news_quality,
        "announcements": announcement_quality,
    }
    build_workbook(
        output,
        normalized_symbol,
        start_date,
        end_date,
        mode,
        entity,
        prices,
        financials,
        news,
        announcements,
        indicators,
        qualities,
        source_records,
        pipeline_status,
    )
    return {
        "symbol": normalized_symbol,
        "company_name": entity["company_name"],
        "market": entity["market"],
        "market_name": entity["market_name"],
        "currency": entity["currency"],
        "mode": mode,
        "output": str(output),
        "price_rows": len(prices),
        "financial_rows": len(financials),
        "news_rows": len(news),
        "announcement_rows": len(announcements),
        "indicator_rows": len(indicators),
        "qualities": qualities,
        # Keep stage-one summary keys available to existing callers.
        "price_quality": price_quality,
        "news_quality": news_quality,
        "status": pipeline_status,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect multi-market prices, financials, news, and notices into a traceable Excel workbook")
    parser.add_argument("--symbol", required=True, help="single stock symbol, such as 600519, 00700.HK, or AAPL")
    parser.add_argument("--market", choices=["cn_a", "cn_b", "hk", "us"], help="optional market override")
    parser.add_argument("--start-date", help="inclusive YYYY-MM-DD date")
    parser.add_argument("--end-date", help="inclusive YYYY-MM-DD date")
    parser.add_argument("--mode", choices=["fixture", "online"], default="fixture")
    parser.add_argument("--output", required=True, type=Path, help=".xlsx output path")
    parser.add_argument("--prices-fixture", type=Path, default=DEFAULT_PRICES_FIXTURE)
    parser.add_argument("--news-fixture", type=Path, default=DEFAULT_NEWS_FIXTURE)
    parser.add_argument("--entity-fixture", type=Path, default=DEFAULT_ENTITY_FIXTURE)
    parser.add_argument("--financials-fixture", type=Path, default=DEFAULT_FINANCIALS_FIXTURE)
    parser.add_argument("--submissions-fixture", type=Path, default=DEFAULT_SUBMISSIONS_FIXTURE)
    parser.add_argument("--twelve-data-api-key")
    parser.add_argument("--sec-user-agent", help="organization or application name plus a contact email")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--announcement-limit", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_pipeline(
            symbol=args.symbol,
            start_date=args.start_date,
            end_date=args.end_date,
            mode=args.mode,
            output=args.output,
            market=args.market,
            prices_fixture=args.prices_fixture,
            news_fixture=args.news_fixture,
            entity_fixture=args.entity_fixture,
            financials_fixture=args.financials_fixture,
            submissions_fixture=args.submissions_fixture,
            api_key=args.twelve_data_api_key,
            sec_user_agent=args.sec_user_agent,
            cache_dir=args.cache_dir,
            refresh_cache=args.refresh_cache,
            announcement_limit=args.announcement_limit,
            timeout=args.timeout,
        )
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
