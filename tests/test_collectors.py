from datetime import date

import pytest

from esun_agent.collectors import exchange_date, normalize_quotes
from esun_agent.common import AgentError


def test_exchange_dates():
    assert exchange_date("115/10/23") == date(2026, 10, 23)
    assert exchange_date("20261023") == date(2026, 10, 23)


@pytest.mark.parametrize(
    "market,row",
    [
        ("twse", {"Code": "2330", "Date": "1151023", "ClosingPrice": "1,000"}),
        ("tpex", {"SecuritiesCompanyCode": "5274", "Date": "115/10/23", "Close": "1,000"}),
    ],
)
def test_normalize_official_quotes(market, row):
    assert normalize_quotes([row], market, date(2026, 10, 23))[0]["close"] == 1000
    with pytest.raises(AgentError, match="拒絕"):
        normalize_quotes([row], market, date(2026, 10, 26))


def test_no_inferred_exchange_dates():
    with pytest.raises(AgentError, match="缺 Date"):
        normalize_quotes([{"Code": "2330", "ClosingPrice": "100"}], "twse", date(2026, 10, 23))


@pytest.mark.parametrize(
    "market,table",
    [
        (
            "twse",
            {"fields": ["證券代號", "證券名稱", "收盤價"], "data": [["2330", "台積電", "1,000"]]},
        ),
        ("tpex", {"fields": ["代號", "名稱", "收盤"], "data": [["5274", "信驊", "1,000"]]}),
    ],
)
def test_historical_quotes_validate_date_and_named_columns(market, table):
    from esun_agent.collectors import normalize_history

    data = {"stat": "OK", "date": "20261023", "tables": [table]}
    assert normalize_history(data, market, date(2026, 10, 23))[0]["close"] == 1000
    data["date"] = "20261022"
    with pytest.raises(AgentError, match="回傳日期"):
        normalize_history(data, market, date(2026, 10, 23))


def test_twse_legacy_history_columns():
    from esun_agent.collectors import normalize_history

    d = {
        "stat": "OK",
        "date": "20261023",
        "fields9": ["證券代號", "收盤價"],
        "data9": [["2330", "1000"], ["9999", "99999"]],
    }
    rows = normalize_history(d, "twse", date(2026, 10, 23))
    assert len(rows) == 1 and rows[0]["ticker"] == "2330"


def test_official_calendar_observed_holiday_is_not_a_trading_day():
    from esun_agent.collectors import trading_dates

    schedule = [
        {"Date": "1151026", "Name": "光復節補假"},
        {"Date": "1150102", "Name": "國曆新年開始交易日"},
    ]
    days = trading_dates(2026, schedule)
    assert "2026-10-26" not in days
    assert days[days.index("2026-10-27") - 1] == "2026-10-23"


def test_http_200_with_empty_price_table_is_not_ready():
    from esun_agent.collectors import normalize_history

    body = {
        "stat": "ok",
        "date": "20261023",
        "tables": [{"fields": ["代號", "名稱", "收盤"], "data": []}],
    }
    with pytest.raises(AgentError, match="沒有官方股票池價格"):
        normalize_history(body, "tpex", date(2026, 10, 23))


def test_incomplete_historical_row_is_reported():
    from esun_agent.collectors import normalize_history

    body = {
        "stat": "ok",
        "date": "20261023",
        "tables": [{"fields": ["代號", "名稱", "收盤"], "data": [["5274"]]}],
    }
    with pytest.raises(AgentError, match="欄位不完整"):
        normalize_history(body, "tpex", date(2026, 10, 23))
