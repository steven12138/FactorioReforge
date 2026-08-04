<p align="center">
  <img src="docs/banner.svg" alt="FactorioReforge —— Factorio headless 服务器的进程托管与插件框架" width="100%">
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white">
  <img alt="Factorio 2.0" src="https://img.shields.io/badge/factorio-2.0%20headless-d4761a">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="465 tests" src="https://img.shields.io/badge/tests-465%20passing-brightgreen">
  <img alt="i18n" src="https://img.shields.io/badge/i18n-en%20%C2%B7%20zh__cn-blue">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>简体中文</b>
</p>

---

**FactorioReforge 托管你的 Factorio headless 服务器，让你在游戏聊天框、终端
或 Telegram 里管理它。**

它接管服务器进程，把输出解析成结构化事件，再分发给插件。自带二十一个插件：
带撤销的槽位备份、Telegram 远程控制、从 mod 门户装 mod、渲染世界地图、
崩溃诊断、蓝图库、产量曲线等等。

设计参照 [MCDReforged](https://github.com/MCDReforged/MCDReforged)——
Minecraft 那边做同样事情的项目。基于 **Factorio 2.0.77** headless 实机验证。

```
14:02:11 INF reforge        已加载 13 个插件
14:02:14 INF factorio       Hosting game at IP ADDR:({0.0.0.0:34197})
14:02:16 INF reforge        启动检查：0 个问题，2 条提示，3 条正常现象
14:02:31 INF factorio       2026-08-02 14:02:31 [JOIN] Alice joined the game
14:02:48 INF factorio       2026-08-02 14:02:48 [CHAT] Alice: !!qb make 打虫之前
14:02:48 INF save           已备份到槽位 1（24.1 MB，0.4 秒）
```

---

## 快速开始

```bash
git clone https://github.com/steven12138/FactorioReforge.git
cd FactorioReforge
./scripts/install.sh          # 下载 Factorio、创建地图、建 .venv、写好 config.yml
./scripts/run.sh
```

然后在游戏里 **多人游戏 → 连接到地址 → `127.0.0.1:34197`**，
在聊天框或终端里敲 `!!FR help`。

已经有 headless 服务器了？`./scripts/install.sh --no-server`
只装 Python 这一侧，然后照 [配置](docs/configuration_zh.md) 指向你的安装目录。

**第一次开 Factorio 服务器？**[新手教程](docs/TUTORIAL_zh.md)
从一台空机器一路讲到 Telegram 控制，十三节，每条命令都在真实服务器上跑过。

## 文档

| | |
|---|---|
| [**新手教程**](docs/TUTORIAL_zh.md) | 从零到一台跑起来并被托管的服务器 |
| [**命令参考**](docs/commands_zh.md) | 每一条命令、作用、谁能用 |
| [**配置**](docs/configuration_zh.md) | `config.yml`，以及它拒绝启动的几种情况 |
| [**自带插件**](docs/plugins_zh.md) | 每个插件是什么、怎么配 |
| [**写插件**](docs/writing-plugins_zh.md) | 事件、服务器 API、存储、国际化 |
| [**开一个 Factorio 服务器**](docs/factorio-server_zh.md) | 纯 headless 服务器本身，不涉及本项目 |
| [**架构**](docs/architecture_zh.md) | 内部怎么工作的，以及为什么这么做 |
| [**Factorio 实测笔记**](docs/factorio-notes_zh.md) | 在真实 2.0.77 上实测出来的行为 |
| [**参与贡献**](CONTRIBUTING_zh.md) | 测试、风格、一个改动该带上什么 |
| [**更新日志**](CHANGELOG_zh.md) | 改了些什么 |

## 它做了什么

**接管进程。** 启动、优雅停服、崩溃检测、可选自动重启。Ctrl-C 会先停
Factorio 并等它真正退出，不会因为你着急而丢掉任何一个 tick。

**读懂服务器。** Factorio stdout 的每一行都被解析成事件——进出服、聊天、
死亡、引擎日志——再分发给插件。同一行不会被解析两次。

**两条通道，各司其职。** 聊天和管理命令走 stdin，随时可用、不占端口。
凡是**要拿返回值**的——玩家列表、Lua 表达式、私聊——走 RCON，
并用 `helpers.table_to_json` 包一层，插件拿到的是真正的 Python
数据而不是待抓取的文本。

**命令随处可用。** `!!` 开头的命令在终端、游戏聊天框和 Telegram 里都能用，
背后是五级权限模型。

**备份可以撤销。** 槽位模型照搬
[QuickBackupM](https://github.com/TISUnion/QuickBackupM)——在 Minecraft
服务器上跑了很多年。回档前会先把当前世界另存一份，所以回错档也救得回来。

**绝不作弊。** FactorioReforge 执行的一切都走 `/sc`（silent-command），
从不用 `/c`，你的存档永远不会被标记为作弊——而且有测试 grep
整个代码树来保证这一点。

**说你的语言。** 中英文全覆盖，日志也不例外。每个插件自带自己的翻译。

## 自带插件

| 插件 | 给你什么 |
|---|---|
| [`save_guard`](docs/plugins_zh.md#存档管理) | `!!qb` —— 槽位备份、两步回档、撤销 |
| [`auto_snapshot`](docs/plugins_zh.md#auto_snapshot) | 定时备份，以及最后一名玩家离开时备份 |
| [`telegram_bridge`](docs/plugins_zh.md#telegram_bridge) | 聊天双向转发，手机上完整控制服务器 |
| [`mod_manager`](docs/plugins_zh.md#mod_manager) | 从 mod 门户搜索、安装、更新 mod |
| [`map_render`](docs/plugins_zh.md#map_render) | `!!map` —— 一 tile 一像素画出整个世界 |
| [`crash_doctor`](docs/plugins_zh.md#crash_doctor) | 服务器挂了时说清原因和修法 |
| [`server_admin`](docs/plugins_zh.md#server_admin) | `!!server` —— 在聊天里改 `server-settings.json` |
| [`server_utils`](docs/plugins_zh.md#server_utils) | `!!here` `!!info` `!!list` `!!seen` `!!stats` `!!tp` |
| [`warp`](docs/plugins_zh.md#warp) | 命名地点，可点击、可标记在地图上 |
| [`blueprints`](docs/plugins_zh.md#blueprints) | 服务端共享蓝图库 |
| [`calculator`](docs/plugins_zh.md#calculator) | `==1+1`，以及 `!!ratio` —— 造任何东西要多少机器、带和电 |
| [`ups_watch`](docs/plugins_zh.md#ups_watch) | `!!ups` —— 更新率，以及是什么在吃掉它 |
| [`alerts`](docs/plugins_zh.md#alerts) | 遭袭与游戏内警报，空服时也能发现 |
| [`trains`](docs/plugins_zh.md#trains) | `!!trains` —— 无路径和卡住的列车 |
| [`power`](docs/plugins_zh.md#power) | `!!power` —— 在断电之前看蓄电池电量 |
| [`research`](docs/plugins_zh.md#research) | `!!research` —— 查看和修改科技队列 |
| [`vote`](docs/plugins_zh.md#vote) | `!!vote` —— 把一个问题交给玩家表决 |
| [`mail`](docs/plugins_zh.md#mail) | `!!mail` —— 给不在线的玩家留言 |
| [`production`](docs/plugins_zh.md#production) | 跨会话保留的产量历史 |
| [`world_watch`](docs/plugins_zh.md#world_watch) | 进化度、污染、科技和火箭提醒 |
| [`leaderboard`](docs/plugins_zh.md#leaderboard) | `!!top` —— 在线时长、击杀、产量 |
| [`join_motd`](docs/plugins_zh.md#join_motd) | 用实时数据拼出来的欢迎语 |
| [`web_panel`](docs/plugins_zh.md#web_panel) | 只读状态页，带地图和曲线 |

想自己写一个？往 `plugins/` 里放一个目录就行 ——
见 [写插件](docs/writing-plugins_zh.md)。

## 环境要求

- Linux，Python **3.11+**
- 一份 Factorio **headless** 服务端（`install.sh` 会帮你下）
- 可选：`prompt_toolkit` 让日志不打断你正在输入的那一行、
  `python-telegram-bot` 用于 Telegram

## 目录结构

```
factorio_reforge/    框架本体
├── core/            进程、输出解析、事件、RCON、控制台
├── plugin/          加载、事件、面向插件的 API
├── command/         命令树与分发
├── permission/      五级权限，持久化
└── saves/           槽位、备份、回档
plugins/             自带插件，每个一个包
config/              各插件的配置
snapshots/           备份槽位
```

## 许可证

MIT，见 [LICENSE](LICENSE)。

设计上参考了 [MCDReforged](https://github.com/MCDReforged/MCDReforged)
和 [QuickBackupM](https://github.com/TISUnion/QuickBackupM)，这是有意为之。
Factorio 是 Wube Software 的商标，本项目与其无关联。
