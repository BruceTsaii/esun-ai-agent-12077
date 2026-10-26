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
