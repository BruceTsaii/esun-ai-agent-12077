import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from esun_agent.codex_provider import CodexProvider, failure_detail
from esun_agent.common import AgentError
from esun_agent.models import ModelConfig
from esun_agent.providers import Provider


def config():
    return ModelConfig(provider="openai", model="gpt-5.4", execution_backend="codex_cli")


def fake_process(monkeypatch, *, exit_code=0, events=None, reply='{"ok":true}', login=0):
    monkeypatch.setattr("esun_agent.codex_provider.shutil.which", lambda name: "/fake/codex")
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:] == ["login", "status"]:
            return SimpleNamespace(returncode=login, stdout="Logged in using ChatGPT", stderr="")
        if args[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli TEST", stderr="")
        if reply is not None:
            Path(args[args.index("--output-last-message") + 1]).write_text(reply)
        output = (
            events
            if events is not None
            else [
                {"type": "thread.started", "thread_id": "test-thread"},
                {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 5}},
            ]
        )
        return SimpleNamespace(
            returncode=exit_code, stdout="\n".join(map(json.dumps, output)), stderr=""
        )

    monkeypatch.setattr("esun_agent.codex_provider.subprocess.run", execute)
    return calls


def test_cli_backend_without_any_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = fake_process(monkeypatch)
    provider = Provider(config())
    result, usage = provider.generate("system", {"facts": []})
    assert result == {"ok": True} and usage["input_tokens"] == 12
    args, kwargs = calls[-1]
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in args and "--ignore-user-config" in args
    assert not kwargs.get("shell")
    assert "system" in kwargs["input"] and "<untrusted_input_json>" in kwargs["input"]
    assert provider.last_call["thread_id"] == "test-thread"


def test_no_installed_cli(monkeypatch):
    monkeypatch.setattr("esun_agent.codex_provider.shutil.which", lambda name: None)
    with pytest.raises(AgentError, match="找不到"):
        CodexProvider(config()).ready()


def test_login_is_required(monkeypatch):
    fake_process(monkeypatch, login=1)
    with pytest.raises(AgentError, match="尚未登入"):
        CodexProvider(config()).ready()


def test_cli_provider_identity_must_be_openai():
    with pytest.raises(ValidationError):
        ModelConfig(provider="google", model="some-model", execution_backend="codex_cli")


@pytest.mark.parametrize(
    "events,reply,match",
    [
        ([], '{"ok":true}', "未完成有效回合"),
        ([{"type": "turn.completed"}], None, "未完成有效回合"),
        ([{"type": "turn.completed"}], '{"x":1,"x":2}', "JSON"),
        ([{"type": "turn.completed"}], "[]", "JSON object"),
        ([{"type": "item.completed", "item": {"type": "command_execution"}}], "{}", "額外工具"),
        ([{"type": "turn.failed", "error": {"message": "unauthorized"}}], "{}", "失敗"),
    ],
)
def test_invalid_cli_outputs_cannot_be_used(monkeypatch, events, reply, match):
    fake_process(monkeypatch, events=events, reply=reply)
    with pytest.raises(AgentError, match=match):
        CodexProvider(config()).generate("system", {})


def test_cli_failure_does_not_echo_private_logs(monkeypatch):
    fake_process(monkeypatch, exit_code=1, events=[{"type": "error", "message": "private-secret"}])
    with pytest.raises(AgentError) as caught:
        CodexProvider(config()).generate("system", {})
    assert "private-secret" not in str(caught.value)


def test_proxy_timeout_has_actionable_diagnosis(monkeypatch):
    calls = fake_process(monkeypatch)
    original = subprocess.run

    def execute(args, **kwargs):
        if "exec" in args:
            raise subprocess.TimeoutExpired(
                args, 120, output=b"Proxy connection failed: HTTP CONNECT failed with status 403"
            )
        return original(args, **kwargs)

    monkeypatch.setattr("esun_agent.codex_provider.subprocess.run", execute)
    with pytest.raises(AgentError, match="network_proxy: HTTP CONNECT 403"):
        CodexProvider(config()).generate("system", {})
    assert calls


def test_diagnostic_does_not_show_unknown_credentials():
    assert "SENSITIVE" not in failure_detail("SENSITIVE JWT OR TOKEN", "unknown failure")


def test_complete_pipeline_uses_codex_and_records_real_backend(scenario, monkeypatch):
    from esun_agent.common import load_json
    from esun_agent.pipeline import run

    s = scenario
    fake_process(monkeypatch, reply=json.dumps(s["response"], ensure_ascii=False))
    folder, report = run(
        s["root"] / "datapack.json",
        s["root"] / "bundle.json",
        Path("config/codex.json"),
        s["root"] / "codex-runs",
        mode="test",
    )
    assert report["passed"]
    metadata = load_json(folder / "run.json")
    assert metadata["model_runtime_calls"][0]["execution_backend"] == "codex_cli"
    plan = load_json(folder / metadata["output_file"])
    assert plan["agent_metadata"]["model_provider"] == "openai"
    assert plan["agent_metadata"]["model_version"] == "gpt-5.4"
    assert "TEST ONLY" in plan["_NOTE"]
