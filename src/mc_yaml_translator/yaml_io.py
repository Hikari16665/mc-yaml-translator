"""Round-trip YAML load / scan / replace / dump, preserving order, comments, and style."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    PlainScalarString,
    SingleQuotedScalarString,
)

from mc_yaml_translator.heuristics import path_to_str, should_translate


def make_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 10**9  # do not re-wrap long lines
    yaml.allow_duplicate_keys = True
    # Do not force indent; ruamel keeps the indent it observed on load.
    yaml.default_flow_style = None
    return yaml


@dataclass
class LoadedYaml:
    path: Path
    data: Any
    yaml: YAML
    had_bom: bool
    trailing_newline: bool
    raw_bytes: bytes


def load_yaml(path: Path) -> LoadedYaml:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    trailing = text.endswith("\n")
    yaml = make_yaml()
    data = yaml.load(text) if text.strip() else None
    return LoadedYaml(
        path=path,
        data=data,
        yaml=yaml,
        had_bom=had_bom,
        trailing_newline=trailing,
        raw_bytes=raw,
    )


def restyle_scalar(old: Any, new_text: str) -> Any:
    """Create a replacement scalar of the same ruamel ScalarString type."""
    if isinstance(old, LiteralScalarString):
        return LiteralScalarString(new_text)
    if isinstance(old, FoldedScalarString):
        return FoldedScalarString(new_text)
    if isinstance(old, SingleQuotedScalarString):
        return SingleQuotedScalarString(new_text)
    if isinstance(old, DoubleQuotedScalarString):
        return DoubleQuotedScalarString(new_text)
    if isinstance(old, PlainScalarString):
        return PlainScalarString(new_text)
    # Plain untagged str: keep plain unless the new text would be misparsed.
    return new_text


@dataclass
class TranslatableItem:
    path: tuple[object, ...]
    value: str
    # How to write the translation back. parent + key covers maps and lists.
    parent: Any
    key: object

    @property
    def path_str(self) -> str:
        return path_to_str(self.path)


def _walk(
    node: Any,
    path: tuple[object, ...],
    skip_patterns: Sequence[re.Pattern[str]] | None,
    seen: set[int],
) -> Iterator[TranslatableItem]:
    ident = id(node)
    # Skip already-visited containers to avoid cycles / shared graphs;
    # still visit shared scalars from each path so we can rewrite each key.
    if not isinstance(node, (str, bytes, int, float, bool, type(None))) and ident in seen:
        return
    if not isinstance(node, (str, int, float, bool, type(None), bytes)):
        seen.add(ident)

    if isinstance(node, CommentedMap):
        for key in node.keys():
            val = node[key]
            sub = path + (key,)
            if isinstance(val, str) or _is_scalar_str(val):
                if should_translate(str(val), sub, skip_patterns):
                    yield TranslatableItem(path=sub, value=str(val), parent=node, key=key)
            else:
                yield from _walk(val, sub, skip_patterns, seen)
        return

    if isinstance(node, CommentedSeq) or isinstance(node, list):
        for idx, val in enumerate(node):
            sub = path + (idx,)
            if isinstance(val, str) or _is_scalar_str(val):
                if should_translate(str(val), sub, skip_patterns):
                    yield TranslatableItem(path=sub, value=str(val), parent=node, key=idx)
            else:
                yield from _walk(val, sub, skip_patterns, seen)
        return

    if isinstance(node, dict):
        for key, val in node.items():
            sub = path + (key,)
            if isinstance(val, str) or _is_scalar_str(val):
                if should_translate(str(val), sub, skip_patterns):
                    yield TranslatableItem(path=sub, value=str(val), parent=node, key=key)
            else:
                yield from _walk(val, sub, skip_patterns, seen)


def _is_scalar_str(val: Any) -> bool:
    return isinstance(
        val,
        (
            str,
            LiteralScalarString,
            FoldedScalarString,
            SingleQuotedScalarString,
            DoubleQuotedScalarString,
            PlainScalarString,
        ),
    )


def scan_translatable(
    data: Any,
    skip_patterns: Sequence[Any] | None = None,
) -> list[TranslatableItem]:
    if data is None:
        return []
    return list(_walk(data, (), skip_patterns, set()))


def apply_translation(item: TranslatableItem, new_text: str) -> None:
    old = item.parent[item.key]
    item.parent[item.key] = restyle_scalar(old, new_text)


def dump_yaml(loaded: LoadedYaml, dest: Path) -> None:
    from io import StringIO

    buf = StringIO()
    loaded.yaml.dump(loaded.data, buf)
    text = buf.getvalue()
    if loaded.trailing_newline and not text.endswith("\n"):
        text += "\n"
    data = text.encode("utf-8")
    if loaded.had_bom:
        data = b"\xef\xbb\xbf" + data
    dest.write_bytes(data)


def output_path_for(source: Path, suffix: str) -> Path:
    # messages.yml + ".translated" → messages.translated.yml
    return source.with_name(source.stem + suffix + source.suffix)


def failure_path_for(output: Path) -> Path:
    return output.with_name(output.stem + ".failures.txt")
