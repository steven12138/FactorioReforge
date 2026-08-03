# 命令参考

每条 `!!` 命令在三个地方都能用——FactorioReforge 终端、游戏内聊天框，
以及（桥接暴露出来的那些）Telegram。在终端里输入的**不以 `!!` 开头**的内容
会直接进 Factorio 的 stdin，所以 `/players`、`/promote alice`
和你一直以来用的完全一样。

各插件注册了哪些命令，运行时也查得到：

```
!!FR help              全部命令，按提供它的插件分组
!!FR help warp         单个插件：版本、作者、做什么、有哪些命令
!!FR plugin list       全部插件，带版本和命令
```

## 权限等级

`guest(0) → user(1) → helper(2) → admin(3) → owner(4)`，
存在 `config/permission.yml`。新玩家拿到 `config.yml` 里的
`permission.default_level`，默认是 `user`。

FactorioReforge 终端**永远**是 `owner`，且不可配置：
握着那个终端的人本来就能直接停掉进程，装作不是只是演戏。
在 Telegram 里，等级来自桥接配置里的用户 id 名单。

没权限的命令会明说没权限。而**根本不存在**的命令——比如传送被关掉时的
`!!tp`——说的是它不存在，因为那就是事实。

## 框架 —— `!!FR`

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!FR help` | guest | 全部命令，按插件分组 |
| `!!FR help <插件>` | guest | 单个插件详情 |
| `!!FR status` | user | 服务器、RCON、插件和备份状态 |
| `!!FR plugin list` | admin | 已加载插件；文件有改动的会被标出来 |
| `!!FR plugin reload <id>` | admin | 重载一个插件 |
| `!!FR plugin unload <id>` | admin | 卸载一个插件 |
| `!!FR reload` | admin | 重载所有文件有改动的插件 |
| `!!FR server start\|stop\|restart` | admin | Factorio 生命周期 |
| `!!FR server kill` | owner | SIGKILL——上次存档之后的一切都没了 |
| `!!FR permission list` | admin | 谁是什么等级 |
| `!!FR permission set <玩家> <等级>` | owner | 改某人的等级 |
| `!!FR lang` | user | 当前语言，以及各语言还缺什么 |
| `!!FR lang missing <语言>` | user | 具体还有哪些 key 没翻 |
| `!!FR lang set <语言>` | admin | 切换语言，立即生效并写入配置 |
| `!!FR exit` | owner | 停服，然后退出 FactorioReforge |

## 备份 —— `!!qb`

名字来自 [QuickBackupM](https://github.com/TISUnion/QuickBackupM)，
命令集也照它来，这样从 Minecraft 那边过来的手感是连着的。
`!!save` 仍然可用、也不会被删掉，只是不再是正式名字。

槽位模型、以及一次回档具体做了什么，见
[备份与回档](architecture_zh.md#备份与回档)。

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!qb` / `!!qb list` | guest | 所有槽位，带时间、大小和备注 |
| `!!qb make [备注]` | user | 备份到槽位 1，其余顺次下移 |
| `!!qb back [槽位]` | helper | **预备**回档（默认槽位 1），此时还什么都没做 |
| `!!qb confirm` | user | 倒计时之后执行预备好的回档 |
| `!!qb abort` | user | 取消，无论是预备中还是倒计时中 |
| `!!qb del <槽位>` | helper | 删掉一个槽位 |
| `!!qb rename <槽位> <备注>` | helper | 改备注 |

`!!qb back` 自己绝不会真的回档。它只是预备，然后 `!!qb confirm`
在聊天里倒计时，倒计时期间任何人都可以 `!!qb abort`。

**自动备份有自己独立的槽位**，列在手动槽位下面，用 `a` 开头寻址：
`!!qb back a2`、`!!qb del a3`。否则一个每半小时跑一次的定时器，
一晚上就能把整段历史挤出去 —— 而被挤掉的恰好就是某人在动手改之前
特意留的那一份。**光写数字永远指的是人手动做的那一档。**

## 服务器设置 —— `!!server`

