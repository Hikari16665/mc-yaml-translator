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
3. 打开 `config.toml`，完整填写模型的 `base_url`、`api_key`、`model`。源语言、目标语言也在这个文件里改（源语言默认 `auto`，目标语言默认 `zh_CN`，目标语言也可以写成列表）。也可以在某个提供商里声明限流和重试，可以添加多个提供商  
4. 把要翻的 `.yml` / `.yaml` 拖到 exe 上就行，可以多选拖入。  

翻译完成后会生成新的文件，不会覆盖原来的。目标语言只写了一个字符串时，`messages.yml` 会得到 `translated_messages.yml`。如果写成列表（哪怕只有一项，也按列表模式处理），每种语言各出一份，比如 `translated_zh_CN_messages.yml`、`translated_ja_JP_messages.yml`。新的文件的键顺序和嵌套层级与原始文件一致。  

全部任务都成功的话，窗口将自动关闭，任意任务失败时控制台会停留并展示错误消息（列表模式下会带上语言代码）。因为原文不是你指定的源语言而跳过的，不算失败。配置错误或原始文件格式错误不开始翻译，保留控制台，展示错误消息。  

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

源语言 `source_language` 默认是 `auto`，交给模型自己判断，文件里的自然语言都会翻。写成 `en`、`zh_CN` 这种具体代码时，只有这种语言的句子会被翻，已经是别的语言的原文会原样留下，不算失败，摘要里会单独写「跳过原文语言」。  

目标语言 `target_language` 可以是一个字符串，也可以是一个列表。只写 `"zh_CN"` 时，行为和以前一样，进度条还是键名，输出 `translated_messages.yml`。写成列表时（哪怕里面只有一项，也按列表模式来），每个键会再按每种目标语言拆成一个小任务，进度条标题变成 `welcome → ja_JP: …` 这种，总进度按小任务数来数。每种语言各写一个文件，名字是 `translated_<语言代码>_原文件名`。失败报告也按语言分开，里面会写明是哪个语言的小任务出了问题。  

模型按 `[[models]]` 提供的顺序使用，依次降级，某个模型不行（重试也用尽了）就换下一个，降级重试成功了也可以。全部走不通就算翻译失败，仍会输出文件，并附失败报告。单目标时是 `translated_messages.failures.txt`，列表模式则是 `translated_zh_CN_messages.failures.txt` 这种。  

请求默认会带 `response_format=json_object`，让模型按固定 JSON 回来。要是这个模型或中转不认这个参数、回了 400，会在同一个模型上自动去掉再试一次，这次不算失败。要是去掉以后还是 400，失败报告里会带上服务商返回的原文，方便看出真正原因。  

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

为了防止模型识别占位符的失误，工具内部在提交给LLM之前，会先把可识别的占位符换成内部记号`__MCYT_PH_{n}__`，n为占位符的位置序号。  
有提示词和约束，保证模型不会在重写文字时搞错意思，或是提供错误顺序。  
翻译完毕后将会按顺序重建成真实的占位符。  

## 错误代号
`NETWORK`：不可达或超时。  
`PROVIDER`：被限流，模型拒绝，验证失败，或者模型/接口不接受某个参数（比如不支持 `response_format`）。  
`CLIENT-CONFIG`：密钥、地址、配置缺省或错误。HTTP 401/403 也算这类；单纯的「不支持某个参数」不会再标成配置错误。  

## 从源码运行

运行环境： Python 3.11+，Pip。  

```bash
pip install -e ".[dev]"
mc-yaml-translator --scan samples/messages.yml
python -m pytest -q
```

## 关于

MIT 许可，详见 [LICENSE](LICENSE)。
