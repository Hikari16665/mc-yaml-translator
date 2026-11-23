from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mc_yaml_translator.config import (
    ConfigError,
    DEFAULT_CONFIG_TOML,
    bundled_example_config,
    load_config,
    parse_config,
    write_example_config,
)


def test_parse_limits_zero_and_omitted():
    cfg = parse_config(
        {
            "models": [
                {
                    "base_url": "http://localhost/v1",
                    "api_key": "sk-real-key",
                    "model": "m",
                    "qps": 0,
                    "qpm": 0,
                    # max_streams omitted
                }
            ]
        }
    )
    m = cfg.models[0]
    assert m.qps is None
    assert m.qpm is None
    assert m.max_streams is None
    assert m.completions_url().endswith("/chat/completions")


def test_unparseable_limits_become_unlimited(caplog):
    with caplog.at_level(logging.WARNING, logger="mc_yaml_translator"):
        cfg = parse_config(
            {
                "models": [
                    {
                        "base_url": "http://localhost/v1",
                        "api_key": "sk-real-key",
                        "model": "m",
                        "qps": "not-a-number",
                        "qpm": ["nope"],
                        "max_streams": True,
                    }
                ]
            }
        )
    m = cfg.models[0]
    assert m.qps is None
    assert m.qpm is None
    assert m.max_streams is None
    text = caplog.text
    assert "qps" in text and "不限制" in text
    assert "qpm" in text
    assert "max_streams" in text


def test_parse_positive_limits():
    cfg = parse_config(
        {
            "target_language": "ja",
            "retries": 1,
            "models": [
                {
                    "base_url": "http://x/v1",
                    "api_key": "sk-abc",
                    "model": "gpt",
                    "qps": 2.5,
                    "qpm": 60,
                    "max_streams": 3,
                }
            ],
        }
    )
    assert cfg.target_language == "ja"
    assert cfg.attempts_per_model == 2
    assert cfg.models[0].qps == 2.5
    assert cfg.models[0].qpm == 60
    assert cfg.models[0].max_streams == 3
    assert cfg.output_prefix == "translated_"


def test_rejects_placeholder_api_key():
    with pytest.raises(ConfigError, match="示例"):
        parse_config(
            {
                "models": [
                    {
                        "base_url": "http://x/v1",
                        "api_key": "sk-your-api-key-here",
                        "model": "m",
                    }
                ]
            }
        )


def test_requires_models_and_fields():
    with pytest.raises(ConfigError, match="CLIENT-CONFIG"):
        parse_config({})
    with pytest.raises(ConfigError, match="base_url"):
        parse_config({"models": [{"api_key": "k", "model": "m"}]})
    with pytest.raises(ConfigError, match="api_key"):
        parse_config(
            {"models": [{"base_url": "http://x/v1", "api_key": "", "model": "m"}]}
        )
    with pytest.raises(ConfigError, match="api_key"):
        parse_config(
            {"models": [{"base_url": "http://x/v1", "api_key": "   ", "model": "m"}]}
        )


def test_bad_toml_format(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("models = [\nnot toml", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(p)


def test_example_config_from_bundled_file(tmp_path: Path):
    pkg = Path(__file__).resolve().parents[1] / "src" / "mc_yaml_translator" / "config.toml.example"
    root = Path(__file__).resolve().parents[1] / "config.toml.example"
    bundled = bundled_example_config()
    assert pkg.is_file()
    assert bundled == pkg.read_text(encoding="utf-8")
    assert bundled == root.read_text(encoding="utf-8")
    assert bundled == DEFAULT_CONFIG_TOML
    assert "output_prefix" in bundled
    assert "translated_" in bundled
    assert '"null"' not in bundled
    assert "pause_at_end" not in bundled

    p = tmp_path / "config.toml"
    write_example_config(p)
    assert p.read_text(encoding="utf-8") == bundled
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text(bundled.replace("sk-your-api-key-here", "sk-live"), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.models[0].model
    assert cfg.output_prefix == "translated_"
    assert cfg.target_language == "zh_CN"
    assert cfg.source_language == "auto"
    assert cfg.source_is_auto
    assert cfg.is_multi_target is False
    assert cfg.target_languages == ["zh_CN"]
    assert "source_language" in bundled
    assert "translated_<语言代码>_" in bundled or "translated_<" in bundled


def test_source_language_auto_default_and_specific():
    base = {"models": [{"base_url": "http://x/v1", "api_key": "sk-abc", "model": "m"}]}
    cfg = parse_config(base)
    assert cfg.source_language == "auto"
    assert cfg.source_is_auto
    cfg = parse_config({**base, "source_language": "EN"})
    assert cfg.source_language == "EN"
    assert not cfg.source_is_auto
    cfg = parse_config({**base, "source_language": "  Auto  "})
    assert cfg.source_language == "auto"
    cfg = parse_config({**base, "source_language": ""})
    assert cfg.source_language == "auto"
    with pytest.raises(ConfigError, match="语言代码"):
        parse_config({**base, "source_language": "en/US"})
    with pytest.raises(ConfigError, match="语言代码"):
        parse_config({**base, "source_language": "../x"})


def test_target_language_string_or_list():
    base = {"models": [{"base_url": "http://x/v1", "api_key": "sk-abc", "model": "m"}]}
    cfg = parse_config({**base, "target_language": "ja_JP"})
    assert cfg.target_language == "ja_JP"
    assert cfg.is_multi_target is False
    assert cfg.target_languages == ["ja_JP"]

    cfg = parse_config({**base, "target_language": ["zh_CN", "ja_JP"]})
    assert cfg.is_multi_target is True
    assert cfg.target_languages == ["zh_CN", "ja_JP"]

    # a one-item list is still list mode
    cfg = parse_config({**base, "target_language": ["zh_CN"]})
    assert cfg.is_multi_target is True
    assert cfg.target_languages == ["zh_CN"]


def test_target_language_dedupe_and_invalid(caplog):
    base = {"models": [{"base_url": "http://x/v1", "api_key": "sk-abc", "model": "m"}]}
    import logging
    with caplog.at_level(logging.WARNING, logger="mc_yaml_translator"):
        cfg = parse_config({**base, "target_language": ["zh_CN", "zh-CN", "ZH_CN", "ja_JP", ""]})
    assert cfg.target_languages == ["zh_CN", "ja_JP"]
    assert "重复" in caplog.text or "空项" in caplog.text

    with pytest.raises(ConfigError, match="不能为空"):
        parse_config({**base, "target_language": []})
    with pytest.raises(ConfigError, match="不能为空"):
        parse_config({**base, "target_language": ["", "  "]})
    with pytest.raises(ConfigError, match="语言代码|字符串"):
        parse_config({**base, "target_language": ["zh_CN", 1]})
    with pytest.raises(ConfigError, match="auto"):
        parse_config({**base, "target_language": "auto"})
    with pytest.raises(ConfigError, match="语言代码"):
        parse_config({**base, "target_language": "zh CN"})