读写 `server-settings.json`。Factorio 只在启动时读一次这个文件，
所以每次改动都会告诉你需要重启。

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!server` / `!!server show` | user | 当前设置 |
| `!!server name <文字>` | admin | 改服务器名 |
| `!!server description <文字>` | admin | 改描述 |
| `!!server password [文字]` | admin | 设置进服密码，不带参数则清除 |
| `!!server maxplayers <n>` | admin | 0 表示不限 |
| `!!server public on\|off` | admin | 是否出现在官方公开服务器列表 |
| `!!server lan on\|off` | admin | 是否在局域网广播 |
| `!!server autosave <分钟>` | admin | 自动存档间隔 |
| `!!server pause on\|off` | admin | 无人时是否暂停 |
| `!!server verify on\|off` | admin | 是否校验 factorio.com 账号 |
| `!!server commands <值>` | owner | **拒绝 `true`**，见下 |

`!!server commands true` 会让每个玩家都能用 `/c`，那会把世界永久标记为作弊。
这是「决定不再玩这个游戏」而不是一个服务器设置，
不该靠在聊天里打一个词就达成，所以它被拒绝。真要开就手动改文件。

## 玩家与世界

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!here` | user | 把你的位置作为可点击的地图标记喊出来，并钉在地图上 |
| `!!list` | user | 谁在线，带在线时长 |
| `!!info [玩家]` | user | 在线时长、权限等级、位置 |
| `!!seen <玩家>` | user | 在线时长，以及上次在线是什么时候 |
| `!!stats` | user | 进化度、污染、科研、世界运行时间 |
| `!!tp <玩家> <目标\|x y>` | 可配置 | 传送——**默认关闭** |
| `!!top [time\|kills\|built]` | user | 排行榜 |
| `!!watch` | user | 进化度、污染、科研和火箭状态 |

