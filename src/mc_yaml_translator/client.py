"""OpenAI SDK streaming chat client and pydantic structured-output parsing."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

from mc_yaml_translator.config import AppConfig, ModelConfig
from mc_yaml_translator.errors import (
    ErrorCategory,
    HTTPStatusError,
    ParseError,
    RefusalError,
    TranslateError,
    classify_error,
)

# Re-export so existing imports from client continue to work.
__all__ = [
    "ErrorCategory",
    "HTTPStatusError",
    "ModelClient",
    "ParseError",
    "RefusalError",
    "TranslateError",
    "TranslationOutput",
    "build_messages",
    "classify_error",
    "parse_structured",
    "StructuredResult",
    "preview_translation",
]

JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class TranslationOutput(BaseModel):
    """Structured LLM response. Refusal or invalid payloads never become a translation."""

    model_config = ConfigDict(extra="ignore")

    status: str = "ok"
    translation: str | None = None
    skip: bool = False
    unchanged: bool = False


@dataclass
class StructuredResult:
    """Parsed model output: a translation, or a source-language skip."""

    translation: str
    skipped: bool = False


_SKIP_STATUSES = frozenset({"skip", "skipped", "unchanged", "same", "keep"})
_OK_STATUSES = frozenset({"ok", "success", "translated", "done", ""})
_REFUSE_STATUSES = frozenset({"refused", "refuse", "rejected", "error", "denied"})


def build_messages(
    source_text: str,
    config: AppConfig,
    *,
    target_language: str,
) -> list[dict[str, str]]:
    extra = config.extra_prompt.strip()
    extra_block = f"\nAdditional instructions from the user:\n{extra}\n" if extra else ""
    if config.source_is_auto:
        source_clause = (
            f"Detect the source language yourself and translate all natural-language text "
            f"to {target_language}. Do not leave translatable text unchanged, and do not use "
            f'status "skip".'
        )
        skip_rule = (
            '- Do not use status "skip" or "unchanged". Every natural-language string should be translated.\n'
        )
        schema = (
            'You MUST reply with a single JSON object and nothing else, using exactly this schema:\n'
            '{"status":"ok","translation":"<translated text>"}\n'
            "or, if the text is not natural-language content and should not be translated:\n"
            '{"status":"refused","translation":""}'
        )
    else:
        src = config.source_language
        source_clause = (
            f"The expected source language is {src}. Translate the text to {target_language} "
            f"ONLY if it is in {src} (or is primarily {src}, including when mixed with placeholders, "
            f"color codes, or MiniMessage tags). If the text is clearly in a different natural language, "
            f"do not translate it."
        )
        skip_rule = (
            f"- If the text is clearly not in {src}, reply with "
            '{"status":"skip","translation":""}. This is not a refusal and is not an error.\n'
        )
        schema = (
            'You MUST reply with a single JSON object and nothing else, using exactly this schema:\n'
            '{"status":"ok","translation":"<translated text>"}\n'
            "or, if the text is not in the expected source language and must be left unchanged:\n"
            '{"status":"skip","translation":""}\n'
            "or, if the text is not natural-language content and should not be translated:\n"
            '{"status":"refused","translation":""}'
        )
    system = f"""You are a professional translator for Minecraft plugin YAML language files.
{source_clause}

{schema}

Rules:
- Never put explanations, apologies, or refusals in the translation field.
- If you cannot or will not translate (other than a source-language skip), use status "refused" and an empty translation.
{skip_rule}- Preserve every placeholder token EXACTLY as given. Tokens look like __MCYT_PH_0__, __MCYT_PH_1__, and so on. Copy them verbatim, do not translate, drop, reorder the numeric id, or add spaces inside them.
- Also preserve any remaining format codes or tags that were not tokenized (MiniMessage tags such as <hover:...>, <click:...>, etc.). You may translate natural-language text that appears inside quotes in those tags, but you must keep the tag name and structure.
- Do not add or remove placeholders.
- Keep a concise in-game UI tone; do not wrap the result in quotes unless the original itself was quote-wrapped as part of the text.
- Keep newlines and whitespace structure of the original, except where the target language requires a natural adjustment of word order.
{extra_block}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": source_text},
    ]


def extract_json_object(text: str) -> str | None:
    s = text.strip()
    s = JSON_FENCE_RE.sub("", s).strip()
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def decode_partial_json_string(s: str) -> str:
    """Decode a possibly still-open JSON string (for streaming preview)."""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            break
        if ch == "\\":
            if i + 1 >= n:
                break
            nxt = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt == "u":
                hexes = s[i + 2 : i + 6]
                if len(hexes) < 4:
                    break
                try:
                    out.append(chr(int(hexes, 16)))
                except ValueError:
                    break
                i += 6
                continue
            break
        out.append(ch)
        i += 1
    return "".join(out)


def preview_translation(buffer: str) -> str:
    """Best-effort extraction of the translation field from a partial stream."""
    m = re.search(r'"translation"\s*:\s*"', buffer)
    if not m:
        return ""
    return decode_partial_json_string(buffer[m.end() :])


