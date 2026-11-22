from __future__ import annotations

from mc_yaml_translator.placeholders import (
    extract_placeholders,
    mask_placeholders,
    unmask_placeholders,
    validate_placeholders,
)


def test_extracts_common_placeholders():
    text = "&aHello {0} %player% %s %1$s {name} &#FF00AA §l <red>x</red> \\n $var ${foo} {{bar}}"
    got = extract_placeholders(text)
    assert "&a" in got
    assert "{0}" in got
    assert "%player%" in got
    assert "%s" in got
    assert "%1$s" in got
    assert "{name}" in got
    assert "&#FF00AA" in got
    assert "§l" in got
    assert "<red>" in got
    assert "</red>" in got
    assert "\\n" in got
    assert "$var" in got
    assert "${foo}" in got
    assert "{{bar}}" in got


def test_hex_amp_not_split():
    toks = extract_placeholders("color &#00FF00 done")
    assert toks == ["&#00FF00"]


def test_mask_roundtrip_and_validation():
    original = "&aWelcome, %player%! You have {0} coins. <red>Go</red>"
    masked = mask_placeholders(original)
    assert "%player%" not in masked.text
    assert "&a" not in masked.text
    assert "__MCYT_PH_0__" in masked.text
    # reconstruction with identity "translation"
    back = unmask_placeholders(masked.text, masked)
    assert back == original
    assert validate_placeholders(original, back) is None


def test_mismatch_dropped_and_extra():
    original = "Hello {0} {1} %player%"
    bad = "你好 {0} %player%"
    err = validate_placeholders(original, bad)
    assert err is not None
    assert "{1}" in err.missing
    ok = "你好 {1} {0} %player%"  # order may change
    assert validate_placeholders(original, ok) is None


def test_complex_minimessage_inner_text_may_change():
    original = "<hover:show_text:'Hello there'>Click</hover>"
    translated = "<hover:show_text:'你好呀'>点击</hover>"
    # hover tag is complex (quoted) so it is NOT masked; skeleton must match
    masked = mask_placeholders(original)
    assert "<hover:show_text:" in masked.text
    assert validate_placeholders(original, translated) is None
    broken = "<hover:show_item:'x'>点击"  # lost closing and changed tag type
    err = validate_placeholders(original, broken)
    assert err is not None


def test_color_and_index_must_be_exact():
    original = "&cError #{0}"
    # # may start a hex if followed by 6 hex digits; here {0} so '#' is literal...
    # "#{0}" — '#' is not a 6-digit hex, {0} is a placeholder.
    assert extract_placeholders(original) == ["&c", "{0}"]
    assert validate_placeholders(original, "&c错误 #{0}") is None
    assert validate_placeholders(original, "错误 #{0}") is not None
    assert validate_placeholders(original, "&c错误 #{1}") is not None
