"""Load and validate config.toml."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from mc_yaml_translator.console_win import app_dir

DEFAULT_CONFIG_TOML = r'''# mc-yaml-translator 示例配置
# 第一次双击 exe 时，会在 exe 同目录生成一份同名的 config.toml。
# 编辑好后再把 .yml / .yaml 文件拖到 exe 上开始翻译。
#
# 不要把填好 api_key 的 config.toml 提交到 Git 或发给别人。

target_language = "zh_CN"
source_language = "en"

# 输出文件名：messages.yml → messages.translated.yml（不会覆盖原文件）
output_suffix = ".translated"

# 每个模型的额外重试次数（总尝试次数 = 1 + retries）
retries = 2
timeout_seconds = 60
temperature = 0.2

# 拖到 exe 上运行结束时暂停，避免控制台一闪而过。从已有终端运行时不会暂停。
pause_at_end = true

# 附加到系统提示词的说明，例如术语表或风格要求。可留空。
extra_prompt = ""

# 跳过键路径的正则（匹配完整点分路径，如 messages.gui.items.0.material）
# 默认已内置 material/sound/permission 等启发式，这里只需补充项目特殊规则。
skip_key_patterns = []

# SSL 校验。仅在公司代理使用自签证书时才改为 false。
verify_ssl = true

# ---------------------------------------------------------------------------
# 模型降级链：按书写顺序尝试。某一个模型失败会降级到下一个；
# 降级本身不算失败。只有链上所有模型都失败，该键才算翻译失败
#（保留原文，并写入 .failures.txt）。
#
# 每个模型必填：base_url、api_key、model
# 可选限流（均可省略）：max_streams、qps、qpm
#
# TOML 没有 null。省略该键、写 0、或写字符串 "null" 都表示「不限制该项」。
#
# 限流同时生效（同时配置了就会同时执行），排队时的检查顺序是：
#   1. max_streams  —— 该模型同时进行的流式请求上限（并发）
#   2. qps          —— 每秒最多发起多少个请求
#   3. qpm          —— 每 60 秒滑动窗口内最多发起多少个请求
# ---------------------------------------------------------------------------

[[models]]
base_url = "https://api.openai.com/v1"
api_key = "sk-your-api-key-here"
model = "gpt-4o-mini"
qps = 2
qpm = 60
max_streams = 4

# 需要 failover 时取消注释并填写第二个模型：
# [[models]]
# base_url = "https://api.openai.com/v1"
# api_key = "sk-your-fallback-key-here"
# model = "gpt-4o-mini"
# qps = 1
# qpm = 30
# max_streams = 2
'''


class ConfigError(ValueError):
    """Invalid or missing configuration."""


def _opt_limit(value: Any) -> float | None:
    """Omit / 0 / "null" / "" → no limit; otherwise a positive number."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"", "null", "none", "nil"}:
            return None
        try:
            value = float(s)
        except ValueError as exc:
            raise ConfigError(f"限流参数无法解析: {value!r}") from exc
    if isinstance(value, bool):
        raise ConfigError("限流参数不能是布尔值")
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return float(value)
    raise ConfigError(f"限流参数类型无效: {value!r}")


def _opt_int_limit(value: Any) -> int | None:
    n = _opt_limit(value)
    if n is None:
        return None
    return max(1, int(n))


@dataclass
class ModelConfig:
    base_url: str
    api_key: str
    model: str
    qps: float | None = None
    qpm: float | None = None
    max_streams: int | None = None

    def completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


@dataclass
class AppConfig:
    models: list[ModelConfig] = field(default_factory=list)
    target_language: str = "zh_CN"
    source_language: str = "en"
    output_suffix: str = ".translated"
    retries: int = 2
    timeout_seconds: float = 60.0
    temperature: float = 0.2
    pause_at_end: bool = True
    extra_prompt: str = ""
    skip_key_patterns: list[re.Pattern[str]] = field(default_factory=list)
    verify_ssl: bool = True

    @property
    def attempts_per_model(self) -> int:
        return 1 + max(0, self.retries)


def default_config_path() -> Path:
    return Path(app_dir()) / "config.toml"


def write_example_config(path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path


def load_config(path: Path | None = None) -> AppConfig:
    path = path or default_config_path()
    if not path.is_file():
        raise ConfigError(f"找不到配置文件: {path}\n请先双击运行一次生成示例配置，或使用 --config 指定路径。")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件 TOML 语法错误: {exc}") from exc
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> AppConfig:
    models_raw = data.get("models")
    if not models_raw or not isinstance(models_raw, list):
        raise ConfigError("配置中至少需要一个 [[models]] 条目。")

    models: list[ModelConfig] = []
    for i, item in enumerate(models_raw, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"models[{i}] 必须是表。")
        try:
            base_url = str(item["base_url"]).strip()
            api_key = str(item["api_key"]).strip()
            model = str(item["model"]).strip()
        except KeyError as exc:
            raise ConfigError(
                f"models[{i}] 缺少必填字段 {exc.args[0]}（需要 base_url、api_key、model）"
            ) from exc
        if not base_url or not api_key or not model:
            raise ConfigError(f"models[{i}] 的 base_url、api_key、model 不能为空。")
        if api_key in {"sk-your-api-key-here", "sk-your-fallback-key-here"}:
            raise ConfigError(
                f"models[{i}] 仍是示例 api_key，请先在 config.toml 中填入真实密钥。"
            )
        models.append(
            ModelConfig(
                base_url=base_url,
                api_key=api_key,
                model=model,
                qps=_opt_limit(item.get("qps")),
                qpm=_opt_limit(item.get("qpm")),
                max_streams=_opt_int_limit(item.get("max_streams")),
            )
        )

    patterns: list[re.Pattern[str]] = []
    raw_pats = data.get("skip_key_patterns") or []
    if not isinstance(raw_pats, list):
        raise ConfigError("skip_key_patterns 必须是字符串数组。")
    for p in raw_pats:
        try:
            patterns.append(re.compile(str(p)))
        except re.error as exc:
            raise ConfigError(f"skip_key_patterns 正则无效 {p!r}: {exc}") from exc

    suffix = str(data.get("output_suffix", ".translated") or ".translated")
    if not suffix.startswith("."):
        suffix = "." + suffix

    retries = int(data.get("retries", 2))
    timeout = float(data.get("timeout_seconds", 60))
    temperature = float(data.get("temperature", 0.2))
    if retries < 0:
        raise ConfigError("retries 不能为负数。")
    if timeout <= 0:
        raise ConfigError("timeout_seconds 必须为正数。")

    return AppConfig(
        models=models,
        target_language=str(data.get("target_language", "zh_CN") or "zh_CN"),
        source_language=str(data.get("source_language", "en") or "en"),
        output_suffix=suffix,
        retries=retries,
        timeout_seconds=timeout,
        temperature=temperature,
        pause_at_end=bool(data.get("pause_at_end", True)),
        extra_prompt=str(data.get("extra_prompt", "") or ""),
        skip_key_patterns=patterns,
        verify_ssl=bool(data.get("verify_ssl", True)),
    )
