from __future__ import annotations

import pytest

from mc_yaml_translator.config import ConfigError, parse_config, write_example_config


def test_parse_limits_null_and_zero():
    cfg = parse_config(
        {
            "models": [
                {
                    "base_url": "http://localhost/v1",
                    "api_key": "sk-real-key",
                    "model": "m",
                    "qps": 0,
                    "qpm": "null",
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
    with pytest.raises(ConfigError):
        parse_config({})
    with pytest.raises(ConfigError, match="base_url"):
        parse_config({"models": [{"api_key": "k", "model": "m"}]})


def test_example_config_roundtrip(tmp_path):
    from mc_yaml_translator.config import DEFAULT_CONFIG_TOML, load_config

    p = tmp_path / "config.toml"
    write_example_config(p)
    text = p.read_text(encoding="utf-8")
    assert "[[models]]" in text
    assert "max_streams" in text
    # example key must be rejected when loaded for real work
    with pytest.raises(ConfigError):
        load_config(p)
    # After replacing the key it should load
    p.write_text(text.replace("sk-your-api-key-here", "sk-live"), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.models[0].model
    assert cfg.pause_at_end is True
    assert DEFAULT_CONFIG_TOML.startswith("#")
