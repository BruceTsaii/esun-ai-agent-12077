from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from .collectors import collect_history, collect_quotes, public_fetch
from .common import AgentError, load_json, sha256, stamp, write_json
from .diagnostics import test_gemini
from .evidence import EvidenceBundle, build_observations, verify_bundle
from .extraction import prepare
from .models import DataPack, RunConfig
from .pipeline import run
from .providers import Provider
from .validation import validate_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description="玉山 AI CUP 2026 主動台股事件傳導精選")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="檢查 DataPack 與摘要；不呼叫模型")
    inspect.add_argument("datapack", type=Path)
    doctor = sub.add_parser("doctor", help="檢查正式模式前置需求，不顯示金鑰")
    doctor.add_argument("--config", type=Path, default=Path("config/codex.json"))
    doctor.add_argument("--datapack", type=Path, required=True)
    doctor.add_argument("--evidence", type=Path)
    probe = sub.add_parser(
        "test-gemini", help="最小 Gemini 請求，遮蔽錯誤並區分憑證/連線/權限/配額"
    )
    probe.add_argument("--config", type=Path, default=Path("config/google.json"))
    probe.add_argument("--output", type=Path)
    model_probe = sub.add_parser("test-model", help="測試設定的模型執行器（含 Codex CLI）")
    model_probe.add_argument("--config", type=Path, default=Path("config/codex.json"))
    execute = sub.add_parser("run", help="執行 Agent，只有正式檢核全通過才產出正式檔名")
    execute.add_argument("--datapack", type=Path, required=True)
    execute.add_argument("--evidence", type=Path, required=True)
    execute.add_argument("--config", type=Path, default=Path("config/codex.json"))
    execute.add_argument("--output", type=Path, default=Path("runs"))
    execute.add_argument("--mode", choices=["live", "test"], default="live")
    execute.add_argument("--test-response", type=Path)
    validate = sub.add_parser("validate", help="獨立驗證 D-Plan、官方公式與證據鏈")
    validate.add_argument("plan", type=Path)
    validate.add_argument("--datapack", type=Path, required=True)
    validate.add_argument("--evidence", type=Path, required=True)
    validate.add_argument("--config", type=Path, default=Path("config/codex.json"))
    validate.add_argument("--mode", choices=["live", "test"], default="live")
    extract = sub.add_parser(
        "prepare-evidence", help="模型從公開來源快照擷取事件/關係，再逐條核對原文"
    )
    extract.add_argument("--manifest", type=Path, required=True)
    extract.add_argument("--datapack", type=Path, required=True)
    extract.add_argument("--config", type=Path, default=Path("config/codex.json"))
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument("--audit-root", type=Path, default=Path("runs"))
    extract.add_argument("--mode", choices=["live", "test"], default="live")
    quotes = sub.add_parser("collect-quotes", help="擷取並核對上市/上櫃最新官方收盤價")
    quotes.add_argument("--date", type=date.fromisoformat, required=True)
    quotes.add_argument("--output", type=Path, required=True)
    historical = sub.add_parser("collect-history", help="擷取指定交易日的官方上市/上櫃收盤價")
    historical.add_argument("--date", type=date.fromisoformat, required=True)
    historical.add_argument("--market", choices=["twse", "tpex"], required=True)
    historical.add_argument("--output", type=Path, required=True)
    fetch = sub.add_parser("fetch-source", help="留存公開來源原始快照與雜湊；不自動推定事實")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            dp = DataPack.model_validate(load_json(args.datapack))
            print(json.dumps(dp.summary(), ensure_ascii=False, indent=2))
            dp.ready()
        elif args.command == "doctor":
            cfg = RunConfig.model_validate(load_json(args.config))
            issues = []
            key = cfg.model.api_key_env
            if cfg.model.execution_backend == "codex_cli":
                print(f"模型 {cfg.model.model}: 使用 Codex CLI 既有登入，不要求 API Key")
            else:
                print(
                    f"模型 {cfg.model.provider}/{cfg.model.model}: {key} "
                    + ("已設定" if os.environ.get(key) else "缺少")
                )
            try:
                Provider(cfg.model).ready()
            except AgentError as e:
                issues.append(str(e))
            dp = DataPack.model_validate(load_json(args.datapack))
            print(json.dumps(dp.summary(), ensure_ascii=False, indent=2))
            try:
                dp.ready()
            except AgentError as e:
                issues.append(str(e))
            if args.evidence:
                try:
                    b = EvidenceBundle.model_validate(load_json(args.evidence))
                    v = verify_bundle(b, args.evidence.parent, dp, cfg.strategy, "live")
                    _, obs, _, features = build_observations(b, v)
                    print(f"證據核對: {len(obs)} 事實 / {len(features)} 價格趨勢")
                    from .common import resource

                    required = {r["code"] for r in resource("universe.json")["active_etfs"]}
                    missing = required - {r.code for r in b.benchmarks}
                    if missing:
                        issues.append(f"缺 {len(missing)} 檔主動 ETF 當期持股")
                except (AgentError, ValidationError) as e:
                    issues.append(str(e) if isinstance(e, AgentError) else "證據格式錯誤")
            else:
                issues.append("尚未提供正式行情、公開事件、企業關係與 ETF 持股證據包")
            if issues:
                raise AgentError("；".join(issues))
            print("前置資料檢查通過；尚未呼叫模型，也未產生或提交 D-Plan")
        elif args.command == "test-gemini":
            cfg = RunConfig.model_validate(load_json(args.config))
            if args.output and args.output.exists():
                raise AgentError("不覆寫既有診斷，請指定新檔名")
            report = test_gemini(cfg.model)
            if args.output:
                write_json(args.output, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 2
        elif args.command == "test-model":
            cfg = RunConfig.model_validate(load_json(args.config))
            result, usage = Provider(cfg.model).generate(
                'Return exactly {"ok":true}. Use no tools.', {"task": "minimal_connectivity_test"}
            )
            passed = result == {"ok": True}
            print(
                json.dumps(
                    {
                        "passed": passed,
                        "execution_backend": cfg.model.execution_backend,
                        "model": cfg.model.model,
                        "usage": usage,
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if passed else 2
        elif args.command == "run":
            folder, report = run(
                args.datapack,
                args.evidence,
                args.config,
                args.output,
                args.mode,
                args.test_response,
            )
            print(
                json.dumps(
                    {
                        "mode": args.mode,
                        "run_dir": str(folder),
                        "passed": report["passed"],
                        "warnings": report["warnings"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "validate":
            dp = DataPack.model_validate(load_json(args.datapack))
            dp.ready()
            cfg = RunConfig.model_validate(load_json(args.config))
            b = EvidenceBundle.model_validate(load_json(args.evidence))
            v = verify_bundle(b, args.evidence.parent, dp, cfg.strategy, args.mode)
            sources, obs, _, _ = build_observations(b, v)
            plan = load_json(args.plan)
            if plan.get("sources") != sources or plan.get("observations") != obs:
                raise AgentError("D-Plan 的來源/事實與已核對證據包不一致")
            if args.mode == "live" and (
                "TEST" in str(plan.get("_NOTE", "")).upper()
                or plan["agent_metadata"]["model_version"].startswith("TEST")
            ):
                raise AgentError("正式模式拒絕測試 D-Plan")
            report = validate_plan(
                plan,
                dp,
                v["prices"],
                b,
                cfg.strategy,
                args.mode,
                args.plan.name if args.mode == "live" else None,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not report["passed"]:
                return 2
        elif args.command == "prepare-evidence":
            folder = prepare(
                args.manifest, args.datapack, args.config, args.output, args.audit_root, args.mode
            )
            print(
                json.dumps(
                    {"evidence": str(args.output), "audit_dir": str(folder)}, ensure_ascii=False
                )
            )
        elif args.command == "collect-quotes":
            print(json.dumps(collect_quotes(args.date, args.output), ensure_ascii=False, indent=2))
        elif args.command == "collect-history":
            print(
                json.dumps(
                    collect_history(args.date, args.market, args.output),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "fetch-source":
            if args.output.exists():
                raise AgentError("不覆寫既有證據快照，請指定新路徑")
            raw = public_fetch(args.url)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(raw)
            metadata = {"url": args.url, "fetched_at": stamp(), "sha256": sha256(raw)}
            write_json(args.output.with_suffix(args.output.suffix + ".meta.json"), metadata)
            print(json.dumps(metadata, ensure_ascii=False))
        return 0
    except (AgentError, OSError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            message = json.dumps(
                exc.errors(include_input=False, include_context=False), ensure_ascii=False
            )
        elif isinstance(exc, AgentError):
            message = str(exc)
        else:
            message = f"檔案/輸入錯誤 ({type(exc).__name__})"
        print(json.dumps({"status": "blocked", "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
