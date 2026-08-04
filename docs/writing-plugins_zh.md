# 写插件

一个插件就是 `plugins/` 下面一个带 `__init__.py` 的目录。
它可以注册命令、监听事件、查询运行中的游戏、存自己的配置、带自己的翻译。
自带插件能做的一切你的插件都能做——它们没有任何特权。

## 最小的一个

```
plugins/greeter/
└── __init__.py
```

```python
from factorio_reforge.command.builder import Literal, GreedyText
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "greeter",
    "version": "1.0.0",
    "name": "Greeter",
    "description": "打个招呼",
    "author": "你",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}


def on_load(server, prev):
    server.register_command(
        Literal("!!hello")
        .requires(PermissionLevel.USER)
        .runs(lambda source: source.reply("hi"))
    )
    server.register_help_message("!!hello", "打个招呼")


async def on_player_joined(server, player, info):
    await server.say(f"欢迎，{player}！")


async def on_unload(server):
    ...   # 取消任务、关闭连接
```

`!!FR plugin reload greeter` 不用重启 Factorio 就能生效。

单个 `.py` 文件也能加载，但你想要的是目录：
只有目录能放[翻译](#国际化)，而且别的东西放在目录里也都更好扩展。

`dependencies` 除了 `factorio_reforge` 也可以写其他插件，加载顺序由此推出。
出现环会按名字报出来，而不是卡住。

## 命令

命令是树。每个节点匹配一个词，参数是带类型的。

```python
from factorio_reforge.command.builder import Literal, Text, GreedyText, Integer

server.register_command(
    Literal("!!shop")
    .requires(PermissionLevel.USER)
    .runs(overview)                                   # 光敲 "!!shop"
    .then(Literal("list").runs(show_list))
    .then(Literal("buy").then(Text("item").then(Integer("count").runs(buy))))
    .then(Literal("gift").requires(PermissionLevel.ADMIN)
          .then(GreedyText("message").runs(gift)))
)
```

| 节点 | 匹配 |
|---|---|
| `Literal("word")` | 就是这个词 |
| `Text("name")` | 一个以空白分隔的词 |
| `GreedyText("name")` | 这一行剩下的全部 |
| `Integer("name")` | 一个整数 |

处理函数收到的是 `(source, **参数)`，参数名就是节点名：

```python
async def buy(source, item: str, count: int):
    await source.reply(f"{count} 个 {item}")
```

`.requires(等级)` 作用于该节点及其下面的一切，
所以上面的 `!!shop gift` 是 admin 专属，其余不是。

输入对不上时报的是**最深的**那个失败——敲 `!!shop buy`
会告诉你 `buy` 还想要什么，而不是说 `!!shop` 没看懂。
节点有字面量子节点时会把它们列出来：
`未知选项 'lst'。可选：list、buy、gift`。

在你的 `lang/` 目录里放一个 `description` 键，`!!FR help`
的那一行摘要就会用读者的语言显示；`PLUGIN_METADATA["description"]`
是兜底，而它是 Python 字面量，永远是英文。

`register_help_message(前缀, 说明, detail=(...))` 把命令放进 `!!FR help`；
`detail` 里的行会出现在 `!!FR help <你的插件>` 里。

## 事件

函数名和事件同名就会被自动注册：

```python
async def on_player_death(server, player, info):
    await server.say(f"{player} 寄了")
```

| 事件 | 何时触发 |
|---|---|
| `on_load(server, prev)` | 插件已加载；重载时 `prev` 是旧模块 |
| `on_unload(server)` | 插件即将消失——在这里清理 |
| `on_info(server, info)` | 每一行解析结果 |
| `on_user_info(server, info)` | 只有人产生的那些行 |
| `on_player_joined(server, player, info)` | 有玩家进服 |
| `on_player_left(server, player, info)` | 有玩家离开 |
| `on_player_death(server, player, info)` | 有玩家死亡 |
| `on_server_start_pre(server)` | 即将启动 Factorio |
| `on_server_start(server)` | 进程已启动 |
| `on_server_startup(server)` | 世界已加载，玩家可以连了 |
| `on_server_stop_pre(server)` | 正在关闭，**服务器还活着** |
| `on_server_stop(server, code)` | 进程**已退出**，带退出码 |
| `on_server_crash(server, code)` | 没人让它退出，它自己退了 |
| `on_rcon_connected(server)` / `on_rcon_lost(server)` | RCON 通了 / 断了 |
| `on_snapshot_created(server, slot)` | 一次备份完成 |
| `on_rollback_started(server, slot)` / `on_rollback_finished(server, ok)` | 回档 |
| `on_reforge_start(server)` / `on_reforge_stop(server)` | FactorioReforge 自身 |

**`on_server_stop` 是在进程真的没了之后才触发的**，
这正是它和 `on_server_stop_pre` 分开的全部理由。
任何要动 Factorio 打开过的文件的操作——首当其冲是 `mod-list.json`——
都必须等它，否则改动会在服务器写出自己那份时被丢掉。

回调可以是 `def` 也可以是 `async def`，参数也可以比事件带的少。
抛异常的监听器会被记录并跳过，一个坏插件不会带走其他插件。

也可以显式注册、带优先级、或者用装饰器：

```python
server.register_event_listener("reforge.player_joined", callback, priority=50)

from factorio_reforge.plugin.events import event_listener

@event_listener("reforge.player_joined", priority=50)
async def welcome(server, player, info): ...
```

## 和服务器说话

```python
await server.say("大家好")                     # stdin：聊天
await server.execute("/promote alice")        # stdin：一条原始命令
await server.tell("alice", "悄悄话")           # RCON：单个玩家
await source.reply("...")                     # 命令是从哪来的就回哪去
```

命令处理函数里该用的是 `source.reply`：
它会根据命令来自终端、游戏还是 Telegram，回到对应的地方。

任何**要拿返回值**的东西走 RCON，回来的是解析好的 Python 而不是待抓取的文本：

```python
stats = await server.get_server_stats()
# {'tick': 18569, 'evolution': 0.00123, 'pollution': 0.0,
#  'research': None, 'players_online': 0, 'surface': 'nauvis', ...}

for p in await server.get_online_player_details():
    print(p["name"], p["online_time"], p["position"])

count = await server.lua_json("game.forces.player.get_entity_count('lab')")

await server.teleport_player("alice", {"x": 100, "y": 200})
await server.add_map_marker({"x": 0, "y": 0}, "基地",
                            icon={"type": "virtual", "name": "signal-info"})
```

`lua_json` 接受一个 Lua **表达式**，用 `helpers.table_to_json` 包起来，
返回真正的 Python 对象。Lua 报错会以异常形式抛出并带着 Lua
的错误消息，而不是一个碰巧以 "Cannot execute command" 开头的字符串。

两种失败都派生自同一个异常，所以插件代码只需要 catch 一个东西：

```python
from factorio_reforge.core.errors import QueryError   # RconError、LuaError

try:
    stats = await server.get_server_stats()
except QueryError as exc:
    await source.reply(f"查不到：{exc}")
```

玩家名请用 `lua.lua_string()` 插值，绝对不要用 f-string。
Factorio 跑的是 Lua 5.2，没有 `\u` 转义，
所以 `json.dumps` 对一个非 ASCII 名字生成的是编译不过的源码。

一切都走 `/sc` 而不是 `/c`，所以插件做的任何事都不会把世界标记为作弊。
请保持这一点——有测试在 grep 整个代码树找 `/c`。

## 富文本

Factorio 的聊天会渲染内联标签，而 `[gps=x,y,surface]` 是**可点击的**——
它会在所有人的地图上闪一下那个位置。

```python
from factorio_reforge.core import lua

await server.game_print(f"{player} 在 {lua.gps(x, y, surface)}")
```

`lua.gps()`、`lua.item_tag()`、`lua.technology_tag()` 和 `lua.colored()`
负责拼这些标签。正是它让 `!!here` 和 `!!warp` 真的有用，
而不只是打印一串坐标。图表标记是它的补充：
gps 标签说的是「现在看这里」，图表标记说的是「这个地方有个名字」。

## 存储

```python
config = server.load_config_simple("config.json", {"enabled": True, "radius": 32})
config["radius"] = 64
server.save_config_simple(config)

path = server.get_data_folder()      # config/<你的id>/，会自动创建
```

缺失的键会从默认值补上，所以新版本加了一个设置不会逼服主手动改文件。

## 国际化

插件像拥有自己的代码一样拥有自己的翻译：

```
plugins/greeter/
├── __init__.py
└── lang/
    ├── en.yml
    └── zh_cn.yml
```

```yaml
# zh_cn.yml
welcome: "欢迎，{player}！"
error:
  no_such_place: "没有叫 {name} 的地点"
```

```python
await server.say(server.tr("welcome", player=player))
await source.reply(server.tr("error.no_such_place", name=name))
```

键会自动挂在你的插件 id 命名空间下，所以两个插件都可以有 `failed`。
你的目录里没定义的键会落到核心目录去找，
`common.enabled` 这类共用字符串就放在那里。

三条刻意为之的行为：

- **缺失的键原样显示为键名。** 聊天里看到一个 `greeter.welcome`
  就直接告诉你该补什么；显示成空白则什么也没告诉你。
- **英文永远是兜底**，所以翻译到一半的语言仍然能用，而不是变成一堆窟窿。
- **占位符对不上时回退到原始模板**，
  这样漏了 `{player}` 的译者造成的是一句稍微不对的话，
  而不是命令处理函数里的一个异常。

> **YAML 会把 `yes`、`no`、`on`、`off` 当成布尔值，键也一样。**
> 一个裸的 `yes:` 键会变成 `True`，于是所有 `common.yes`
> 的查找都会显示成键名。请加引号，或者换个名字。
> 有测试专门拒绝含这种键的目录。

有测试保证：每个自带插件都带两种语言、两边没有对方缺的键、
相同的键带相同的占位符——占位符漂移的键只会在其中一种语言里格式化错误。
加一门语言就是把核心目录和每个插件里的 `en.yml` 复制成 `<语言代码>.yml`
再翻译内容。

## Telegram 子插件

`telegram_bridge` 是一个其他插件可以注册进来的**服务**，
所以你的插件不用 import `telegram`、也不用碰 token，就能被 Telegram 驱动：

```python
def on_load(server, prev):
    bridge = server.get_plugin_instance("telegram_bridge")
    if bridge is not None:
        bridge.register_command(
            "my_plugin", "hello", handler, level="admin", help="打个招呼"
        )
    # 桥接在重载后会重新广播自己，那时也要重新注册。
    server.register_event_listener("telegram.ready", lambda s: on_load(s, None))


async def handler(ctx):
    if not await ctx.confirm("真的要这么干吗？"):
        return
    await ctx.reply(f"好了，{ctx.user_name}")
```

`ctx` 带着 `args`、`text`、`user_id`、`user_name`、`level`、`is_admin`
和 `is_owner`，外加 `reply()`——会按 Telegram 的 4096 字符上限自动分段——
以及 `confirm()`，它给出内联的「是 / 取消」按钮，超时返回 `False`。

等级是 `viewer` / `admin` / `owner`，由桥接配置里的 id 名单决定。
注册按拥有它的插件归类，所以卸载你的插件会一并带走它的 Telegram 命令。

## 测试

自带插件的逻辑是完全不需要服务器就能测的：
`tests/test_plugin_logic.py` 按路径 import 插件（和插件管理器同样的方式），
直接调用它们的纯函数。
把解析、格式化和算术留在「收数据、返数据」的函数里，它们就一直是可测的。

需要服务器的部分，`tests/fake_factorio.py` 是一个复刻了真实二进制行为的替身，
包括那些让人意外的行为——见 [Factorio 实测笔记](factorio-notes_zh.md)。

```bash
python -m pytest tests/ -q
```
