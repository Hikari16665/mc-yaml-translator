from __future__ import annotations

from pathlib import Path

from mc_yaml_translator.cli import main
from mc_yaml_translator.config import DEFAULT_CONFIG_TOML


def test_no_args_writes_example_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    cfg = tmp_path / "config.toml"
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert "[[models]]" in text
    assert "sk-your-api-key-here" in text
    out = capsys.readouterr().out
    assert "已生成示例配置" in out
    # second run: config exists, no rewrite required, still exits 0
    stamp = cfg.read_text(encoding="utf-8")
    assert main([]) == 0
    assert cfg.read_text(encoding="utf-8") == stamp
    assert "已存在" in capsys.readouterr().out


def test_scan_lists_keys(tmp_path: Path, capsys):
    yml = tmp_path / "m.yml"
    yml.write_text('welcome: "Hello there friend"\ncount: 3\n', encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        DEFAULT_CONFIG_TOML.replace("sk-your-api-key-here", "sk-live"),
        encoding="utf-8",
    )
    code = main(["--config", str(cfg), "--scan", str(yml)])
    assert code == 0
    out = capsys.readouterr().out
    assert "welcome" in out
    assert "Hello there friend" in out
    assert "count" not in out or "待翻译" in out
