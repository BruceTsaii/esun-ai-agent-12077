import copy

import pytest
from pydantic import ValidationError

from esun_agent.common import AgentError, load_json, resource
from esun_agent.models import DataPack


def test_real_pack_identity_and_account():
    p = DataPack.model_validate(load_json("compitition/DataPack_647_2026-10-27.json"))
    p.ready()
    assert p.team_id == "TEAM_12077"
    assert str(p.trade_date) == "2026-10-27"
    assert p.account.cash == p.account.nav_tradable == 1e9
    assert p.holdings == []


def test_official_universe():
    u = resource("universe.json")
    assert len(u["stocks"]) == 150
    assert len({r["code"] for r in u["stocks"]}) == 150
    assert sum(r["market"] == "twse" for r in u["stocks"]) == 100
    assert len(u["active_etfs"]) == 30
    assert {r["code"]: r["name"] for r in u["stocks"]}["2330"] == "台積電"


def test_missing_settlement_is_not_empty_portfolio(scenario):
    d = copy.deepcopy(scenario["raw_dp"])
    d.update(missing=["prev_settlement"], account=None, holdings=None)
    p = DataPack.model_validate(d)
    with pytest.raises(AgentError, match="缺資料"):
        p.ready()
    d["holdings"] = []
    with pytest.raises(ValidationError):
        DataPack.model_validate(d)


@pytest.mark.parametrize(
    "field,value",
    [
        ("cash_ratio_tradable", 0.1),
        ("nav_tradable", 9e8),
        ("holdings_count", 1),
        ("cash", float("nan")),
    ],
)
def test_inconsistent_account(scenario, field, value):
    d = copy.deepcopy(scenario["raw_dp"])
    d["account"][field] = value
    with pytest.raises(ValidationError):
        DataPack.model_validate(d)


def test_receivable_cash_is_not_tradable(scenario):
    d = copy.deepcopy(scenario["raw_dp"])
    d["as_of_date"] = "2026-10-23"
    d["account"].update(receivable_cash=10000, aum=1000010000)
    p = DataPack.model_validate(d)
    assert p.account.nav_tradable == 1e9


def test_duplicate_json_and_nan(tmp_path):
    from esun_agent.common import load_json

    for text in ['{"x": 1, "x": 2}', '{"x": NaN}']:
        p = tmp_path / "bad.json"
        p.write_text(text)
        with pytest.raises(AgentError):
            load_json(p)
