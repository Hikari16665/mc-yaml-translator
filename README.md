# mc-yaml-translator

Minecraft 插件的语言文件、配置里经常全是英文，而且还跟材料名、权限节点、颜色码挤在同一个 YAML 里。这个小工具就是把那些给人看的句子翻成你要的语言；数字、ID、命令那些数据它尽量不动，`{0}`、`%player%`、`&a` 这类占位符也会留下来。

Windows 下是一个单独的 exe。源码是 Python，用 GitHub Actions 打包。

## 怎么用

1. 从 [Releases](https://github.com/Hikari16665/mc-yaml-translator/releases) 把 `mc-yaml-translator.exe` 下下来，放进一个文件夹。
2. 先双击一次 exe，先别拖文件。它会把内置的示例配置写到同目录的 `config.toml`，然后马上退出，窗口基本不会留着。
3. 打开 `config.toml`，把模型的 `base_url`、`api_key`、`model` 填上。目标语言也在这个文件里改（默认 `zh_CN`）。限流、重试这些选项全都写在配置里，没有单独的命令行去改语言。
4. 把要翻的 `.yml` / `.yaml` 拖到 exe 上就行，一次可以拖好几个。

译文写在原文件旁边，不会覆盖原来的。比如 `messages.yml` 会得到 `translated_messages.yml`。

全部键都翻成功的话，窗口会自己关掉。只要有一个键失败，控制台会停在那里，把失败的键路径和原因列出来，按一下键再关。配置写错了，或者 YAML 本身都解析不了，也是先报错、不开始翻译，窗口留着给你看。

已经在终端里跑的话，成功了不会暂停。`--no-pause` 是给脚本用的。

```text
mc-yaml-translator.exe messages.yml
mc-yaml-translator.exe --config D:\path\config.toml --scan messages.yml
```

`--scan` 只列出它打算翻译哪些键，不会真的去调模型。拿不准的时候可以先看一眼。

## 配置

模型按 `[[models]]` 从上往下排，组成一条降级链。某个模型不行（重试也用尽了）就换下一个，换模型这件事本身不算失败。整条链都走不通，这个键才算失败：原文保留，另外写出一份 `translated_messages.failures.txt`。

每个模型必填 `base_url`、`api_key`、`model`。`base_url` 写成 OpenAI 兼容的根路径，例如 `https://api.openai.com/v1`。缺字段、留空、或者还是示例里的密钥，开始翻译前就会报 `CLIENT-CONFIG`，不会去请求。

可选的限流是 `max_streams`（同时多少路流式请求）、`qps`（每秒几个请求）、`qpm`（60 秒里最多几个）。写 `0` 就是不限制这项，不写也是不限制。填了没法解析、或者类型不对的值，也会当成 0，并给一句警告。这三项如果都设了大于 0 的数，会一起生效：先卡并发，再卡每秒，再卡每分钟。

其它像重试次数、术语表、要额外跳过的键，也都在 `config.toml` 里，打开示例看注释就行。

别把填好密钥的 `config.toml` 传到 Git，也别发给别人。

## 它会怎么翻

嵌套的表、列表、多行字符串都会递归处理。键的顺序、结构、注释和引号风格会尽量按原样留着。

看起来像数据的东西会跳过，比如纯数字、布尔、UUID、`minecraft:stone`、`DIAMOND_SWORD`、路径、网址、`/command`、只剩颜色码或占位符的字符串、权限节点、音效名，以及键名本身就叫 material / sound / permission 这类、值也不像人话的项。拿不准时默认不翻，所以建议先 `--scan` 看一眼。

占位符会先换成内部记号再送给模型，回来后再换回去，并核对种类和数量对不对。对不上就重试，或者换下一个模型。模型必须返回 `{"status":"ok","translation":"..."}` 这种 JSON；它要是拒绝了，或者回了一堆解释，那些文字不会写进 YAML。

失败原因会标成三类：`NETWORK`（连不上、超时）、`PROVIDER`（服务商限流、模型拒绝、占位符丢了之类）、`CLIENT-CONFIG`（密钥、地址、配置缺字段这些问题）。

## 从源码跑

需要 Python 3.11+。

```bash
pip install -e ".[dev]"
mc-yaml-translator --scan samples/messages.yml
python -m pytest -q
```

Windows 的 exe 由 `.github/workflows/build.yml` 在 `windows-latest` 上用 PyInstaller 打成单个文件，示例配置会打包进 exe 里。推送 `v*` 标签就会挂到 Release 上。

## 关于

MIT 许可，详见 [LICENSE](LICENSE)。
