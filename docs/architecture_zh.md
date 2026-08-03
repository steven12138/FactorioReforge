# 架构

FactorioReforge 内部是怎么工作的，以及为什么。
这里的大多数决定是被实测出来的东西**逼**成这样的，而不是从设计文档里选出来的
——测量本身记在 [Factorio 实测笔记](factorio-notes_zh.md)。

```
factorio_reforge/
├── core/            进程、handler、info、reactor、rcon、console、loglens、server
├── plugin/          manager、registry、interface、events、metadata、builtin
├── command/         命令树构建、分发、命令来源
├── permission/      五级权限，持久化到 config/permission.yml
├── saves/           槽位、备份、回档
└── config.py
```

整体形态贴近 [MCDReforged](https://github.com/MCDReforged/MCDReforged)，
贴到它的概念可以直接迁移过来；备份模型则是直接照搬
[QuickBackupM](https://github.com/TISUnion/QuickBackupM)。
两者背后都有多年的实际运维。这边不一样的地方，都是 Factorio 不一样的地方。

## 两条通道

| 通道 | 承载 | 为什么 |
|---|---|---|
| **stdin** | 聊天、管理命令、`/quit` | 随时可用、不占端口、没有返回值 |
| **RCON** | 玩家列表、Lua 求值、私聊 | 唯一能拿到返回值的方式 |

`server.say()` 走 stdin。`server.get_online_players()` 走 RCON，
RCON 不可用时它**抛异常**，而不是假装成功了。
两者之间不会静默降级，因为「消息发丢了」和「玩家列表真的是空的」
在调用方看起来一模一样，但根本不是一个问题。

### 结构化查询

RCON 返回的是字符串，所以要读点什么出来本来就得靠抓取。
这里的做法是在 Lua 那侧把每个查询都用 `helpers.table_to_json`
包一层，插件拿到的就是真正的 Python 数据。
就这一个决定，让 `get_server_stats`、`get_online_player_details`、
`lua_json` 和产量采样器都只有几行，而不是一堆下个补丁就会挂掉的正则。

名字用 `lua.lua_string()` 插值，它把非 ASCII 转义成十进制字节。
Factorio 跑的是 Lua 5.2，没有 `\u` 转义，`json.dumps` 生成的源码编译不过。

2.0 里挪过位置、值得知道的 API：`game.table_to_json` →
`helpers.table_to_json`；`force.get_evolution_factor()` 现在要传 surface；
`force.item_production_statistics` → `force.get_item_production_statistics(surface)`。

## 托管进程

Factorio 的 stdout 就是用最朴素的 asyncio 读管道——之所以先去测，
是因为一个 C++ 程序往管道写通常是全缓冲的，
那意味着事件会以几 KB 一批的方式到达，得上 `pty` 才能绕开。
结果并不是：行是立刻到的。测量过程见
[Factorio 实测笔记](factorio-notes_zh.md)，`scripts/probe_stdout.py` 可以重跑。

**stdin 永不关闭。** 另一个测出来的意外：stdin 收到 EOF **不会**让
Factorio 服务器停下来，所以关管道不是一种关服手段，而一直开着也不花什么代价。

关服是逐级升级、每级都等的：`/quit` → SIGINT → SIGTERM → SIGKILL。
Ctrl-C 走的是同一条路，所以它要花点时间——Factorio 在存档。
`on_server_stop` 只在进程真的退出之后才触发并带上退出码，
因为要动服务器打开过的文件（`mod-list.json`）的插件，早一点跑都是错的。

## 解析输出

Factorio 产生的是**四种**形状的行，不是 wiki 上看起来的两种：

```
   0.001 2026-08-02 14:02:11; Factorio 2.0.77 (build 84115, linux64, headless)
   1.234 Info ServerMultiplayerManager.cpp:791: updateTick(4) changing state ...
2026-08-02 14:02:31 [JOIN] Alice joined the game
Online players (1):
```

带级别的引擎行、只有秒数的引擎行、`[TAG]` 游戏事件行，
以及对你敲进去的东西的裸回复。每一行都变成一个 `Info`，
带 `source`、`content`、`tag`、`player`、`is_user` 和一个行为标志。

匹配不上任何一种的行会带着警告作为 `GENERAL_INFO` 透传，而不是被丢掉——
未来某个补丁改了格式，该退化的是依赖它的那些功能，不是整个托管框架。

`[CHAT] <server>: ...` 会被识别为 FactorioReforge 自己的声音并丢弃。
没有这一步，Telegram 桥接会永远转发自己转发的东西。

## 命令分发

命令跑在**自己的任务**里，绝不在读取循环上。

这不是性能考虑。像 `!!save make` 这样的处理函数要等 Factorio 打印
"Saving finished"——而那一行只可能从 stdout 泵那里到来。
把处理函数内联在泵上跑，它就在等一行只有它自己能读到的字：
控制台不响应了、游戏看起来卡住了，然后整件事在一百二十秒后靠超时解开。
有一个回归测试复现的正是这个形状。

解析和事件分发仍然是内联的，以保证行的顺序。

## 备份与回档

槽位模型照搬 QuickBackupM，而不是重新发明。

**槽位。** 备份永远进**槽位 1**，其余顺次下移。
被牺牲掉腾位置的是第一个空槽，没有空槽则是编号最大且已过
`delete_protection` 的那个。如果所有槽位都还在保护期内，
这次备份会被**拒绝**，而不是毁掉某个有人特意要求留下的东西。
`saves.slot_protection` 是一个秒数列表，它的**长度**就是槽位数。

**回档**，`!!save back <槽位>` 然后 `!!save confirm`：

1. 校验该槽位里是一个有效的 zip
2. 在聊天里逐秒倒计时，期间可以 `!!save abort`
3. 停服，并等进程真的退出
4. **把当前世界复制到固定的 `overwrite` 槽位** —— QBM 用来撤销「回错档」的那一手
5. 通过临时文件 + rename 替换 `current_save`，这样拷到一半被打断也不会把世界截断
6. 重新启动服务器
7. 失败时把 `overwrite` 里的世界放回去，并说明情况

第 4 步失败就拒绝往下走，是刻意的。没有退路的回档是一扇单向门。

### 有两件事 Factorio 做得比 Minecraft 好

`/server-save <名字>` 写出一份**独立完整**的存档并且不动正在跑的世界，
所以备份是直接写进槽位的——不用拷贝，也不用「为了备份而覆盖世界」，
而后者正是这个项目早期版本里一个裸 `/server-save` 干的事。

而且一个世界就是一个 zip，不是一个活的目录，
所以 QBM 那套 `save-off` / `save-all flush` 在这里没有对应物，直接不存在。

## 产能比例与求解器

`!!ratio` 是这里唯一一处复杂到值得单独讲的计算，而且它的设计并不原创：
Kirk McDonald 的计算器、FactorioLab、YAFC 最后都收敛到了同一个形状，
所以 `calculator/solver.py` 也照着来，而不是再发明第四种答案。

配方变成一个**矩阵**：行是物品，列是配方，格子里是一次制作对该物品的净值
——产物为正、原料为负。你要的速率就是 `A x >= b` 的解。

沿着原料树递归展开对图里的大部分是够用的，而这只是因为大多数物品只有一条配方。
有两种形状会把它打破，而且都在原版里：

* **多条配方产出互相重叠的物品。** 高级石油裂解同时产出重油、轻油和石油气；
  重油裂化把重油变轻油；轻油裂化把轻油变石油气。
  各跑多少不是任何单条配方的性质，递归展开只能猜。
* **一条配方消耗自己产出的东西。** Kovarex 把铀-238 同时列为原料和产物，
  递归展开会在那里无限下去。

这两种情况是同一次求解。裂不裂化由「最小化成本」决定；
而循环在系数里自然抵消 —— 一个物品被当成原料的条件是
**没有任何配方对它的净值为正**，正是这一条让求解器不会以为
Kovarex 能凭空造出自己的铀-238。

**全程精确算术。** 这个游戏里的比例是分数：一条带 3/2 个齿轮、7/12 台机器。
换成浮点就成了 0.5833333333333334，机器数会算出 2.9999999999999996 ——
技术上没错，看上去全错。一切都是 `fractions.Fraction`，直到打印那一刻才取整。

**用 Bland 规则，而不是最快的选主元法。** 退化顶点在这里是常态而不是边角情况：
两条配方以相同比例产出同一个物品就是一次平局，而 Factorio 的配方图里全是这个。
Bland 规则是唯一被证明不会循环的选法。问题规模是几十个变量，
所以它慢的那点完全测不出来，而它保证的是「一定会停」。

数据不在这个仓库里。它是通过和其他一切相同的 RCON 通道从运行中的游戏里读的，
这让答案对你这个版本、你这套 mod 都是对的，也让「表过期了」这件事不可能发生。
代价是服务器必须开着。见 [`calculator`](plugins_zh.md#calculator)。

## 插件

发现、按依赖排序加载、热重载。重载时先清空注册表，
所以上一版的命令、事件监听、帮助条目和翻译不可能残留到新版里。

重载用的是一个绕过字节码缓存的 loader。没有它，
文件在同一秒内被改动时 Python 会开开心心地给你**上一份**编译结果——
一次静默什么都没做的重载，比一次失败的重载更糟。

面向插件的 API 就是一个对象 `ServerInterface`，
这样需要保持稳定的接口面只有一个。

## 控制台

不管是谁产生的，都是同一种格式：

```
14:02:11 INF reforge        已加载 13 个插件
14:02:14 INF factorio       Hosting game at IP ADDR:({0.0.0.0:34197})
14:02:14 INF mod_manager    只提供为 Factorio 2.0.77 构建的 mod
```

来源那一列说明这行是谁产生的——`reforge`、`factorio`，或者某个插件 id。
Factorio 的输出以前是走一个裸 `print` 的，
那让控制台里并排出现两种毫不相干的格式，更糟的是，
服务器自己说的话根本没有进 `logs/reforge.log`。

**Factorio 的行是原样的。** 不改写、不加注、不重新分级。
你看到的和游戏自己的日志一致，也和你可能贴进 bug 报告里的一致。

只在 stdout 是一个想要颜色的终端时上色；文件 handler 永远不上色，
所以 `logs/reforge.log` 一直是可 grep 的。
装了 `prompt_toolkit` 的话，日志行不会打断你正在输入的那一行。

### 启动检查报告

正因为 Factorio 的行保持原样，所有**想对它们说的话**都单独说，
在服务器起来几秒之后：

```
启动检查：0 个问题，2 条提示，3 条正常现象
  正在 0.0.0.0:34197 (UDP) 上等待玩家
  RCON 绑在 127.0.0.1:27015，外部无法访问
  音频已关闭 —— headless 服务器的正常现象
  没有 Steam 云端玩家数据 —— headless 服务器的正常现象
  没有个人蓝图库（blueprint-storage-2.dat）；headless 服务器没有本地玩家，
  所以它从来不会创建这个文件。无害，而且每次启动都会出现。
  不要手动创建这个文件 —— 空文件会被当成损坏的。
```

有好几行 Factorio 的正常输出写着 "not found"——蓝图库回退、缺失的云端数据——
而如果没有一个地方把这件事说清楚，每个服主都会去查一遍。

蓝图那条建议之所以这么具体，是因为它回答的是下一个问题：
在 2.0.77 上实测，headless 服务器即使干净关闭也从不写
`blueprint-storage-2.dat`，而为了让提示消失去创建一个空文件，会得到
`Loading local blueprint storage failed: Couldn't read from input file`——
比它本来想消掉的那条消息更糟。

问题排在最前面，而一次干净的启动什么都不说。
报告是等一小会儿而不是在「进入游戏」标记那一刻触发的，
因为有些值得报告的行——首先是 RCON 的绑定地址——是在那之后才打印的。

## 测试

```bash
python -m pytest tests/ -q        # 358 项
```

解析测试跑的是从真实服务器采样下来的输出，在
`docs/factorio_output_samples.txt`。进程测试驱动的是 `tests/fake_factorio.py`，
一个复刻了真实二进制行为的替身——包括「stdin 收到 EOF 也不死」，
而这正是 FactorioReforge 从不关那根管子的原因。

有些测试的存在是为了守住一个承诺而不是为了抓一个 bug：
没有任何源文件发出 `/c`、每个自带插件都带两种语言且占位符一致、
以及没有任何翻译目录含 YAML 布尔键。
