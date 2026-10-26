from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .analysis import Analysis, check_analysis, instructions
from .common import AgentError, code_snapshot, load_json, resource, stamp, write_json
from .evidence import EvidenceBundle, build_observations, verify_bundle
from .models import DataPack, RunConfig
from .portfolio import derive_orders, target_shares
from .providers import Provider
from .validation import validate_plan


def assemble(a, dp, prices, sources, observations, provider, model, version, started, usage):
    held = {h.code: h.shares for h in dp.holdings}
    decisions, holds, inferences = [], [], []
    for row in a.analyses:
        iid = f"I{len(inferences) + 1}"
        logic = (
            row.logic
            + "；營運敏感度："
            + row.impact_sensitivity
            + "；價格檢查："
            + row.price_review
        )
        inferences.append(
            {
                "inf_id": iid,
                "premise_refs": row.premise_refs,
                "logic": logic,
                "counter_evidence": row.counter_evidence,
            }
        )
        target = target_shares(row.target_weight, dp.account.nav_tradable, prices[row.ticker].close)
        current = held.get(row.ticker, 0)
        if target == current:
            if row.ticker in held:
                holds.append({"ticker": row.ticker, "reason_refs": [iid]})
            continue
        action = (
            ("BUY" if not current else "ADD")
            if target > current
            else ("SELL_ALL" if not target else "TRIM")
        )
        decisions.append(
            {
                "decision_id": f"D{len(decisions) + 1}",
                "ticker": row.ticker,
                "action": action,
                "target_weight": row.target_weight,
                "inference_refs": [iid],
            }
        )
    ids = {d["ticker"]: d["decision_id"] for d in decisions}
    rows = {r.ticker: r for r in a.analyses}
    for d in decisions:
        funding = rows[d["ticker"]].funding_for_tickers
        if funding:
            if any(t not in ids for t in funding):
                raise AgentError("資金調度指向非異動決策")
            d["funding_for"] = [ids[t] for t in funding]
    return {
        "schema_version": "4.3",
        "doc_type": "D-Plan",
        "team_id": dp.team_id,
        "trade_date": str(dp.trade_date),
        "sources": sources,
        "observations": observations,
        "market_view": a.market_view,
        "inferences": inferences,
        "decisions": decisions,
        "no_trade_decisions": holds,
        "orders": derive_orders(decisions, dp, prices),
        "agent_metadata": {
            "model_provider": provider,
            "model_version": model,
            "run_started_at": started,
            "run_completed_at": stamp(),
            "code_version": version,
            **usage,
        },
    }


