"""Generate explicit SYNTHETIC test inputs, never market or account evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from esun_agent.common import resource, sha256, write_json
from esun_agent.evidence import EvidenceBundle, build_observations, verify_bundle
from esun_agent.models import DataPack, StrategyConfig


def generate(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    tickers = [r["code"] for r in resource("universe.json")["stocks"][:20]]
    facts = [
        {
            "key": "market",
            "kind": "market",
            "tickers": [],
            "statement": "SYNTHETIC TEST 市場指數上漲 1 百分點",
            "values": {"change_pct": 1},
            "citations": [{"source_key": "public", "quote": "TEST 市場指數上漲 1 百分點"}],
        }
    ]
    text = ["TEST 市場指數上漲 1 百分點"]
    relationships = []
    for group in range(4):
        event_quote = f"SYNTHETIC 客戶{group} 宣布資本支出增加 10 百分點"
        counter_quote = f"SYNTHETIC 客戶{group} 訂單可能延後，需求風險指標為 2"
        text += [event_quote, counter_quote]
        for key, kind, quote in [
            (f"e{group}", "event", event_quote),
            (f"c{group}", "counter", counter_quote),
        ]:
            facts.append(
                {
                    "key": key,
                    "kind": kind,
                    "entity": f"客戶{group}",
                    "tickers": tickers[group * 5 : (group + 1) * 5],
                    "statement": quote,
                    "values": {"capex_pct": 10} if kind == "event" else {"risk_indicator": 2},
                    "citations": [{"source_key": "public", "quote": quote}],
                }
            )
    prices, history, snapshot = [], [], []
    for i, ticker in enumerate(tickers):
        group = i // 5
        quote = f"SYNTHETIC 客戶{group} 公開列出 {ticker} 為設備供應商，合約數為 3"
        text.append(quote)
        facts.append(
            {
                "key": "r" + ticker,
                "kind": "relation",
                "tickers": [ticker],
                "statement": quote,
                "values": {"contracts": 3},
                "citations": [{"source_key": "public", "quote": quote}],
            }
        )
        relationships.append(
            {
                "key": "link" + ticker,
                "from_entity": f"客戶{group}",
                "to_ticker": ticker,
                "relation": "設備供應商",
                "fact_keys": ["r" + ticker],
            }
        )
        prices.append(
            {"ticker": ticker, "date": "2026-10-23", "close": 100, "source_key": "quotes"}
        )
        for j in range(5):
            day = f"2026-10-{19 + j}"
            history.append(
                {
                    "ticker": ticker,
                    "date": day,
                    "close": 96 + j,
                    "source_key": "quotes",
                    "benchmark_close": 100 + 2 * j,
                    "benchmark_source_key": "quotes",
                }
            )
            snapshot.append({"ticker": ticker, "date": day, "close": 96 + j})
    snapshot += [
        {"ticker": "benchmark", "date": f"2026-10-{19 + j}", "close": 100 + 2 * j} for j in range(5)
    ]
    (root / "public.txt").write_text("\n".join(text))
    write_json(root / "prices.json", {"prices": snapshot, "warning": "SYNTHETIC TEST ONLY"})
    weights = {str(9000 + i): 0.05 for i in range(15)}
    etfs = resource("universe.json")["active_etfs"]
    write_json(
        root / "etfs.json",
        {
            "warning": "SYNTHETIC TEST ONLY",
            "etf_holdings": {
                e["code"]: {"as_of_date": "2026-10-23", "weights": weights} for e in etfs
            },
        },
    )
    sources = []
    for key, path, auth, day in [
        ("public", "public.txt", "other", "21"),
        ("quotes", "prices.json", "twse", "23"),
        ("etfs", "etfs.json", "fininst", "23"),
    ]:
        sources.append(
            {
                "key": key,
                "authority": auth,
                "url": f"https://synthetic.example.com/{key}",
                "content_as_of": f"2026-10-{day}T14:00:00+08:00",
                "fetched_at": "2026-10-24T09:00:00+08:00",
                "snapshot_path": path,
                "sha256": sha256((root / path).read_bytes()),
            }
        )
    bundle = {
        "is_test": True,
        "previous_close_date": "2026-10-23",
        "sources": sources,
        "facts": facts,
        "relationships": relationships,
        "prices": prices,
        "history": history,
        "benchmarks": [
            {
                "code": e["code"],
                "as_of_date": "2026-10-23",
                "source_key": "etfs",
                "weights": weights,
            }
            for e in etfs
        ],
    }
    dp = {
        "schema_version": "3.0",
        "doc_type": "DataPack",
        "team_id": "TEAM_TEST",
        "trade_date": "2026-10-26",
        "as_of_date": None,
        "generated_at": "2026-10-24T09:00:00+08:00",
        "missing": [],
        "prev_day_orders": {"outcome": "none", "void_reasons": [], "excluded_codes": []},
        "account": {
            "cash": 1e9,
            "cash_ratio_tradable": 1,
            "nav_tradable": 1e9,
            "aum": 1e9,
            "receivable_cash": 0,
            "holdings_count": 0,
        },
        "holdings": [],
        "compliance": {"violations": 0, "notices": 0, "incidents": []},
    }
    b = EvidenceBundle.model_validate(bundle)
    d = DataPack.model_validate(dp)
    verified = verify_bundle(b, root, d, StrategyConfig(), "test")
    _, _, ids, features = build_observations(b, verified)
    analyses = []
    for i, ticker in enumerate(tickers):
        group = i // 5
        analyses.append(
            {
                "ticker": ticker,
                "target_weight": 0.044,
                "bucket": "core",
                "event_keys": [f"e{group}"],
                "relationship_keys": ["link" + ticker],
                "premise_refs": [
                    ids[f"e{group}"],
                    ids["r" + ticker],
                    features[ticker]["obs_id"],
                    ids[f"c{group}"],
                ],
                "impact_sensitivity": "合成測試：設備供應關係支持資本支出的營運傳導，敏感度中等。",
                "price_review": "合成測試：事件後相對報酬落後基準，但五日趨勢為正，非單純追落後。",
                "logic": "合成測試：公開事件與關係事實、價格趨勢及反方證據綜合後配置核心部位。",
                "counter_evidence": "合成測試：來源指出訂單可能延後，配置保留現金並分散共同事件。",
                "counter_refs": [ids[f"c{group}"]],
            }
        )
    response = {
        "market_view": {
            "basis_refs": [ids["market"]],
            "logic": "合成測試：市場級事實與事件證據支持適度建立股票曝險，保留費稅與風險緩衝。",
            "regime": "neutral",
            "stance": "neutral",
            "posture": {"net_exposure_intent": "increase", "target_cash_pct_range": [0.08, 0.18]},
            "counter_evidence": "合成測試：資本支出可能延遲，若新證據惡化則減碼。",
        },
        "analyses": analyses,
    }
    write_json(root / "datapack.json", dp)
    write_json(root / "bundle.json", bundle)
    write_json(root / "response.json", response)
    return dp, bundle, response


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path("runs/demo-inputs"))
    args = p.parse_args()
    generate(args.output)
    print(json.dumps({"is_test": True, "output": str(args.output)}))
