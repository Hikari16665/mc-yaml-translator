"""Decide which YAML scalar values are natural-language text worth translating."""

from __future__ import annotations

import re
from collections.abc import Sequence

from mc_yaml_translator.placeholders import strip_placeholders

# Key (last path segment or any segment) that almost always stores data, not copy.
DATA_KEY_NAMES = frozenset(
    {
        "material",
        "materials",
        "sound",
        "sounds",
        "permission",
        "permissions",
        "perm",
        "permission-node",
        "permission_node",
        "command",
        "commands",
        "cmd",
        "cmds",
        "id",
        "ids",
        "uuid",
        "world",
        "worlds",
        "key",
        "keys",
        "namespace",
        "path",
        "file",
        "filename",
        "filepath",
        "icon",
        "texture",
        "skin",
        "slot",
        "slots",
        "amount",
        "count",
        "data",
        "model",
        "custommodeldata",
        "custom_model_data",
        "custom-model-data",
        "entity",
        "enchantment",
        "enchant",
        "enchants",
        "potion",
        "effect",
        "effects",
        "particle",
        "particles",
        "gamemode",
        "difficulty",
        "ingredient",
        "ingredients",
        "block",
        "blocks",
        "main",
        "version",
        "api-version",
        "api_version",
        "softdepend",
        "depend",
        "dependson",
        "loadbefore",
        "website",
        "host",
        "port",
        "username",
        "password",
        "jdbc",
        "database",
        "locale",
        "lang",
        "language",
        "location",
        "locations",
        "spawn-location",
        "spawn_location",
        "url",
        "uri",
        "token",
        "secret",
        "apikey",
        "api_key",
        "api-key",
        "permissionnode",
        "sound-name",
        "sound_name",
        "material-name",
        "material_name",
        "item-id",
        "item_id",
        "block-id",
        "block_id",
        "nbt",
        "tag",
        "tags",
        "group",
        "groups",
        "channel",
        "channels",
        "server",
        "servers",
        "ip",
        "address",
        "color",
        "colour",
        "hex",
        "rgb",
        "permission-message",  # sometimes a message! don't include? actually it's often a message
    }
    - {"permission-message"}
)

# Keys whose values are almost always player-facing copy.
DISPLAY_KEY_NAMES = frozenset(
    {
        "message",
        "messages",
        "msg",
        "text",
        "texts",
        "name",
        "displayname",
        "display-name",
        "display_name",
        "title",
        "titles",
        "subtitle",
        "subtitles",
        "lore",
        "description",
        "descriptions",
        "desc",
        "format",
        "formats",
        "prefix",
        "suffix",
        "header",
        "footer",
        "motd",
        "broadcast",
        "kick",
        "kick-message",
        "kick_message",
        "death",
        "info",
        "help",
        "usage",
        "tooltip",
        "hover",
        "button",
        "label",
        "caption",
        "announcement",
        "news",
        "menu",
        "line",
        "lines",
        "content",
        "contents",
        "body",
        "reason",
        "warn",
        "warning",
        "error",
        "success",
        "fail",
        "failure",
        "notify",
        "notification",
        "actionbar",
        "action-bar",
        "action_bar",
        "bossbar",
        "boss-bar",
        "displayname",
        "display",
        "prompt",
        "question",
        "answer",
        "hint",
        "placeholder",  # careful: sometimes a %placeholder% token; still often text
        "empty-message",
        "no-permission",
        "nopermission",
        "denied",
        "welcome",
        "goodbye",
        "join",
        "leave",
        "quit",
        "chat",
    }
)

BOOL_STRINGS = frozenset(
    {
        "true",
        "false",
        "yes",
        "no",
        "on",
        "off",
        "y",
        "n",
        "t",
        "f",
        "enabled",
        "disabled",
    }
)

