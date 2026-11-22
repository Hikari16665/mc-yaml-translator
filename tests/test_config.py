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
