from __future__ import annotations

import pytest

from mc_yaml_translator.client import (
    ParseError,
    RefusalError,
    parse_structured,
    preview_translation,
)


def test_parse_ok_plain_and_fenced():
    got = parse_structured('{"status":"ok","translation":"你好"}')
    assert got.translation == "你好" and got.skipped is False
    fenced = '```json\n{"status":"ok","translation":"你好"}\n```'
    assert parse_structured(fenced).translation == "你好"
    # missing status but translation present is accepted
    assert parse_structured('{"translation":"只给了译文"}').translation == "只给了译文"


def test_refusal_never_returned():
    with pytest.raises(RefusalError):
        parse_structured('{"status":"refused","translation":"I cannot translate this"}')
    with pytest.raises(RefusalError):
        parse_structured('{"status":"error","translation":"no"}')


def test_malformed_and_unknown_status():
    with pytest.raises(ParseError):
        parse_structured("sorry I cannot help with that")
    with pytest.raises(ParseError):
        parse_structured('{"status":"maybe","translation":"x"}')
    with pytest.raises(ParseError):
        parse_structured('{"status":"ok"}')


def test_preview_partial_stream():
    partial = '{"status":"ok","translation":"你好，%play'
    assert preview_translation(partial) == "你好，%play"
    escaped = '{"status":"ok","translation":"a\\nb\\t'
    assert preview_translation(escaped) == "a\nb\t"
    assert preview_translation('{"status":"ok"') == ""


def test_parse_source_language_skip():
    for raw in (
        '{"status":"skip","translation":""}',
        '{"status":"skipped","translation":""}',
        '{"status":"unchanged","translation":""}',
        '{"status":"ok","translation":"ignore me","skip":true}',
        '{"status":"ok","unchanged":true}',
    ):
        got = parse_structured(raw)
        assert got.skipped is True
