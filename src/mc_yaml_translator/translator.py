"""Scan YAML, translate keys concurrently with per-model failover and rate limits."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mc_yaml_translator.client import (
    ErrorCategory,
    HTTPStatusError,
    ModelClient,
    ParseError,
    RefusalError,
    TranslateError,
    classify_error,
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
class AttemptError:
    """One model attempt that failed, with a stable error category."""

    model: str
    attempt: int  # 1-based within that model
    category: ErrorCategory
    message: str

    def format(self) -> str:
        return f"[{self.model} 第{self.attempt}次] {self.category.value}: {self.message}"


@dataclass
class KeyFailure:
    path: str
    original: str
    error: str
    attempts: list[AttemptError] = field(default_factory=list)


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
    attempts: list[AttemptError] = []
    slot: KeySlot | None = None

    def record(model: ModelConfig, attempt: int, exc: BaseException) -> AttemptError:
        msg = str(exc) or type(exc).__name__
        # Never leak a model's refusal prose; the exception message is already safe.
        rec = AttemptError(
            model=model.model,
            attempt=attempt,
            category=classify_error(exc),
            message=msg,
        )
        attempts.append(rec)
        return rec

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
                except HTTPStatusError as exc:
                    record(model, attempt + 1, exc)
                    retry_after = 1.5 if exc.status == 429 else None
                    if attempt + 1 < config.attempts_per_model or mi + 1 < len(config.models):
                        await asyncio.sleep(_backoff_seconds(attempt, retry_after))
                    continue
                except (RefusalError, ParseError, TranslateError) as exc:
                    record(model, attempt + 1, exc)
                except Exception as exc:  # noqa: BLE001 — isolate per key
                    wrapped = TranslateError(
                        f"意外错误 {type(exc).__name__}: {exc}",
                        category=classify_error(exc),
                    )
                    record(model, attempt + 1, wrapped)
                if attempt + 1 < config.attempts_per_model:
                    await asyncio.sleep(_backoff_seconds(attempt))
            # Fallback to the next model is not itself a failure.
    finally:
        progress.release_slot(slot)

    progress.tick(False)
    summary = attempts[-1].format() if attempts else "未知错误"
    return KeyFailure(
        path=item.path_str,
        original=item.value,
        error=summary,
        attempts=attempts,
    )


async def translate_document(
    source: Path,
    config: AppConfig,
    *,
    client: ModelClient | None = None,
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

    owns_client = client is None
    if client is None:
        client = ModelClient(config)
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
        if owns_client:
            await client.aclose()

    dest = output_path_for(source, config.output_prefix)
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
        if f.attempts:
            lines.append("  attempts:")
            for att in f.attempts:
                lines.append(f"    {att.format()}")
        else:
            lines.append(f"  error: {f.error}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def scan_file(source: Path, config: AppConfig) -> list[TranslatableItem]:
    loaded = load_yaml(source)
    return scan_translatable(loaded.data, config.skip_key_patterns)
