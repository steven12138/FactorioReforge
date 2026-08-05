# 自带插件

`plugins/` 里自带二十一个插件，每个都是一个包，
自己拥有自己的代码、配置和翻译。它们都可以被重载、卸载或直接删掉，
不会影响框架——没有哪一个是特殊的。

它们各自的命令在 [命令参考](commands_zh.md)；这一页讲的是每个插件**是什么**、
以及怎么配置。想自己写一个见 [写插件](writing-plugins_zh.md)。

```
!!FR plugin list       加载了哪些，带版本和命令
!!FR help <插件>       单个插件详情
!!FR plugin reload <id>
```

配置在 `config/<插件id>/config.json`，首次加载时用默认值创建。
缺失的键会从默认值补上，所以新版本加了一个设置不会逼你手动改文件。

---

## 存档管理

备份属于框架而不是插件——`!!qb` 永远都在。
槽位模型和一次回档具体做了什么，见 [备份与回档](architecture_zh.md#备份与回档)。

## auto_snapshot

定时备份，以及最后一名玩家离开时备份。

它的备份进的是**自动那一圈槽位** —— `a1`、`a2`，`!!qb` 里单独列出 ——
所以定时器永远不会花掉一个人手动留下的槽位。回档用 `!!qb back a2`。

无人在线时定时器还会跳过。开着 `auto_pause` 的话，空服的世界根本没动过，
那些备份会是一模一样的重复。

```jsonc
{
  "interval_minutes": 30,
  "on_last_player_left": true,
  "skip_when_empty": true
}
```

## telegram_bridge

聊天双向转发，并把服务器搬到你手机上。

```jsonc
{
  "token": "",                  // 从 @BotFather 拿
  "allowed_chat_ids": [],       // 不填这个什么都不会工作
  "admin_user_ids": [],         // 可以备份、回档、重启
  "owner_user_ids": [],         // 可以用 /cmd
  "forward_chat": true,
  "forward_join_leave": true,
  "forward_death": true
}
```

怎么拿到自己的 chat id：填好 `token`，重载，给机器人发一条消息，
然后从日志里读那个 id——消息被拒绝并记录下来，
而记下来的正是你要填进 `allowed_chat_ids` 的东西。

`/rollback` 永远需要单独一次 `/confirm`。`/cmd`
能执行任意命令，因此只对 `owner` 开放且会先确认。
未授权的 chat 会被静默丢弃，而不是告诉对方被拒绝了。

这个桥接本身还是一个其他插件可以注册进来的**服务**，
所以插件不用 import `telegram` 也能被 Telegram 驱动 ——
见 [Telegram 子插件](writing-plugins_zh.md#telegram-子插件)。

## mod_manager

从 [mod 门户](https://mods.factorio.com) 搜索、安装、更新、删除 mod，
在聊天里或在 Telegram 里（`/mods` `/modsearch` `/modinfo` `/modinstall`
`/modremove` `/modupdates`）都行。

凭据来自 `config/mod_manager/config.json`，取不到则回退到你
`~/.factorio/player-data.json` 里的 `service-username` 和 `service-token`。
浏览不需要凭据，下载需要一个拥有本游戏的账号。token 从不被打印或回显。

三件容易做错、而它处理掉了的事：

**版本过滤。** 它在加载时问二进制文件要 `--version`，
只提供为那个 Factorio 版本构建的发布版。这不是锦上添花：
把 flib 0.17.2（为 2.1 构建）装到 2.0.77 的服务器上，
下次启动服务器就以退出码 1 挂掉。

**Factorio 会覆盖 `mod-list.json`。** 运行中的服务器把 mod 列表存在内存里，
停止时写出自己那一份，把底下被改过的东西全丢掉。
所以这个插件把自己的意图另外记下来，在 `on_server_stop`——也就是进程真的没了之后
——再重新应用一遍。

**只装必需依赖。** `?` 和 `(?)` 的条目会被跳过——
把一个大型整合 mod 的所有可选依赖都装上，会拖进来几十个不相干的 mod。

搜索是在完整 mod 列表的本地缓存上过滤的（约 22500 条、13 MB、抓一次约 14 秒、
按 TTL 刷新），因为门户没有文本搜索接口。
完全匹配和前缀匹配排在子串匹配前面，同分时按下载量。

## version_manager

`!!version` —— 这台服务器跑的是哪个 Factorio 版本，以及怎么在不丢世界的前提下换掉它。

```
!!version
  当前 Factorio 2.0.77 (build 84539, linux64, headless)
  它能打开 1.0.0-0 到 2.0.77-0 之间的存档
  当前存档是 2.0.77-0 写的
  可切换：当前 2.0.77，已装 2 个版本
```

**存档格式升级是一扇单向门。** 2.0.78 一旦加载并保存过这个世界，
2.0.77 就再也打不开它了。所以升级是平常事，降级根本不是"换版本"这一件事 ——
它是换版本**加上**回档，两者必须一起做，否则服务器会起在一个它读不了的世界上。
下面所有设计都是从这一条推出来的。

**这里没有任何硬编码的兼容规则。** 二进制自己会声明它的窗口，2.0.77 实测：

```
$ ./bin/x64/factorio --version
Version: 2.0.77 (build 84539, linux64, headless)
Map input version: 1.0.0-0
Map output version: 2.0.77-0
```

而存档在 `level-init.dat` 的前八个字节里声明自己的版本，四个小端 `uint16` ——
`02 00 00 00 4d 00 00 00` 就是 2.0.77-0。两者一对，
能不能换就是**在停服之前**算出来的一道算术题，
而不是靠试出来的 —— 试一次的代价是一次停服、一次失败启动和一次回档。

**版本是目录，当前版本是软链。**

```
server/
├── versions/2.0.77/     解包出来的树：bin/ data/
├── versions/2.0.78/
├── shared/              saves/ mods/ config/ server-settings.json …
└── factorio -> versions/2.0.78
```

这样回滚的代价就只是翻一下软链：本地、瞬间、断网也能做 ——
而这恰恰就是"新版本起不来"时你所处的境地，原地覆盖升级在那一刻只能去联网重下。
世界之所以要软链回每一棵版本树里，是因为 Factorio 从可执行文件推算数据路径
（`write-data=__PATH__executable__/../..`），
不这么做的话 2.0.78 写出来的存档会落在 `versions/2.0.78/saves` 里，
一旦回滚就凭空消失。而软链保持在原来的路径上，
所以 `working_directory` 和 `start_command` 一个字都不用改。

**下载和切换是分开的。** `install` 不需要停机、要几分钟；`use` 需要停服、只要几秒。
合成一条命令的话，一次失败的下载会让服务器既停着又是坏的。

`use` 只是预备，`confirm` 才动手，和 `!!qb back` 一样。停服之前先做检查：
世界是什么版本、目标版本自称能打开到哪、有没有 mod 是给别的 `factorio_version`
做的、谁在线并且即将掉线。属于"拦截"的直接拒绝，属于"提醒"的打出来然后继续。

**资料片不是隐患。** `space-age`、`quality`、`elevated-rails`
都在 `data/` 里面，和本体锁步 —— 2.0.77 的 `data/space-age/info.json` 写的就是
`"version": "2.0.77"` —— 所以换版本会把它们和本体一起原子换掉。
需要检查的只有 `mods/` 里的第三方 mod，而它们钉的是 `major.minor`，
所以补丁级升级不可能让任何 mod 失效，而 2.0 → 2.1 会让它们**同时**全部失效。

**换版本之前的那个世界，会进一个专属的固定备份槽位** `pre-upgrade`，
不参与任何轮转。它是第二个 `overwrite` 槽，存在的理由完全相同，
区别在于 `overwrite` 会被下一次回档用掉，而这一个必须活得比那更久 ——
它是唯一一份旧版本还打得开的世界。这也正是降级之所以能被表达出来的原因：
`!!version use 2.0.77 with-save pre-upgrade` 把二进制和世界作为一次操作一起退回去。

如果新版本没起来，软链退回去、世界退回去、服务器重新起来，全程不需要任何人敲命令。

没有做过布局改造的安装依然能报告一切 —— 当前版本、存档版本、官方放出了什么。
只有"切换"需要那套布局，而 `!!version adopt` 会在停服状态下就地完成改造，
中途任何一步失败都会把前面几步全部撤销。

```jsonc
{
  "versions_directory": "",     // 留空：就放在当前安装旁边
  "build": "headless",
  "distro": "linux64",
  "countdown_seconds": 15,
  "confirm_window_seconds": 120
}
```

## map_render

`!!map` 画出世界并把图送出去——送到 Web 面板、Telegram 或文件。

Factorio **在 headless 上截不了图**。`game.take_screenshot`
在那边是存在的，调用也不报错，但不会产生任何文件，因为进程里根本没有渲染器。
所以这张地图不是**截**出来的，是**画**出来的：Lua 那边一个 tile 返回一个字符，
图在这边合成，包含地形、每一棵树、每一块矿和每一个建筑物的真实位置。
一个 409 区块的完整世界，一 tile 一像素是 421 KB、约半秒。
PNG 编码器是直接用 `zlib` 和 `struct` 写的，所以不需要装任何图像库。

直接读存档文件这条路考虑过并被否决了：Factorio
的存档是没有文档的二进制块，不像 Minecraft 的 NBT region 有 unmined
这类工具能解析，所以不逆向格式就无从读起。

采样步长由世界尺寸和 `max_dimension` 算出来，
所以巨型基地会降级成一张更粗的地图，而不是拒绝生成或吐出一亿像素的 PNG。

```jsonc
{
  "max_dimension": 2048,        // 像素；采样步长由它推出
  "send_to_telegram": true
}
```

地图发到 Telegram 时是**文件**而不是照片——
Telegram 会重新压缩照片，而一 tile 一像素恰恰是那种一压就毁的细节。

## crash_doctor

维护一个滚动的输出缓冲区，当服务器非预期退出时，
拿它去匹配真实的故障特征，说出原因**以及**修它的命令。
开发过程中真实发生的那次 mod 不兼容，它报的是：

```
服务器以退出码 1 退出：mod 'flib' 加载失败
  Incompatible Factorio version (current: 2.0, required: 2.1); Dependency base >= 2.1.0 is not satisfied
  试试：!!mod remove flib
```

两条规则让匹配真正有用。一是最新的故障优先，
这样缓冲区里残留的旧错误不会盖住刚发生的这一个。
二是在同一个故障块里，点名罪魁祸首的那一行优先于描述症状的缩进行——
否则每次 mod 故障都会被报成「版本不兼容」而不说是哪个 mod。

它能识别 mod 加载失败、依赖不满足、端口被占用、存档损坏、内存耗尽，
以及第二个实例占着锁文件。`!!why` 重放上一次诊断。

## server_admin

`!!server` 在聊天里读写 `server-settings.json`——
名称、描述、密码、人数上限、可见性、自动存档、暂停、账号校验。

写入走临时文件 + rename，因为一个被截断的 `server-settings.json`
会让服务器**根本起不来**。Factorio 只在启动时读一次这个文件，
所以每次改动都会说需要重启，而不是暗示改动已经生效了。

`!!server commands true` 会被拒绝，见
[命令参考](commands_zh.md#服务器设置--server)。

## server_utils

`!!here` `!!info` `!!list` `!!seen` `!!stats` `!!tp`，
移植自 MCDReforged 那边大家最想念的几个插件。

`!!here` 发出去的是可点击的 `[gps=]` 标签，
它会在所有人的地图上闪一下那个位置，**同时**钉一个图表标记让那个点留在地图上。
见 [富文本](writing-plugins_zh.md#富文本)。

```jsonc
{
  "enable_teleport": false,
  "teleport_permission": "admin"    // admin | user
}
```

**`!!tp` 默认关闭。** 传送跳过了走路、火车和危险，
而整个游戏正是围绕这些设计的，所以一个服要不要它是服主的决定，不该是默认值。
关闭时这条命令**根本不会被注册**，这比注册了再在调用时拒绝更彻底。

## warp

命名地点，用可点击的 `[gps=]` 标签喊出来，并钉成图表标记。
**不会传送任何人**——它是 `!!tp` 里「提供信息」的那一半，
没有「破坏平衡」的那一半，所以在 `!!tp` 默认关闭时它默认是开的。

```jsonc
{ "manage_permission": "admin" }    // 谁可以设置和删除
```

## blueprints

服务端共享蓝图库。手上拿着蓝图时 `!!bp save <名字>` 存的就是它，
`!!bp get <名字>` 直接把它放到别人手上。

这个动作和游戏自带蓝图库里的习惯是一样的，所以手不空时 `!!bp save`
就是这个意思。手是空的才回退成抓取你周围的区域 —— 也就是它原来一直的行为。
蓝图书、拆除规划器、升级规划器也都能存，它们导出走的是同一个调用。

有两个细节决定手感对不对。从**个人蓝图库**里拿出来的蓝图在光标里是
`cursor_record` 而不是 `cursor_stack`，只读 stack 会让最常见的持有方式
看起来像空手。以及，光标里已经拿着东西时绝不覆盖 ——
那会毁掉你正在用的工具 —— 而是放进物品栏并告诉你放哪了。

完全在服务端通过一个临时物品栏完成，所以客户端不需要装任何东西。
蓝图字符串在存入时就校验，畸形的蓝图在保存那一刻被拒绝，
而不是等到有人来取的时候才失败。

```jsonc
{ "radius": 32, "manage_permission": "user" }
```

## calculator

`==1400/7.5` 算数，`!!ratio` 回答那个真正会让人去开浏览器标签页的问题：
造这个东西，需要什么。

```
!!ratio electronic-circuit 5/s
electronic-circuit 5/s —— 共 8.33 台机器，1.25 MW
  3.33 台 assembling-machine-3  electronic-circuit  5/s
  5.00 台 assembling-machine-3  copper-cable        15/s
  需要投入：copper-plate 7.5/s, iron-plate 5/s
  产出相当于 0.33 条 transport-belt（15/s）
```

**配方来自你的服务器，不是来自这个仓库。** 别的 Factorio 计算器都自带一份
从某个版本扒下来的数据，所以它们都有一个版本下拉框，并且都不知道你装了什么 mod。
这个插件通过 RCON 读 `prototypes.recipe`，所以算的就是你服务器真会跑的数。
代价是 `!!ratio` 需要服务器在跑；`==` 算数不需要。

**算法和那几个成熟计算器是同一套。** 配方变成一个矩阵 ——
行是物品、列是配方，产物为正、原料为负 —— 速率则是一个线性规划，
用精确有理数的单纯形法解出来。递归展开是不够的，而且这不是假想：
高级石油裂解、重油裂化、轻油裂化产出的流体互相重叠，
各跑多少并不是任何单条配方的性质；而 Kovarex 会消耗自己产出的东西，
递归展开会在那里无限下去。这两种情况在同一次求解里一起解决。见
[产能比例与求解器](architecture_zh.md#产能比例与求解器)。

**别的计算器里最难受的一步是把物品名打出来**，而这个插件跑在游戏里面，
所以大多数时候根本不用打：

| 你做什么 | 它算什么 |
|---|---|
| 鼠标指着一台组装机，敲 `!!ratio` | 那台机器里设的配方 |
| 手上拿着一个物品，敲 `!!ratio` | 你光标里的那个物品 |
| `!!ratio [item=iron-gear-wheel]` | 游戏内图标选择器插进来的图标 |
| `!!ratio green circuit 30/m` | 空格、大小写和俗称都认 |

**物品名由游戏自己翻译。** 一份写满 `electronic-circuit`、`assembling-machine-3`
的方案，对大多数看它的人来说是读不懂的；而在服务端做翻译并不值得 ——
Factorio 支持 **LocalisedString**，由每个客户端在本地渲染，
所以同一行对一个玩家是中文、对另一个是英文，用的还是游戏自带的词条。
终端和 Telegram 拿到的仍是 prototype id，因为那边没有 Factorio 来渲染。

**机器是你造得出来的那一台。** 以前不管存档研究到哪一步，方案一律写
`assembling-machine-3` —— 因为机器列表来自 prototype，而 prototype
根本不知道科技树的事。现在默认取「其物品能被已研究配方产出」的最快机器，
或者地图上已经立着的（既然有人已经造出来了）。想钉死就写进 `machines`，
或者单次提问用 `machine=`。

```jsonc
{
  "expression_prefix": "==",
  "announce_expression_results": true,   // 反正大家都看见问题了
  "machines": [],                        // 留空：用你造得出来的最好的
  "only_researched_machines": true,
  "belt": "transport-belt",
  "default_rate": "1/s",
  "only_researched": true,               // 只用这个存档已经研究出来的配方
  "raw_items": ["steam"],                // 展开到这里就停
  "raw_costs": {"water": 0.01},
  "exclude_categories": ["recycling", "recycling-or-hand-crafting", "parameters"],
  "exclude_patterns": ["-barrel"],
  "max_steps": 14
}
```

**`only_researched` 是让答案真正能用的那个开关**，而它是被一台真实的
Space Age 服务器逼出来的。不开它的时候，问电路板，求解器回答的是
**用铸造厂从熔融铁水铸造** —— 按每单位矿算确实更省，
但在你上 Vulcanus 之前根本够不着；问石油气，它搭了一条 Gleba
的 bioflux 链去制硫。限制成 `force.recipes[name].enabled`
之后，答案从「理论最优」变成「你现在就能去建的东西」。
想按全部配方算就在问题里加 `all=1`；
如果是回退到全配方才算出来的，回复里会说明这一点。

另外三个默认值也都来自同一次实测：

- **`exclude_categories`** —— 那台服务器 659 条配方里有 310 条是回收，
  每一条都把被拆解的东西列为产物。不排除的话，
  造电路板最便宜的办法是回收废料。
- **`raw_items: ["steam"]`** —— 蒸汽来自锅炉，而锅炉不是一条**配方**。
  配方里唯一能产出蒸汽的是酸中和，于是求解器高高兴兴地搭了一条硫酸链，
  就为了把蒸汽当副产物拿到。
- **`raw_costs`** —— 唯一一个属于判断而不是游戏事实的数字，而且绕不开：
  裂化重油和多抽原油都能产出石油气，哪个更划算取决于你的地图。
  把水和原油算成一样贵，求解器就会干脆不裂化。

算数是把解析出来的语法树按节点类型白名单走一遍算出来的。
绝不对玩家输入调用 `eval` —— 不是沙箱里的 `eval`，
也不是去掉 `__builtins__` 的 `eval`，而是根本不调用 ——
因为输入来自任何一个能在聊天框里打字的人。
剩下的风险是体量而不是权限，所以 `9**9**9` 是在**执行前**被拒绝的。

信标是刻意不建模的。2.0 给信标加了随数量递减的 `profile`，
而一个悄悄算错的信标数量，比一个明说自己不知道的计算器更糟；
`speed=` 留给已经自己算明白的人。

## ups_watch

`!!ups` 看更新率，`!!ups why` 看是什么在吃掉它。

Factorio 不是靠崩溃失败的，它是靠变慢失败的：60 UPS 变成 55、再变成 40，
等到有人说「这游戏怎么有点卡」时，它已经连着下滑一个星期了。
游戏没有 UPS 接口，所以这里用 `game.tick` 对墙钟采样 ——
两次采样在已知时间内的差值**本身**就是更新率。

有两条规则决定了它是「大家会看的告警」还是「大家会屏蔽的告警」：

- **暂停的服务器不是变慢的服务器。** 在 2.0.77 上实测，开着 `auto_pause`
  的空服读数是 **0.5 ticks/s**。无人在线时采到的样本会被丢弃，
  而不是在凌晨四点报一次「工厂崩溃」。
- **窗口取的是中位数，不是平均数。** 自动存档或者一次区块生成只会拉低**一个**样本；
  60、60、30、60、60 的平均值是 54，会被报成工厂变慢，而它的中位数是 60。

```jsonc
{
  "sample_interval_seconds": 60,
  "warn_below_ups": 55.0,
  "critical_below_ups": 45.0,
  "window": 5,
  "history_length": 240
}
```

## alerts

`!!alerts` —— 遭袭，以及游戏自己的警报。

两套探测，因为 Factorio 的警报系统属于**玩家**：`player.get_alerts`
是按玩家来的，只有人连着的时候才有意义。那正好覆盖了「有人正盯着屏幕」的情况，
而漏掉了真正值得把人叫醒的那种。

- **警报**从在线玩家那里读出来转发，按区块去重 ——
  同一座炮塔每次报出来的坐标都会差一点点。
- **损失**来自定时统计本势力建筑的数量。除了遭袭，没有什么会让四十段墙消失；
  计数很便宜，空服也能用，而且不需要配套 mod。它说不出是什么来袭，
  但它能说「你掉了 40 段墙和 6 座炮塔」—— 这句话足以让人爬起来登录。

传送带这类玩家本来就经常拆的东西不计入，掉一个也不报 —— 那是有人在重建。

```jsonc
{
  "poll_interval_seconds": 60,
  "loss_threshold": 5,
  "watch_types": ["wall", "gate", "ammo-turret", "..."],
  "ignore_alerts": ["no_material_for_construction"]
}
```

## trains

`!!trains` 看整体，`!!trains stuck` 看是哪一列堵着。

只要列车多过几列，「东西怎么没运到」就是每周都要问一次的问题，
而答案通常只有两种：找不到路径的列车，或者在站台上停到已经不会回来的列车。
两者都写在 `train.state` 里，而两者在游戏里不沿着铁路走一遍都看不见。

无路径是立刻就错的，所以立刻报。等待类状态短时间内是正常的，
只有超过 `stuck_after_minutes` 才报；而且列车一换状态计时就清零 ——
动过的列车不算卡住，哪怕它又停下了。

`force.get_trains()` 在 2.0 被移除了 —— 实测会抛
*"LuaForce doesn't contain key get_trains"*。这里用的是
`game.train_manager.get_trains{}`。

```jsonc
{ "poll_interval_seconds": 120, "stuck_after_minutes": 15, "max_reported": 5 }
```

## power

`!!power` —— 蓄电池电量与供电缺口。

Factorio 的供电失败是无声的：蓄电池放空，机器变慢而不是停下，
第一个看得见的症状是科技研究得比预期慢。太阳能基地上，天亮时的电量就是全部故事，
所以看的就是它 —— 在 Lua 那边求和，对照 prototype 的 `buffer_capacity`
（实测 5 MJ），这样巨型基地也只花一次查询，而不是一个蓄电池一次。

阈值只在下穿和回升时各报一次 —— 电量卡在 29% 不是每两分钟一条新闻。

```jsonc
{ "poll_interval_seconds": 120, "charge_thresholds": [0.3, 0.1] }
```

## research

`!!research` 看实验室在研究什么；`add`、`cancel`、`search` 用来改。

科技队列通过 API 改起来很容易，而在矿场里或者在手机上根本够不着 ——
这就是它填的空。前置条件不满足的科技是**游戏**拒绝的，
这里把游戏的拒绝原样转达，而不是自己去预测、然后预测错。

`research_queue_enabled` 在 2.0.77 上不存在 —— 那是 1.1 的属性。

```jsonc
{ "manage_permission": "helper", "announce_changes": true }
```

## vote

`!!vote start <问题>`，然后大家 `!!vote yes` / `!!vote no`。

重启、回档、关灯，都是一个管理员可以决定、而好几个玩家要承受的事情。
计票规则就是这个插件的全部：

- **只有发起时在线的玩家能投票。** 中途进来的人没听到问题，
  而且允许他们投票，就等于允许靠喊人来赢一次投票。
- **结果一旦无法改变就立刻结束**，而不是把一个已经定局的计时器跑完 ——
  后者正是大家学会无视投票的原因。
- **不表态算反对。** 「在场多数」这个说法只有在「不回答等于不同意」时才有意义。

投票结束只会发出 `vote.finished` 事件，别的什么都不做。
把「通过」接到真正的重启上，是另一个需要**刻意**做出的选择，不是默认行为。

```jsonc
{ "duration_seconds": 120, "majority": 0.5, "minimum_voters": 2 }
```

## mail

`!!mail <玩家> <留言>` —— 给不在线的人留言。

玩家分布在不同时区的服务器，会丢掉他们之间说的绝大部分话。
信箱是最老的解法，而且在这个框架上几乎不花什么成本 ——
进服事件和插件存储都是现成的。

送达会在进服后等几秒：刚连上的玩家还在看加载界面，
这时候打出来的消息会被进服刷屏顶掉。而如果对方**正**在线，就立刻送到 ——
为了让他听见一句他站在那儿时说的话，还要等他重连一次，这很荒谬。
信箱满了丢的是最老的一条而不是拒收最新的一条，
因为信箱满通常意味着这个人一个月没上线了。

```jsonc
{ "deliver_after_seconds": 8, "max_per_player": 20, "max_length": 200 }
```

## production

定时采样 `get_flow_count`，攒出跨会话保留的产量历史——
Factorio 自带的产量图是每客户端的，你每次连进去都是从空的开始。

在聊天里渲染成 Unicode 火花线，在 Web 面板里渲染成 SVG，两边都不用画图库。

```jsonc
{
  "items": ["iron-plate", "copper-plate", "electronic-circuit"],
  "sample_interval_minutes": 5,
  "history_length": 288
}
```

## world_watch

进化度和污染提醒，加上科技和火箭里程碑。
做成一个插件是因为两者本来就是同一套机制：轮询、和上次比较、播报变化。

提醒按**跨过阈值**触发一次，而不是每次轮询都触发——
进化度停在 51% 不是每五分钟一条新闻。状态会持久化，
所以重启不会把这个世界历史上所有里程碑重播一遍。

```jsonc
{
  "evolution_thresholds": [0.25, 0.5, 0.75, 0.9],
  "pollution_thresholds": [10000, 50000],
  "announce_research": true,
  "announce_rockets": true,
  "poll_interval_minutes": 5
}
```

## leaderboard

`!!top` 在线时长排行——是准确的，因为 Factorio 本来就按玩家记
`online_time`——外加全 force 的击杀和产量总计。

制造物品数和步行距离是刻意没有的：Factorio 不按玩家记这些，
而排行榜上一个编出来的数字比没有排行榜更糟。

## join_motd

玩家进服时用实时数据拼一句欢迎语。

```jsonc
{
  "message": "欢迎 {player}！当前 {online}/{total} 人在线，第 {day} 天，进化度 {evolution}"
}
```

可用占位符：`{player} {online} {total} {uptime} {day} {evolution} {pollution}
{research} {snapshots} {last_snapshot}`。

## web_panel

`127.0.0.1:8080` 上的只读状态页，`/api` 是 JSON，
`/map.png` 是最新地图，还有产量曲线。

刻意做成只读：没有停服按钮、没有回档、没有控制台。
一个没有认证也没有写入路径的页面，没法被利用来搞破坏。
从机器外面控制请走 Telegram，那边是认证过的。

```jsonc
{ "host": "127.0.0.1", "port": 8080 }
```

host 请保持在 localhost，然后用 SSH 隧道访问
（`ssh -L 8080:127.0.0.1:8080 你@服务器`）。
绑 `0.0.0.0` 等于把你的世界地图和玩家列表公开给任何扫到这个端口的人。