`!!tp` 默认不开，除非你在 `config/server_utils/config.json` 里设
`enable_teleport: true`——传送跳过了走路、火车和危险，
而整个游戏正是围绕这些设计的。见 [`server_utils`](plugins_zh.md#server_utils)。

## 命名地点 —— `!!warp`

是地点，不是传送：**不会移动任何人**。

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!warp` / `!!warp list` | user | 所有命名地点 |
| `!!warp <名字>` | user | 把某个地点作为可点击标记喊出来 |
| `!!warp set <名字>` | 可配置 | 给你当前位置起个名 |
| `!!warp del <名字>` | 可配置 | 删掉一个 |

## 蓝图 —— `!!bp`

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!bp list` | user | 蓝图库 |
| `!!bp info <名字>` | user | 尺寸、内容、谁存的 |
| `!!bp get <名字>` | user | 直接放到你手上 |
| `!!bp save <名字>` | 可配置 | 保存你手上拿着的蓝图 |
| `!!bp save <名字> <半径>` | 可配置 | 改成把你周围的区域做成蓝图 |
| `!!bp del <名字>` | 可配置 | 删掉一个 |

手上拿着蓝图时 `!!bp save x` 存的就是**它**；手是空的才回退成抓取你周围的区域。
蓝图书、拆除规划器、升级规划器都支持。`!!bp get` 会直接放到你光标上，
除非你手上已经拿着东西——那样会放进物品栏并告诉你。

## Mod —— `!!mod`

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!mod search <关键词>` | user | 搜索 mod 门户 |
| `!!mod info <名字>` | user | 详情和依赖 |
| `!!mod list` | user | 已安装的 |
| `!!mod install <名字> [版本]` | admin | 下载、安装并启用 |
| `!!mod remove <名字>` | admin | 删掉 |
| `!!mod enable\|disable <名字>` | admin | 只开关，不删 |
| `!!mod updates` | admin | 哪些有新版本 |
| `!!mod refresh` | admin | 重新抓取门户的 mod 索引 |

只会提供为你这个 Factorio 版本构建的发布版——见
[`mod_manager`](plugins_zh.md#mod_manager)。

## 计算器 —— `!!ratio`

算数，以及用你服务器真正在跑的配方解出来的产能比例。见
[`calculator`](plugins_zh.md#calculator)。

| 命令 | 等级 | 作用 |
|---|---|---|
| `==<表达式>` | user | 在聊天框里算数：`==1400/7.5` |
| `!!calc <表达式>` | user | 同上，用于终端和 Telegram |
| `!!ratio [物品] [速率]` | user | 造它要多少机器、投入、传送带和电 |
| `!!ratio refresh` | helper | 丢掉配方缓存，下次重新从游戏里读 |
| `!!recipe [物品]` | user | 单条配方：时间、原料、单台产率 |
| `!!belt <速率>` | user | 这个速率各档传送带各要几条 |

不写物品时，`!!ratio` 和 `!!recipe` 用**你鼠标正指着的那台机器**里设的配方，
指不到就用你手上拿着的东西。游戏内图标选择器插进来的
`[item=iron-plate]` 可以直接当参数用，空格、大小写和俗称也都认：
`!!ratio green circuit 30/m`。

可选项写成 `key=value`，放在这一行的任何位置：

| 选项 | 作用 |
|---|---|
| `30/m`、`5/s`、`90/h` | 速率。不写单位就是每秒 |
| `machine=foundry` | 优先用某台机器，排在配置的列表前面 |
| `prod=20` `speed=50` | 模块效果，按百分比 |
| `modules=speed-module-3*4` | 同上，但从模块真实的 prototype 里读 |
| `raw=iron-plate` | 展开到这里就停，当成外部买进来的 |
| `use=advanced-oil-processing` | 有多条配方可选时钉死一条 |
| `cost:water=0.5` | 改某种原料在求解器眼里的价值 |
| `all=1` | 按全部配方算，不限于已研究的 |

默认只用**这个存档已经研究出来**的配方，所以答案是你现在就能去建的东西。
这样造不出来的东西会作为投入列出来，并且会点名说它是还没研究的，
而不是和矿石混在一起显示。

## 监控与协作

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!ups` | user | 更新率，取最近窗口的中位数 |
| `!!ups why` | helper | 这个世界里堆了什么 —— 通常就是原因 |
| `!!alerts` | user | 现存建筑数，以及上次损失是什么时候 |
| `!!alerts check` | helper | 立刻查一次，不等定时器 |
| `!!trains` | user | 所有列车，按状态分组 |
| `!!trains stuck` | user | 找不到路的，和不再动的 |
| `!!power` | user | 蓄电池电量、发电量和余量 |
| `!!research` | user | 实验室在研究什么，后面排了什么 |
| `!!research add <科技>` | 可配置 | 排一项科技 |
| `!!research cancel` | 可配置 | 停掉当前研究 |
| `!!research search <关键词>` | user | 找到科技的准确名字 |
| `!!vote start <问题>` | 可配置 | 向玩家提问 |
| `!!vote yes` / `!!vote no` | user | 投票（发起时你得在线） |
| `!!vote cancel` | admin | 取消投票 |
| `!!mail <玩家> <留言>` | user | 给不在线的人留言 |
| `!!mail` / `!!mail clear` | user | 读自己的，或者全部丢掉 |
| `!!mail all <留言>` | admin | 给所有人留一条 |

## 产量、地图与诊断

| 命令 | 等级 | 作用 |
|---|---|---|
| `!!prod [物品]` | user | 产量，附带历史走势的火花线 |
| `!!prod top` | user | 产量最高的是什么 |
| `!!prod watch <物品>` | admin | 开始采样另一种物品 |
| `!!map` | user | 渲染世界并把图发出来 |
| `!!autosnap` | helper | 自动备份状态 |
| `!!autosnap now` | helper | 立刻备份一次 |
| `!!why` | admin | 服务器上次为什么非正常退出 |
| `!!web` | admin | Web 面板地址 |

## Telegram

这些是发给机器人的，不是在游戏里打的。等级来自
`config/telegram_bridge/config.json` 里的 id 名单。

| 命令 | 等级 | 作用 |
|---|---|---|
| `/status` `/players` | viewer | 服务器状态；谁在线 |
| `/say <消息>` | viewer | 在游戏里说话 |
| `/save [备注]` `/saves` | admin | 备份；列出槽位 |
| `/rollback <槽位>` → `/confirm` | admin | 回档，永远要第二步 |
| `/restart` `/stopserver` `/startserver` | admin | 生命周期 |
| `/cmd <原始命令>` | owner | 执行任意命令，带确认 |
| `/mods` `/modsearch` `/modinfo` | viewer | 浏览 mod |
| `/modinstall` `/modremove` `/modupdates` | admin | 改动 mod，带确认 |

插件可以在不 import `telegram` 的前提下加自己的 Telegram 命令 ——
见 [Telegram 子插件](writing-plugins_zh.md#telegram-子插件)。
