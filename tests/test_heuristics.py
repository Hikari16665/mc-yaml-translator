from __future__ import annotations

import pytest

from mc_yaml_translator.heuristics import should_translate


@pytest.mark.parametrize(
    "value,path,expected",
    [
        # data types
        (54, ("gui", "size"), False),
        (True, ("enabled",), False),
        (None, ("x",), False),
        (3.14, ("ratio",), False),
        ("", ("message",), False),
        ("   ", ("message",), False),
        # numbers / bools as strings
        ("123", ("amount",), False),
        ("-2.5", ("offset",), False),
        ("true", ("enabled",), False),
        ("FALSE", ("enabled",), False),
        ("yes", ("flag",), False),
        ("on", ("toggle",), False),
        # namespaced keys / materials
        ("minecraft:stone", ("item",), False),
        ("minecraft:entity.player.hurt", ("sound",), False),
        ("spigot:example", ("key",), False),
        ("DIAMOND_SWORD", ("material",), False),
        ("NETHERITE_CHESTPLATE", ("reward", "item"), False),
        ("AIR", ("material",), False),
        # paths / urls / files
        ("https://example.com/a", ("website",), False),
        ("http://localhost:8080", ("url",), False),
        (r"C:\plugins\Foo\bar.yml", ("path",), False),
        ("plugins/Foo/config.yml", ("file",), False),
        ("config.yml", ("file",), False),
        # commands
        ("/warp spawn", ("command",), False),
        ("/give %player% stone 1", ("commands", 0), False),
        ("/spawn", ("cmd",), False),
        # colors, placeholders only
        ("#FF00AA", ("color",), False),
        ("&a", ("color",), False),
        ("&l&a&r", ("format",), False),
        ("§c", ("c",), False),
        ("{0}", ("message",), False),
        ("%player%", ("format",), False),
        ("{0} %s &a", ("text",), False),
        # permissions / sounds
        ("myplugin.admin.use", ("permission",), False),
        ("essentials.fly", ("perm",), False),
        ("entity.player.hurt", ("sound",), False),
        ("block.note_block.harp", ("sounds", 0), False),
        # uuids
        ("123e4567-e89b-12d3-a456-426614174000", ("uuid",), False),
        # key-name hints skip identifiers
        ("stone", ("material",), False),
        ("diamond_sword", ("item_id",), False),
        # natural language — should translate
        ("&aWelcome, %player%!", ("messages", "welcome"), True),
        ("Hello, world!", ("messages", "hello"), True),
        ("You do not have permission.", ("no-permission",), True),
        ("A legendary sword\nof the ancients", ("lore",), True),
        ("Shop", ("title",), True),
        ("Cancel", ("button",), True),
        ("Welcome", ("join-message",), True),
        ("点击这里打开菜单", ("gui", "hint"), True),
        ("<red>Danger!</red> Stay back, {0}.", ("alert",), True),
        ("Player {0} joined the game.", ("join",), True),
        # display key + short label
        ("Shop", ("menu", "name"), True),
        ("OK", ("button", "label"), True),  # 2 letters, capitalized; display key
        # default skip: lone lowercase identifier under a generic key
        ("stone", ("default",), False),
        ("lobby", ("spawn-world",), False),
        # locale / coordinates / jdbc
        ("en_US", ("language",), False),
        ("zh_CN", ("locale",), False),
        ("world 0 64 0", ("spawn-location",), False),
        ("jdbc:mysql://localhost:3306/mc", ("storage", "url"), False),
    ],
)
def test_should_translate(value, path, expected):
    assert should_translate(value, path) is expected


def test_skip_key_patterns():
    import re

    pats = [re.compile(r"(^|\.)debug(\.|$)")]
    text = "A debug sentence here."
    assert should_translate(text, ("messages", "welcome")) is True
    assert should_translate(text, ("debug", "note")) is True  # no skip pattern → still a sentence
    assert should_translate(text, ("debug", "note"), pats) is False
    assert should_translate("Hello there friends.", ("messages", "x"), pats) is True


def test_display_key_with_placeholder_and_word():
    assert should_translate("&6Welcome %player%", ("prefix",)) is True


def test_command_description_not_command_key():
    # under description, a sentence starting without slash
    assert should_translate("Warps you to spawn.", ("commands", "spawn", "description")) is True


def test_multiline_lore():
    text = "Line one of the tale\nLine two of the tale"
    assert should_translate(text, ("items", "sword", "lore")) is True
