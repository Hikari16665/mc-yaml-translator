"""Error categories and exception types shared by config, client, and CLI."""

from __future__ import annotations

import ast
import json
import re
from enum import Enum

_DETAIL_LIMIT = 500

# A 400/422 that is really "this model/provider does not accept a parameter"
# (especially response_format=json_object). Not a user config typo.
_UNSUPPORTED_PARAM_RE = re.compile(
    r"response_format|json_object|json[ _-]?mode|json_schema|"
    r"unsupported (?:parameter|param|value|argument|field|property)|"
    r"unknown (?:parameter|param|argument|field|property)|"
    r"unexpected (?:parameter|param|keyword|argument|field)|"
    r"unrecognized (?:request )?argument|"
    r"(?:is|are|was|were) not (?:supported|enabled|allowed|accepted|valid)|"
    r"does not support|do not support|doesn't support|"
    r"invalid parameter|invalid argument|"
    r"no endpoints? found that support|"
    r"requested parameters?|"
    r"not a valid (?:value|parameter) for",
    re.IGNORECASE,
)

# 400 that clearly points at the user's key, model id, or URL.
_CLIENT_CONFIG_BODY_RE = re.compile(
    r"invalid api key|incorrect api key|invalid_api_key|"
    r"you did(?:n't| not) provide an api key|"
    r"invalid api.?key|authentication failed|unauthorized|"
    r"unknown model|invalid model|model not found|model_not_found|"
    r"does not exist as a model|no such model|not a valid model|"
    r"invalid model(?: id| name| specified)?|"
    r"the model .* does not exist|"
    r"invalid url|unknown url|invalid endpoint",
    re.IGNORECASE,
)

# Statuses where, if we just sent response_format, a single retry without it
# is worth doing on the same model (not recorded as a failure).
FORMAT_FALLBACK_STATUSES = frozenset({400, 422})


class ErrorCategory(str, Enum):
    """Stable labels shown in the console and the failure report."""

    NETWORK = "NETWORK"
    PROVIDER = "PROVIDER"
    CLIENT_CONFIG = "CLIENT-CONFIG"


class ConfigError(ValueError):
    """Invalid or missing configuration, always CLIENT-CONFIG."""

    category = ErrorCategory.CLIENT_CONFIG


class TranslateError(Exception):
    """A single attempt failed (network, parse, refusal, placeholders, …)."""

    category = ErrorCategory.PROVIDER

    def __init__(self, message: str, category: ErrorCategory | None = None) -> None:
        super().__init__(message)
        if category is not None:
            self.category = category


class RefusalError(TranslateError):
    category = ErrorCategory.PROVIDER

    def __init__(self, raw: str = "") -> None:
        self.raw = raw
        super().__init__("模型拒绝翻译", category=ErrorCategory.PROVIDER)


class ParseError(TranslateError):
    category = ErrorCategory.PROVIDER

    def __init__(self, raw: str, reason: str = "无法解析结构化输出") -> None:
        self.raw = raw
        super().__init__(reason, category=ErrorCategory.PROVIDER)


class HTTPStatusError(TranslateError):
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body or ""
        detail = http_error_detail(self.body)
        msg = f"HTTP {status}"
        if detail:
            # Don't repeat "HTTP 400: HTTP 400: …" if the body already starts that way.
            prefix = f"HTTP {status}"
            trimmed = detail
            if trimmed.startswith(prefix):
                trimmed = trimmed[len(prefix) :].lstrip(" :,-")
            if trimmed:
                msg = f"HTTP {status}: {trimmed}"
        super().__init__(msg, category=category_of_http(status, self.body))


