from __future__ import annotations

import json
import gzip
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from marketsignal import (
    PipelineError,
    _redact_url,
    _cache_path,
    _fetch_text,
    _sec_headers,
    clean_financials,
    clean_news,
    clean_prices,
    extract_financial_rows,
    run_pipeline,
)


class MarketSignalTests(unittest.TestCase):
    def test_clean_prices_removes_duplicate_and_invalid_rows(self) -> None:
        rows = [
            {"date": "2026-08-11", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "10"},
            {"date": "2026-08-11", "open": "10", "high": "12", "low": "9", "close": "11", "volume": "10"},
            {"date": "2026-08-12", "open": "10", "high": "8", "low": "9", "close": "9", "volume": "10"},
        ]
        cleaned, quality = clean_prices(rows)
        self.assertEqual(1, len(cleaned))
        self.assertEqual(1, quality["duplicates_removed"])
        self.assertEqual(1, quality["invalid_rows"])

    def test_clean_news_removes_duplicate_and_missing_title(self) -> None:
        rows = [
            {"title": "Headline", "url": "https://example.com/1", "published_at": "time"},
            {"title": "Headline", "url": "https://example.com/1", "published_at": "time"},
            {"title": "", "url": "https://example.com/2", "published_at": "time"},
        ]
        cleaned, quality = clean_news(rows)
        self.assertEqual(1, len(cleaned))
        self.assertEqual(1, quality["duplicates_removed"])
        self.assertEqual(1, quality["invalid_rows"])

    def test_fixture_pipeline_generates_required_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "report.xlsx"
            summary = run_pipeline(
                symbol="AAPL",
                start_date="2026-08-03",
                end_date="2026-08-14",
                mode="fixture",
                output=output,
            )
            self.assertTrue(output.exists())
            self.assertEqual("pass", summary["status"])
            self.assertEqual(5, summary["price_rows"])
            self.assertEqual(2, summary["news_rows"])
            self.assertEqual(1, summary["price_quality"]["duplicates_removed"])
            self.assertEqual(1, summary["price_quality"]["invalid_rows"])
            workbook = load_workbook(output, data_only=True)
            self.assertEqual(
                ["README", "主体信息", "行情数据", "财务数据", "新闻舆情", "公告数据", "指标分析", "数据来源", "数据质量"],
                workbook.sheetnames,
            )
            self.assertEqual("field", workbook["README"]["A1"].value)
            self.assertEqual("symbol", workbook["README"]["A2"].value)
            self.assertEqual("AAPL", workbook["README"]["B2"].value)
            self.assertEqual("Apple Inc.", workbook["主体信息"]["B2"].value)
            self.assertEqual(6, workbook["行情数据"].max_row)
            self.assertEqual(11, workbook["财务数据"].max_row)
            self.assertEqual(3, workbook["新闻舆情"].max_row)
            self.assertEqual(2, workbook["公告数据"].max_row)
            quality_rows = list(workbook["数据质量"].iter_rows(min_row=2, values_only=True))
            self.assertIn(("prices", "out_of_range", 0, "processing quality metric"), quality_rows)
            self.assertIn(("news", "out_of_range", 0, "processing quality metric"), quality_rows)
            self.assertIn(("pipeline", "status", "pass", "warning means one or more required datasets have no clean rows"), quality_rows)
            self.assertGreater(workbook["指标分析"].max_row, 1)
            self.assertEqual(6, workbook["数据来源"].max_row)
            source_locations = [
                row[5] for row in workbook["数据来源"].iter_rows(min_row=2, values_only=True)
            ]
            self.assertTrue(all(not str(location).startswith("fixture:/") for location in source_locations))
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn("docProps/core.xml", archive.namelist())

    def test_fixture_pipeline_applies_requested_date_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = run_pipeline(
                symbol="AAPL",
                start_date="2026-08-12",
                end_date="2026-08-14",
                mode="fixture",
                output=Path(temporary_directory) / "report.xlsx",
            )
            self.assertEqual(3, summary["price_rows"])
            self.assertEqual(2, summary["news_rows"])
            self.assertEqual(2, summary["price_quality"]["out_of_range"])
            self.assertEqual(0, summary["news_quality"]["out_of_range"])

    def test_invalid_range_fails_before_pipeline_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(PipelineError):
                run_pipeline(
                    symbol="AAPL",
                    start_date="2026-08-14",
                    end_date="2026-08-03",
                    mode="fixture",
                    output=Path(temporary_directory) / "report.xlsx",
                )

    def test_financial_rows_are_normalized(self) -> None:
        payload = json.loads((ROOT / "fixtures" / "aapl_financials.json").read_text(encoding="utf-8"))
        rows = extract_financial_rows(payload, "fixture")
        cleaned, quality = clean_financials(rows)
        self.assertEqual(10, len(cleaned))
        self.assertEqual(0, quality["invalid_rows"])
        self.assertTrue({row["metric"] for row in cleaned} >= {"revenue", "net_income", "assets"})

    def test_financial_rows_keep_distinct_reporting_periods(self) -> None:
        rows = [
            {
                "period_start": "2026-04-01",
                "period_end": "2026-06-30",
                "filed_date": "2026-08-01",
                "form": "10-Q",
                "metric": "revenue",
                "metric_label": "Revenue",
                "value": 94,
                "unit": "USD",
            },
            {
                "period_start": "2026-01-01",
                "period_end": "2026-06-30",
                "filed_date": "2026-08-01",
                "form": "10-Q",
                "metric": "revenue",
                "metric_label": "Revenue",
                "value": 182,
                "unit": "USD",
            },
        ]
        cleaned, quality = clean_financials(rows)
        self.assertEqual(2, len(cleaned))
        self.assertEqual(0, quality["duplicates_removed"])
        self.assertEqual({"2026-04-01", "2026-01-01"}, {row["period_start"] for row in cleaned})

    def test_sec_user_agent_and_url_redaction(self) -> None:
        self.assertEqual(
            "https://example.test/data?apikey=redacted&symbol=AAPL",
            _redact_url("https://example.test/data?apikey=secret&symbol=AAPL"),
        )
        self.assertEqual(
            {"User-Agent": "MarketSignal test@example.com", "Accept-Encoding": "gzip, deflate"},
            _sec_headers("MarketSignal test@example.com"),
        )
        with self.assertRaises(PipelineError):
            _sec_headers("MarketSignal without contact")

    def test_response_cache_avoids_repeat_request(self) -> None:
        class FakeResponse:
            class Headers:
                def get(self, name, default=""):
                    return "gzip" if name == "Content-Encoding" else default

            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return gzip.compress(b"{\"ok\": true}")

        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory)
            url = "https://example.test/payload"
            with patch("marketsignal.urllib.request.urlopen", return_value=FakeResponse()) as request:
                content, cache_status = _fetch_text(url, 1, cache_dir=cache_dir)
                self.assertEqual("miss", cache_status)
                self.assertEqual('{"ok": true}', content)
                self.assertEqual(1, request.call_count)
            content, cache_status = _fetch_text(url, 1, cache_dir=cache_dir)
            self.assertEqual("hit", cache_status)
            self.assertEqual('{"ok": true}', content)
            self.assertTrue(_cache_path(cache_dir, url).exists())


if __name__ == "__main__":
    unittest.main()