def parse_structured(text: str) -> StructuredResult:
    """Return a StructuredResult, or raise RefusalError / ParseError.

    Refusal or unparsable payloads never become a translation.
    A source-language skip (status skip/unchanged, or skip/unchanged flags)
    is returned as skipped=True and is not an error.
    """
    raw_obj = extract_json_object(text)
    if not raw_obj:
        raise ParseError(text, "响应中没有 JSON 对象")
    try:
        obj = TranslationOutput.model_validate_json(raw_obj)
    except ValidationError as exc:
        raise ParseError(text, f"JSON 结构不符合要求: {exc}") from exc

    status = (obj.status or "").strip().lower()
    flagged_skip = bool(obj.skip) or bool(obj.unchanged)
    if status in _SKIP_STATUSES or (flagged_skip and status in _OK_STATUSES):
        return StructuredResult(translation=obj.translation or "", skipped=True)
    if status in _REFUSE_STATUSES:
        raise RefusalError(raw=text)
    if obj.translation is None:
        raise ParseError(text, "缺少 translation 字段")
    if not isinstance(obj.translation, str):
        raise ParseError(text, "translation 不是字符串")
    if status and status not in _OK_STATUSES:
        raise ParseError(text, f"未知 status: {status}")
    return StructuredResult(translation=obj.translation, skipped=False)


def _wrap_openai(exc: Exception) -> TranslateError:
    """Map an OpenAI SDK exception to a categorized TranslateError."""
    if isinstance(exc, TranslateError):
        return exc
    status = getattr(exc, "status_code", None)
    body = ""
    try:
        body = str(getattr(exc, "body", "") or exc)
    except Exception:
        body = str(exc)
    if isinstance(status, int) and status >= 400:
        return HTTPStatusError(status, body)
    category = classify_error(exc)
    if category is ErrorCategory.NETWORK:
        if isinstance(exc, openai.APITimeoutError):
            return TranslateError(f"请求超时: {exc}", category=category)
        return TranslateError(f"网络错误: {exc}", category=category)
    if category is ErrorCategory.CLIENT_CONFIG:
        return TranslateError(f"客户端配置错误: {exc}", category=category)
    return TranslateError(f"服务商错误: {exc}", category=category)


class ModelClient:
    """Thin wrapper around openai.AsyncOpenAI with streaming and format fallback."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._sdk: dict[str, AsyncOpenAI] = {}
        self._http: dict[str, httpx.AsyncClient] = {}
        self._response_format_ok: dict[str, bool] = {}

    def _flag_key(self, model: ModelConfig) -> str:
        return f"{model.base_url}::{model.model}"

    def _client(self, model: ModelConfig) -> AsyncOpenAI:
        key = self._flag_key(model)
        existing = self._sdk.get(key)
        if existing is not None:
            return existing
        kwargs: dict[str, Any] = {
            "api_key": model.api_key,
            "base_url": model.base_url.rstrip("/"),
            "timeout": self.config.timeout_seconds,
            "max_retries": 0,  # retries / failover are handled by the translator
        }
        if not self.config.verify_ssl:
            http = httpx.AsyncClient(verify=False, follow_redirects=True)
            self._http[key] = http
            kwargs["http_client"] = http
        client = AsyncOpenAI(**kwargs)
        self._sdk[key] = client
        return client

    async def aclose(self) -> None:
        for client in self._sdk.values():
            await client.close()
        self._sdk.clear()
        for http in self._http.values():
            await http.aclose()
        self._http.clear()

    async def complete(
        self,
        model: ModelConfig,
        source_text: str,
        on_delta: Callable[[str], None] | None = None,
        *,
        target_language: str | None = None,
    ) -> str:
        target = target_language or self.config.target_languages[0]
        key = self._flag_key(model)
        want_fmt = self._response_format_ok.get(key, True)
        try:
            raw = await self._stream(model, source_text, on_delta, use_format=want_fmt, target_language=target)
        except HTTPStatusError as exc:
            if want_fmt and exc.status == 400:
                self._response_format_ok[key] = False
                raw = await self._stream(
                    model, source_text, on_delta, use_format=False, target_language=target
                )
            else:
                raise
        else:
            if want_fmt:
                self._response_format_ok[key] = True
        return raw

    async def _stream(
        self,
        model: ModelConfig,
        source_text: str,
        on_delta: Callable[[str], None] | None,
        use_format: bool,
        *,
        target_language: str,
    ) -> str:
        client = self._client(model)
        kwargs: dict[str, Any] = {
            "model": model.model,
            "messages": build_messages(source_text, self.config, target_language=target_language),
            "temperature": self.config.temperature,
            "stream": True,
        }
        if use_format:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            stream = await client.chat.completions.create(**kwargs)
            parts: list[str] = []
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    parts.append(content)
                    if on_delta:
                        on_delta("".join(parts))
            return "".join(parts)
        except HTTPStatusError:
            raise
        except openai.BadRequestError as exc:
            if use_format:
                raise HTTPStatusError(400, str(exc)) from exc
            raise _wrap_openai(exc) from exc
        except (openai.APITimeoutError, openai.APIConnectionError, openai.APIStatusError, openai.APIError) as exc:
            raise _wrap_openai(exc) from exc
