"""Exchange-calendar helpers used by ML forecast outputs."""

from __future__ import annotations

from datetime import date

import pandas as pd

try:
    import exchange_calendars as xcals
except ImportError as exc:  # pragma: no cover - exercised by installation checks
    raise RuntimeError("exchange-calendars is required for market-aware target dates") from exc


CALENDAR_NAMES = {
    "cn_a": "CN_A_SHARE",
    "cn_b": "CN_B_SHARE",
    "hk": "HKEX",
    "us": "NYSE_NASDAQ",
}

EXCHANGE_CODES = {
    "CN_A_SHARE": "XSHG",
    "CN_B_SHARE": "XSHG",
    "HKEX": "XHKG",
    "NYSE_NASDAQ": "XNYS",
}


def calendar_name(market: str) -> str:
    return CALENDAR_NAMES.get(market, market.upper())


def is_trading_day(value: date, calendar: str) -> bool:
    exchange = xcals.get_calendar(EXCHANGE_CODES[calendar])
    timestamp = pd.Timestamp(value)
    return bool(exchange.is_session(timestamp))


def next_trading_date(start: str, steps: int, calendar: str) -> str:
    exchange = xcals.get_calendar(EXCHANGE_CODES[calendar])
    session = exchange.date_to_session(pd.Timestamp(start), direction="next")
    offset = exchange.session_offset(session, int(steps))
    return offset.date().isoformat()
