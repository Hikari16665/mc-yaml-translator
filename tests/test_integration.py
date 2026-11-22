from __future__ import annotations

import json
from pathlib import Path

import pytest

from mc_yaml_translator.config import parse_config
from mc_yaml_translator.translator import translate_document
from mc_yaml_translator.yaml_io import load_yaml
from mock_openai import MockOpenAI, _user_text, ok_payload, scripted_handler


def _cfg(base_url: str, model: str = "test-model", **more):
    entry = {"base_url": base_url, "api_key": "sk-test", "model": model, **more}
    return parse_config(
        {
            "target_language": "zh_CN",
            "source_language": "en",
            "retries": 1,
            "timeout_seconds": 10,
            "pause_at_end": False,
            "models": [entry],
        }
    )


def _cfg_chain(urls_models: list[tuple[str, str]], retries: int = 0):
    return parse_config(
        {
            "retries": retries,
            "timeout_seconds": 10,
            "pause_at_end": False,
            "models": [
                {"base_url": u, "api_key": "sk-test", "model": m, "max_streams": 2}
                for u, m in urls_models
            ],
        }
    )


SAMPLE = """# keep me
messages:
  welcome: "&aHello, %player%!"
  money: "You have {0} coins."
  material: DIAMOND_SWORD
  count: 3
  lore: |
    A fine blade.
  title: "Shop"
"""


@pytest.mark.asyncio
async def test_happy_path_preserves_structure_and_placeholders(tmp_path: Path):
    src = tmp_path / "messages.yml"
    src.write_text(SAMPLE, encoding="utf-8")
    with MockOpenAI() as srv:
        cfg = _cfg(srv.base_url, max_streams=3, qps=20)
        result = await translate_document(src, cfg, disable_progress=True)
    assert result.output is not None
    assert result.failed == []
    assert result.translated >= 3
    out = result.output.read_text(encoding="utf-8")
    assert out.startswith("# keep me")
    assert "译:" in out
    assert "%player%" in out
    assert "{0}" in out
    assert "DIAMOND_SWORD" in out
    assert "count: 3" in out
    assert "lore: |" in out
    # original not overwritten
    assert "Hello, %player%" in src.read_text(encoding="utf-8")
    assert result.output.name == "messages.translated.yml"


@pytest.mark.asyncio
async def test_refusal_malformed_and_dropped_placeholders_do_not_enter_output(tmp_path: Path):
    src = tmp_path / "m.yml"
    src.write_text(
        'a: "Alpha sentence here"\n'
        'b: "Bravo sentence here"\n'
        'c: "Charlie has {0} apples"\n',
        encoding="utf-8",
    )

    def handler(body, headers):
        src_text = _user_text(body)
        # After masking, we look at the original-ish user content.
        if "Alpha" in src_text:
            return 200, json.dumps({"status": "refused", "translation": "I will not translate this"})
        if "Bravo" in src_text:
            return 200, "this is not json at all, just a refusal in prose"
        if "Charlie" in src_text or "apples" in src_text:
            # well-formed JSON but placeholder dropped
            return 200, ok_payload("查理有苹果")
        return 200, ok_payload("译:" + src_text)

    with MockOpenAI(handler) as srv:
        cfg = _cfg(srv.base_url, retries=0)
        result = await translate_document(src, cfg, disable_progress=True)

    assert result.output is not None
    text = result.output.read_text(encoding="utf-8")
    # originals kept; refusal prose must never appear
    assert "I will not translate" not in text
    assert "this is not json" not in text
    assert "Alpha sentence here" in text
    assert "Bravo sentence here" in text
    assert "Charlie has {0} apples" in text
    assert "查理有苹果" not in text
    assert len(result.failed) == 3
    assert result.failure_log is not None
    log = result.failure_log.read_text(encoding="utf-8")
    assert "a" in log and "b" in log and "c" in log


@pytest.mark.asyncio
async def test_http_errors_fallback_to_next_model(tmp_path: Path):
    src = tmp_path / "m.yml"
    src.write_text('hello: "A full greeting sentence"\n', encoding="utf-8")

    calls = {"primary": 0, "secondary": 0}

    def primary(body, headers):
        calls["primary"] += 1
        return 500, {"error": {"message": "boom"}}

    def secondary(body, headers):
        calls["secondary"] += 1
        return 200, ok_payload("你好，旅行者")

    with MockOpenAI(primary) as a, MockOpenAI(secondary) as b:
        cfg = _cfg_chain([(a.base_url, "bad-model"), (b.base_url, "good-model")], retries=1)
        result = await translate_document(src, cfg, disable_progress=True)

    assert result.failed == []
    assert "你好，旅行者" in result.output.read_text(encoding="utf-8")
    # primary retried then fell over; fallback itself is not a failure
    assert calls["primary"] >= 2
    assert calls["secondary"] == 1


@pytest.mark.asyncio
async def test_429_then_success_on_retry(tmp_path: Path):
    src = tmp_path / "m.yml"
    src.write_text('hello: "A full greeting sentence"\n', encoding="utf-8")

    def script(body, n):
        if n == 1:
            return 429, {"error": {"message": "rate"}}
        return 200, ok_payload("译成功")

    with MockOpenAI(scripted_handler(script)) as srv:
        cfg = _cfg(srv.base_url, retries=2)
        result = await translate_document(src, cfg, disable_progress=True)
    assert result.failed == []
    assert "译成功" in result.output.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_scan_skips_data_in_realistic_sample(sample_dir: Path, tmp_path: Path):
    src = sample_dir / "messages.yml"
    dest = tmp_path / "messages.yml"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    with MockOpenAI() as srv:
        cfg = _cfg(srv.base_url, max_streams=4)
        result = await translate_document(dest, cfg, disable_progress=True)
    assert result.total > 5
    out = result.output.read_text(encoding="utf-8")
    # data left alone
    assert "minecraft:golden_apple" in out
    assert "DIAMOND_SWORD" in out or "diamond_sword" in out
    assert "myplugin.shop.excalibur" in out
    assert "/warp spawn" in out
    assert "entity.player.levelup" in out
    # comments survive
    assert "language file" in out.lower() or "语言" in out or "header" in out.lower() or "#" in out
