import json

import httpx
import pytest

from esun_agent.common import AgentError
from esun_agent.diagnostics import redact, response_error, transport_error
from esun_agent.diagnostics import test_gemini as probe
from esun_agent.models import ModelConfig


@pytest.mark.parametrize(
    "http_status,status,message,reason,category",
    [
        (400, "INVALID_ARGUMENT", "API key not valid.", "API_KEY_INVALID", "credentials"),
        (401, "UNAUTHENTICATED", "No consumer identity.", "", "credentials"),
        (403, "PERMISSION_DENIED", "Model permission denied.", "", "model_or_service_permissions"),
        (
            403,
            "PERMISSION_DENIED",
            "Restricted API key.",
            "API_KEY_SERVICE_BLOCKED",
            "credential_restrictions",
        ),
        (
            404,
            "NOT_FOUND",
            "Model not found or not accessible.",
            "",
            "model_unavailable_or_inaccessible",
        ),
        (429, "RESOURCE_EXHAUSTED", "Quota exceeded.", "", "quota_or_rate_limit"),
        (400, "INVALID_ARGUMENT", "Invalid parameter.", "", "request_configuration"),
        (503, "UNAVAILABLE", "Service temporarily unavailable.", "", "provider_service"),
    ],
)
def test_google_error_categories(http_status, status, message, reason, category):
    response = httpx.Response(
        http_status,
        json={"error": {"status": status, "message": message, "details": [{"reason": reason}]}},
    )
    result = response_error(response)
    assert result["api_http_status"] == http_status
    assert result["category"] == category


def test_error_redaction_covers_server_echoes_and_proxy_credentials():
    key = "private-value-only-for-test"
    text = f"{key} AIza123456789012345678901234567890123456 Bearer abcdef ?key=example-secret https://user:pass@proxy.test"
    safe = redact(text, [key])
    for value in [key, "AIza123", "example-secret", "user:pass", "abcdef"]:
        assert value not in safe
    response = httpx.Response(400, json={"error": {"message": f"Key invalid: {key}"}})
    assert key not in json.dumps(response_error(response, [key]))


def test_proxy_status_is_not_gemini_http_status():
    result = transport_error(
        httpx.ProxyError("403 Forbidden (policy); https://user:pass@proxy.test")
    )
    assert result["category"] == "network_proxy"
    assert result["proxy_http_status"] == 403
    assert result["api_http_status"] is None
    assert "user:pass" not in result["error_message"]


def test_missing_injection_does_not_invent_or_send_a_key(monkeypatch):
    monkeypatch.delenv("TEST_GEMINI_KEY", raising=False)

    def handler(request):
        assert "x-goog-api-key" not in request.headers
        assert "key=" not in str(request.url)
        return httpx.Response(
            403,
            json={
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "Method doesn't allow unregistered callers.",
                }
            },
        )

    config = ModelConfig(provider="google", model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY")
    result = probe(config, httpx.MockTransport(handler))
    assert not result["passed"]
    assert result["primary_blocker"] == "credential_not_injected"
    assert not result["model_access_verified"] and not result["quota_verified"]
    assert result["api_http_status"] == 403


def test_authenticated_minimal_success_and_no_key_in_report(monkeypatch):
    key = "secret-value-for-mocked-request-only"
    monkeypatch.setenv("TEST_GEMINI_KEY", key)

    def handler(request):
        assert request.headers["x-goog-api-key"] == key
        assert key not in str(request.url) and key.encode() not in request.content
        body = json.loads(request.content)
        assert body["generationConfig"]["maxOutputTokens"] == 32
        assert body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 5},
            },
        )

    result = probe(
        ModelConfig(model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY"),
        httpx.MockTransport(handler),
    )
    assert result["passed"] and result["model_access_verified"]
    assert key not in json.dumps(result)


def test_truncated_generation_is_not_success(monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "mock-key")

    def handler(request):
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"ok":true}'}]}}
                ]
            },
        )

    result = probe(
        ModelConfig(model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY"),
        httpx.MockTransport(handler),
    )
    assert not result["passed"]
    assert result["api_http_status"] == 200 and result["category"] == "response_format"


def test_probe_network_error_does_not_leak(monkeypatch):
    key = "mock-key-never-echo"
    monkeypatch.setenv("TEST_GEMINI_KEY", key)

    def handler(request):
        raise httpx.ConnectError(f"Unable to connect; token={key}")

    result = probe(
        ModelConfig(model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY"),
        httpx.MockTransport(handler),
    )
    assert result["api_http_status"] is None and result["category"] == "network"
    assert key not in json.dumps(result)


def test_no_key_http200_is_not_an_authenticated_test(monkeypatch):
    monkeypatch.delenv("TEST_GEMINI_KEY", raising=False)

    def handler(request):
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"ok":true}'}]}}
                ]
            },
        )

    result = probe(
        ModelConfig(model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY"),
        httpx.MockTransport(handler),
    )
    assert not result["passed"] and not result["authenticated_request"]


def test_unrelated_provider_is_rejected_without_network():
    with pytest.raises(AgentError):
        probe(ModelConfig(provider="openai", model="gpt-test"))


def test_html_denial_is_not_assumed_to_be_model_permission():
    result = response_error(httpx.Response(403, text="<html>Forbidden by a proxy</html>"))
    assert result["category"] == "http_error_origin_unconfirmed"
    assert "html" not in result["error_message"]


def test_provider_records_masked_status_and_quota_reason(monkeypatch):
    from esun_agent.providers import Provider

    key = "mock-api-key-must-stay-secret"
    monkeypatch.setenv("TEST_GEMINI_KEY", key)

    def handler(request):
        return httpx.Response(
            429,
            json={
                "error": {"status": "RESOURCE_EXHAUSTED", "message": f"Quota exceeded for {key}"}
            },
        )

    provider = Provider(
        ModelConfig(model="gemini-2.5-flash", api_key_env="TEST_GEMINI_KEY"),
        httpx.MockTransport(handler),
    )
    with pytest.raises(AgentError) as caught:
        provider.generate("system", {})
    assert "429" in str(caught.value) and "quota_or_rate_limit" in str(caught.value)
    assert key not in str(caught.value)
