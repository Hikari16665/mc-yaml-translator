"""Command-line entry: first-run config generation, drag-and-drop translation."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mc_yaml_translator import __version__
from mc_yaml_translator.config import (
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
from mc_yaml_translator.yaml_io import output_path_for


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
    p.add_argument("--no-pause", action="store_true", help="结束后不要暂停")
    p.add_argument("-v", "--verbose", action="store_true", help="打印每个键的结果摘要")
    p.add_argument("--version", action="version", version=f"mc-yaml-translator {__version__}")
    return p.parse_args(argv)


def _print_result(result: FileResult, verbose: bool) -> None:
    if result.skipped_no_keys:
        print(f"{result.source}: 没有需要翻译的文本。")
        return
    print(
        f"{result.source} → {result.output}: "
        f"完成 {result.translated}/{result.total}"
        + (f"，失败 {len(result.failed)}" if result.failed else "")
    )
    if result.failure_log:
        print(f"  失败详情: {result.failure_log}")
    if verbose:
        for f in result.failed:
            print(f"  ! {f.path}: {f.error}")


async def _run_files(files: list[Path], args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    exit_code = 0
    for path in files:
        if not path.is_file():
            print(f"找不到文件: {path}", file=sys.stderr)
            exit_code = 1
            continue
        if not _is_yaml(path):
            print(f"跳过非 YAML 文件: {path}", file=sys.stderr)
            exit_code = 1
            continue
        if args.scan:
            items = scan_file(path, config)
            print(f"{path}: {len(items)} 个待翻译键")
            for it in items:
                preview = it.value.replace("\n", "\\n")
                if len(preview) > 80:
                    preview = preview[:80] + "…"
                print(f"  {it.path_str}: {preview}")
            continue
        dest = output_path_for(path, config.output_suffix)
        if dest.resolve() == path.resolve():
            print(f"拒绝覆盖原文件: {path}", file=sys.stderr)
            exit_code = 1
            continue
        try:
            result = await translate_document(path, config)
        except Exception as exc:  # noqa: BLE001
            print(f"处理 {path} 时出错: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        _print_result(result, args.verbose)
        if result.failed:
            exit_code = 1
    return exit_code


def main(argv: list[str] | None = None) -> int:
    enable_windows_ansi()
    args = _parse_args(argv)
    config_path = Path(args.config) if args.config else default_config_path()

    if not args.files:
        # --scan / --version already handled; no paths means first-run / usage.
        if args.scan:
            print("请提供至少一个 YAML 文件。", file=sys.stderr)
            return 2
        return _handle_no_args(config_path)

    files = [Path(f) for f in args.files]
    try:
        code = asyncio.run(_run_files(files, args))
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        code = 130

    pause = bool(not args.no_pause)
    if args.config or True:
        # Honour config.pause_at_end when the file exists; --no-pause always wins.
        try:
            from mc_yaml_translator.config import load_config as _lc

            if config_path.is_file():
                pause = _lc(config_path).pause_at_end and not args.no_pause
        except ConfigError:
            pass
    pause_if_needed(pause)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
