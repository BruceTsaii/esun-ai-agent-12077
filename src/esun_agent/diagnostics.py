"""Minimal Gemini connectivity check with credential-safe, structured diagnostics."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

import httpx

from .common import AgentError, stamp
from .models import ModelConfig


def redact(message: str, secrets=()) -> str:
    text = str(message)
    for value in secrets:
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:key|api_key|token|access_token)=)[^&\s\"']+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/\s@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"[A-Za-z0-9_=-]{32,}", "[REDACTED]", text)
    return text[:900]


def response_error(response: httpx.Response, secrets=()) -> dict:
    """Keep only Google's whitelisted error fields; never persist raw bodies/headers."""
    status, message, reasons = None, "非結構化錯誤本文已省略", []
    structured = False
    try:
        error = response.json().get("error", {})
        structured = isinstance(error, dict) and isinstance(error.get("message"), str)
        status = error.get("status")
        message = error.get("message", message)
        for detail in error.get("details", []):
            if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
                reasons.append(detail["reason"])
    except (ValueError, AttributeError, TypeError):
        pass
    combined = (str(status) + " " + str(message) + " " + " ".join(reasons)).lower()
    code = response.status_code
    if not structured:
        category = "http_error_origin_unconfirmed"
    elif code == 429 or "resource_exhausted" in combined or "quota" in combined:
        category = "quota_or_rate_limit"
    elif "billing" in combined:
        category = "billing_configuration"
    elif code == 401 or any(
        x in combined
        for x in [
            "api_key_invalid",
            "api_key_expired",
            "api key not valid",
            "unregistered callers",
            "unauthenticated",
        ]
    ):
        category = "credentials"
    elif code == 403 and any(x in combined for x in ["api_key_", "referrer", "ip address"]):
        category = "credential_restrictions"
    elif code == 404:
        category = "model_unavailable_or_inaccessible"
    elif code == 403:
        category = "model_or_service_permissions"
    elif code == 400:
        category = "request_configuration"
    elif code >= 500:
        category = "provider_service"
    else:
        category = "http_error"
    return {
        "category": category,
        "api_http_status": code,
        "proxy_http_status": None,
        "error_status": redact(status or "", secrets),
        "error_reasons": [redact(reason, secrets) for reason in reasons],
        "error_message": redact(message, secrets),
    }


def transport_error(exc: httpx.HTTPError, secrets=()) -> dict:
    proxy_status = None
    if isinstance(exc, httpx.ProxyError):
        match = re.search(r"\b([45]\d{2})\b", str(exc))
        proxy_status = int(match.group(1)) if match else None
    return {
        "category": "network_proxy" if isinstance(exc, httpx.ProxyError) else "network",
        "api_http_status": None,
        "proxy_http_status": proxy_status,
        "error_type": type(exc).__name__,
        "error_message": redact(str(exc), secrets),
    }


def test_gemini(config: ModelConfig, transport=None) -> dict:
    """One request. If key injection is missing, explicitly make an unauthenticated probe."""
    if config.provider != "google":
        raise AgentError("test-gemini 只適用於 google 配置")
    if not all(c.isalnum() or c in "-_." for c in config.model):
        raise AgentError("模型名稱格式錯誤")
    base = config.base_url or "https://generativelanguage.googleapis.com/v1beta"
    url = urlparse(base)
    if (
        url.scheme != "https"
        or not url.hostname
        or any([url.username, url.password, url.query, url.fragment])
    ):
        raise AgentError("base_url 須為無憑證/查詢參數的 HTTPS URL")
    key = os.environ.get(config.api_key_env)
    result = {
        "started_at": stamp(),
        "model": config.model,
        "host": url.hostname,
        "credential_variable": config.api_key_env,
        "credential_present": bool(key),
        "authenticated_request": bool(key),
        "test_type": "authenticated_generation" if key else "unauthenticated_network_probe",
        "passed": False,
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["x-goog-api-key"] = key
    body = {
        "contents": [{"parts": [{"text": 'Return exactly {"ok":true}.'}]}],
        "generationConfig": {"maxOutputTokens": 32, "responseMimeType": "application/json"},
    }
    # Gemini 2.5 may otherwise spend the entire small budget on reasoning.
    if config.model.startswith("gemini-2.5-flash"):
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    try:
        with httpx.Client(
            timeout=min(config.timeout_seconds, 30), follow_redirects=False, transport=transport
        ) as client:
            r = client.post(
                base.rstrip("/") + f"/models/{config.model}:generateContent",
                headers=headers,
                json=body,
            )
        if r.status_code != 200:
            result.update(response_error(r, [key]))
        else:
            result.update(api_http_status=200, proxy_http_status=None, category="response_format")
            try:
                response = r.json()
                candidate = response["candidates"][0]
                text = "".join(
                    p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought")
                )
                matched = json.loads(text) == {"ok": True}
                passed = bool(key) and candidate.get("finishReason") == "STOP" and matched
                result.update(
                    passed=passed,
                    category="ok" if passed else "response_format",
                    finish_reason=redact(candidate.get("finishReason", ""), [key]),
                    expected_response_matched=matched,
                )
                usage = response.get("usageMetadata", {})
                result["usage"] = {
                    n: usage[n]
                    for n in ["promptTokenCount", "candidatesTokenCount", "totalTokenCount"]
                    if type(usage.get(n)) is int
                }
                # Do not echo raw model content, which could repeat a credential.
            except (ValueError, KeyError, IndexError, TypeError):
                result["error_message"] = "API 回應成功，但未完成預期的最小 JSON 回覆；原文已省略"
    except httpx.HTTPError as exc:
        result.update(transport_error(exc, [key]))
    if not key:
        result["primary_blocker"] = "credential_not_injected"
        result["model_access_verified"] = False
        result["quota_verified"] = False
    else:
        result["model_access_verified"] = result["passed"]
        result["quota_verified"] = result["passed"]
    result["completed_at"] = stamp()
    return result
