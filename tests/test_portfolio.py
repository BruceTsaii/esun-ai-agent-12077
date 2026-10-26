import copy
from types import SimpleNamespace

import pytest

from esun_agent.common import AgentError
from esun_agent.models import DataPack
from esun_agent.portfolio import active_share, derive_orders, project, target_shares


def test_decimal_boundary_formula():
    # Decimal boundary avoids binary floating point flooring to the wrong lot.
    assert target_shares(0.03, 1000000, 0.1) == 300000
    assert target_shares(0.045, 102000000, 42.5) == 108000
    assert target_shares(0.23, 102000000, 1480) == 15000


def test_fees_and_cash_use_nav_not_aum(scenario):
    s = scenario
    result = project(s["dp"], s["plan"]["orders"], s["verified"]["prices"])
    assert result["holdings_count"] == 20
    assert result["buy_notional"] == 880000000
    assert result["fee_reserve"] == 1254000
    assert result["cash"] == 118746000
    assert 0.11 < result["cash_ratio"] < 0.13
    assert sum(result["weights"].values()) + result["cash_ratio"] == pytest.approx(1)


def position_pack(scenario, shares=10000):
    d = copy.deepcopy(scenario["raw_dp"])
    t = scenario["plan"]["decisions"][0]["ticker"]
    d["as_of_date"] = "2026-10-23"
    d["account"].update(
        cash=1e9 - shares * 100, cash_ratio_tradable=(1e9 - shares * 100) / 1e9, holdings_count=1
    )
    d["holdings"] = [
        {
            "code": t,
            "shares": shares,
            "receivable_shares": 1000,
            "close_price": 100,
            "tradable_weight": shares * 100 / 1e9,
        }
    ]
    return DataPack.model_validate(d), t


def test_sell_tax_and_no_oversell(scenario):
    dp, t = position_pack(scenario)
    p = scenario["verified"]["prices"]
    order = {"ticker": t, "side": "SELL", "shares": 10000}
    result = project(dp, [order], p)
    assert result["fee_reserve"] == 1425
    assert result["tax_reserve"] == 3000
    assert result["holdings_count"] == 0  # receivable shares do not count
    assert result["cash"] == 1e9 - 4425
    with pytest.raises(AgentError, match="超賣"):
        project(dp, [{**order, "shares": 11000}], p)


def test_no_manual_odd_lot_adjustments(scenario):
    dp, t = position_pack(scenario, shares=10500)
    with pytest.raises(AgentError, match="零股"):
        derive_orders(
            [{"decision_id": "D1", "ticker": t, "action": "SELL_ALL", "target_weight": 0}],
            dp,
            scenario["verified"]["prices"],
        )


def test_net_settlement_not_daily_buy_limit(scenario):
    dp, t = position_pack(scenario)
    # Test a cash-poor account; proceeds from sales cover a same-day different stock buy.
    raw = dp.model_dump(mode="json")
    raw["account"].update(
        cash=1000, nav_tradable=1001000, aum=1001000, cash_ratio_tradable=1000 / 1001000
    )
    raw["holdings"][0]["tradable_weight"] = 1000000 / 1001000
    dp = DataPack.model_validate(raw)
    other = "2454"
    p = {t: SimpleNamespace(close=100), other: SimpleNamespace(close=100)}
    result = project(
        dp,
        [
            {"ticker": other, "side": "BUY", "shares": 9000},
            {"ticker": t, "side": "SELL", "shares": 10000},
        ],
        p,
    )
    assert result["cash"] > 0


def test_active_share_union_and_top15():
    assert active_share({"a": 0.5, "b": 0.3}, {"a": 0.5, "c": 0.3}) == pytest.approx(0.3)
    assert active_share({"a": 0.8}, {"a": 0.4}, normalized=True) == 0
