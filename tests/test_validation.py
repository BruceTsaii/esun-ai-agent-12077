import copy

import pytest
from jsonschema import Draft202012Validator

from esun_agent.common import load_json
from esun_agent.validation import validate_plan


def check(s, plan=None, mode="test"):
    return validate_plan(
        plan or s["plan"], s["dp"], s["verified"]["prices"], s["bundle"], s["config"].strategy, mode
    )


def test_official_schema_examples(official_schema):
    validator = Draft202012Validator(official_schema)
    # Both examples meet STRUCTURAL schema, so semantic checks are indispensable.
    for team in ["042", "043"]:
        validator.validate(load_json(f"compitition/D-Plan_TEAM_{team}_2026-10-28.json"))


def test_valid_synthetic_plan(scenario):
    result = check(scenario)
    assert result["passed"], result["errors"]
    assert len(result["active_share"]) == 30


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda p: p["decisions"][0].update(inference_refs=["I999"]), "引用不存在"),
        (lambda p: p["observations"][0].update(source_ref=["S999"]), "引用不存在"),
        (lambda p: p["sources"][0].update(source_id="S01"), "連續"),
        (lambda p: p["orders"][0].update(shares=1000), "官方公式"),
        (lambda p: p["orders"][0].update(shares=1001), "schema"),
        (lambda p: p["market_view"]["posture"].update(net_exposure_intent="reduce"), "方向"),
        (lambda p: p["market_view"]["posture"].update(target_cash_pct_range=[0.2, 0.23]), "現金比"),
        (lambda p: p["decisions"][0].update(funding_for=["D2"]), "僅限賣出"),
        (lambda p: p.update(team_id="TEAM_042"), "隊伍"),
        (lambda p: p["orders"].append(p["orders"][0]), "多筆委託"),
    ],
)
def test_reject_bad_plans(scenario, mutation, match):
    p = copy.deepcopy(scenario["plan"])
    mutation(p)
    result = check(scenario, p)
    assert not result["passed"]
    assert match in " ".join(result["errors"])


def test_missing_active_share_is_not_formally_ready(scenario):
    s = scenario
    s["bundle"].benchmarks = []
    assert not check(s, mode="live")["passed"]
    test_report = check(s)
    assert test_report["passed"] and not test_report["active_share_complete"]


def test_high_overlap_fails(scenario):
    s = scenario
    s["bundle"].benchmarks[0].weights = check(s)["projection"]["weights"]
    result = check(s)
    assert not result["passed"]
    assert any("Active Share 低於" in x for x in result["errors"])


def test_holdings_coverage_and_receivables(scenario):
    from esun_agent.models import DataPack

    s = scenario
    raw = s["dp"].model_dump(mode="json")
    raw["as_of_date"] = "2026-10-23"
    raw["holdings"] = [
        {
            "code": "2454",
            "shares": 0,
            "receivable_shares": 1000,
            "close_price": 100,
            "tradable_weight": None,
        }
    ]
    s["dp"] = DataPack.model_validate(raw)
    p = copy.deepcopy(s["plan"])
    p["decisions"] = [d for d in p["decisions"] if d["ticker"] != "2454"]
    result = check(s, p)
    assert not result["passed"]
    assert any("覆蓋不完整" in x for x in result["errors"])


def test_all_hold_no_trade_roundtrip(scenario):
    from esun_agent.common import stamp
    from esun_agent.models import DataPack
    from esun_agent.pipeline import assemble

    s = scenario
    raw = s["dp"].model_dump(mode="json")
    raw["as_of_date"] = "2026-10-23"
    raw["account"].update(cash=120000000, cash_ratio_tradable=0.12, holdings_count=20)
    raw["holdings"] = [
        {
            "code": r.ticker,
            "shares": 440000,
            "receivable_shares": 0,
            "close_price": 100,
            "tradable_weight": 0.044,
        }
        for r in s["analysis"].analyses
    ]
    dp = DataPack.model_validate(raw)
    a = s["analysis"].model_copy(deep=True)
    a.market_view["posture"]["net_exposure_intent"] = "hold"
    p = assemble(
        a,
        dp,
        s["verified"]["prices"],
        s["plan"]["sources"],
        s["plan"]["observations"],
        "other",
        "TEST-only",
        "test:v1",
        stamp(),
        {},
    )
    assert not p["orders"] and not p["decisions"]
    assert len(p["no_trade_decisions"]) == 20
    report = validate_plan(
        p, dp, s["verified"]["prices"], s["bundle"], s["config"].strategy, "test"
    )
    assert report["passed"], report["errors"]
