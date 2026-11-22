"""OpenAI-compatible streaming chat client and structured-output parsing."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from mc_yaml_translator.config import AppConfig, ModelConfig

JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


class TranslateError(Exception):
    """A single attempt failed (network, parse, refusal, placeholders, …)."""


class RefusalError(TranslateError):
    def __init__(self, raw: str = "") -> None:
        self.raw = raw
        super().__init__("模型拒绝翻译")


class ParseError(TranslateError):
    def __init__(self, raw: str, reason: str = "无法解析结构化输出") -> None:
        self.raw = raw
        super().__init__(f"{reason}")


class HTTPStatusError(TranslateError):
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}")


def build_messages(source_text: str, config: AppConfig) -> list[dict[str, str]]:
    extra = config.extra_prompt.strip()
    extra_block = f"\nAdditional instructions from the user:\n{extra}\n" if extra else ""
    system = f"""You are a professional translator for Minecraft plugin YAML language files.
Translate the user text from {config.source_language} to {config.target_language}.

You MUST reply with a single JSON object and nothing else, using exactly this schema:
{{"status":"ok","translation":"<translated text>"}}
or, if the text is not natural-language content and should not be translated:
{{"status":"refused","translation":""}}

Rules:
- Never put explanations, apologies, or refusals in the translation field.
- If you cannot or will not translate, use status "refused" and an empty translation.
- Preserve every placeholder token EXACTLY as given. Tokens look like __MCYT_PH_0__, __MCYT_PH_1__, and so on. Copy them verbatim, do not translate, drop, reorder the numeric id, or add spaces inside them.
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
            mapping = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', '/': '/'}
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


def parse_structured(text: str) -> str:
    """Return the translation, or raise RefusalError / ParseError.

    Refusal or unparsable payloads never become a translation.
    """
    raw_obj = extract_json_object(text)
    if not raw_obj:
        raise ParseError(text, "响应中没有 JSON 对象")
    try:
        obj = json.loads(raw_obj)
    except json.JSONDecodeError as exc:
        raise ParseError(text, f"JSON 解析失败: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError(text, "JSON 根不是对象")

    status = str(obj.get("status", "") or "").strip().lower()
    translation = obj.get("translation")

    if status in {"refused", "refuse", "rejected", "error", "denied"}:
        raise RefusalError(raw=text)
    if translation is None:
        raise ParseError(text, "缺少 translation 字段")
    if not isinstance(translation, str):
        raise ParseError(text, "translation 不是字符串")
    if status and status not in {"ok", "success", "translated", "done"}:
        raise ParseError(text, f"未知 status: {status}")
    return translation


@dataclass
class StreamResult:
    text: str
    used_response_format: bool


class ModelClient:
    def __init__(self, http: httpx.AsyncClient, config: AppConfig) -> None:
        self.http = http
        self.config = config
        # Remember per base_url+model whether response_format is accepted.
        self._response_format_ok: dict[str, bool] = {}

    def _flag_key(self, model: ModelConfig) -> str:
        return f"{model.base_url}::{model.model}"

    async def complete(
        self,
        model: ModelConfig,
        source_text: str,
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        key = self._flag_key(model)
        want_fmt = self._response_format_ok.get(key, True)
        try:
            raw = await self._stream(model, source_text, on_delta, use_format=want_fmt)
        except HTTPStatusError as exc:
            if want_fmt and exc.status == 400:
                self._response_format_ok[key] = False
                raw = await self._stream(model, source_text, on_delta, use_format=False)
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
    ) -> str:
        payload: dict[str, Any] = {
            "model": model.model,
            "messages": build_messages(source_text, self.config),
            "temperature": self.config.temperature,
            "stream": True,
        }
        if use_format:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {model.api_key}",
            "Content-Type": "application/json",
        }
        url = model.completions_url()
        timeout = httpx.Timeout(
            connect=min(30.0, self.config.timeout_seconds),
            read=self.config.timeout_seconds,
            write=self.config.timeout_seconds,
            pool=self.config.timeout_seconds,
        )
        try:
            async with self.http.stream(
                "POST", url, json=payload, headers=headers, timeout=timeout
            ) as resp:
                if resp.status_code == 400 and use_format:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise HTTPStatusError(400, body)
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise HTTPStatusError(resp.status_code, body)
                buf = await self._read_sse(resp, on_delta)
        except httpx.TimeoutException as exc:
            raise TranslateError(f"请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TranslateError(f"网络错误: {exc}") from exc
        return buf

    async def _read_sse(
        self,
        resp: httpx.Response,
        on_delta: Callable[[str], None] | None,
    ) -> str:
        raw_parts: list[str] = []
        async for line in resp.aiter_lines():
            if not line:
                continue
            if line.startswith(":") or line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = _delta_text(chunk)
            if delta:
                raw_parts.append(delta)
                if on_delta:
                    on_delta("".join(raw_parts))
        return "".join(raw_parts)


def _delta_text(chunk: dict[str, Any]) -> str:
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return str(chunk.get("content") or "")
        c0 = choices[0]
        delta = c0.get("delta") or c0.get("message") or {}
        return str(delta.get("content") or "")
    except Exception:
        return ""
