<p align="center">
  <img src="docs/banner.svg" alt="FactorioReforge — Factorio 无头服务器的进程托管与插件框架" width="100%">
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white">
  <img alt="Factorio 2.0" src="https://img.shields.io/badge/factorio-2.0%20headless-d4761a">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="222 tests" src="https://img.shields.io/badge/tests-222%20passing-brightgreen">
  <img alt="i18n" src="https://img.shields.io/badge/i18n-en%20%C2%B7%20zh__cn-blue">
</p>

<p align="center">
  <b>简体中文</b> · <a href="README.md">English</a>
</p>

<p align="center">
  <a href="docs/TUTORIAL_zh.md"><b>📖 分步教程</b></a> ·
  <a href="#第一部分--把-factorio-联机服务器跑起来">开服</a> ·
  <a href="#第二部分--factorioreforge">使用</a> ·
  <a href="#写一个插件">写插件</a> ·
  <a href="#自带插件">插件</a>
</p>

---

> **第一次用？** 直接看 **[分步教程](docs/TUTORIAL_zh.md)** ——
> 从零安装到 Telegram 远程控制，13 节，每条命令都实测过。
> 下面这份 README 是完整参考手册。

---

一个面向 Factorio 无头服务器的进程托管与插件框架，形态参照
[MCDReforged](https://github.com/MCDReforged/MCDReforged)：它接管服务器进程，
把服务器输出解析成结构化事件，再分发给插件；插件可以注册命令、响应游戏内发生的事。

已在 **Factorio 2.0.77** headless / Linux 上实测验证。

- 第一部分讲的是怎么把 Factorio 联机服务器跑起来（不依赖本项目）。
- 第二部分才是 FactorioReforge 本身。

---

# 第一部分 — 把 Factorio 联机服务器跑起来

## 三种联机方式

| 方式 | 适用场景 | 说明 |
|---|---|---|
| 客户端直接开房 | 临时和朋友玩 | 主机退出即结束，无法被任何工具托管 |
| **Headless 专用服务端** | 长期开服 ← 本项目面向的场景 | 无图形无音频，占用小，可 7×24 无人值守 |
| 官方 Matching Server | 不想做端口转发 | `visibility.public = true` 会注册到官方服务器列表 |

## 安装 headless 服务端

headless 是和游戏客户端**分开的一份分发包**，要放在独立目录，不要指向 `~/.factorio`。

```bash
mkdir -p ~/project/FactorioReforge/server && cd ~/project/FactorioReforge/server
curl -L -o factorio-headless.tar.xz "https://factorio.com/get-download/stable/headless/linux64"
tar -xJf factorio-headless.tar.xz
./factorio/bin/x64/factorio --version
```

解出来是自包含的（`config-path.cfg` 里 `use-system-read-write-data-directories=false`），
所以存档、mod、配置全在 `factorio/` 目录内：

```
factorio/
├── bin/x64/factorio
├── data/                 # 游戏基础数据，也含各种 .example.json 配置模板
├── saves/                # 存档 .zip —— 回档操作的对象
├── mods/
└── config/config.ini
```

玩家的 mod 和 DLC 组合必须与服务端一致，否则连不上。

## 创建地图

```bash
cd ~/project/FactorioReforge/server/factorio
./bin/x64/factorio --create ./saves/reforge.zip
# 可选：--map-gen-settings ./map-gen-settings.json --map-settings ./map-settings.json
```

## server-settings.json

```bash
cp data/server-settings.example.json ./server-settings.json
```

真正需要关心的字段：

| 键 | 为什么重要 |
|---|---|
| `visibility.public` / `visibility.lan` | 公开需要填 `username` + `token`（token 在 `~/.factorio/player-data.json`） |
| `game_password` | 最简单的准入控制 |
| `require_user_verification` | 校验玩家的 factorio.com 账号 |
| `allow_commands` | `true` / `false` / `admins-only`。允许作弊指令会**永久标记存档** |
| `autosave_interval` / `autosave_slots` | 轮转的 `_autosave1..N.zip` |
| `auto_pause` | 无人时暂停省 CPU，但世界不再推进 —— 定时类插件要考虑这一点 |
| `non_blocking_saving` | 存档时不卡服，建议开 |

配套的三个名单文件，内容都是玩家名字符串数组：

```bash
echo '["你的factorio用户名"]' > server-adminlist.json
echo '[]' > server-whitelist.json
echo '[]' > server-banlist.json
```

## 启动

```bash
cd ~/project/FactorioReforge/server/factorio
./bin/x64/factorio \
  --start-server ./saves/reforge.zip \
  --server-settings ./server-settings.json \
  --server-adminlist ./server-adminlist.json \
  --server-banlist  ./server-banlist.json \
  --port 34197 \
  --rcon-port 27015 --rcon-password 'CHANGE_ME'
```

| 参数 | 含义 |
|---|---|
| `--start-server FILE` | 加载指定存档 |
| `--start-server-load-latest` | 加载最新存档 —— **不要和 FactorioReforge 一起用**，原因见备份一节 |
| `--start-server-load-scenario [MOD/]NAME` | 从场景开服 |
| `--console-log FILE` | 把控制台输出（含聊天）另存一份 |
| `--port N` / `--bind ADDR[:PORT]` | 游戏端口，默认 34197/**UDP** |
| `--rcon-port N` / `--rcon-password PW` / `--rcon-bind ADDR:PORT` | 远程控制台（TCP） |
| `--mod-directory PATH` | 指定 mod 目录 |

## 网络

- 游戏流量是 **34197/UDP**，不是 TCP。端口转发和防火墙都要按 UDP 放行。
- RCON 是 **27015/TCP**，协议明文 —— 只绑 `127.0.0.1`，绝不要暴露公网。
- 局域网：`visibility.lan = true`，同网段客户端自动发现。
- 直连：客户端 **Multiplayer → Connect to address** 填 `IP:34197`。

```bash
sudo ufw allow 34197/udp     # 如果开了防火墙
```

## 服务端控制台

**你在 stdin 敲的任何一行，都等同于服务器在游戏里发言。** 纯文本是广播，
以 `/` 开头的是命令。

`/players` `/admins` `/version` `/time` `/seed` `/promote` `/demote` `/kick`
`/ban` `/unban` `/mute` `/whitelist add|remove` `/server-save`
`/quit`（先存档再退出）`/c`（作弊 Lua，**会永久标记存档**）
`/sc`（silent-command Lua，不标记）

输出有**四种**形态，这也是解析器写成那样的原因：

```
   0.578 Info ServerMultiplayerManager.cpp:808: ... to(InGame)   引擎日志，带等级
   0.577 Hosting game at IP ADDR:({0.0.0.0:34197})               引擎日志，只有时间戳
2026-08-02 02:16:35 [CHAT] Alice: hello                          游戏事件
Players (0):                                                     命令回执，零前缀
```

## 存档与回档

自动存档轮转 `saves/_autosave1.zip`…`_autosaveN.zip`。
FactorioReforge **不复用**它们：备份是让服务器另写独立文件，
所以自动存档的轮转永远不会覆盖掉你想留的备份。

**Factorio 无法在运行时换存档。** 回档就意味着：停服 → 替换存档文件 → 重新启动。
第二部分的整套备份机制都是围绕这个约束设计的。

---

# 第二部分 — FactorioReforge

## 它做了什么

- 托管 Factorio 进程：启动、优雅停止、崩溃检测、可选自动重启
- 把 stdout 解析成结构化事件并分发给插件
- 支持从控制台**和游戏内聊天**发 `!!` 前缀命令，五级权限模型
- 槽位式备份与编排式回档，不会让你在中途失去世界
- 插件热重载
- 全程中英双语
- 自带 13 个插件：Telegram 控制、mod 安装、地图渲染、崩溃诊断、蓝图库、生产曲线等

## 安装

```bash
cd ~/project/FactorioReforge
python -m venv .venv && . .venv/bin/activate
pip install -e ".[console,telegram,dev]"
```

## 配置

```bash
python -m factorio_reforge init      # 生成 config.yml 以及 plugins/ config/ logs/ snapshots/
```

然后编辑 `config.yml`：把 `working_directory` 和 `start_command` 指向第一部分装好的
headless，并让 `rcon.password` 与 `start_command` 里的一致。

有两件事会在启动时被检查并**直接拒绝**，而不是默默出错：

- `start_command` 不能用 `--start-server-load-latest`。回档替换的是
  `saves.current_save`，但自动存档更新，服务器会加载到错误的地图。
- `--start-server` 指定的文件必须和 `saves.current_save` 是同一个文件，
  否则回档会写到服务器根本不读的位置。

## 运行

```bash
python -m factorio_reforge
```

服务器输出会回显到你的终端。在同一个终端输入：`!!` 开头的是 FactorioReforge 命令，
其他内容原样转发给 Factorio 的 stdin。

## 命令

```
!!FR help                        列出命令
!!FR status                      服务器、RCON、插件、备份状态
!!FR plugin list                 已加载插件（会标记文件已改动的）
!!FR plugin reload <id>          重载单个插件
!!FR plugin unload <id>
!!FR reload                      重载所有文件已改动的插件
!!FR server start|stop|restart   Factorio 生命周期
!!FR server kill                 SIGKILL，会丢失上次存档之后的一切
!!FR permission list
!!FR permission set <玩家> <guest|user|helper|admin|owner>
!!FR exit                        停服并退出

!!save                           列出备份槽位
!!save make [备注]                备份到槽位 1
!!save back [槽位]                准备回档（默认槽位 1）
!!save confirm                   确认执行，随后进入倒计时
!!save abort                     取消（待确认的和倒计时中的都能取消）
!!save del <槽位>
!!save rename <槽位> <备注>

!!here                           广播你的位置并在地图上钉标记
!!info [玩家]                     游玩时长、权限、位置
!!list                           在线玩家及时长
!!seen <玩家>                     游玩时长与最后在线时间
!!stats                          进化度、污染、科研、世界时长
!!tp <玩家> <目标|x y>            传送 —— 默认关闭，见下文
!!autosnap [now]                 自动快照状态

!!mod search <关键词>             在 mod 门户搜索
!!mod info <名字>                 详情与依赖
!!mod list                       已安装的 mod
!!mod install <名字> [版本]        下载并启用（admin）
!!mod remove|enable|disable <名字> （admin）
!!mod updates                    有新版本的 mod（admin）

!!warp [名字] / set / del         命名地点 —— 不传送任何人
!!bp list / save / get / del     共享蓝图库
!!prod [物品] / top               生产速率，带迷你走势图
!!top [time|kills|built]         排行榜
!!watch                          进化度、污染、科研、火箭
!!why                            服务器上次为什么退出（admin）
!!web                            Web 面板地址（admin）
!!map                            渲染世界地图并发送
!!FR lang                        翻译状态
```

权限：`guest(0) user(1) helper(2) admin(3) owner(4)`，持久化在
`config/permission.yml`。FactorioReforge 的控制台**永远是 owner** ——
能碰那个终端的人本来就能直接停掉进程。

## 两条通道各自负责什么

| 通道 | 承载 | 原因 |
|---|---|---|
| **stdin** | 聊天、管理命令、`/quit` | 永远可用、不需要额外端口、但拿不到返回值 |
| **RCON** | 玩家列表、Lua 求值、私聊 | 唯一能读回结果的途径 |

`server.say()` 走 stdin；`server.get_online_players()` 走 RCON，
RCON 不可用时会**抛异常**而不是假装成功。

### 结构化查询

RCON 返回的是字符串，按常理读回来的东西都得靠正则刮。这里所有查询都包在
`helpers.table_to_json` 里，于是拿到的是真实数据：

```python
stats = await server.get_server_stats()
# {'tick': 18569, 'evolution': 0.00123, 'pollution': 0.0,
#  'research': None, 'players_online': 0, 'surface': 'nauvis', ...}

for p in await server.get_online_player_details():
    print(p["name"], p["online_time"], p["position"])

await server.add_map_marker({"x": 0, "y": 0}, "base", icon={"type": "virtual", "name": "signal-info"})
await server.teleport_player("alice", {"x": 100, "y": 200})

value = await server.lua_json("game.forces.player.get_entity_count('lab')")
```

`lua_json` 接受一个 Lua **表达式**，返回解析好的 Python 数据。Lua 报错会变成带
Lua 原始信息的异常，而不是一段以 "Cannot execute command" 开头的文本。

两类失败 —— RCON 断了（`RconError`）和 Lua 执行失败（`LuaError`）——
都派生自 `factorio_reforge.core.errors.QueryError`，插件只需要 catch 一个：

```python
from factorio_reforge.core.errors import QueryError
try:
    stats = await server.get_server_stats()
except QueryError as exc:
    await source.reply(f"查不到：{exc}")
```

玩家名通过 `lua.lua_string()` 插值，非 ASCII 转成十进制字节转义 ——
Factorio 跑的是 Lua 5.2，**没有 `\u` 转义**，所以 `json.dumps` 生成的源码根本编译不过。

已针对 2.0.77 验证。注意几个从 1.1 变过的 API：
`game.table_to_json` → `helpers.table_to_json`；
`force.get_evolution_factor()` 现在要传 surface；
`force.item_production_statistics` → `force.get_item_production_statistics(surface)`。

## 回档

`!!save back <id>` 然后 `!!save confirm` 会执行：

1. 校验快照存在且是合法 zip
2. 游戏内倒计时广播
3. **先给当前世界做一次快照**，这样回错了还有退路
4. 停服并等待进程真正退出
5. 通过临时文件 + rename 替换 `current_save`，中断的拷贝不会截断存档
6. 重新启动服务器
7. 失败时自动恢复第 3 步的快照并明确报告

第 3 步失败就整个中止，是刻意的：没有退路的回档是一扇单向门。

## 写一个插件

往 `plugins/` 里丢一个 `.py` 文件，或者一个带 `__init__.py` 的目录。

```python
from factorio_reforge.command.builder import Literal, GreedyText
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "greeter",
    "version": "1.0.0",
    "name": "Greeter",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}

def on_load(server, prev):
    server.register_command(
        Literal("!!hello")
        .requires(PermissionLevel.USER)
        .runs(lambda source: source.reply("hi"))
    )

async def on_player_joined(server, player, info):
    await server.say(f"欢迎，{player}！")

async def on_unload(server):
    ...   # 取消任务、关闭连接
```

事件：`on_info` `on_user_info` `on_player_joined` `on_player_left`
`on_player_death` `on_server_start_pre` `on_server_start` `on_server_startup`
`on_server_stop` `on_server_crash` `on_rcon_connected` `on_rcon_lost`
`on_snapshot_created` `on_rollback_started` `on_rollback_finished`
`on_server_stop_pre` `on_reforge_start` `on_reforge_stop` `on_load` `on_unload`。

`on_server_stop` 是在进程**真正退出之后**触发的，并带上返回码 ——
任何要碰 Factorio 占用过的文件（尤其是 `mod-list.json`）的操作都必须等它。
`on_server_stop_pre` 才是服务器还活着时触发的那个。

也可以显式注册（带优先级）或用装饰器：

```python
server.register_event_listener("reforge.player_joined", callback, priority=50)

from factorio_reforge.plugin.events import event_listener
@event_listener("reforge.player_joined", priority=50)
async def welcome(server, player, info): ...
```

回调可以是同步的也可以是 `async def`，参数个数可以少于事件携带的数量。
某个监听器抛异常只会被记录并跳过 —— 一个插件坏掉不会影响其他插件收到事件。

插件私有存储在 `config/<插件id>/`：

```python
config = server.load_config_simple("config.json", {"enabled": True})
server.save_config_simple(config)
```

缺失的键会用默认值补齐，所以新版本加配置项不需要用户手动改文件。

## 自带插件

**`telegram_bridge`** —— 双向转发聊天，并提供 `/status` `/players` `/say`
`/save` `/saves` `/rollback` `/restart` `/stopserver` `/startserver` `/cmd`。
配置 `config/telegram_bridge/config.json`：把 BotFather 给的 token 填进 `token`，
然后给 bot 发条消息，从日志里读出 chat id 填进 `allowed_chat_ids`。
破坏性命令需要 `admin_user_ids`；能执行任意命令的 `/cmd` 需要 `owner_user_ids`。
`/rollback` 必须再点一次确认按钮。

**`auto_snapshot`** —— 定时快照，以及最后一个玩家离开时快照。无人在线时跳过定时快照：
开了 `auto_pause` 世界并没有推进，那些快照会是完全一样的。

**`server_utils`** —— `!!here` `!!info` `!!list` `!!seen` `!!stats` `!!tp`，
移植自 MCDReforged 里最常被念叨的那几个插件。`!!here` 发送一个可点击的 `[gps=]`
标签（点了会在所有人地图上 ping 那个位置），同时钉一个 chart tag 让位置长期可见 ——
见[富文本](#富文本)一节。

`!!tp` **默认关闭**。传送跳过了步行、火车和危险，而这些正是这个游戏的构成部分，
所以开不开是服主的决定，不该是默认值。在 `config/server_utils/config.json` 里设
`enable_teleport: true` 才会注册这个命令，`teleport_permission`（`admin` 或 `user`）
决定谁能用。关闭时**这个命令根本不存在** —— 比"注册了再拒绝"是更强的保证，
也不会出现在帮助里。

**`join_motd`** —— 玩家进服时用实时数据拼一条欢迎语：
`{player} {online} {total} {uptime} {day} {evolution} {pollution} {research}
{snapshots} {last_snapshot}`。

**`mod_manager`** —— 从 [mod 门户](https://mods.factorio.com)搜索、安装、更新、
卸载 mod，聊天和 Telegram 都能操作。

凭据来自 `config/mod_manager/config.json`，缺省时回落到你
`~/.factorio/player-data.json` 里的 `service-username` 和 `service-token`。
浏览不需要凭据，下载需要一个拥有游戏的账号。token 不会被记录也不会被回显。

三个容易踩坑、这个插件替你处理掉的地方：

- **版本过滤。** 加载时执行 `--version` 问二进制，只提供为该版本构建的 release。
  跳过这一步不是美观问题：把为 2.1 构建的 flib 0.17.2 装到 2.0.77 上，
  服务器下次启动会直接以退出码 1 失败。
- **Factorio 会覆盖 `mod-list.json`。** 运行中的服务器把 mod 列表存在内存里，
  退出时写回自己的版本，把运行期间的任何改动全部丢弃。插件单独记录自己的意图，
  并在 `on_server_stop`（进程真正退出之后）重新应用。
- **只装必需依赖。** `?` 和 `(?)` 前缀的条目会被跳过 ——
  大型整合 mod 的可选依赖装全了会拖进几十个无关 mod。

搜索走的是本地缓存的完整 mod 列表（约 22500 条、13 MB、拉取约 14 秒、按 TTL 刷新），
因为门户没有文本搜索接口。精确匹配和前缀匹配优先于子串匹配，下载量用来打破平局。

### Telegram 子插件

`telegram_bridge` 同时是一个**服务**，其他插件可以向它注册，
从而在**不 import `telegram`** 的前提下被 Telegram 触达：

```python
def on_load(server, prev):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is not None:
        bridge.register_command(
            "my_plugin", "hello", handler, level="admin", help="打个招呼"
        )
    # 桥接重载后会重新广播自己，在那里也要重新注册一次。
    server.register_event_listener("telegram.ready", lambda s: on_load(s, None))

async def handler(ctx):
    if not await ctx.confirm("真的要执行吗？"):
        return
    await ctx.reply(f"完成，{ctx.user_name}")
```

`ctx` 携带 `args`、`text`、`user_id`、`user_name`、`level`、`is_admin`、
`is_owner`，以及 `reply()`（自动切分 Telegram 的 4096 字符上限）和
`confirm()`（inline 是/取消按钮，超时返回 `False`）。
级别是 `viewer` / `admin` / `owner`，由桥接配置里的 chat id 和 user id 列表决定。
注册按所属插件归属，卸载插件时它的命令一并消失。

`mod_manager` 就是这么实现 `/mods` `/modsearch` `/modinfo` `/modinstall`
`/modremove` `/modupdates` 的 —— `/modinstall` 先确认，装完再问要不要重启。

### 其余插件

**`crash_doctor`** —— 维护一个输出滚动缓冲区，服务器意外退出时用真实的失败特征去匹配，
说出原因和修复命令。对开发过程中真实发生的那次 mod 不兼容事故，它报告的是：

```
Server exited with code 1: the mod 'flib' could not be loaded
  Incompatible Factorio version (current: 2.0, required: 2.1); Dependency base >= 2.1.0 is not satisfied
  Try: !!mod remove flib
```

两条规则让匹配可用：块间取最新，让缓冲区里上一次启动的陈旧错误不会盖住刚发生的；
块内取最具体，让指名元凶的表头胜过描述症状的缩进细节行。`!!why` 可以重放上次诊断。

**`warp`** —— 命名地点，以可点击的 `[gps=]` 标签广播并钉成 chart tag。
**不传送任何人** —— 这是 `!!tp` 的信息价值那一半，去掉了破坏平衡那一半。
管理员设置，所有人可以查。

**`blueprints`** —— 服务端蓝图库。`!!bp save <名字>` 把你周围的区域做成蓝图，
`!!bp get <名字>` 把它放进别人的物品栏。全程通过临时 inventory 在服务端完成，
客户端不需要装任何东西。字符串在存入时就验证，坏的会当场拒绝，
而不是等别人来取时才失败。

**`production`** —— 定时采样 `get_flow_count`，建立跨会话存活的历史数据 ——
Factorio 自己的生产曲线是每客户端的、退出即消失。聊天里渲染成 Unicode 迷你走势图，
Web 面板里渲染成 SVG，两边都不需要绘图库。

**`world_watch`** —— 进化度与污染告警，加上科研和火箭里程碑。合成一个插件是因为
两者是同一个机制：轮询、和上次比对、播报变化。告警按**阈值跨越**触发一次，
而不是每次轮询都报。状态会持久化，所以重启不会把世界经历过的里程碑全部重播一遍。

**`leaderboard`** —— `!!top` 游玩时长排行（精确 —— Factorio 原生按玩家统计
`online_time`），加上全势力的击杀和产量总计。**手工制作数和行走距离刻意没做**：
Factorio 不按玩家统计这两项，而排行榜上放一个编出来的数字比没有排行榜更糟。

**`web_panel`** —— `127.0.0.1:8080` 上的只读状态页，`/api` 提供 JSON。
只读是刻意的：没有停服按钮、没有回档、没有控制台。一个没有鉴权也没有写入路径的页面，
无法被利用去造成破坏。要从机器外面控制，走 Telegram，那边是有鉴权的。

### 富文本

Factorio 聊天会渲染内联标签，其中 `[gps=x,y,surface]` 是**可点击的** ——
点了会在所有人地图上 ping 那个位置。`lua.gps()`、`lua.item_tag()`、
`lua.technology_tag()`、`lua.colored()` 负责构造它们：

```python
await server.game_print(f"{player} is at {lua.gps(x, y, surface)}")
```

这才是 `!!here` 和 `!!warp` 真正有用的原因，而不只是打印一串坐标。
chart tag 与之互补：gps 标签说的是"现在看这里"，chart tag 说的是"这地方有名字"。

## 多语言

所有给人看的文本都走翻译层。在 `config.yml` 里设 `language`；
自带 `en` 和 `zh_cn`，缺失的键回落到英文，所以翻译一半也不影响使用。

```
!!FR lang                  当前语言，以及各语言还缺哪些词条
!!FR lang missing zh_cn    具体缺失的键
```

**自带的 13 个插件全部已翻译**，不只是核心。

要加语言：把 `factorio_reforge/lang/en.yml` 复制成 `<语言代码>.yml` 翻译，
然后对每个插件在 `plugins/lang/<插件id>/` 下做同样的事。
键会挂在插件 id 下 —— 插件里 `server.tr("failed")` 先找 `<插件id>.failed`，
找不到再落到核心词表（比如 `common.yes` 这种公共文案）。

文件位置：

| 插件形态 | 翻译文件 |
|---|---|
| 单文件 `plugins/warp.py` | `plugins/lang/warp/<语言代码>.yml` |
| 目录 `plugins/telegram_bridge/` | `plugins/telegram_bridge/lang/<语言代码>.yml` |

单文件插件各自有子目录而不是共用一个 `plugins/lang/` ——
因为一次加载只能挂一个命名空间，共用会互相覆盖。

测试会断言：每个插件都带齐两种语言、两边的键完全一致、
且同一个键在两种语言里的占位符相同 —— 占位符对不上会导致只有一种语言格式化出错。

缺失的键会直接显示成键名而不是空白 ——
聊天里出现一个 `save.restore.confirm` 正好告诉你该补什么。

## 测试

```bash
python -m pytest tests/ -q
```

解析器测试跑的是从真实服务器采样下来的输出；进程测试驱动
`tests/fake_factorio.py` —— 一个复刻了真实二进制关键行为的替身，
其中包括"stdin 收到 EOF 后仍然存活"，这正是 FactorioReforge 永不关闭那根管道的原因。

`scripts/probe_stdout.py` 可以重跑当初测量真实服务器输出缓冲行为的实验，
`docs/M0-findings.md`（[中文](docs/M0-findings_zh.md)）记录了当时的结论。

## 目录结构

```
factorio_reforge/
├── core/      进程、解析器、Info、反应链、RCON、Lua、控制台、总装
├── plugin/    加载器、注册表、接口、事件、元数据、内置命令
├── command/   命令树构造器、分发、命令来源
├── permission/
├── mods/      mod 门户客户端与本地 mod 目录管理
├── saves/     快照与回档
└── config.py
plugins/       插件目录（自带的 12 个都在这里）
config/        config.yml、permission.yml、各插件私有配置
snapshots/     快照 zip + index.json
```
