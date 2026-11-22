# Nyankoluni!!🌠  / mc-yaml-translator

Minecraft 插件的语言文件、配置里经常全是英文，还有byd会把语言文件和材料名，权限节点，颜色码挤在同一个 YAML 里。
本工具可以筛选真实的语言内容，翻成你要的语言。
由正则与LLM共同保证数字、ID、命令那些数据尽量得到完整保留，包含一个内置替换方案，用于较为安全的保留位置占位符，名称占位符和颜色代码。

Windows 下提供单独的 exe。支持直接使用和命令行调用，并提供了自动化相关的参数方便接入。
效果预览：

![预览](docs/preview.gif)

## 使用方法

1. 从 [Releases](https://github.com/Hikari16665/mc-yaml-translator/releases) 下载 `mc-yaml-translator.exe`
2. 双击一次.exe文件，软件会所在目录生成示例配置文件，内部含有完整注释指导。
3. 打开 `config.toml`，完整填写模型的 `base_url`、`api_key`、`model`。目标语言也在这个文件里改（默认 `zh_CN`）。也可以在某个提供商里声明限流和重试，可以添加多个提供商
4. 把要翻的 `.yml` / `.yaml` 拖到 exe 上就行，可以多选拖入。

翻译完成后会生成新的文件，比如 `messages.yml` 会得到 `translated_messages.yml`。新的文件的键顺序和嵌套层级与原始文件一致。

全部键都翻成功的话，窗口将自动关闭，任意键失败时控制台会停停留并展示错误消息，配置错误或原始文件格式错误不开始翻译，保留控制台，展示错误消息。

在终端环境下使用则不会暂停。示例使用方式如下，此处展示两个示例：

```text
mc-yaml-translator.exe messages.yml
```
自动读取配置并开始翻译`messages.yml`。
```text
mc-yaml-translator.exe --config D:\path\config.toml --scan messages.yml
```
读取`D:\path\config.toml`，并展示`messages.yml`将被翻译的项，不执行翻译。

`--scan` 可以用于先查看再执行的情况。

## 配置

模型按 `[[models]]` 提供的顺序被使用，依次降级，某个模型不行（重试也用尽了）就换下一个，降级重试成功了也可以。全部走不通就算翻译失败，仍会输出文件，仍然会附 `translated_messages.failures.txt`。

每个模型必填 `base_url`，`api_key`，和`model`。配置检查如果失败，开始翻译前会报 `CLIENT-CONFIG`错误。

目前支持限流方式： `max_streams`（同时多少路流式请求），`qpm`（60 秒滚动窗口内限制请求数），`qps`（每秒几个请求）。写0不限制，三个限制可同时生效，按max_streams, qpm, qps顺序限制并发。

其余设置项看注释。

注意不要泄露包含你的密钥的config.toml。

## 翻译条件
支持嵌套的表，列表，多行字符串
会尽量保留顺序，结构，格式允许的注释以及引号风格
数据类型与正则验证，自动跳过纯数字，UUID, 物品ID, 真实的Material值，路径，网址，斜线开头的命令
只剩颜色代码，只剩下占位符的不翻译
启发性的跳过权限节点，音效名，不像文字的键名

## 占位符替换与重建

为了防止模型识别占位符的失误，工具内部在提交给LLM之前，会先把可识别的占位符换成内部记号`__MCYT_PH_{n}__`，n为占位符的位置序号，另外有提示词来模型不会在重写文字的时候改变位置语义或是提供错误顺序。
翻译完毕后将会按顺序重建成真实的占位符。

## 错误代号
`NETWORK`：不可达或超时。
`PROVIDER`：被限流，模型拒绝，验证失败。
`CLIENT-CONFIG`：密钥、地址、配置缺省或错误。

## 从源码运行

运行环境： Python 3.11+，Pip。

```bash
pip install -e ".[dev]"
mc-yaml-translator --scan samples/messages.yml
python -m pytest -q
```

## 关于

MIT 许可，详见 [LICENSE](LICENSE)。
