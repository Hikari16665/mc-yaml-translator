from __future__ import annotations

from mc_yaml_translator.errors import (
    ConfigError,
    ErrorCategory,
    HTTPStatusError,
    RefusalError,
    TranslateError,
    classify_error,
)


def test_http_status_categories():
    assert classify_error(HTTPStatusError(401)) is ErrorCategory.CLIENT_CONFIG
    assert classify_error(HTTPStatusError(403)) is ErrorCategory.CLIENT_CONFIG
    assert classify_error(HTTPStatusError(404)) is ErrorCategory.CLIENT_CONFIG
    assert classify_error(HTTPStatusError(400)) is ErrorCategory.CLIENT_CONFIG
    assert classify_error(HTTPStatusError(429)) is ErrorCategory.PROVIDER
    assert classify_error(HTTPStatusError(500)) is ErrorCategory.PROVIDER
    assert classify_error(HTTPStatusError(503)) is ErrorCategory.PROVIDER


def test_network_and_config_errors():
    assert classify_error(TranslateError("请求超时", category=ErrorCategory.NETWORK)) is ErrorCategory.NETWORK
    assert classify_error(RefusalError()) is ErrorCategory.PROVIDER
    assert classify_error(ConfigError("bad")) is ErrorCategory.CLIENT_CONFIG


def test_openai_exception_mapping():
    import openai

    assert classify_error(openai.APITimeoutError(request=None)) is ErrorCategory.NETWORK
    assert classify_error(openai.APIConnectionError(request=None)) is ErrorCategory.NETWORK

    # Status errors need a mock response
    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.headers = {}
            self.request = None

        def json(self):
            return {"error": {"message": "x"}}

        @property
        def text(self):
            return '{"error":{"message":"x"}}'

    auth = openai.AuthenticationError("nope", response=_Resp(401), body=None)
    assert classify_error(auth) is ErrorCategory.CLIENT_CONFIG
    rate = openai.RateLimitError("slow", response=_Resp(429), body=None)
    assert classify_error(rate) is ErrorCategory.PROVIDER
    boom = openai.InternalServerError("boom", response=_Resp(500), body=None)
    assert classify_error(boom) is ErrorCategory.PROVIDER
