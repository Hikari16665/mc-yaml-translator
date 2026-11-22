"""Detect, mask, and validate Minecraft / plugin placeholders and format codes."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# Longest-first alternation so &#RRGGBB wins over #RRGGBB, %1$s over %s,
# {{x}} over {x}, </tag> / <tag:...> over stray '<' comparisons.
PLACEHOLDER_RE = re.compile(
    r"(&#[0-9A-Fa-f]{6})"
    r"|(§[0-9a-fk-orxA-FK-ORX])"
    r"|(&[0-9a-fk-orxA-FK-ORX])"
    r"|(#[0-9A-Fa-f]{6})"
    r"|(\{\{[^{}]+\}\})"
    r"|(\{\d+\})"
    r"|(\{[a-zA-Z_][a-zA-Z0-9_-]*\})"
    r"|(%\d+\$[sdifoxXeEgGc])"
    r"|(%[sdifoxXeEgGc])"
    r"|(%[a-zA-Z_][a-zA-Z0-9_-]*%)"
    r"|(\$\{[a-zA-Z_][a-zA-Z0-9_]*\})"
    r"|(\$[a-zA-Z_][a-zA-Z0-9_]*)"
    r"|(\\n|\\t|\\r)"
    r"|(</[a-zA-Z_][a-zA-Z0-9_-]*>)"
    r"|(<[a-zA-Z_][a-zA-Z0-9_-]*(?::[^<>]*)?>)"
)

TOKEN_FMT = "__MCYT_PH_{n}__"
TOKEN_RE = re.compile(r"__MCYT_PH_(\d+)__")

# MiniMessage / XML tags whose quoted inner text may be translated.
_QUOTED_IN_TAG_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _is_tag(token: str) -> bool:
    return token.startswith("<") and token.endswith(">")


def _is_complex_tag(token: str) -> bool:
    return _is_tag(token) and bool(_QUOTED_IN_TAG_RE.search(token))


def tag_skeleton(token: str) -> str:
    """Normalize a MiniMessage tag so inner quoted copy may change."""
    if not _is_tag(token):
        return token
    return _QUOTED_IN_TAG_RE.sub(lambda m: m.group(0)[0] + "…" + m.group(0)[-1], token)


def iter_placeholders(text: str) -> list[tuple[int, int, str]]:
    """Non-overlapping placeholders as (start, end, exact_token), left to right."""
    return [(m.start(), m.end(), m.group(0)) for m in PLACEHOLDER_RE.finditer(text)]


def extract_placeholders(text: str) -> list[str]:
    return [tok for _, _, tok in iter_placeholders(text)]


def strip_placeholders(text: str) -> str:
    return PLACEHOLDER_RE.sub(" ", text)


def validation_multiset(text: str) -> Counter[str]:
    """Placeholders that must be preserved, with complex tags skeletoned."""
    items: list[str] = []
    for tok in extract_placeholders(text):
        if _is_complex_tag(tok):
            items.append(tag_skeleton(tok))
        else:
            items.append(tok)
    return Counter(items)


@dataclass
class MaskResult:
    text: str
    tokens: list[str]

    def unmask(self, translated: str) -> str:
        out = translated
        for i, tok in enumerate(self.tokens):
            out = out.replace(TOKEN_FMT.format(n=i), tok)
        return out

    def leftover_tokens(self, translated: str) -> list[str]:
        return TOKEN_RE.findall(self.unmask(translated) if False else translated)


def mask_placeholders(text: str) -> MaskResult:
    """Replace placeholders with stable ASCII tokens before sending to the model.

    Complex MiniMessage tags that contain quoted inner language (hover/click)
    are left in place so the model can translate the inner copy; their
    structure is still checked by validate_placeholders().
    """
    tokens: list[str] = []
    parts: list[str] = []
    last = 0
    for start, end, tok in iter_placeholders(text):
        parts.append(text[last:start])
        if _is_complex_tag(tok):
            parts.append(tok)
        else:
            parts.append(TOKEN_FMT.format(n=len(tokens)))
            tokens.append(tok)
        last = end
    parts.append(text[last:])
    return MaskResult(text="".join(parts), tokens=tokens)


def unmask_placeholders(translated: str, masked: MaskResult) -> str:
    return masked.unmask(translated)


@dataclass
class PlaceholderMismatch:
    missing: Counter[str]
    extra: Counter[str]
    leftover_masks: list[str]

    def message(self) -> str:
        bits: list[str] = []
        if self.missing:
            bits.append("缺失 " + ", ".join(f"{k}×{v}" for k, v in sorted(self.missing.items())))
        if self.extra:
            bits.append("多余 " + ", ".join(f"{k}×{v}" for k, v in sorted(self.extra.items())))
        if self.leftover_masks:
            bits.append("残留掩码 " + ", ".join(self.leftover_masks))
        return "占位符不一致: " + "; ".join(bits) if bits else "占位符不一致"


def validate_placeholders(
    original: str, translated: str, masked: MaskResult | None = None
) -> PlaceholderMismatch | None:
    """Return a mismatch description, or None if the placeholder multiset matches.

    Exact tokens ({0}, %player%, &a, simple <red> tags, …) must appear with
    the same counts. Complex tags are compared by skeleton (tag name / type,
    quoted inner text allowed to change). Ordered-index placeholders such as
    {0}{1} or %1$s are included as exact tokens, so dropping or changing an
    index is a mismatch; their *position* in the sentence may change with
    target-language word order.
    """
    left = validation_multiset(original)
    right = validation_multiset(translated)
    missing = left - right
    extra = right - left
    leftover: list[str] = []
    if masked is not None:
        leftover = [TOKEN_FMT.format(n=n) for n in TOKEN_RE.findall(translated)]
        # After a proper unmask there should be none; if unmask was already
        # applied, leftover means the model corrupted a token.
        # Also catch leftover in the pre-unmask string when caller passes it.
    if missing or extra or leftover:
        return PlaceholderMismatch(missing=missing, extra=extra, leftover_masks=leftover)
    return None
