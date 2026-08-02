# 参与贡献

欢迎 bug 报告、插件和补丁。English: [CONTRIBUTING.md](CONTRIBUTING.md)

## 环境准备

```bash
git clone https://github.com/steven12138/FactorioReforge.git
cd FactorioReforge
./scripts/install.sh          # 已经有 Factorio 的话加 --no-server
.venv/bin/python -m pytest tests/ -q
```

主分支是 **`master`**。

## 提 PR 之前

```bash
.venv/bin/python -m pytest tests/ -q       # 279 项必须全过
.venv/bin/ruff check .
.venv/bin/python scripts/check_docs.py     # 没有死链和死锚点
```

CI 会在 Python 3.11 到 3.13 上跑同样这三条。

## 一个改动该带上什么

**一个没有它就会失败的测试。** 这里大部分测试的存在都是因为真的坏过：
`!!save make` 的死锁、一次静默复用了旧字节码的重载、
一个被 YAML 解析成布尔值的翻译键。现在每一个都成了测试，于是它们一直是修好的。

**用户能看到的东西要带两种语言。** 任何人会读到的字符串都走翻译器，
`en.yml` 和 `zh_cn.yml` 必须同步。
有测试检查两边没有对方缺的键、相同的键带相同的占位符。

**改变服主操作方式的，要更新文档。** 新命令改 [命令参考](docs/commands_zh.md)，
新配置项改 [自带插件](docs/plugins_zh.md)，
内部机制相关的改 [架构](docs/architecture_zh.md)——
以及你改动的那份的另一语言版本。

## 风格

机械的部分交给 ruff（`ruff.toml`），其余靠约定。

**注释写为什么，不写是什么。** 复述代码的注释是噪音；
解释「为什么显而易见的写法是错的」的注释，才是这行代码长这样的全部原因。
这个代码库里有用的注释大多是第二种，因为大多数意外来自 Factorio 而不是 Python。

**先测，别假设。** 这个项目最初有三个关于 Factorio 行为的设计判断是错的
——stdout 缓冲、stdin EOF、headless 截图——每一个都是靠跑真实服务器定下来的，
而不是靠读 wiki。如果你正打算绕开 Factorio 的某个行为，先确认它真的会这样。
[Factorio 实测笔记](docs/factorio-notes_zh.md) 是放这些测量的地方，
`scripts/probe_stdout.py` 是做一次测量的模板。

**在边界上大声失败。** RCON 断了和 Lua 执行失败都会抛异常，
而不是返回一个看着挺合理的东西。静默降级成一个空列表，
和一个真正的空列表是分不出来的。

**永远不用 `/c`。** 一切都走 `/sc`（silent-command）。
`/c` 会把存档永久标记为作弊，有测试 grep 整个代码树找它。

## 写插件

不一定要贡献到这里来——往 `plugins/` 里丢一个目录就能加载。
[写插件](docs/writing-plugins_zh.md) 讲了 API、事件、存储和国际化。

如果确实想让插件被自带，它需要两种语言的翻译目录、
针对纯逻辑的测试，以及 [自带插件](docs/plugins_zh.md) 及其英文版里的条目。

## 报告 bug

请附上你的 Factorio 版本（`./bin/x64/factorio --version`）、
`logs/reforge.log` 里相关的部分，以及**删掉了 RCON 密码**的 `config.yml`。
日志刻意不带颜色，密码在 FactorioReforge 打印它的地方也做了脱敏，
但 `config.yml` 里它是明文的。
