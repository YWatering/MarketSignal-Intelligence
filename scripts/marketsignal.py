#!/usr/bin/env python3
"""Stage-one data collection, cleaning, and Excel export for MarketSignal Intelligence."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICES_FIXTURE = PROJECT_ROOT / "fixtures" / "aapl_prices.json"
DEFAULT_NEWS_FIXTURE = PROJECT_ROOT / "fixtures" / "aapl_news.xml"
TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
YAHOO_NEWS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
USER_AGENT = "MarketSignal-Intelligence/0.1"
DATE_FORMAT = "%Y-%m-%d"


class PipelineError(RuntimeError):
    """Expected user-facing pipeline failure."""


def _parse_date(value: str, field_name: str) -> str:
    try:
        parsed = datetime.strptime(value, DATE_FORMAT)
    except ValueError as exc:
        raise PipelineError(f"{field_name} must use YYYY-MM-DD format: {value}") from exc
    return parsed.strftime(DATE_FORMAT)


def _validate_request(symbol: str, start_date: str | None, end_date: str | None, mode: str, output: Path) -> None:
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


def _fetch_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, application/rss+xml, application/xml, text/xml",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PipelineError(f"source request failed: {url} ({reason})") from exc


def _fetch_json(url: str, timeout: int) -> dict[str, Any]:
    try:
        payload = json.loads(_fetch_text(url, timeout))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"source returned invalid JSON: {url}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"source returned an unexpected JSON shape: {url}")
    return payload


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
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"fixture not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"fixture is not valid JSON: {path}") from exc


def _load_xml(path: Path) -> ET.Element:
    try:
        return ET.fromstring(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"fixture not found: {path}") from exc
    except ET.ParseError as exc:
        raise PipelineError(f"fixture is not valid XML: {path}") from exc


def _normalize_price_rows(payload: dict[str, Any], source: str) -> list[dict[str, Any]]:
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
            }
        )
    return rows


def _normalize_news_rows(root: ET.Element, source: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        source_element = _child_element(item, "source")
        publisher = (source_element.text or "").strip() if source_element is not None else source
        rows.append(
            {
                "published_at": _child_text(item, "pubDate"),
                "title": html.unescape(_child_text(item, "title")),
                "source": publisher or source,
                "url": _child_text(item, "link"),
                "summary": html.unescape(_child_text(item, "description")),
                "sentiment": "unclassified",
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
            date = _parse_date(str(row.get("date", "")), "price date")
            values = {field: float(row[field]) for field in ("open", "high", "low", "close")}
            volume = int(float(row["volume"]))
            if values["high"] < values["low"] or volume < 0:
                raise ValueError("invalid price range or volume")
        except (KeyError, TypeError, ValueError, PipelineError):
            invalid_count += 1
            continue
        if date in seen_dates:
            duplicate_count += 1
            continue
        seen_dates.add(date)
        cleaned.append({"date": date, **values, "volume": volume, "source": row.get("source", "unknown")})
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
        cleaned.append(
            {
                "published_at": published_at,
                "title": title,
                "source": str(row.get("source", "unknown")).strip() or "unknown",
                "url": url,
                "summary": str(row.get("summary", "")).strip(),
                "sentiment": str(row.get("sentiment", "unclassified")).strip() or "unclassified",
            }
        )
    cleaned.sort(key=lambda item: item["published_at"], reverse=True)
    return cleaned, {
        "raw_rows": len(source_rows),
        "clean_rows": len(cleaned),
        "duplicates_removed": duplicate_count,
        "invalid_rows": invalid_count,
    }


def filter_prices_by_range(
    rows: Iterable[dict[str, Any]], start_date: str | None, end_date: str | None
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    excluded_count = 0
    for row in rows:
        date = row["date"]
        if (start_date and date < start_date) or (end_date and date > end_date):
            excluded_count += 1
            continue
        filtered.append(row)
    return filtered, excluded_count


def filter_news_by_range(
    rows: Iterable[dict[str, Any]], start_date: str | None, end_date: str | None
) -> tuple[list[dict[str, Any]], int]:
    if not start_date and not end_date:
        return list(rows), 0
    filtered: list[dict[str, Any]] = []
    excluded_count = 0
    for row in rows:
        try:
            publication_date = parsedate_to_datetime(row["published_at"]).date().strftime(DATE_FORMAT)
        except (TypeError, ValueError, IndexError):
            excluded_count += 1
            continue
        if (start_date and publication_date < start_date) or (end_date and publication_date > end_date):
            excluded_count += 1
            continue
        filtered.append(row)
    return filtered, excluded_count


def fetch_online_prices(symbol: str, start_date: str | None, end_date: str | None, api_key: str, timeout: int) -> tuple[list[dict[str, Any]], str]:
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
    payload = _fetch_json(url, timeout)
    if payload.get("status") == "error" or "code" in payload:
        message = payload.get("message", "price source rejected the request")
        raise PipelineError(f"Twelve Data error: {message}")
    return _normalize_price_rows(payload, "Twelve Data"), url


def fetch_online_news(symbol: str, timeout: int) -> tuple[list[dict[str, Any]], str]:
    params = {"s": symbol, "region": "US", "lang": "en-US"}
    url = f"{YAHOO_NEWS_URL}?{urllib.parse.urlencode(params)}"
    try:
        root = ET.fromstring(_fetch_text(url, timeout))
    except ET.ParseError as exc:
        raise PipelineError("Yahoo Finance returned invalid RSS/XML") from exc
    return _normalize_news_rows(root, "Yahoo Finance RSS"), url


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
        sheet.column_dimensions[get_column_letter(column_index)].width = min(max(longest + 2, 12), 48)
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
        if cell.value:
            cell.hyperlink = str(cell.value)
            cell.style = "Hyperlink"


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
    prices: list[dict[str, Any]],
    news: list[dict[str, Any]],
    price_quality: dict[str, int],
    news_quality: dict[str, int],
    price_source: str,
    news_source: str,
) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.creator = "MarketSignal Intelligence"
    workbook.properties.title = "MarketSignal Intelligence stage-one market report"

    readme = workbook.create_sheet("README")
    readme_rows = [
        ["symbol", symbol, "Single-stock stage-one run"],
        ["start_date", start_date or "not specified", "Inclusive request boundary"],
        ["end_date", end_date or "not specified", "Inclusive request boundary"],
        ["mode", mode, "fixture is deterministic; online uses source adapters"],
        ["price_source", price_source, "Source URL or fixture path"],
        ["news_source", news_source, "Source URL or fixture path"],
        ["price_rows", len(prices), "Rows after cleaning"],
        ["news_rows", len(news), "Rows after cleaning"],
        ["forecast_status", "not implemented in stage one", "Forecasting is planned for a later stage"],
        ["limitations", "Financial statements and sentiment scoring are not included", "Do not treat this workbook as investment advice"],
    ]
    _write_table(readme, ["field", "value", "notes"], readme_rows)

    price_sheet = workbook.create_sheet("行情数据")
    price_rows = [
        [row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"], row["source"]]
        for row in prices
    ]
    _write_table(price_sheet, ["date", "open", "high", "low", "close", "volume", "source"], price_rows)
    for row in price_sheet.iter_rows(min_row=2, min_col=2, max_col=5):
        for cell in row:
            cell.number_format = "0.0000"
    for cell in price_sheet[1]:
        if cell.value == "volume":
            for data_cell in price_sheet.iter_cols(min_col=cell.column, max_col=cell.column, min_row=2):
                for value_cell in data_cell:
                    value_cell.number_format = "#,##0"

    news_sheet = workbook.create_sheet("新闻舆情")
    news_rows = [
        [row["published_at"], row["title"], row["source"], row["url"], row["summary"], row["sentiment"]]
        for row in news
    ]
    _write_table(news_sheet, ["published_at", "title", "source", "url", "summary", "sentiment"], news_rows)
    _add_hyperlinks(news_sheet, "url")

    quality_sheet = workbook.create_sheet("数据质量")
    quality_rows = [
        ["prices", "raw_rows", price_quality["raw_rows"], "rows received from source"],
        ["prices", "clean_rows", price_quality["clean_rows"], "rows written to workbook"],
        ["prices", "duplicates_removed", price_quality["duplicates_removed"], "duplicate dates removed"],
        ["prices", "invalid_rows", price_quality["invalid_rows"], "invalid rows discarded"],
        ["prices", "out_of_range", price_quality["out_of_range"], "rows outside request boundaries"],
        ["news", "raw_rows", news_quality["raw_rows"], "rows received from source"],
        ["news", "clean_rows", news_quality["clean_rows"], "rows written to workbook"],
        ["news", "duplicates_removed", news_quality["duplicates_removed"], "duplicate records removed"],
        ["news", "invalid_rows", news_quality["invalid_rows"], "invalid rows discarded"],
        ["news", "out_of_range", news_quality["out_of_range"], "rows outside request boundaries"],
        ["pipeline", "status", "pass" if prices and news else "warning", "warning means a source returned no clean rows"],
    ]
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
    prices_fixture: Path = DEFAULT_PRICES_FIXTURE,
    news_fixture: Path = DEFAULT_NEWS_FIXTURE,
    api_key: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    _validate_request(symbol, start_date, end_date, mode, output)
    if mode == "fixture":
        price_payload = _load_json(prices_fixture)
        news_root = _load_xml(news_fixture)
        raw_prices = _normalize_price_rows(price_payload, f"fixture:{prices_fixture}")
        raw_news = _normalize_news_rows(news_root, f"fixture:{news_fixture}")
        price_source = f"fixture:{prices_fixture}"
        news_source = f"fixture:{news_fixture}"
    else:
        key = api_key or os.environ.get("MARKETSIGNAL_TWELVE_DATA_API_KEY")
        if not key:
            raise PipelineError("online mode requires --twelve-data-api-key or MARKETSIGNAL_TWELVE_DATA_API_KEY")
        raw_prices, price_source = fetch_online_prices(symbol, start_date, end_date, key, timeout)
        raw_news, news_source = fetch_online_news(symbol, timeout)

    prices, price_quality = clean_prices(raw_prices)
    news, news_quality = clean_news(raw_news)
    prices, price_quality["out_of_range"] = filter_prices_by_range(prices, start_date, end_date)
    news, news_quality["out_of_range"] = filter_news_by_range(news, start_date, end_date)
    if mode == "online" and not prices:
        raise PipelineError("online price source returned no valid rows")
    if mode == "online" and not news:
        raise PipelineError("online news source returned no valid rows")
    build_workbook(
        output,
        symbol,
        start_date,
        end_date,
        mode,
        prices,
        news,
        price_quality,
        news_quality,
        price_source,
        news_source,
    )
    return {
        "symbol": symbol,
        "mode": mode,
        "output": str(output),
        "price_rows": len(prices),
        "news_rows": len(news),
        "price_quality": price_quality,
        "news_quality": news_quality,
        "status": "pass" if prices and news else "warning",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect market and news data into a traceable Excel workbook")
    parser.add_argument("--symbol", required=True, help="single stock symbol")
    parser.add_argument("--start-date", help="inclusive YYYY-MM-DD date")
    parser.add_argument("--end-date", help="inclusive YYYY-MM-DD date")
    parser.add_argument("--mode", choices=["fixture", "online"], default="fixture")
    parser.add_argument("--output", required=True, type=Path, help=".xlsx output path")
    parser.add_argument("--prices-fixture", type=Path, default=DEFAULT_PRICES_FIXTURE)
    parser.add_argument("--news-fixture", type=Path, default=DEFAULT_NEWS_FIXTURE)
    parser.add_argument("--twelve-data-api-key")
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
            prices_fixture=args.prices_fixture,
            news_fixture=args.news_fixture,
            api_key=args.twelve_data_api_key,
            timeout=args.timeout,
        )
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