def run(
    datapack: Path,
    evidence: Path,
    config_path: Path,
    output: Path,
    mode="live",
    response_file: Path | None = None,
    provider_override=None,
):
    # A unique run dir prevents stale successful artifacts being mistaken for a new success.
    run_dir = output / mode / uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    started = stamp()
    record = {
        "mode": mode,
        "is_test": mode == "test",
        "status": "started",
        "started_at": started,
        "code_version": None,
        "attempts": [],
    }
    write_json(run_dir / "run.json", record)
    try:
        config = RunConfig.model_validate(load_json(config_path))
        record["config"] = config.model_dump(mode="json")
        version = code_snapshot(run_dir / "code.zip", record["config"])
        record["code_version"] = version
        write_json(run_dir / "run.json", record)
        dp_raw = load_json(datapack)
        dp = DataPack.model_validate(dp_raw)
        write_json(run_dir / "datapack.json", dp_raw)
        write_json(run_dir / "account-summary.json", dp.summary())
        dp.ready()
        bundle_raw = load_json(evidence)
        bundle = EvidenceBundle.model_validate(bundle_raw)
        if response_file and mode != "test":
            raise AgentError("正式模式禁止預先指定模型回覆/手動配置；請使用模型 API")
        verified = verify_bundle(bundle, evidence.parent, dp, config.strategy, mode)
        sources, observations, obs_keys, features = build_observations(bundle, verified)
        # Retain the exact evidence used, beside the DataPack and code for replay/audit.
        for s in bundle.sources:
            rel = f"snapshots/{s.key}.txt"
            (run_dir / "evidence" / "snapshots").mkdir(parents=True, exist_ok=True)
            raw = (evidence.parent / s.snapshot_path).read_bytes()
            (run_dir / "evidence" / rel).write_bytes(raw)
            for saved in bundle_raw["sources"]:
                if saved["key"] == s.key:
                    saved["snapshot_path"] = rel
        write_json(run_dir / "evidence" / "bundle.json", bundle_raw)
        if mode == "live":
            required = {r["code"] for r in resource("universe.json")["active_etfs"]}
            missing = required - {b.code for b in bundle.benchmarks}
            if missing:
                raise AgentError(
                    "正式模式缺當期主動 ETF 持股，無法驗證 Active Share: "
                    + ",".join(sorted(missing))
                )
        system = instructions()
        (run_dir / "system-prompt.txt").write_text(system)
        payload = {
            "datapack": dp_raw,
            "strategy_config": config.strategy.model_dump(),
            "sources": sources,
            "observations": observations,
            "fact_to_observation": obs_keys,
            "relationships": [r.model_dump() for r in bundle.relationships],
            "facts": [f.model_dump() for f in bundle.facts],
            "features": features,
            "analysis_schema": Analysis.model_json_schema(),
            "official_market_view_schema": resource("dplan.schema.json")["$defs"]["market_view"],
        }
        provider = provider_override or Provider(config.model)
        if not response_file:
            provider.ready()
        totals = {}
        for attempt in range(1, 4):
            write_json(run_dir / f"request-{attempt}.json", payload)
            raw, usage = (
                (load_json(response_file), {})
                if response_file
                else provider.generate(system, payload)
            )
            write_json(run_dir / f"response-{attempt}.json", raw)
            for k, v in usage.items():
                totals[k] = totals.get(k, 0) + v
            try:
                analysis = Analysis.model_validate(raw)
                check_analysis(analysis, dp, bundle, verified, obs_keys, features, config.strategy)
                plan = assemble(
                    analysis,
                    dp,
                    verified["prices"],
                    sources,
                    observations,
                    "other" if response_file else config.model.provider,
                    "TEST_ONLY-synthetic-response" if response_file else config.model.model,
                    version,
                    started,
                    totals,
                )
                if mode == "test":
                    plan["_NOTE"] = "TEST ONLY：合成資料/測試流程，禁止當成正式競賽決策提交。"
                report = validate_plan(plan, dp, verified["prices"], bundle, config.strategy, mode)
                write_json(run_dir / "candidate.json", plan)
                write_json(run_dir / "validation.json", report)
                if not report["passed"]:
                    raise AgentError("；".join(report["errors"]))
                record["attempts"].append({"attempt": attempt, "passed": True, "usage": usage})
                name = (
                    f"D-Plan_{dp.team_id}_{dp.trade_date}.json"
                    if mode == "live"
                    else f"TEST_ONLY_{dp.team_id}_{dp.trade_date}.json"
                )
                write_json(run_dir / name, plan)
                record.update(
                    status="validated" if mode == "live" else "test_validated",
                    completed_at=stamp(),
                    output_file=name,
                    usage=totals,
                )
                write_json(run_dir / "run.json", record)
                return run_dir, report
            except (AgentError, ValidationError) as exc:
                # Pydantic errors exclude actual inputs, which may be proprietary evidence.
                message = (
                    json.dumps(
                        exc.errors(include_input=False, include_context=False), ensure_ascii=False
                    )
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                record["attempts"].append(
                    {"attempt": attempt, "passed": False, "error": message, "usage": usage}
                )
                write_json(run_dir / "run.json", record)
                if response_file or attempt == 3:
                    raise AgentError(message) from None
                payload["previous_response"] = raw
                payload["validation_errors"] = message
        raise AgentError("模型修正次數用盡")
    except (AgentError, ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status="blocked", completed_at=stamp(), error_type=type(exc).__name__)
        record["error"] = (
            str(exc)
            if isinstance(exc, AgentError)
            else "輸入格式或檔案錯誤，請執行 inspect / 檢查資料格式"
        )
        write_json(run_dir / "run.json", record)
        raise AgentError(f"{record['error']}；執行紀錄: {run_dir / 'run.json'}") from None
