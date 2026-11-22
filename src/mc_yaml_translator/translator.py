"""Scan YAML, translate keys concurrently with per-model failover and rate limits."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from mc_yaml_translator.client import (
    HTTPStatusError,
    ModelClient,
    ParseError,
    RefusalError,
    TranslateError,
    parse_structured,
)
from mc_yaml_translator.config import AppConfig, ModelConfig
from mc_yaml_translator.placeholders import (
    mask_placeholders,
    unmask_placeholders,
    validate_placeholders,
)
from mc_yaml_translator.progress import KeySlot, ProgressUI
from mc_yaml_translator.rate_limit import RateLimiter
from mc_yaml_translator.yaml_io import (
    TranslatableItem,
    apply_translation,
    dump_yaml,
    failure_path_for,
    load_yaml,
    output_path_for,
    scan_translatable,
)


@dataclass
class KeyFailure:
    path: str
    original: str
    error: str


@dataclass
class FileResult:
    source: Path
    output: Path | None
    total: int
    translated: int
    failed: list[KeyFailure] = field(default_factory=list)
    skipped_no_keys: bool = False
    failure_log: Path | None = None


def _backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    if retry_after is not None and retry_after > 0:
        return min(retry_after, 30.0)
    return min(8.0, 0.4 * (2 ** attempt))


async def _translate_one_model(
    client: ModelClient,
    model: ModelConfig,
    limiter: RateLimiter,
    original: str,
    slot: KeySlot | None,
) -> str:
    masked = mask_placeholders(original)

    def on_delta(buf: str) -> None:
        if slot is not None:
            slot.update_preview(buf)

    raw = await client.complete(model, masked.text, on_delta=on_delta)
    translation = parse_structured(raw)
    unmasked = unmask_placeholders(translation, masked)
    mismatch = validate_placeholders(original, unmasked)
    if mismatch:
        raise TranslateError(mismatch.message())
    if re.search(r"__MCYT_PH_\d+__", unmasked):
        raise TranslateError("译文残留掩码标记，模型可能改写了占位符")
    return unmasked


async def translate_item(
    item: TranslatableItem,
    config: AppConfig,
    client: ModelClient,
    limiters: list[RateLimiter],
    progress: ProgressUI,
) -> KeyFailure | None:
    last_err = "未知错误"
    slot: KeySlot | None = None
    try:
        slot = progress.acquire_slot(item.path_str)
        for mi, (model, limiter) in enumerate(zip(config.models, limiters)):
            for attempt in range(config.attempts_per_model):
                try:
                    async with limiter:
                        text = await _translate_one_model(
                            client, model, limiter, item.value, slot
                        )
                    apply_translation(item, text)
                    progress.tick(True)
                    return None
                except RefusalError as exc:
                    last_err = f"{model.model}: 拒绝翻译"
                    # Do not leak refusal text into the YAML; just retry/fallback.
                    _ = exc.raw
                except ParseError as exc:
                    last_err = f"{model.model}: {exc}"
                except HTTPStatusError as exc:
                    last_err = f"{model.model}: {exc}"
                    retry_after = None
                    if exc.status == 429:
                        retry_after = 1.5
                    if attempt + 1 < config.attempts_per_model or mi + 1 < len(config.models):
                        await asyncio.sleep(_backoff_seconds(attempt, retry_after))
                    continue
                except TranslateError as exc:
                    last_err = f"{model.model}: {exc}"
                except Exception as exc:  # noqa: BLE001 — isolate per key
                    last_err = f"{model.model}: 意外错误 {type(exc).__name__}: {exc}"
                if attempt + 1 < config.attempts_per_model:
                    await asyncio.sleep(_backoff_seconds(attempt))
            # Fallback to the next model is not itself a failure.
    finally:
        progress.release_slot(slot)

    progress.tick(False)
    return KeyFailure(path=item.path_str, original=item.value, error=last_err)


async def translate_document(
    source: Path,
    config: AppConfig,
    *,
    http: httpx.AsyncClient | None = None,
    progress_factory: Callable[[int], ProgressUI] | None = None,
    disable_progress: bool = False,
) -> FileResult:
    loaded = load_yaml(source)
    items = scan_translatable(loaded.data, config.skip_key_patterns)
    if not items:
        return FileResult(
            source=source,
            output=None,
            total=0,
            translated=0,
            skipped_no_keys=True,
        )

    owns_http = http is None
    if http is None:
        http = httpx.AsyncClient(verify=config.verify_ssl, follow_redirects=True)
    client = ModelClient(http, config)
    limiters = [RateLimiter.from_model(m) for m in config.models]
    # Bound total in-flight tasks so we don't spawn tens of thousands at once.
    global_cap = 0
    for m in config.models:
        global_cap += m.max_streams or 8
    global_cap = max(4, min(64, global_cap))
    sem = asyncio.Semaphore(global_cap)

    failures: list[KeyFailure] = []
    ui_factory = progress_factory or (lambda n: ProgressUI(n, disable=disable_progress))

    async def work(item: TranslatableItem) -> None:
        async with sem:
            fail = await translate_item(item, config, client, limiters, ui)
            if fail is not None:
                failures.append(fail)

    try:
        with ui_factory(len(items)) as ui:
            async with asyncio.TaskGroup() as tg:
                for item in items:
                    tg.create_task(work(item))
    finally:
        if owns_http:
            await http.aclose()

    dest = output_path_for(source, config.output_suffix)
    dump_yaml(loaded, dest)
    fail_log = None
    if failures:
        fail_log = failure_path_for(dest)
        _write_failures(fail_log, source, items, failures)
    return FileResult(
        source=source,
        output=dest,
        total=len(items),
        translated=len(items) - len(failures),
        failed=failures,
        failure_log=fail_log,
    )


def _write_failures(
    path: Path,
    source: Path,
    items: list[TranslatableItem],
    failures: list[KeyFailure],
) -> None:
    lines = [
        f"# Translation failures for {source}",
        f"# {len(failures)} failed / {len(items)} total",
        "",
    ]
    for f in failures:
        orig = f.original.replace("\n", "\\n")
        if len(orig) > 400:
            orig = orig[:400] + "…"
        lines.append(f.path)
        lines.append(f"  original: {orig}")
        lines.append(f"  error: {f.error}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def scan_file(source: Path, config: AppConfig) -> list[TranslatableItem]:
    loaded = load_yaml(source)
    return scan_translatable(loaded.data, config.skip_key_patterns)
