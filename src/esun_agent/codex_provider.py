"""Use an existing Codex login; no API key extraction or credential-file access."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import AgentError, load_json
from .diagnostics import redact
from .models import ModelConfig


def failure_detail(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    def decode(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""

    content = decode(stdout) + "\n" + decode(stderr)
    if "403" in content and any(s in content.lower() for s in ["proxy", "connect failed"]):
        return "network_proxy: HTTP CONNECT 403；請允許 chatgpt.com 的 HTTPS 存取"
    if any(s in content.lower() for s in ["quota", "usage limit", "rate limit"]):
        return "quota_or_rate_limit: 請檢查 Codex 帳戶可用額度"
    if any(s in content.lower() for s in ["401", "unauthorized", "not logged in"]):
        return "credentials: Codex 登入尚未生效或已失效，請使用既有帳戶重新登入"
    if any(
        s in content.lower()
        for s in ["model not found", "model is not supported", "model_not_found"]
    ):
        return "model_access: 設定的模型不存在或此帳戶無權使用"
    # Unknown logs may echo credentials or prompt data; never include them in an exception.
    return "runtime_error: Codex 未正常完成；未回顯原始輸出或認證資訊"


class CodexProvider:
    def __init__(self, config: ModelConfig):
        self.config = config
        self.last_call = {}

    def ready(self):
        exe = shutil.which(self.config.codex_executable)
        if exe is None:
            raise AgentError("找不到 Codex CLI；請安裝 CLI 並以既有 ChatGPT 帳戶登入")
        try:
            status = subprocess.run(
                [exe, "login", "status"], capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AgentError("無法確認 Codex CLI 登入狀態") from None
        if status.returncode != 0:
            raise AgentError("Codex CLI 尚未登入；請於本機執行 codex login")
        return exe

    def generate(self, system: str, payload: dict) -> tuple[dict, dict]:
        exe = self.ready()
        try:
            version = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AgentError("無法取得 Codex CLI 版本") from None
        # Read-only, isolated final-response file; never reuse a previous run's output.
        with tempfile.TemporaryDirectory(prefix="esun-codex-") as temp:
            out = Path(temp) / "response.json"
            args = [
                exe,
                "--no-daemon",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "--json",
                "--model",
                self.config.model,
                "--output-last-message",
                str(out),
                "-",
            ]
            prompt = (
                system + "\n\n僅依下列 JSON 資料回答。不得使用工具、讀檔、執行命令或委派子代理。"
                "不要變更來源、股票、參數或驗證規則。只輸出要求的 JSON，不要 Markdown。\n"
                "<untrusted_input_json>\n"
                + json.dumps(payload, ensure_ascii=False, allow_nan=False)
                + "\n</untrusted_input_json>\n"
            )
            try:
                process = subprocess.run(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise AgentError(
                    "Codex CLI 逾時；" + failure_detail(exc.stdout, exc.stderr)
                ) from None
            except OSError:
                raise AgentError("Codex CLI 無法啟動") from None
            if process.returncode != 0:
                raise AgentError(
                    "Codex CLI 失敗；" + failure_detail(process.stdout, process.stderr)
                )
            completed, usage, thread_id = False, {}, None
            for line in process.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    thread_id = event.get("thread_id")
                if event.get("type") == "turn.failed":
                    raise AgentError("Codex 回合失敗；" + failure_detail(line, process.stderr))
                if event.get("type") == "item.completed":
                    kind = event.get("item", {}).get("type")
                    if kind not in {"agent_message", "reasoning"}:
                        raise AgentError("模型執行器嘗試使用額外工具；此回覆不作為投資輸出")
                if event.get("type") == "turn.completed":
                    completed = True
                    raw_usage = event.get("usage", {})
                    usage = {
                        k: raw_usage[k]
                        for k in ["input_tokens", "output_tokens"]
                        if type(raw_usage.get(k)) is int and raw_usage[k] >= 0
                    }
            if not completed or not out.is_file():
                raise AgentError("Codex 未完成有效回合或未產生 JSON 回覆")
            try:
                result = load_json(out)
            except (ValueError, OSError):
                raise AgentError("Codex 回覆不是有效且唯一鍵的 JSON") from None
            if not isinstance(result, dict):
                raise AgentError("Codex 回覆必須是 JSON object")
            self.last_call = {
                "execution_backend": "codex_cli",
                "model": self.config.model,
                "cli_version": redact(version.stdout.strip()),
                "thread_id": thread_id,
            }
            return result, usage
