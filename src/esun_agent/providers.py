"""Small HTTP adapters. Credentials stay in headers and are never persisted."""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import httpx

from .common import AgentError
from .diagnostics import response_error, transport_error
from .models import ModelConfig


class Provider:
    def __init__(self, config: ModelConfig, transport=None):
        self.config = config
        self.transport = transport

    def ready(self):
        if self.config.execution_backend == "codex_cli":
            from .codex_provider import CodexProvider

            return CodexProvider(self.config).ready()
        if not os.environ.get(self.config.api_key_env):
            raise AgentError(f"缺模型憑證環境變數 {self.config.api_key_env}；請於環境設定安全提供")
        base = self.config.base_url
        if base:
            u = urlparse(base)
            if u.scheme != "https" or u.username or u.password or u.query or u.fragment:
                raise AgentError("模型 base_url 須為無憑證/查詢參數的 HTTPS URL")
        if self.config.provider == "other" and not base:
            raise AgentError("OpenAI-compatible 自訂供應商必須設定 base_url")

    def generate(self, system: str, payload: dict) -> tuple[dict, dict]:
        if self.config.execution_backend == "codex_cli":
            from .codex_provider import CodexProvider

            backend = CodexProvider(self.config)
            result = backend.generate(system, payload)
            self.last_call = backend.last_call
            return result
        self.ready()
        c = self.config
        key = os.environ[c.api_key_env]
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        headers = {"Content-Type": "application/json"}
        if c.provider in {"openai", "other"}:
            url = (c.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
            headers["Authorization"] = "Bearer " + key
            body = {
                "model": c.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": c.max_output_tokens,
            }
        elif c.provider == "google":
            if not all(x.isalnum() or x in "-_." for x in c.model):
                raise AgentError("Google model 名稱格式錯誤")
            url = (c.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            url += f"/models/{c.model}:generateContent"
            headers["x-goog-api-key"] = key
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "maxOutputTokens": c.max_output_tokens,
                },
            }
        else:
            url = (c.base_url or "https://api.anthropic.com/v1").rstrip("/") + "/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            body = {
                "model": c.model,
                "system": system,
                "messages": [{"role": "user", "content": text}],
                "max_tokens": c.max_output_tokens,
            }
        try:
            with httpx.Client(timeout=c.timeout_seconds, transport=self.transport) as client:
                response = client.post(url, headers=headers, json=body)
            if response.status_code != 200:
                diagnostic = response_error(response, [key])
                raise AgentError("模型 API " + json.dumps(diagnostic, ensure_ascii=False))
            data = response.json()
            if c.provider in {"openai", "other"}:
                choice = data["choices"][0]
                if choice["finish_reason"] != "stop":
                    raise AgentError("模型回覆截斷或未正常完成")
                content, usage = choice["message"]["content"], data.get("usage", {})
                tokens = {
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                }
            elif c.provider == "google":
                candidate = data["candidates"][0]
                if candidate.get("finishReason") != "STOP":
                    raise AgentError("Google 模型回覆截斷/阻擋")
                content = "".join(
                    p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought")
                )
                usage = data.get("usageMetadata", {})
                tokens = {
                    "input_tokens": usage.get("promptTokenCount"),
                    "output_tokens": usage.get("candidatesTokenCount"),
                }
            else:
                if data.get("stop_reason") != "end_turn":
                    raise AgentError("Anthropic 模型回覆截斷/未正常完成")
                content = "".join(p.get("text", "") for p in data["content"] if p["type"] == "text")
                usage = data.get("usage", {})
                tokens = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                }
            return json.loads(content), {k: v for k, v in tokens.items() if v is not None}
        except httpx.HTTPError as exc:
            diagnostic = transport_error(exc, [key])
            raise AgentError("模型連線 " + json.dumps(diagnostic, ensure_ascii=False)) from None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            # Invalid responses may contain credentials; don't echo raw content.
            raise AgentError(f"模型傳輸或 JSON 回覆錯誤 ({type(exc).__name__})") from None
