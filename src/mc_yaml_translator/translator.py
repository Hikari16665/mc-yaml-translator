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
    LoadedYaml,
    TranslatableItem,
    apply_translation,
    clone_loaded,
    dump_yaml,
    failure_path_for,
    load_yaml,
    output_path_for,
    rebind_item,
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
    target_language: str | None = None

    def display_path(self, *, list_mode: bool = False) -> str:
        if list_mode and self.target_language:
            return f"{self.path} → {self.target_language}"
        return self.path


@dataclass
class FileResult:
    source: Path
    output: Path | None
    total: int
    translated: int
    failed: list[KeyFailure] = field(default_factory=list)
    skipped_no_keys: bool = False
    failure_log: Path | None = None
    outputs: list[Path] = field(default_factory=list)
    failure_logs: list[Path] = field(default_factory=list)
    skipped_lang: int = 0
    multi_target: bool = False


@dataclass
class _Subtask:
    item: TranslatableItem
    target_language: str
    list_mode: bool

    @property
    def bar_title(self) -> str:
        if self.list_mode:
            return f"{self.item.path_str} → {self.target_language}"
        return self.item.path_str


@dataclass
class _ItemOutcome:
    failure: KeyFailure | None = None
    skipped_lang: bool = False


def _backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    if retry_after is not None and retry_after > 0:
        return min(retry_after, 30.0)
    return min(8.0, 0.4 * (2 ** attempt))


class _LanguageSkip(Exception):
    """Internal: the model reported a source-language skip. Not a failure."""


async def _translate_one_model(
    client: ModelClient,
    model: ModelConfig,
    limiter: RateLimiter,
    original: str,
    slot: KeySlot | None,
    *,
    target_language: str,
    allow_skip: bool,
) -> str:
    masked = mask_placeholders(original)

    def on_delta(buf: str) -> None:
        if slot is not None:
            slot.update_preview(buf)

    raw = await client.complete(
        model, masked.text, on_delta=on_delta, target_language=target_language
    )
    parsed = parse_structured(raw)
    if parsed.skipped:
        if allow_skip:
            raise _LanguageSkip()
        raise TranslateError("源语言为 auto 时不应跳过可翻译文本（模型返回了 skip）")
    translation = parsed.translation
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
    *,
    target_language: str | None = None,
    list_mode: bool = False,
) -> _ItemOutcome:
    attempts: list[AttemptError] = []
    slot: KeySlot | None = None
    target = target_language or config.target_languages[0]
    allow_skip = not config.source_is_auto
    sub = _Subtask(item=item, target_language=target, list_mode=list_mode)

    def record(model: ModelConfig, attempt: int, exc: BaseException) -> AttemptError:
        msg = str(exc) or type(exc).__name__
        rec = AttemptError(
            model=model.model,
            attempt=attempt,
            category=classify_error(exc),
            message=msg,
        )
        attempts.append(rec)
        return rec

    try:
        for mi, (model, limiter) in enumerate(zip(config.models, limiters)):
            for attempt in range(config.attempts_per_model):
                try:
                    async with limiter:
                        # Acquire only while the request is actually in flight so
                        # waiting keys do not occupy preview rows, and so slots
                        # stay available for later keys (see ProgressUI reuse).
                        slot = progress.acquire_slot(sub.bar_title)
                        try:
                            text = await _translate_one_model(
                                client,
                                model,
                                limiter,
                                item.value,
                                slot,
                                target_language=target,
                                allow_skip=allow_skip,
                            )
                        finally:
                            progress.release_slot(slot)
                            slot = None
                    apply_translation(item, text)
                    progress.tick(True)
                    return _ItemOutcome()
                except _LanguageSkip:
                    progress.tick(True, skipped=True)
                    return _ItemOutcome(skipped_lang=True)
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
    return _ItemOutcome(
        failure=KeyFailure(
            path=item.path_str,
            original=item.value,
            error=summary,
            attempts=attempts,
            target_language=target if list_mode else None,
        )
    )


# Backward-compatible name used in older tests if any imported the coroutine contract.
# New code should use the _ItemOutcome return.


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
    langs = config.target_languages
    multi = config.is_multi_target
    if not items:
        return FileResult(
            source=source,
            output=None,
            total=0,
            translated=0,
            skipped_no_keys=True,
            multi_target=multi,
        )

    if multi:
        trees: dict[str, LoadedYaml] = {lang: clone_loaded(loaded) for lang in langs}
    else:
        trees = {langs[0]: loaded}

    subtasks: list[_Subtask] = []
    for lang in langs:
        for it in items:
            bound = rebind_item(it, trees[lang].data)
            subtasks.append(_Subtask(item=bound, target_language=lang, list_mode=multi))

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
    skipped_lang = 0
    fail_lock = asyncio.Lock()
    unit = "任务" if multi else "键"
    ui_factory = progress_factory or (
        lambda n: ProgressUI(n, disable=disable_progress, unit=unit)
    )

    async def work(sub: _Subtask) -> None:
        nonlocal skipped_lang
        async with sem:
            outcome = await translate_item(
                sub.item,
                config,
                client,
                limiters,
                ui,
                target_language=sub.target_language,
                list_mode=sub.list_mode,
            )
            if outcome.failure is not None or outcome.skipped_lang:
                async with fail_lock:
                    if outcome.failure is not None:
                        failures.append(outcome.failure)
                    if outcome.skipped_lang:
                        skipped_lang += 1

    try:
        with ui_factory(len(subtasks)) as ui:
            async with asyncio.TaskGroup() as tg:
                for sub in subtasks:
                    tg.create_task(work(sub))
    finally:
        if owns_client:
            await client.aclose()

    outputs: list[Path] = []
    fail_logs: list[Path] = []
    for lang in langs:
        dest = output_path_for(source, config.output_prefix, lang if multi else None)
        dump_yaml(trees[lang], dest)
        outputs.append(dest)
        lang_fails = [
            f for f in failures if (f.target_language == lang if multi else True)
        ]
        if lang_fails:
            log = failure_path_for(dest)
            _write_failures(log, source, subtasks, lang_fails, list_mode=multi, lang=lang if multi else None)
            fail_logs.append(log)

    translated = len(subtasks) - len(failures) - skipped_lang
    return FileResult(
        source=source,
        output=outputs[0] if outputs else None,
        outputs=outputs,
        total=len(subtasks),
        translated=translated,
        failed=failures,
        failure_log=fail_logs[0] if fail_logs else None,
        failure_logs=fail_logs,
        skipped_lang=skipped_lang,
        multi_target=multi,
    )


def _write_failures(
    path: Path,
    source: Path,
    subtasks: list[_Subtask],
    failures: list[KeyFailure],
    *,
    list_mode: bool,
    lang: str | None,
) -> None:
    total = len(subtasks) if not lang else sum(1 for s in subtasks if s.target_language == lang)
    header_lang = f" ({lang})" if lang else ""
    lines = [
        f"# Translation failures for {source}{header_lang}",
        f"# {len(failures)} failed / {total} total",
        "",
    ]
    for f in failures:
        orig = f.original.replace("\n", "\\n")
        if len(orig) > 400:
            orig = orig[:400] + "…"
        lines.append(f.display_path(list_mode=list_mode))
        if list_mode and f.target_language:
            lines.append(f"  lang: {f.target_language}")
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
