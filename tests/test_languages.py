"""source_language auto/filter and multi-target fan-out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mc_yaml_translator.client import build_messages
from mc_yaml_translator.config import parse_config
from mc_yaml_translator.progress import ProgressUI
from mc_yaml_translator.translator import translate_document
from mock_openai import MockOpenAI, _user_text, ok_payload


def _cfg(base_url: str, **more):
    data = {
        "target_language": "zh_CN",
        "source_language": "auto",
        "retries": 0,
        "timeout_seconds": 10,
        "models": [
            {"base_url": base_url, "api_key": "sk-test", "model": "test-model", "max_streams": 4}
        ],
    }
    data.update(more)
    # models may be overwritten if passed; keep url if only language keys given
    if "models" not in more:
        data["models"][0]["base_url"] = base_url
    return parse_config(data)


@pytest.mark.asyncio
async def test_source_language_skip_is_not_a_failure(tmp_path: Path):
    src = tmp_path / "messages.yml"
    src.write_text(
        'en: "Hello there, adventurer"\n'
        'zh: "欢迎回来，旅人"\n'
        'also: "You have {0} coins."\n',
        encoding="utf-8",
    )

    def handler(body, headers):
        text = _user_text(body)
        # Chinese already: skip. English: translate.
        if "欢迎" in text:
            return 200, json.dumps({"status": "skip", "translation": ""})
        if "Hello" in text:
            return 200, ok_payload("你好啊，冒险者")
        if "coins" in text or "{0}" in text or "__MCYT_PH_" in text:
            return 200, ok_payload("你有 __MCYT_PH_0__ 枚硬币。") if "__MCYT_PH_0__" in text else ok_payload("你有 {0} 枚硬币。")
        return 200, ok_payload("译:" + text)

    with MockOpenAI(handler) as srv:
        cfg = _cfg(srv.base_url, source_language="en")
        result = await translate_document(src, cfg, disable_progress=True)

    assert result.failed == []
    assert result.skipped_lang == 1
    assert result.translated == 2
    assert result.total == 3
    assert result.failure_log is None
    out = result.output.read_text(encoding="utf-8")
    assert "你好啊，冒险者" in out
    assert "欢迎回来，旅人" in out  # left untouched
    assert "{0}" in out
    assert "Hello there" not in out


@pytest.mark.asyncio
async def test_auto_source_does_not_accept_skip(tmp_path: Path):
    src = tmp_path / "m.yml"
    src.write_text('hello: "A full greeting sentence"\n', encoding="utf-8")

    def handler(body, headers):
        return 200, json.dumps({"status": "skip", "translation": ""})

    with MockOpenAI(handler) as srv:
        cfg = _cfg(srv.base_url, source_language="auto")
        result = await translate_document(src, cfg, disable_progress=True)
    assert result.failed
    assert result.skipped_lang == 0
    assert "A full greeting sentence" in result.output.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fan_out_writes_one_file_per_language(tmp_path: Path):
    src = tmp_path / "messages.yml"
    src.write_text(
        'welcome: "Hello, %player%!"\n'
        'coins: "You have {0} coins."\n'
        'skip_me: "DIAMOND_SWORD"\n',
        encoding="utf-8",
    )
    titles: list[str] = []

    class CaptureUI(ProgressUI):
        def acquire_slot(self, path: str):
            titles.append(path)
            return None

    def handler(body, headers):
        # Inspect the system prompt via the request to pick the target language.
        msgs = body.get("messages") or []
        system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
        text = _user_text(body)
        is_coins = "coins" in text.lower() or "coin" in text.lower()
        if "zh_CN" in system:
            if is_coins:
                ph = "__MCYT_PH_0__" if "__MCYT_PH_0__" in text else "{0}"
                return 200, ok_payload(f"你有 {ph} 枚硬币。")
            ph = "__MCYT_PH_0__" if "__MCYT_PH_0__" in text else "%player%"
            return 200, ok_payload(f"你好，{ph}！")
        if "ja_JP" in system:
            if is_coins:
                ph = "__MCYT_PH_0__" if "__MCYT_PH_0__" in text else "{0}"
                return 200, ok_payload(f"あなたは {ph} 枚のコインを持っています。")
            ph = "__MCYT_PH_0__" if "__MCYT_PH_0__" in text else "%player%"
            return 200, ok_payload(f"こんにちは、{ph}！")
        return 200, ok_payload("???")

    with MockOpenAI(handler) as srv:
        cfg = _cfg(srv.base_url, target_language=["zh_CN", "ja_JP"])
        result = await translate_document(
            src,
            cfg,
            disable_progress=True,
            progress_factory=lambda n: CaptureUI(n, disable=True),
        )

    assert result.multi_target is True
    assert result.failed == []
    assert result.total == 4  # 2 translatable keys × 2 langs
    assert result.translated == 4
    names = sorted(p.name for p in result.outputs)
    assert names == ["translated_ja_JP_messages.yml", "translated_zh_CN_messages.yml"]
    assert (tmp_path / "translated_messages.yml").exists() is False
    zh = (tmp_path / "translated_zh_CN_messages.yml").read_text(encoding="utf-8")
    ja = (tmp_path / "translated_ja_JP_messages.yml").read_text(encoding="utf-8")
    assert "你好，%player%！" in zh
    assert "{0}" in zh and "硬币" in zh
    assert "こんにちは、%player%！" in ja
    assert "{0}" in ja
    assert "DIAMOND_SWORD" in zh and "DIAMOND_SWORD" in ja
    # bar titles follow "<key> → <lang>"
    assert "welcome → zh_CN" in titles
    assert "welcome → ja_JP" in titles
    assert "coins → zh_CN" in titles
    assert all(" → " in t for t in titles)


@pytest.mark.asyncio
async def test_one_item_list_uses_list_mode_naming(tmp_path: Path):
    src = tmp_path / "messages.yml"
    src.write_text('hello: "A full greeting sentence"\n', encoding="utf-8")
    with MockOpenAI() as srv:
        cfg = _cfg(srv.base_url, target_language=["zh_CN"])
        result = await translate_document(src, cfg, disable_progress=True)
    assert result.multi_target is True
    assert result.output is not None
    assert result.output.name == "translated_zh_CN_messages.yml"
    assert (tmp_path / "translated_messages.yml").exists() is False
    assert "译:" in result.output.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fan_out_failure_includes_language(tmp_path: Path):
    src = tmp_path / "m.yml"
    src.write_text(
        'ok: "A full greeting sentence"\n'
        'bad: "This one will fail to translate"\n',
        encoding="utf-8",
    )

    def handler(body, headers):
        msgs = body.get("messages") or []
        system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
        text = _user_text(body)
        if "fail" in text or "This one" in text:
            if "ja_JP" in system:
                return 200, json.dumps({"status": "refused", "translation": "nope"})
            return 200, ok_payload("这一句失败过但中文成功")
        if "ja_JP" in system:
            return 200, ok_payload("こんにちは")
        return 200, ok_payload("你好")

    with MockOpenAI(handler) as srv:
        cfg = _cfg(srv.base_url, target_language=["zh_CN", "ja_JP"])
        result = await translate_document(src, cfg, disable_progress=True)

    assert result.translated == 3
    assert len(result.failed) == 1
    fail = result.failed[0]
    assert fail.target_language == "ja_JP"
    assert fail.path == "bad"
    logs = {p.name: p.read_text(encoding="utf-8") for p in result.failure_logs}
    assert "translated_ja_JP_m.failures.txt" in logs
    assert "translated_zh_CN_m.failures.txt" not in logs
    ja_log = logs["translated_ja_JP_m.failures.txt"]
    assert "bad → ja_JP" in ja_log
    assert "lang: ja_JP" in ja_log
    zh = (tmp_path / "translated_zh_CN_m.yml").read_text(encoding="utf-8")
    ja = (tmp_path / "translated_ja_JP_m.yml").read_text(encoding="utf-8")
    assert "这一句失败过但中文成功" in zh
    assert "This one will fail to translate" in ja  # kept original
    assert "こんにちは" in ja
    assert "你好" in zh


def test_build_messages_auto_vs_filtered():
    auto = parse_config(
        {
            "source_language": "auto",
            "target_language": "zh_CN",
            "models": [{"base_url": "http://x/v1", "api_key": "sk-abc", "model": "m"}],
        }
    )
    msgs = build_messages("Hello", auto, target_language="zh_CN")
    system = msgs[0]["content"]
    assert "Detect the source language" in system
    assert "zh_CN" in system
    assert '{"status":"ok","translation":"<translated text>"}' in system
    assert "{{" not in system
    # auto prompt tells the model not to use skip
    assert "do not use" in system.lower() or "Do not use status" in system

    specific = parse_config(
        {
            "source_language": "en",
            "target_language": ["zh_CN", "ja_JP"],
            "models": [{"base_url": "http://x/v1", "api_key": "sk-abc", "model": "m"}],
        }
    )
    msgs = build_messages("Hello", specific, target_language="ja_JP")
    system = msgs[0]["content"]
    assert "en" in system
    assert "ja_JP" in system
    assert "zh_CN" not in system  # this subtask is Japanese only
    assert '{"status":"skip","translation":""}' in system
    assert "{{" not in system


@pytest.mark.asyncio
async def test_fan_out_placeholder_mismatch_is_per_subtask(tmp_path: Path):
    src = tmp_path / "m.yml"
    src.write_text('coins: "You have {0} coins."\n', encoding="utf-8")

    def handler(body, headers):
        msgs = body.get("messages") or []
        system = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
        text = _user_text(body)
        if "ja_JP" in system:
            # drop the placeholder
            return 200, ok_payload("コインを持っています。")
        # zh keeps it
        if "__MCYT_PH_0__" in text:
            return 200, ok_payload("你有 __MCYT_PH_0__ 枚硬币。")
        return 200, ok_payload("你有 {0} 枚硬币。")

    with MockOpenAI(handler) as srv:
        cfg = _cfg(srv.base_url, target_language=["zh_CN", "ja_JP"])
        result = await translate_document(src, cfg, disable_progress=True)
    assert result.translated == 1
    assert len(result.failed) == 1
    assert result.failed[0].target_language == "ja_JP"
    zh = (tmp_path / "translated_zh_CN_m.yml").read_text(encoding="utf-8")
    ja = (tmp_path / "translated_ja_JP_m.yml").read_text(encoding="utf-8")
    assert "{0}" in zh and "硬币" in zh
    assert "You have {0} coins." in ja