NUMERIC_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?$")
NAMESPACED_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$", re.I)
UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")
ALL_CAPS_RE = re.compile(r"^[A-Z]{3,}$")
URL_RE = re.compile(r"^(?:https?|ftp|file|jdbc|mysql|redis|mongodb)://\S+$", re.I)
URI_RE = re.compile(r"^\S+://\S+$", re.I)
LOCALE_RE = re.compile(r"^[a-z]{2,3}[_-][A-Za-z]{2}(?:[_-]\w+)?$")
COORD_RE = re.compile(
    r"^\S+(?:\s+[-+]?\d+(?:\.\d+)?){2,}$"
)
WINDOWS_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")
UNIX_PATH_RE = re.compile(
    r"^(?:(?:\.{1,2})?/)[\w./\\-]+(?:\.\w+)?$|^(?:[\w.-]+/)+[\w.-]+$"
)
FILE_NAME_RE = re.compile(r"^[\w.\-]+\.(?:yml|yaml|json|txt|properties|conf|cfg|dat|schem|nbt|png|jpg)$", re.I)
COMMAND_RE = re.compile(r"^/[a-zA-Z0-9_:-]+(?:\s+\S+)*$")
PERMISSION_RE = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9_-]+){2,}$")
SOUND_RE = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
HEX_COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
AMP_COLOR_RE = re.compile(r"^(?:[&§][0-9a-fk-orxA-FK-ORX]|&#[0-9A-Fa-f]{6}|\s)+$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
WORD_RE = re.compile(r"[A-Za-z]+")


def _norm_key(part: str) -> str:
    return str(part).strip().lower().replace("-", "_")


def path_to_str(path: Sequence[object]) -> str:
    return ".".join(str(p) for p in path)


def last_key_name(path: Sequence[object]) -> str:
    for part in reversed(path):
        if isinstance(part, str):
            return _norm_key(part)
    return ""


def key_is_data(path: Sequence[object]) -> bool:
    name = last_key_name(path)
    if name in DATA_KEY_NAMES:
        return True
    # hyphen/underscore already normalized
    if name.endswith(("_id", "_uuid", "_perm", "_location", "_world", "_file", "_path")):
        return True
    return False


def key_is_display(path: Sequence[object]) -> bool:
    name = last_key_name(path)
    if name in DISPLAY_KEY_NAMES:
        return True
    # Common suffixes: join-message, no_permission_message, motd-header
    for suffix in ("_message", "_msg", "_text", "_name", "_title", "_lore", "_format", "_desc", "_description"):
        if name.endswith(suffix):
            return True
    return False


def is_placeholders_only(value: str) -> bool:
    leftover = strip_placeholders(value)
    leftover = re.sub(r"\s+", "", leftover)
    leftover = leftover.strip(".,;:!?-~•·—–_=/\\|[](){}\"'`")
    return leftover == ""


def looks_like_data_value(value: str) -> bool:
    s = value.strip()
    if not s:
        return True
    if NUMERIC_RE.fullmatch(s):
        return True
    if s.lower() in BOOL_STRINGS and " " not in s:
        return True
    if UUID_RE.fullmatch(s):
        return True
    if NAMESPACED_RE.fullmatch(s):
        return True
    if UPPER_SNAKE_RE.fullmatch(s):
        return True
    if ALL_CAPS_RE.fullmatch(s):
        return True
    if URL_RE.fullmatch(s) or URI_RE.fullmatch(s):
        return True
    if LOCALE_RE.fullmatch(s):
        return True
    if COORD_RE.fullmatch(s):
        return True
    if WINDOWS_PATH_RE.match(s):
        return True
    if UNIX_PATH_RE.fullmatch(s) and " " not in s:
        return True
    if FILE_NAME_RE.fullmatch(s):
        return True
    if COMMAND_RE.fullmatch(s) and not HAS_CJK_RE.search(s):
        # "/warp to safety" has spaces and English words — still a command if it
        # starts with / and has no sentence punctuation. Treat leading-slash
        # tokens as commands (plugin configs almost never store prose that way).
        return True
    if HEX_COLOR_RE.fullmatch(s):
        return True
    if AMP_COLOR_RE.fullmatch(s):
        return True
    if PERMISSION_RE.fullmatch(s):
        return True
    if SOUND_RE.fullmatch(s) and " " not in s:
        return True
    if is_placeholders_only(s):
        return True
    return False


def looks_like_natural_language(value: str) -> bool:
    text = strip_placeholders(value).strip()
    if not text:
        return False
    if HAS_CJK_RE.search(text):
        return True
    if " " in text and WORD_RE.search(text):
        return True
    if re.search(r"[.!?!,;:]", text) and WORD_RE.search(text):
        return True
    words = WORD_RE.findall(text)
    # Single UI label such as "Welcome" / "Cancel" / "Shop"
    if len(words) == 1 and len(words[0]) >= 3 and not IDENTIFIER_RE.fullmatch(text):
        # Mixed case or quoted leftover often means a label
        if words[0][0].isupper() or not text.islower():
            return True
    if len(words) >= 2:
        return True
    # Capitalized single word leftover after stripping codes: "Welcome"
    if len(words) == 1 and words[0][0].isupper() and len(words[0]) >= 2:
        return True
    return False


def is_single_identifier(value: str) -> bool:
    text = strip_placeholders(value).strip()
    if not text or " " in text:
        return False
    return bool(IDENTIFIER_RE.fullmatch(text))


def should_translate(
    value: object,
    path: Sequence[object] = (),
    skip_patterns: Sequence[re.Pattern[str]] | None = None,
) -> bool:
    """Return True if this scalar should be sent to the translation model."""
    if not isinstance(value, str):
        return False
    # ruamel scalar subclasses are still str
    if skip_patterns:
        dotted = path_to_str(path)
        for pat in skip_patterns:
            if pat.search(dotted):
                return False

    s = value
    if not s.strip():
        return False

    if looks_like_data_value(s):
        return False

    if key_is_data(path) and not looks_like_natural_language(s):
        return False
    if key_is_data(path) and is_single_identifier(s):
        return False
    # Strong data keys: even a short phrase might be a namespaced-looking value
    # we already missed; still translate obvious sentences under those keys
    # (e.g. a misnamed 'material' that actually holds lore). Sentences pass.

    if key_is_display(path):
        leftover = strip_placeholders(s).strip()
        if leftover and (looks_like_natural_language(s) or WORD_RE.search(leftover) or HAS_CJK_RE.search(leftover)):
            # display key + leftover letters: translate, including single words like "Shop"
            if is_single_identifier(s) and leftover.lower() in BOOL_STRINGS:
                return False
            return True
        if leftover and len(leftover) >= 2 and not looks_like_data_value(leftover):
            return True

    if looks_like_natural_language(s):
        return True

    # Default: skip (safer not to translate identifiers / materials / keys)
    return False
