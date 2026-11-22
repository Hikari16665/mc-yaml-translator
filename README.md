# mc-yaml-translator

把 Minecraft 插件里的 YAML 语言/配置文件翻译成指定语言的小工具。只翻译自然语言文本，数字、材料名、权限、命令、颜色码等数据会跳过；占位符（`{0}`、`%player%`、`&a`、MiniMessage 标签等）会保留并在代码里校验。

源码用 Python 编写，通过 GitHub Actions 在 Windows 上打包成单个 `mc-yaml-translator.exe`。

## 使用方法

1. 从 [Releases](https://github.com/Hikari16665/mc-yaml-translator/releases) 下载 `mc-yaml-translator.exe` 和 `config.toml.example`，放到同一文件夹。
2. **第一次双击 exe**（不要拖文件）：程序会在 exe 同目录生成 `config.toml` 后立即退出。为了做到「第一次不弹窗、拖文件时又有进度控制台」，exe 以控制台程序打包；无参数且由资源管理器启动时，会立刻隐藏并释放控制台。
3. 用文本编辑器打开 `config.toml`，填入模型的 `base_url`、`api_key`、`model`，以及目标语言等选项。
4. 把要翻译的 `.yml` / `.yaml` 文件**拖到 exe 上**（一次可拖多个）。控制台会显示总进度和正在翻译的键。结束后按 Enter 关闭窗口（可在配置里关掉暂停）。
5. 译文写到原文件旁边，例如 `messages.yml` → `messages.translated.yml`，**不会覆盖原文件**。失败的键会保留原文，并在 `messages.translated.failures.txt` 里说明原因。

也可以在终端里运行：

```text
mc-yaml-translator.exe messages.yml lang/zh.yml
mc-yaml-translator.exe --config D:\path\config.toml --scan messages.yml
mc-yaml-translator.exe --no-pause messages.yml
```

如果已经在现有终端里启动，无参数时会打印简短说明并退出（不会隐藏你的终端窗口）；结束后也不会暂停。

## 配置说明

完整注释见 `config.toml.example`。要点：

- `[[models]]` 按书写顺序组成降级链。每个模型失败（含重试用尽）后才换下一个；**降级本身不算失败**。只有链上所有模型都不可用，该键才算失败。
- 每个模型必填 `base_url`、`api_key`、`model`。`base_url` 写成 OpenAI 兼容的根路径，例如 `https://api.openai.com/v1`（程序会请求 `{base_url}/chat/completions`）。
- 可选限流：`max_streams`（同时进行的流式请求数）、`qps`（每秒请求数）、`qpm`（60 秒滑动窗口内的请求数）。**TOML 没有 null**：省略该键、写 `0` 或写 `"null"` 都表示不限制该项。
- 限流**同时生效**。排队时的检查顺序是：先拿到 `max_streams` 并发名额，再满足 `qps`，再满足 `qpm`。
- 其它常用项：`target_language`、`source_language`、`retries`（每个模型的额外重试次数，总尝试次数 = 1 + retries）、`timeout_seconds`、`temperature`、`output_suffix`、`extra_prompt`（术语表或风格）、`skip_key_patterns`（键路径正则）、`pause_at_end`、`verify_ssl`。

**不要把填好密钥的 `config.toml` 提交到 Git 或发给别人。**

## 翻译行为（摘要）

- 递归处理嵌套的 map / list / 多行字符串。键顺序、嵌套结构、注释和引号风格会尽量用 `ruamel.yaml` 原样保留。
- 启发式跳过：数字、布尔、UUID、`minecraft:stone` 这类命名空间 id、`DIAMOND_SWORD` 大写材料名、路径、URL、`/command`、纯颜色码、只有占位符的字符串、`a.b.c` 权限、`entity.player.hurt` 音效，以及键名像 `material` / `sound` / `permission` / `command` / `id` 且值不像自然句的项。拿不准时默认不翻译。可用 `--scan` 预览将要翻译的键。
- 占位符先掩码成 `__MCYT_PH_0__` 再发给模型，回来后还原，并校验种类和数量（`{0}{1}` 这类带序号的必须原样保留）。带引号的复杂 MiniMessage 标签（如 hover 文本）不掩码，以便翻译引号里的句子，但会校验标签骨架。不一致则重试或降级。
- 要求模型输出 JSON：`{"status":"ok","translation":"..."}`。若接口支持会带 `response_format=json_object`，不支持则自动取消。解析失败或 `status=refused` 当作失败并走重试/降级，**拒绝说明绝不会写入 YAML**。
- 进度：一条总进度条（已完成 / 总键数）+ 若干条正在翻译的小条（键路径和流式输出的末尾几个字符）。小条完成即消失。同时显示的小条数量按终端高度限制；超出的键仍会翻译，只是不占用一行。Windows 控制台会启用 ANSI / colorama。

## 从源码运行

需要 Python 3.11+。

```bash
pip install -e ".[dev]"
mc-yaml-translator --scan samples/messages.yml
python -m pytest -q
```

## 打包

Windows 单文件 exe 由 `.github/workflows/build.yml` 在 `windows-latest` 上用 PyInstaller `--onefile --console` 构建。推送 `v*` 标签时会把 `mc-yaml-translator.exe` 和 `config.toml.example` 附加到 GitHub Release，同时保留 workflow artifact。

本仓库使用 MIT 许可证。