def http_error_detail(body: object, *, limit: int = _DETAIL_LIMIT) -> str:
    """Extract a short, human-readable reason from a provider error body."""
    if body is None or body == "":
        return ""
    if isinstance(body, (dict, list)):
        text = _message_from_payload(body) or json.dumps(body, ensure_ascii=False)
    else:
        raw = str(body).strip()
        if not raw:
            return ""
        parsed = _parse_embedded_payload(raw)
        text = (_message_from_payload(parsed) if parsed is not None else "") or raw
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _parse_embedded_payload(raw: str) -> object | None:
    """Pull a JSON / Python-literal object out of an SDK error string."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    for opener in ("{", "["):
        start = raw.find(opener)
        if start < 0:
            continue
        snippet = raw[start:]
        try:
            return json.loads(snippet)
        except (TypeError, ValueError):
            pass
        try:
            value = ast.literal_eval(snippet)
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def _message_from_payload(obj: object) -> str:
    if isinstance(obj, list) and obj:
        return _message_from_payload(obj[0])
    if not isinstance(obj, dict):
        return ""
    err = obj.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        parts: list[str] = []
        for key in ("message", "msg", "detail"):
            val = err.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
                break
        meta = err.get("metadata")
        if isinstance(meta, dict):
            extra = meta.get("raw") or meta.get("message") or meta.get("detail")
            if extra:
                extra_s = " ".join(str(extra).split())
                if extra_s and extra_s not in parts:
                    parts.append(extra_s)
        return " — ".join(parts)
    for key in ("message", "msg", "detail"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def error_body_from_exc(exc: BaseException) -> str:
    """Best-effort body/text from an OpenAI SDK or HTTP exception."""
    body = getattr(exc, "body", None)
    if body is not None and body != "":
        if isinstance(body, (dict, list)):
            return json.dumps(body, ensure_ascii=False)
        return str(body)
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("text", "content"):
            val = getattr(response, attr, None)
            if not val:
                continue
            if isinstance(val, bytes):
                return val.decode("utf-8", "replace")
            return str(val)
    return str(exc)


def looks_like_unsupported_param(text: str) -> bool:
    return bool(text and _UNSUPPORTED_PARAM_RE.search(text))


def looks_like_client_config_http(text: str) -> bool:
    return bool(text and _CLIENT_CONFIG_BODY_RE.search(text))


def should_retry_without_response_format(status: int, body: str = "") -> bool:
    """True when a request that included response_format should be retried without it."""
    if status in FORMAT_FALLBACK_STATUSES:
        return True
    if status == 404 and looks_like_unsupported_param(body):
        return True
    return False


def category_of_http(status: int, body: str = "") -> ErrorCategory:
    detail = http_error_detail(body) if body else ""
    haystack = f"{detail}\n{body or ''}"

    if status in {401, 403}:
        return ErrorCategory.CLIENT_CONFIG
    if status in {408, 499}:
        return ErrorCategory.NETWORK
    if status in {400, 422}:
        # Unsupported param is a provider/model limitation, not a bad config.toml typo.
        if looks_like_unsupported_param(haystack):
            return ErrorCategory.PROVIDER
        if looks_like_client_config_http(haystack):
            return ErrorCategory.CLIENT_CONFIG
        return ErrorCategory.PROVIDER
    if status in {404, 405}:
        if looks_like_unsupported_param(haystack):
            return ErrorCategory.PROVIDER
        return ErrorCategory.CLIENT_CONFIG
    if status in {413, 415}:
        return ErrorCategory.CLIENT_CONFIG
    return ErrorCategory.PROVIDER


def classify_error(exc: BaseException) -> ErrorCategory:
    cat = getattr(exc, "category", None)
    if isinstance(cat, ErrorCategory):
        return cat
    if isinstance(cat, str):
        try:
            return ErrorCategory(cat)
        except ValueError:
            pass

    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            APIStatusError,
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
            APIError,
        )
    except ImportError:  # pragma: no cover
        APIConnectionError = APITimeoutError = APIStatusError = ()  # type: ignore
        AuthenticationError = BadRequestError = NotFoundError = ()  # type: ignore
        PermissionDeniedError = RateLimitError = APIError = ()  # type: ignore

    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return ErrorCategory.NETWORK
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return ErrorCategory.CLIENT_CONFIG
    if isinstance(exc, RateLimitError):
        return ErrorCategory.PROVIDER
    if isinstance(exc, (BadRequestError, NotFoundError, APIStatusError)):
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        body = error_body_from_exc(exc)
        if isinstance(status, int):
            return category_of_http(status, body)
        if isinstance(exc, NotFoundError):
            return ErrorCategory.CLIENT_CONFIG
        if isinstance(exc, BadRequestError):
            return category_of_http(400, body)
        return ErrorCategory.PROVIDER
    if isinstance(exc, APIError):
        return ErrorCategory.PROVIDER

    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)):
            return ErrorCategory.NETWORK
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, OSError):
        return ErrorCategory.NETWORK
    return ErrorCategory.PROVIDER
