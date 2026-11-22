from __future__ import annotations

from pathlib import Path

from mc_yaml_translator.cli import main
from mc_yaml_translator.config import DEFAULT_CONFIG_TOML, bundled_example_config


def test_no_args_writes_bundled_example_config(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    cfg = tmp_path / "config.toml"
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert text == bundled_example_config()
    assert text == DEFAULT_CONFIG_TOML
    assert "[[models]]" in text
    assert "sk-your-api-key-here" in text
    out = capsys.readouterr().out
    assert "已生成示例配置" in out
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


def _write_live_config(tmp_path: Path, base_url: str) -> Path:
    text = DEFAULT_CONFIG_TOML.replace("sk-your-api-key-here", "sk-live")
    text = text.replace("https://api.openai.com/v1", base_url)
    text = text.replace("retries = 2", "retries = 0")
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_cli_success_does_not_pause(tmp_path: Path, monkeypatch, capsys):
    from mock_openai import MockOpenAI

    paused: list[bool] = []
    monkeypatch.setattr("mc_yaml_translator.cli.pause_if_needed", lambda enabled: paused.append(enabled))
    src = tmp_path / "messages.yml"
    src.write_text('hello: "A full greeting sentence"\n', encoding="utf-8")
    with MockOpenAI() as srv:
        cfg = _write_live_config(tmp_path, srv.base_url)
        code = main(["--config", str(cfg), str(src)])
    assert code == 0
    assert paused == [False]
    assert (tmp_path / "translated_messages.yml").is_file()
    out = capsys.readouterr().out
    assert "失败摘要" not in out


def test_cli_failure_pauses_and_lists_categories(tmp_path: Path, monkeypatch, capsys):
    import json

    from mock_openai import MockOpenAI, _user_text, ok_payload

    paused: list[bool] = []
    monkeypatch.setattr("mc_yaml_translator.cli.pause_if_needed", lambda enabled: paused.append(enabled))

    def handler(body, headers):
        src_text = _user_text(body)
        if "Alpha" in src_text:
            return 200, json.dumps({"status": "refused", "translation": "I will not translate this"})
        return 200, ok_payload("译:" + src_text)

    src = tmp_path / "messages.yml"
    src.write_text(
        'a: "Alpha sentence here for translation"\n'
        'b: "Bravo sentence here for translation"\n',
        encoding="utf-8",
    )
    with MockOpenAI(handler) as srv:
        cfg = _write_live_config(tmp_path, srv.base_url)
        code = main(["--config", str(cfg), str(src)])
    assert code == 1
    assert paused == [True]
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "失败摘要" in combined
    assert "PROVIDER" in combined
    assert "a" in combined  # failed key path
    assert "I will not translate this" not in (tmp_path / "translated_messages.yml").read_text(encoding="utf-8")
    assert (tmp_path / "translated_messages.failures.txt").is_file()


def test_cli_bad_config_pauses_before_translation(tmp_path: Path, monkeypatch, capsys):
    paused: list[bool] = []
    monkeypatch.setattr("mc_yaml_translator.cli.pause_if_needed", lambda enabled: paused.append(enabled))
    src = tmp_path / "messages.yml"
    src.write_text('hello: "A full greeting sentence"\n', encoding="utf-8")

    # empty api_key
    cfg = tmp_path / "empty.toml"
    cfg.write_text(
        'target_language = "zh_CN"\n[[models]]\nbase_url = "http://127.0.0.1:1/v1"\napi_key = ""\nmodel = "m"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(cfg), str(src)]) == 2
    assert paused == [True]
    err = capsys.readouterr().err
    assert "启动前检查未通过" in err
    assert "CLIENT-CONFIG" in err
    assert "api_key" in err
    assert not (tmp_path / "translated_messages.yml").exists()

    paused.clear()
    # bad format
    bad = tmp_path / "bad.toml"
    bad.write_text("this = [is not toml\n", encoding="utf-8")
    assert main(["--config", str(bad), str(src)]) == 2
    assert paused == [True]
    err = capsys.readouterr().err
    assert "格式错误" in err
    assert "CLIENT-CONFIG" in err

    paused.clear()
    # missing field
    miss = tmp_path / "miss.toml"
    miss.write_text(
        '[[models]]\napi_key = "sk-live"\nmodel = "m"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(miss), str(src)]) == 2
    assert paused == [True]
    err = capsys.readouterr().err
    assert "base_url" in err
    assert "CLIENT-CONFIG" in err


def test_cli_bad_yaml_pauses_before_translation(tmp_path: Path, monkeypatch, capsys):
    paused: list[bool] = []
    monkeypatch.setattr("mc_yaml_translator.cli.pause_if_needed", lambda enabled: paused.append(enabled))
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        DEFAULT_CONFIG_TOML.replace("sk-your-api-key-here", "sk-live"),
        encoding="utf-8",
    )
    src = tmp_path / "messages.yml"
    src.write_text("foo: [unterminated\n", encoding="utf-8")
    # config points at a real key but no server is needed — validation happens first
    assert main(["--config", str(cfg), str(src)]) == 2
    assert paused == [True]
    err = capsys.readouterr().err
    assert "启动前检查未通过" in err
    assert "YAML 解析失败" in err
    assert not (tmp_path / "translated_messages.yml").exists()
