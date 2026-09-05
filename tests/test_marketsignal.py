from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from marketsignal import PipelineError, clean_news, clean_prices, run_pipeline


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
            self.assertEqual(["README", "行情数据", "新闻舆情", "数据质量"], workbook.sheetnames)
            self.assertEqual("field", workbook["README"]["A1"].value)
            self.assertEqual("symbol", workbook["README"]["A2"].value)
            self.assertEqual("AAPL", workbook["README"]["B2"].value)
            self.assertEqual(6, workbook["行情数据"].max_row)
            self.assertEqual(3, workbook["新闻舆情"].max_row)
            quality_rows = list(workbook["数据质量"].iter_rows(min_row=2, values_only=True))
            self.assertIn(("prices", "out_of_range", 0, "rows outside request boundaries"), quality_rows)
            self.assertIn(("news", "out_of_range", 0, "rows outside request boundaries"), quality_rows)
            self.assertIn(("pipeline", "status", "pass", "warning means a source returned no clean rows"), quality_rows)
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


if __name__ == "__main__":
    unittest.main()
