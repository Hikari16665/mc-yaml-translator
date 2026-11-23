"""Load and validate config.toml with pydantic, from a bundled example file."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from mc_yaml_translator.console_win import app_dir
from mc_yaml_translator.errors import ConfigError, ErrorCategory

logger = logging.getLogger("mc_yaml_translator")

_EXAMPLE_NAME = "config.toml.example"
_PLACEHOLDER_KEYS = frozenset({"sk-your-api-key-here", "sk-your-fallback-key-here"})

_LANG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_AUTO = "auto"


def lang_identity(code: str) -> str:
    """Case-insensitive key; treat hyphen and underscore as the same."""
    return code.strip().lower().replace("-", "_")


def validate_lang_code(value: Any, *, field: str, allow_auto: bool = False) -> str:
    """Return a cleaned language code, or raise ValueError."""
    if value is None:
        raise ValueError(f"{field} 不能为空。")
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串。")
    code = value.strip()
    if not code:
        raise ValueError(f"{field} 不能为空。")
    if allow_auto and lang_identity(code) == _AUTO:
        return _AUTO
    if not _LANG_RE.fullmatch(code):
        raise ValueError(
            f"{field} 不是有效的语言代码: {value!r}。"
            "请使用如 en、zh_CN、ja-JP 这样的代码，或源语言写 auto。"
        )
    if lang_identity(code) == _AUTO and not allow_auto:
        raise ValueError(f"{field} 不能是 auto（auto 只用于 source_language）。")
    return code



def bundled_example_config() -> str:
    """Read the example config packaged with the app (importlib.resources / _MEIPASS)."""
    try:
        from importlib.resources import files

        text = files("mc_yaml_translator").joinpath(_EXAMPLE_NAME).read_text(encoding="utf-8")
        if text.strip():
            return text
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError, AttributeError):
        pass

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for rel in (
            Path(meipass) / "mc_yaml_translator" / _EXAMPLE_NAME,
            Path(meipass) / _EXAMPLE_NAME,
        ):
            if rel.is_file():
                return rel.read_text(encoding="utf-8")

    nearby = Path(__file__).resolve().parent / _EXAMPLE_NAME
    if nearby.is_file():
        return nearby.read_text(encoding="utf-8")

    raise ConfigError("找不到内置的示例配置 config.toml.example（程序可能未正确打包）。")


# Convenience alias used by tests; always loaded from the bundled file, never a second copy.
DEFAULT_CONFIG_TOML = bundled_example_config()


def _coerce_limit(value: Any, field_name: str) -> float | None:
    """0 / omitted → unlimited (None). Unparseable or incompatible types → None + warning."""
    if value is None:
        return None
    if isinstance(value, bool):
        logger.warning("配置项 %s 类型不兼容（布尔值 %r），按 0（不限制）处理", field_name, value)
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        try:
            n = float(s)
        except ValueError:
            logger.warning("配置项 %s=%r 无法解析，按 0（不限制）处理", field_name, value)
            return None
        if n <= 0:
            return None
        return n
    logger.warning("配置项 %s 类型不兼容（%r），按 0（不限制）处理", field_name, value)
    return None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str
    api_key: str
    model: str
    qps: float | None = None
    qpm: float | None = None
    max_streams: int | None = None

    @field_validator("base_url", "api_key", "model", mode="before")
    @classmethod
    def _required_nonempty(cls, value: Any, info) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"{info.field_name} 为空，请在 config.toml 中填写有效值。")
        return str(value).strip()

    @field_validator("api_key")
    @classmethod
    def _not_placeholder(cls, value: str) -> str:
        if value in _PLACEHOLDER_KEYS:
            raise ValueError("仍是示例 api_key，请先在 config.toml 中填入真实密钥。")
        return value

    @field_validator("qps", "qpm", mode="before")
    @classmethod
    def _limit_float(cls, value: Any, info) -> float | None:
        return _coerce_limit(value, info.field_name)

    @field_validator("max_streams", mode="before")
    @classmethod
    def _limit_int(cls, value: Any, info) -> int | None:
        n = _coerce_limit(value, info.field_name)
        if n is None:
            return None
        return max(1, int(n))

    def completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    models: list[ModelConfig] = Field(..., min_length=1)
    target_language: str | list[str] = "zh_CN"
    source_language: str = "auto"
    output_prefix: str = "translated_"
    retries: int = 2
    timeout_seconds: float = 60.0
    temperature: float = 0.2
    extra_prompt: str = ""
    skip_key_patterns: list[re.Pattern[str]] = Field(default_factory=list)
    verify_ssl: bool = True

    @field_validator("models")
    @classmethod
    def _models_nonempty(cls, value: list[ModelConfig]) -> list[ModelConfig]:
        if not value:
            raise ValueError("配置中至少需要一个 [[models]] 条目。")
        return value

    @field_validator("output_prefix")
    @classmethod
    def _prefix_ok(cls, value: str) -> str:
        value = str(value or "").strip() or "translated_"
        if not value:
            raise ValueError("output_prefix 不能为空（默认 translated_，以免覆盖原文件）。")
        return value

    @field_validator("source_language", mode="before")
    @classmethod
    def _source_lang(cls, value: Any) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "auto"
        return validate_lang_code(value, field="source_language", allow_auto=True)

    @field_validator("target_language", mode="before")
    @classmethod
    def _target_langs(cls, value: Any) -> str | list[str]:
        if value is None or value == "":
            return "zh_CN"
        if isinstance(value, str):
            return validate_lang_code(value, field="target_language")
        if isinstance(value, (list, tuple)):
            out: list[str] = []
            seen: set[str] = set()
            for item in value:
                if item is None or (isinstance(item, str) and not item.strip()):
                    logger.warning("target_language 列表里有空项，已忽略")
                    continue
                if not isinstance(item, str):
                    raise ValueError("target_language 列表的每一项都必须是字符串。")
                code = validate_lang_code(item, field="target_language")
                key = lang_identity(code)
                if key in seen:
                    logger.warning("重复的目标语言 %s，已忽略", item)
                    continue
                seen.add(key)
                out.append(code)
            if not out:
                raise ValueError("target_language 列表不能为空。")
            return out
        raise ValueError("target_language 必须是字符串，或由语言代码组成的列表。")

    @field_validator("retries")
    @classmethod
    def _retries_ok(cls, value: int) -> int:
        if int(value) < 0:
            raise ValueError("retries 不能为负数。")
        return int(value)

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_ok(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("timeout_seconds 必须为正数。")
        return float(value)

    @field_validator("skip_key_patterns", mode="before")
    @classmethod
    def _compile_patterns(cls, value: Any) -> list[re.Pattern[str]]:
        if value is None or value == "":
            return []
        if not isinstance(value, list):
            raise ValueError("skip_key_patterns 必须是字符串数组。")
        compiled: list[re.Pattern[str]] = []
        for p in value:
            try:
                compiled.append(re.compile(str(p)))
            except re.error as exc:
                raise ValueError(f"skip_key_patterns 正则无效 {p!r}: {exc}") from exc
        return compiled

    @property
    def attempts_per_model(self) -> int:
        return 1 + max(0, self.retries)

    @property
    def source_is_auto(self) -> bool:
        return lang_identity(self.source_language) == _AUTO

    @property
    def is_multi_target(self) -> bool:
        """True when target_language was given as a list (including a one-item list)."""
        return isinstance(self.target_language, list)

    @property
    def target_languages(self) -> list[str]:
        if isinstance(self.target_language, list):
            return list(self.target_language)
        return [self.target_language]


def default_config_path() -> Path:
    return Path(app_dir()) / "config.toml"


def write_example_config(path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.write_text(bundled_example_config(), encoding="utf-8")
    return path


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc_parts: list[str] = []
        idxs = list(err["loc"])
        i = 0
        while i < len(idxs):
            x = idxs[i]
            if x == "models" and i + 1 < len(idxs) and isinstance(idxs[i + 1], int):
                loc_parts.append(f"models[{idxs[i + 1] + 1}]")
                i += 2
                continue
            loc_parts.append(str(x))
            i += 1
        loc = ".".join(loc_parts) if loc_parts else "config"
        typ = err.get("type", "")
        msg = err["msg"]
        # pydantic prefixes some messages with "Value error, "
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        if typ == "missing":
            msg = "缺少必填字段（需要 base_url、api_key、model）"
        if typ == "too_short" and "models" in loc:
            msg = "配置中至少需要一个 [[models]] 条目。"
        parts.append(f"{loc}: {msg}")
    return f"{ErrorCategory.CLIENT_CONFIG.value}: 配置错误: " + "; ".join(parts)


def parse_config(data: dict[str, Any]) -> AppConfig:
    models_raw = data.get("models")
    if not models_raw or not isinstance(models_raw, list):
        raise ConfigError(
            f"{ErrorCategory.CLIENT_CONFIG.value}: 配置错误: 配置中至少需要一个 [[models]] 条目。"
        )
    for i, item in enumerate(models_raw, start=1):
        if not isinstance(item, dict):
            raise ConfigError(
                f"{ErrorCategory.CLIENT_CONFIG.value}: 配置错误: models[{i}] 必须是表。"
            )
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_config_path()
    if not path.is_file():
        raise ConfigError(
            f"{ErrorCategory.CLIENT_CONFIG.value}: 找不到配置文件: {path}\n"
            "请先双击运行一次生成示例配置，或使用 --config 指定路径。"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"{ErrorCategory.CLIENT_CONFIG.value}: 无法读取配置文件 {path}: {exc}"
        ) from exc
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{ErrorCategory.CLIENT_CONFIG.value}: 配置文件格式错误 ({path}): {exc}"
        ) from exc
    return parse_config(data)
