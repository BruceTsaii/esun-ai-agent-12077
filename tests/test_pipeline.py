import copy
from pathlib import Path

import httpx
import pytest

from esun_agent.cli import main
from esun_agent.common import AgentError, load_json, write_json
from esun_agent.models import ModelConfig
from esun_agent.pipeline import run
from esun_agent.providers import Provider

CONFIG = Path("config/google.json")


def test_full_test_workflow_and_replay(scenario):
    s = scenario
    folder, report = run(
        s["root"] / "datapack.json",
        s["root"] / "bundle.json",
        CONFIG,
        s["root"] / "runs",
        "test",
        s["root"] / "response.json",
    )
    assert report["passed"]
    record = load_json(folder / "run.json")
    assert record["status"] == "test_validated"
    plan_path = folder / record["output_file"]
    plan = load_json(plan_path)
    assert plan["agent_metadata"]["model_provider"] == "other"
    assert "TEST" in plan["_NOTE"]
    assert (folder / "code.zip").is_file()
    assert not list(folder.glob("D-Plan_*.json"))
    assert (
        main(
            [
                "validate",
                str(plan_path),
                "--datapack",
                str(folder / "datapack.json"),
                "--evidence",
                str(folder / "evidence/bundle.json"),
                "--mode",
                "test",
            ]
        )
        == 0
    )


def test_formal_mode_cannot_replay_human_targets(scenario):
    s = scenario
    with pytest.raises(AgentError, match="正式模式禁止"):
        run(
            s["root"] / "datapack.json",
            s["root"] / "bundle.json",
            CONFIG,
            s["root"] / "runs",
            "live",
            s["root"] / "response.json",
        )
    assert not list((s["root"] / "runs").rglob("D-Plan_*.json"))
    assert load_json(next((s["root"] / "runs").rglob("run.json")))["status"] == "blocked"


def test_invalid_plan_is_not_exported(scenario):
    s = scenario
    r = copy.deepcopy(s["response"])
    r["analyses"][0]["relationship_keys"] = []
    write_json(s["root"] / "bad.json", r)
    with pytest.raises(AgentError):
        run(
            s["root"] / "datapack.json",
            s["root"] / "bundle.json",
            CONFIG,
            s["root"] / "runs",
            "test",
            s["root"] / "bad.json",
        )
    assert not list((s["root"] / "runs").rglob("TEST_ONLY_*.json"))


def test_model_autonomous_repair(scenario):
    s = scenario

    class Fake:
        calls = 0

        def ready(self):
            pass

        def generate(self, system, payload):
            self.calls += 1
            r = copy.deepcopy(s["response"])
            if self.calls == 1:
                r["analyses"][0]["relationship_keys"] = []
            else:
                assert "validation_errors" in payload
            return r, {"input_tokens": 100, "output_tokens": 200}

    fake = Fake()
    folder, report = run(
        s["root"] / "datapack.json",
        s["root"] / "bundle.json",
        CONFIG,
        s["root"] / "runs",
        "test",
        provider_override=fake,
    )
    assert report["passed"] and fake.calls == 2
    assert load_json(folder / "run.json")["usage"]["input_tokens"] == 200


@pytest.mark.parametrize("provider", ["openai", "google", "anthropic", "other"])
def test_provider_adapter(provider, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret-for-mocked-http-only")
    seen = []

    def handler(req):
        seen.append(req)
        assert "secret-for-mocked-http-only" not in str(req.url)
        if provider in {"openai", "other"}:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": '{"analyses": []}'}}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                },
            )
        if provider == "google":
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": '{"analyses": []}'}]},
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
                },
            )
        return httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"analyses": []}'}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )

    c = ModelConfig(
        provider=provider,
        model="test-model",
        api_key_env="TEST_KEY",
        base_url="https://test.invalid/v1",
    )
    data, usage = Provider(c, transport=httpx.MockTransport(handler)).generate("system", {})
    assert data == {"analyses": []} and usage["input_tokens"] == 10 and seen


def test_missing_key_and_api_errors_do_not_leak(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    c = ModelConfig(model="test-model", api_key_env="MISSING_KEY")
    with pytest.raises(AgentError, match="缺模型憑證"):
        Provider(c).ready()
    monkeypatch.setenv("MISSING_KEY", "never-print-this-secret")

    def handler(req):
        return httpx.Response(401, text="never-print-this-secret")

    with pytest.raises(AgentError) as e:
        Provider(c, transport=httpx.MockTransport(handler)).generate("system", {})
    assert "never-print-this-secret" not in str(e.value)


def test_cli_failure_exit_code(scenario, capsys):
    assert main(["doctor", "--datapack", str(scenario["root"] / "datapack.json")]) == 2
    assert "blocked" in capsys.readouterr().out


def test_automatic_public_event_extraction(scenario):
    from esun_agent.extraction import prepare

    s = scenario
    manifest = copy.deepcopy(s["raw_bundle"])
    response = {"facts": manifest.pop("facts"), "relationships": manifest.pop("relationships")}
    write_json(s["root"] / "manifest.json", manifest)

    class Fake:
        def ready(self):
            pass

        def generate(self, system, payload):
            assert "source_texts" in payload and "universe" in payload
            assert "target_weight" not in system
            return response, {"input_tokens": 50, "output_tokens": 100}

    folder = prepare(
        s["root"] / "manifest.json",
        s["root"] / "datapack.json",
        CONFIG,
        s["root"] / "extracted.json",
        s["root"] / "runs",
        "test",
        Fake(),
    )
    assert load_json(folder / "run.json")["status"] == "extracted"
    from esun_agent.evidence import Fact

    assert load_json(s["root"] / "extracted.json")["facts"] == [
        Fact.model_validate(f).model_dump(mode="json") for f in response["facts"]
    ]


def test_runtime_configuration_changes_code_version(tmp_path):
    import zipfile

    from esun_agent.common import code_snapshot

    a = code_snapshot(tmp_path / "a.zip", {"strategy": {"max_event_weight": 0.3}})
    b = code_snapshot(tmp_path / "b.zip", {"strategy": {"max_event_weight": 0.2}})
    assert a != b
    with zipfile.ZipFile(tmp_path / "b.zip") as archive:
        assert b"0.2" in archive.read("runtime-config.json")
