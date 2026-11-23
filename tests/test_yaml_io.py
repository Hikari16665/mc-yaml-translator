from __future__ import annotations

from pathlib import Path

import pytest

from mc_yaml_translator.config import parse_config
from mc_yaml_translator.heuristics import should_translate
from mc_yaml_translator.yaml_io import (
    apply_translation,
    dump_yaml,
    load_yaml,
    output_path_for,
    output_paths_for,
    scan_translatable,
)


SAMPLE = """# header comment
messages:
  welcome: "&aHello, %player%!"  # inline
  amount: 5
  enabled: true
  material: DIAMOND_SWORD
  empty: ""
  lore: |
    A fine blade.
    Use it well.
  list:
    - "First line of text"
    - 42
    - "minecraft:stone"
  gui:
    title: "Shop"
    size: 54
    items:
      0:
        material: STONE
        name: "&bSpecial Stone"
        command: /spawn
"""


def test_scan_and_preserve_structure(tmp_path: Path):
    src = tmp_path / "messages.yml"
    src.write_text(SAMPLE, encoding="utf-8")
    loaded = load_yaml(src)
    items = scan_translatable(loaded.data)
    paths = {it.path_str for it in items}
    assert "messages.welcome" in paths
    assert "messages.lore" in paths
    assert "messages.list.0" in paths
    assert "messages.gui.title" in paths
    assert "messages.gui.items.0.name" in paths
    # data skipped
    assert "messages.amount" not in paths
    assert "messages.enabled" not in paths
    assert "messages.material" not in paths
    assert "messages.list.1" not in paths
    assert "messages.list.2" not in paths
    assert "messages.gui.size" not in paths
    assert "messages.gui.items.0.material" not in paths
    assert "messages.gui.items.0.command" not in paths
    assert "messages.empty" not in paths

    # translate one and dump; comments / order / literal style should remain
    welcome = next(i for i in items if i.path_str == "messages.welcome")
    apply_translation(welcome, "&a你好，%player%！")
    lore = next(i for i in items if i.path_str == "messages.lore")
    apply_translation(lore, "一柄利刃。\n好好使用。")
    dest = tmp_path / "translated_messages.yml"
    dump_yaml(loaded, dest)
    out = dest.read_text(encoding="utf-8")
    assert out.startswith("# header comment")
    assert "# inline" in out
    assert "&a你好，%player%！" in out
    # literal block should still be a literal (pipe)
    assert "lore: |" in out or "lore:|" in out
    # untranslated data intact and in place
    assert "DIAMOND_SWORD" in out
    assert "minecraft:stone" in out
    assert "size: 54" in out
    # key order: welcome still before amount
    assert out.index("welcome:") < out.index("amount:")
    assert out.index("amount:") < out.index("enabled:")


def test_output_path_and_bom(tmp_path: Path):
    src = tmp_path / "lang.yml"
    src.write_bytes(b"\xef\xbb\xbfmsg: \"Hello there\"\n")
    loaded = load_yaml(src)
    assert loaded.had_bom
    items = scan_translatable(loaded.data)
    assert items and items[0].value == "Hello there"
    apply_translation(items[0], "你好呀")
    dest = output_path_for(src)
    assert dest.name == "translated_lang.yml"
    dump_yaml(loaded, dest)
    raw = dest.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")


def test_double_quoted_style_kept(tmp_path: Path):
    src = tmp_path / "q.yml"
    src.write_text('title: "Hello, player"\n', encoding="utf-8")
    loaded = load_yaml(src)
    items = scan_translatable(loaded.data)
    apply_translation(items[0], "你好，玩家")
    dest = tmp_path / "q.out.yml"
    dump_yaml(loaded, dest)
    text = dest.read_text(encoding="utf-8")
    assert 'title: "你好，玩家"' in text


def test_yaml_parse_error(tmp_path: Path):
    from mc_yaml_translator.yaml_io import YamlParseError, failure_path_for

    src = tmp_path / "bad.yml"
    src.write_text("foo: [unterminated\n", encoding="utf-8")
    with pytest.raises(YamlParseError, match="YAML 解析失败"):
        load_yaml(src)
    out = output_path_for(tmp_path / "messages.yml")
    assert out.name == "translated_messages.yml"
    assert failure_path_for(out).name == "translated_messages.failures.txt"


def test_output_path_with_language_code():
    src = Path("dir") / "messages.yml"
    assert output_path_for(src).name == "translated_messages.yml"
    assert output_path_for(src, "translated_", "zh_CN").name == "translated_zh_CN_messages.yml"
    paths = output_paths_for(src, "translated_", ["zh_CN", "ja_JP"], list_mode=True)
    assert [p.name for p in paths] == ["translated_zh_CN_messages.yml", "translated_ja_JP_messages.yml"]
    single = output_paths_for(src, "translated_", ["zh_CN"], list_mode=False)
    assert [p.name for p in single] == ["translated_messages.yml"]
