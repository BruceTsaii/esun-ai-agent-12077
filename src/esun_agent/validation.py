"""Official schema plus checks not expressible in JSON Schema."""

from __future__ import annotations

from datetime import datetime

from jsonschema import Draft202012Validator, FormatChecker

from .common import AgentError, now, resource
from .evidence import EvidenceBundle
from .models import DataPack, StrategyConfig
from .portfolio import active_share, derive_orders, project


def validate_plan(
    plan: dict,
    dp: DataPack,
    prices: dict,
    bundle: EvidenceBundle,
    config: StrategyConfig,
    mode: str,
    filename: str | None = None,
) -> dict:
    errors, warnings = [], []
    schema = Draft202012Validator(resource("dplan.schema.json"), format_checker=FormatChecker())
    for e in schema.iter_errors(plan):
        errors.append(f"schema {'.'.join(map(str, e.absolute_path))}: {e.message}")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings}
    if plan["schema_version"] != "4.3":
        errors.append("本版只驗證官方 4.3")
    if plan["team_id"] != dp.team_id or plan["trade_date"] != str(dp.trade_date):
        errors.append("隊伍/交易日與真實 DataPack 不符")
    expected_name = f"D-Plan_{dp.team_id}_{dp.trade_date}.json"
    if filename is not None and filename != expected_name:
        errors.append(f"正式檔名應為 {expected_name}")
    levels = [
        ("sources", "source_id", "S"),
        ("observations", "obs_id", "O"),
        ("inferences", "inf_id", "I"),
        ("decisions", "decision_id", "D"),
    ]
    maps = {}
    for key, field, prefix in levels:
        rows = plan[key]
        ids = [r[field] for r in rows]
        if ids != [f"{prefix}{i}" for i in range(1, len(rows) + 1)]:
            errors.append(f"{key}: ID 須從 1 連續且唯一，不得跳號/前導零")
        maps[key] = {r[field]: r for r in rows}

    def refs(rows, field, target):
        for row in rows:
            values = row[field]
            values = values if isinstance(values, list) else [values]
            if len(values) != len(set(values)):
                errors.append(f"{field}: 引用重複")
            for value in values:
                if value not in maps[target]:
                    errors.append(f"{field}: 引用不存在 {value}")

    refs(plan["observations"], "source_ref", "sources")
    refs([plan["market_view"]], "basis_refs", "observations")
    refs(plan["inferences"], "premise_refs", "observations")
    refs(plan["decisions"], "inference_refs", "inferences")
    refs(plan["no_trade_decisions"], "reason_refs", "inferences")
    refs(plan["orders"], "decision_ref", "decisions")
    decisions, holds = plan["decisions"], plan["no_trade_decisions"]
    tickers = [d["ticker"] for d in decisions + holds]
    if len(tickers) != len(set(tickers)):
        errors.append("決策重複或異動/續抱兩邊同時出現")
    held = {h.code for h in dp.holdings or []}
    if not held <= set(tickers):
        errors.append("決策覆蓋不完整: " + ",".join(sorted(held - set(tickers))))
    if any(d["ticker"] not in held for d in holds):
        errors.append("非現有持股不能列續抱")
    if len({o["ticker"] for o in plan["orders"]}) != len(plan["orders"]):
        errors.append("同標的多筆委託/當沖不允許")
    metadata = plan["agent_metadata"]
    start = datetime.fromisoformat(metadata["run_started_at"])
    end = datetime.fromisoformat(metadata["run_completed_at"])
    if start > end:
        errors.append("執行完成時間早於開始")
    if mode == "live" and end > now():
        errors.append("執行完成時間在未來")
    for s in plan["sources"]:
        for field in ["content_as_of", "published_at", "fetched_at"]:
            if field in s and datetime.fromisoformat(s[field]) > end:
                errors.append(f"來源 {field} 晚於執行完成")
    try:
        expected = derive_orders(decisions, dp, prices)
        fields = ("ticker", "side", "shares", "decision_ref")
        compact = lambda rows: sorted(tuple(r[k] for k in fields) for r in rows)
        if compact(expected) != compact(plan["orders"]):
            errors.append("委託不符合官方公式或決策對應")
        for d in decisions:
            if d["target_weight"] > (0.25 if d["ticker"] == "2330" else 0.10):
                errors.append(f"{d['ticker']}: 目標權重超限")
        result = project(dp, plan["orders"], prices)
        universe = {r["code"] for r in resource("universe.json")["stocks"]}
        if not set(result["shares"]) <= universe:
            errors.append("持有非官方股票池標的")
        if not 20 <= result["holdings_count"] <= 30:
            errors.append("委託後持股檔數非 20–30")
        if not 0 <= result["cash_ratio"] <= 0.25:
            errors.append("費稅後現金比不在 0–25%")
        for t, w in result["weights"].items():
            if w > (0.25 if t == "2330" else 0.10) + 1e-9:
                # Conservatively reject even passive overruns: no guessed five-day grace period.
                errors.append(f"{t}: 預估費稅後權重超限（被動超限須查官方連續日數）")
        posture = plan["market_view"]["posture"]
        lo, hi = posture["target_cash_pct_range"]
        if lo > hi or not lo - 1e-9 <= result["cash_ratio"] <= hi + 1e-9:
            errors.append("預估現金比不符合宣告區間")
        if lo < config.min_cash_buffer:
            errors.append("宣告現金下限低於策略緩衝")
        net = result["buy_notional"] - result["sell_notional"]
        intent = posture["net_exposure_intent"]
        if (
            (intent == "increase" and net <= 0)
            or (intent == "reduce" and net >= 0)
            or (intent == "hold" and abs(net) > 0.02 * dp.account.nav_tradable)
        ):
            errors.append("淨買賣方向不符合 market_view 宣告")
        funding_ids = set()
        for d in decisions:
            if "funding_for" not in d:
                continue
            funding_ids.add(d["decision_id"])
            if d["action"] not in {"TRIM", "SELL_ALL"}:
                errors.append("funding_for 僅限賣出決策")
            for ref in d["funding_for"]:
                target = maps["decisions"].get(ref)
                if not target or target["action"] not in {"BUY", "ADD"}:
                    errors.append("funding_for 未指向同日買進決策")
        if funding_ids:
            without = [o for o in plan["orders"] if o["decision_ref"] not in funding_ids]
            if project(dp, without, prices)["cash_ratio"] >= lo:
                errors.append("資金缺口不存在，不能宣稱 funding_for")
        stress = project(dp, plan["orders"], prices, stress=config.stress_price_move)
        if not 0 <= stress["cash_ratio"] <= 0.25:
            errors.append("不利成交價壓力測試超出現金限制，需保留更多緩衝")
        benchmark_codes = [b.code for b in bundle.benchmarks]
        required = {r["code"] for r in resource("universe.json")["active_etfs"]}
        missing = sorted(required - set(benchmark_codes))
        if len(set(benchmark_codes)) != len(benchmark_codes):
            errors.append("主動 ETF 基準重複")
        active = {}
        for b in bundle.benchmarks:
            raw = active_share(result["weights"], b.weights)
            normalized = active_share(result["weights"], b.weights, normalized=True)
            active[b.code] = {"nav_weight_top15": raw, "normalized_top15": normalized}
            if min(raw, normalized) < 0.15:
                errors.append(f"{b.code}: Active Share 低於 15%（採兩種解讀較嚴者）")
        if missing:
            msg = "Active Share 尚缺當期 ETF 持股: " + ",".join(missing)
            (errors if mode == "live" else warnings).append(msg)
        warnings.extend(
            [
                "預估用 T-1 收盤價；實際 T 日均價與收盤結算尚未驗證",
                "費稅逐筆向上取整為預留額，非主辦方未揭露的實際進位規則",
                "Active Share 前15大是否再正規化未明，並列兩種結果並採較嚴者",
            ]
        )
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": warnings,
            "projection": result,
            "stress_projection": stress,
            "active_share": active,
            "active_share_complete": not missing,
        }
    except (AgentError, KeyError) as exc:
        errors.append(str(exc))
        return {"passed": False, "errors": errors, "warnings": warnings}
