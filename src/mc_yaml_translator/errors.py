"""Error categories and exception types shared by config, client, and CLI."""

from __future__ import annotations

from enum import Enum


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
        self.body = body
        super().__init__(f"HTTP {status}", category=category_of_http(status))


def category_of_http(status: int) -> ErrorCategory:
    if status in {400, 401, 403, 404, 405, 413, 415, 422}:
        return ErrorCategory.CLIENT_CONFIG
    if status in {408, 499}:
        return ErrorCategory.NETWORK
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
    if isinstance(exc, (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError)):
        return ErrorCategory.CLIENT_CONFIG
    if isinstance(exc, RateLimitError):
        return ErrorCategory.PROVIDER
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if isinstance(status, int):
            return category_of_http(status)
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
