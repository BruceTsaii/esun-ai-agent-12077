import copy

import pytest

from esun_agent.analysis import Analysis, check_analysis
from esun_agent.common import AgentError
from esun_agent.evidence import EvidenceBundle, verify_bundle


def verify(s, raw=None, mode="test"):
    return verify_bundle(
        EvidenceBundle.model_validate(raw or s["raw_bundle"]),
        s["root"],
        s["dp"],
        s["config"].strategy,
        mode,
    )


def test_real_mode_rejects_synthetic(scenario):
    with pytest.raises(AgentError, match="is_test"):
        verify(scenario, mode="live")


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda b: b["sources"][0].update(sha256="0" * 64), "SHA256"),
        (lambda b: b["sources"][0].update(snapshot_path="../outside.txt"), "snapshot"),
        (lambda b: b["facts"][0]["citations"][0].update(quote="不存在的新聞來源內容"), "引文"),
        (lambda b: b["facts"][0].update(values={"number": 999999}), "數值"),
        (lambda b: b["prices"][0].update(close=9999), "快照"),
        (lambda b: b["prices"][0].update(date="2026-10-22"), "前日"),
        (lambda b: b["prices"][0].update(source_key="public"), "錯置"),
        (lambda b: b["relationships"][0].update(from_entity="無關企業"), "兩方"),
        (lambda b: b["history"].append(b["history"][0]), "重複"),
    ],
)
def test_evidence_corruption(scenario, mutation, match):
    b = copy.deepcopy(scenario["raw_bundle"])
    mutation(b)
    with pytest.raises(AgentError, match=match):
        verify(scenario, b)


def test_analysis_complete(scenario):
    s = scenario
    check_analysis(
        s["analysis"],
        s["dp"],
        s["bundle"],
        s["verified"],
        s["ids"],
        s["features"],
        s["config"].strategy,
    )


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("relationship_keys", [], "缺事件"),
        ("event_keys", ["does-not-exist"], "傳導路徑"),
        ("counter_refs", ["O999"], "反方引用"),
        ("target_weight", 0.20, "權重超限"),
    ],
)
def test_analysis_missing_evidence(scenario, field, value, match):
    s = scenario
    response = copy.deepcopy(s["response"])
    # first ticker is 2330, so use second ordinary stock for cap check
    response["analyses"][1][field] = value
    with pytest.raises(AgentError, match=match):
        check_analysis(
            Analysis.model_validate(response),
            s["dp"],
            s["bundle"],
            s["verified"],
            s["ids"],
            s["features"],
            s["config"].strategy,
        )


def test_negative_trend_cannot_buy(scenario):
    s = scenario
    features = copy.deepcopy(s["features"])
    features[s["analysis"].analyses[0].ticker]["trend_return"] = -0.1
    with pytest.raises(AgentError, match="正向價格趨勢"):
        check_analysis(
            s["analysis"],
            s["dp"],
            s["bundle"],
            s["verified"],
            s["ids"],
            features,
            s["config"].strategy,
        )


def test_first_day_requires_verified_session_calendar(scenario):
    from esun_agent.common import sha256, write_json

    s = scenario
    raw = copy.deepcopy(s["raw_bundle"])
    days = ["2026-10-22", "2026-10-23", "2026-10-26"]
    path = s["root"] / "calendar.json"
    write_json(path, {"trade_dates": days})
    source = copy.deepcopy(raw["sources"][1])
    source.update(key="calendar", snapshot_path="calendar.json", sha256=sha256(path.read_bytes()))
    raw["sources"].append(source)
    raw["calendar"] = {"source_key": "calendar", "trade_dates": days}
    verify(s, raw)
    raw["calendar"]["trade_dates"] = ["2026-10-22", "2026-10-26"]
    with pytest.raises(AgentError, match="前一交易日"):
        verify(s, raw)


def test_after_close_event_uses_latest_pre_announcement_close(scenario):
    from esun_agent.evidence import build_observations

    s = scenario
    # All synthetic events at Oct 21 14:00 occur AFTER the Oct 21 close of 98.
    t = s["analysis"].analyses[0].ticker
    expected = (100 / 98 - 1) - (108 / 104 - 1)
    assert s["features"][t]["event_relative_returns"]["e0"] == pytest.approx(expected)
    bundle = s["bundle"].model_copy(deep=True)
    bundle.sources[0].published_at = bundle.sources[1].content_as_of  # Oct 23 14:00
    verified = dict(s["verified"], sources={src.key: src for src in bundle.sources})
    _, _, _, features = build_observations(bundle, verified)
    assert "e0" not in features[t]["event_relative_returns"]
