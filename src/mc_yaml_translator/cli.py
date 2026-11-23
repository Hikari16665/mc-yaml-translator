"""Command-line entry: first-run config generation, drag-and-drop translation."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from mc_yaml_translator import __version__
from mc_yaml_translator.config import (
    AppConfig,
    ConfigError,
    default_config_path,
    load_config,
    write_example_config,
)
from mc_yaml_translator.console_win import (
    enable_windows_ansi,
    hide_and_free_console,
    owns_console,
    pause_if_needed,
)
from mc_yaml_translator.translator import FileResult, scan_file, translate_document
from mc_yaml_translator.yaml_io import (
    YamlParseError,
    load_yaml,
    output_paths_for,
)


YAML_SUFFIXES = {".yml", ".yaml"}


def _is_yaml(path: Path) -> bool:
    return path.suffix.lower() in YAML_SUFFIXES


def _handle_no_args(config_path: Path) -> int:
    """First double-click: write example config and exit without a visible window."""
    created = False
    if not config_path.is_file():
        write_example_config(config_path)
        created = True

    if owns_console():
        # Hide first so a double-click only leaves config.toml on disk.
        hide_and_free_console()
        return 0

    if created:
        print(f"已生成示例配置: {config_path}")
        print("请编辑其中的模型与目标语言，再把 .yml / .yaml 文件拖到本程序上。")
    else:
        print(f"配置文件已存在: {config_path}")
        print("把要翻译的 .yml / .yaml 文件拖到本程序上，或在命令行传入路径。")
        print("示例: mc-yaml-translator messages.yml")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mc-yaml-translator",
        description="将 Minecraft 插件 YAML 中的自然语言翻译为指定语言。",
    )
    p.add_argument("files", nargs="*", help="要翻译的 .yml / .yaml 文件（可拖到 exe 上）")
    p.add_argument("--config", type=Path, help="config.toml 路径（默认与 exe 同目录，或当前工作目录）")
    p.add_argument("--scan", action="store_true", help="只列出将要翻译的键，不调用模型")
    p.add_argument("--no-pause", action="store_true", help="出错时也不暂停（供脚本使用）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印每个键的结果摘要")
    p.add_argument("--version", action="version", version=f"mc-yaml-translator {__version__}")
    return p.parse_args(argv)


def _print_preflight_errors(errors: list[str]) -> None:
    print("启动前检查未通过，尚未开始翻译：", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    print("请修正上述问题后再试。", file=sys.stderr)


def _print_result(result: FileResult, verbose: bool) -> None:
    if result.skipped_no_keys:
        print(f"{result.source}: 没有需要翻译的文本。")
        return
    extra = ""
    if result.skipped_lang:
        extra += f"，跳过原文语言 {result.skipped_lang}"
    if result.failed:
        extra += f"，失败 {len(result.failed)}"
    if result.multi_target and result.outputs:
        print(f"{result.source}: 完成 {result.translated}/{result.total}{extra}")
        for dest in result.outputs:
            print(f"  → {dest}")
    else:
        print(
            f"{result.source} → {result.output}: "
            f"完成 {result.translated}/{result.total}{extra}"
        )
    logs = result.failure_logs or ([result.failure_log] if result.failure_log else [])
    for log in logs:
        print(f"  失败详情已写入: {log}")
    if verbose:
        for f in result.failed:
            print(_format_key_failure(f, list_mode=result.multi_target))


def _format_key_failure(fail, *, list_mode: bool = False) -> str:
    lines = [f"  {fail.display_path(list_mode=list_mode)}"]
    if fail.attempts:
        for att in fail.attempts:
            lines.append(f"    {att.format()}")
    else:
        lines.append(f"    {fail.error}")
    return "\n".join(lines)


def _print_failure_summary(results: list[FileResult]) -> None:
    print()
    print("======== 失败摘要 ========")
    for result in results:
        if not result.failed:
            continue
        print(f"文件 {result.source}（{len(result.failed)} 个失败）：")
        for fail in result.failed:
            print(_format_key_failure(fail, list_mode=result.multi_target))
        logs = result.failure_logs or ([result.failure_log] if result.failure_log else [])
        for log in logs:
            print(f"  失败详情已写入: {log}")


def preflight(files: list[Path], config_path: Path) -> tuple[AppConfig | None, list[str]]:
    """Validate config.toml and every input YAML before any translation starts."""
    errors: list[str] = []
    config: AppConfig | None = None
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))

    for path in files:
        if not path.is_file():
            errors.append(f"找不到文件: {path}")
            continue
        if not _is_yaml(path):
            errors.append(f"不是 YAML 文件（需要 .yml / .yaml）: {path}")
            continue
        try:
            load_yaml(path)
        except YamlParseError as exc:
            errors.append(str(exc))
            continue
        if config is not None:
            for dest in output_paths_for(
                path, config.output_prefix, config.target_languages, list_mode=config.is_multi_target
            ):
                if dest.resolve() == path.resolve():
                    errors.append(f"输出路径会覆盖原文件，已拒绝: {path} → {dest.name}")
    return config, errors


def _run_files(files: list[Path], args: argparse.Namespace, config_path: Path) -> tuple[int, bool]:
    """Return (exit_code, keep_window_open)."""
    config, errors = preflight(files, config_path)
    if errors:
        _print_preflight_errors(errors)
        return 2, True
    assert config is not None

    if args.scan:
        for path in files:
            items = scan_file(path, config)
            print(f"{path}: {len(items)} 个待翻译键")
            for it in items:
                preview = it.value.replace("\n", "\\n")
                if len(preview) > 80:
                    preview = preview[:80] + "…"
                print(f"  {it.path_str}: {preview}")
        return 0, False

    exit_code = 0
    keep_open = False
    results: list[FileResult] = []
    for path in files:
        try:
            result = asyncio.run(translate_document(path, config))
        except YamlParseError as exc:
            # Should have been caught in preflight; still keep the window open.
            print(f"YAML 解析失败: {exc}", file=sys.stderr)
            return 2, True
        except Exception as exc:  # noqa: BLE001
            print(f"处理 {path} 时出错: {exc}", file=sys.stderr)
            keep_open = True
            exit_code = 1
            continue
        _print_result(result, args.verbose)
        results.append(result)
        if result.failed:
            exit_code = 1
            keep_open = True

    if keep_open:
        _print_failure_summary(results)
    return exit_code, keep_open


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    enable_windows_ansi()
    args = _parse_args(argv)
    config_path = Path(args.config) if args.config else default_config_path()

    if not args.files:
        if args.scan:
            print("请提供至少一个 YAML 文件。", file=sys.stderr)
            if not args.no_pause:
                pause_if_needed(True)
            return 2
        return _handle_no_args(config_path)

    files = [Path(f) for f in args.files]
    try:
        code, keep_open = _run_files(files, args, config_path)
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        code, keep_open = 130, True

    # (a) All keys succeeded → do not pause, the console window closes.
    #     Any key failed, or a preflight/config/YAML error → keep the window open.
    pause_if_needed(keep_open and not args.no_pause)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
