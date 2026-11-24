from __future__ import annotations

from mc_yaml_translator.errors import (
    ConfigError,
    ErrorCategory,
    HTTPStatusError,
    RefusalError,
    TranslateError,
    category_of_http,
    classify_error,
    http_error_detail,
    should_retry_without_response_format,
)


def test_http_status_categories():
    assert classify_error(HTTPStatusError(401)) is ErrorCategory.CLIENT_CONFIG
    assert classify_error(HTTPStatusError(403)) is ErrorCategory.CLIENT_CONFIG
    assert classify_error(HTTPStatusError(404)) is ErrorCategory.CLIENT_CONFIG
    # A bare 400 is not assumed to be a config typo.
    assert classify_error(HTTPStatusError(400)) is ErrorCategory.PROVIDER
    assert classify_error(HTTPStatusError(422)) is ErrorCategory.PROVIDER
    assert classify_error(HTTPStatusError(429)) is ErrorCategory.PROVIDER
    assert classify_error(HTTPStatusError(500)) is ErrorCategory.PROVIDER
    assert classify_error(HTTPStatusError(503)) is ErrorCategory.PROVIDER


def test_http_400_unsupported_param_is_provider_not_client_config():
    body = '{"error": {"message": "This model does not support response_format type json_object"}}'
    exc = HTTPStatusError(400, body)
    assert exc.category is ErrorCategory.PROVIDER
    assert "does not support response_format" in str(exc)
    assert classify_error(exc) is ErrorCategory.PROVIDER
    assert category_of_http(400, body) is ErrorCategory.PROVIDER
    assert should_retry_without_response_format(400, body)


def test_http_400_invalid_model_stays_client_config_and_includes_body():
    body = '{"error": {"message": "Invalid model specified: nope"}}'
    exc = HTTPStatusError(400, body)
    assert exc.category is ErrorCategory.CLIENT_CONFIG
    assert "Invalid model specified: nope" in str(exc)
    assert str(exc).startswith("HTTP 400:")


def test_http_404_unsupported_parameters_is_provider_and_triggers_format_fallback():
    body = (
        '{"error": {"message": "No endpoints found that support the requested parameters. '
        'To learn more about provider routing, visit: https://openrouter.ai/docs/provider-routing"}}'
    )
    exc = HTTPStatusError(404, body)
    assert exc.category is ErrorCategory.PROVIDER
    assert should_retry_without_response_format(404, body)
    assert "No endpoints found" in str(exc)


def test_http_error_detail_extracts_openrouter_metadata():
    body = {
        "error": {
            "message": "Provider returned error",
            "metadata": {"raw": "response_format is not supported by this model"},
        }
    }
    detail = http_error_detail(body)
    assert "Provider returned error" in detail
    assert "response_format is not supported" in detail


def test_network_and_config_errors():
    assert classify_error(TranslateError("请求超时", category=ErrorCategory.NETWORK)) is ErrorCategory.NETWORK
    assert classify_error(RefusalError()) is ErrorCategory.PROVIDER
    assert classify_error(ConfigError("bad")) is ErrorCategory.CLIENT_CONFIG


def test_openai_exception_mapping():
    import json

    import httpx
    import openai

    # openai 3.59+ validates request/response objects; use real httpx types.
    req = httpx.Request("POST", "http://example.com/v1/chat/completions")

    def _resp(status: int, payload=None) -> httpx.Response:
        body = payload if payload is not None else {"error": {"message": "x"}}
        return httpx.Response(
            status,
            request=req,
            headers={"Content-Type": "application/json"},
            content=json.dumps(body).encode("utf-8"),
        )

    assert classify_error(openai.APITimeoutError(request=req)) is ErrorCategory.NETWORK
    assert classify_error(openai.APIConnectionError(request=req)) is ErrorCategory.NETWORK

    auth = openai.AuthenticationError("nope", response=_resp(401), body=None)
    assert classify_error(auth) is ErrorCategory.CLIENT_CONFIG
    rate = openai.RateLimitError("slow", response=_resp(429), body=None)
    assert classify_error(rate) is ErrorCategory.PROVIDER
    boom = openai.InternalServerError("boom", response=_resp(500), body=None)
    assert classify_error(boom) is ErrorCategory.PROVIDER

    bad_body = {"error": {"message": "Unknown parameter: 'response_format'"}}
    bad = openai.BadRequestError(
        "unknown param",
        response=_resp(400, bad_body),
        body=bad_body,
    )
    assert classify_error(bad) is ErrorCategory.PROVIDER

    model_body = {"error": {"message": "The model `foo` does not exist"}}
    bad_model = openai.BadRequestError(
        "no model",
        response=_resp(400, model_body),
        body=model_body,
    )
    assert classify_error(bad_model) is ErrorCategory.CLIENT_CONFIG
