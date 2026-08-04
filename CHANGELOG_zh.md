# 更新日志

English: [CHANGELOG.md](CHANGELOG.md)

## 未发布

### 文档

- 重构。README 现在是一个入口页，原来堆在里面的参考资料拆成了
  [命令参考](docs/commands_zh.md)、[配置](docs/configuration_zh.md)、
  [自带插件](docs/plugins_zh.md)、[写插件](docs/writing-plugins_zh.md)、
  [开一个 Factorio 服务器](docs/factorio-server_zh.md) 和
  [架构](docs/architecture_zh.md)，每份都有中英文对照。
- `docs/M0-findings.md` 改名为
  [`docs/factorio-notes.md`](docs/factorio-notes_zh.md)——
  它早就不该再用一个里程碑的编号来命名了。
- 新增 [CONTRIBUTING_zh.md](CONTRIBUTING_zh.md) 和本文件。

### 新增

- **七个用于「人不在跟前」时盯服务器的插件。**
  - `ups_watch` —— `!!ups`。Factorio 不是靠崩溃失败的，是靠变慢失败的。
    用 `game.tick` 对墙钟采样。实测暂停的服务器读数是 0.5 ticks/s，
    所以无人在线时的样本会被丢弃；窗口取中位数，一次自动存档的凹陷不算崩溃。
  - `alerts` —— 遭袭，空服时也能发现。有人在线时转发玩家警报；
    其余情况靠建筑计数兜住 —— 那才是值得把人叫醒的情况，而且不需要配套 mod。
  - `trains` —— `!!trains stuck`。无路径立刻报，等待状态只有持续了才报，
    列车一动计时就清零。
  - `power` —— `!!power`。蓄电池电量在 Lua 那边求和，
    阈值只在下穿和回升时各报一次。
  - `research` —— `!!research add`，在矿场里或手机上都能排科技。
    转达游戏自己的拒绝，而不是自己预测。
  - `vote` —— `!!vote`。只有发起时在场的人能投，结果一旦定局立刻结束，
    不表态算反对。只发出 `vote.finished`，自己不执行任何动作。
  - `mail` —— `!!mail`。进服几秒后送达；对方已经在线则立刻送到。
- `factorio_reforge/core/progress.py`：给慢操作用的限流进度输出，
  `!!mod refresh` 已接入。跑得快的操作什么都不会输出。

- **`calculator`**：`==1400/7.5` 在聊天框里算数，`!!ratio` 回答造一个东西
  需要什么——多少机器、多少投入、多少条传送带、多少电。
  - 配方是从运行中的游戏里读的（RCON 读 `prototypes.recipe`），
    所以对得上你的版本和你的 mod，仓库里也没有会过期的表。
  - 速率是用精确有理数的单纯形法解一个线性规划得到的，
    和 Kirk McDonald 的计算器、FactorioLab、YAFC 最终采用的是同一套做法。
    递归展开答不了石油裂解（产物互相重叠）和 Kovarex（消耗自己的产物），
    这两种情况在同一次求解里一起解决。
  - 不写物品名时，用你鼠标指着的那台机器和它里面设的配方，
    或者你手上拿着的东西。游戏内图标选择器插进来的
    `[item=iron-plate]` 可以直接当参数。
  - 小问题用 `!!recipe` 和 `!!belt`。
  - 算数是按节点类型白名单走一遍语法树算出来的；
    绝不对玩家输入调用 `eval`，`9**9**9` 是在执行前而不是执行后被拒绝的。
- **`blueprints`**：`!!bp save <名字>` 现在保存的是**你手上拿着的蓝图**，
  这和游戏自带蓝图库里的习惯动作是一致的。手是空的时候仍然抓取你周围的区域。
  蓝图书、拆除规划器、升级规划器也都支持；`!!bp get` 会直接放回你光标上
  而不是埋进物品栏 —— 除非你手上已经拿着东西，那个绝不会被覆盖。
- `scripts/probe_prototypes.py` 拿计算器要读的那些 prototype API
  去对一台运行中的服务器做检查，这样某次版本更新把名字挪走时，
  暴露出来的是一行失败，而不是一份悄悄少了一台机器的方案。
- **`server_admin`**：`!!server` 在聊天里读写 `server-settings.json`——
  名称、描述、密码、人数上限、可见性、自动存档、暂停、账号校验。
  写入走临时文件 + rename，因为一个被截断的 `server-settings.json`
  会让服务器根本起不来。
- `!!FR help <插件>` 显示单个插件详情；`!!FR help` 按插件分组；
  `!!FR plugin list` 显示版本、描述和已注册的命令。
  现在不用读源码就能发现一个插件能干什么。
- 插件可以给帮助条目注册 `detail` 行，显示在它自己的帮助里。
- `allow_commands` 已经是 `true` 时，启动检查会警告。

### 变更

- **`!!help` 和 `!!FR help` 一样能用。** 帮助是一个人在还不知道有哪些命令时会敲的东西，
  把它做成最长的那一条是反过来的。同样的索引、同样的翻页、同样的搜索。

- **`!!FR help` 现在是索引，不是全文。** 二十一个插件时，原来的分组形式超过六十行，
  于是排在字母表后面的插件被挤出聊天框顶部，实际上无法被发现。
  现在是每个插件一行 —— id、命令、做什么 —— 并且**只对玩家分页**，
  因为终端和 Telegram 有滚动历史而聊天框没有。
  `!!FR help <页码>` 是翻页，`!!FR help <插件>` 是那个插件，
  其余一律当搜索：`!!FR help ratio` 能找到计算器。
- 索引里的插件摘要优先取插件自己 `lang/` 目录里的 `description` 键，
  所以最宽的那一列不再永远是英文。`PLUGIN_METADATA["description"]` 仍是兜底。

- **`!!save` 改名为 `!!qb`**，和
  [QuickBackupM](https://github.com/TISUnion/QuickBackupM) 统一 ——
  命令集本来就是照它做的。`!!save` 仍然可用（一个悄悄让备份命令失效的改名
  是最糟糕的那种改名），而且两个名字共用同一个预备槽位，
  所以在一个名字下预备、在另一个名字下确认，是同一次回档。
- **自动备份有了自己独立的一圈槽位**，寻址写作 `a1`、`a2`。
  原来共用一圈时，一个每半小时跑一次的定时器一晚上就能把整段历史挤出去，
  而被挤掉的恰好是某人在动手做危险操作之前特意留的那一份 ——
  也就是这个功能存在的全部理由。新的一圈由 `saves.auto_slot_protection` 决定大小。

- **每个插件现在都是一个拥有自己 `lang/` 目录的包。**
  翻译原来共用 `plugins/lang/<id>/`，那只是因为单文件 `.py` 无处安放。
- `!!server commands true` 会被拒绝：它让每个玩家都能用 `/c`，
  这是「决定不再玩这个游戏」而不是一个服务器设置。

### 修复

- **`!!help qb` 什么都找不到。** 框架自己的命令不是插件，不在任何注册表里，
  所以查找看不见它们 —— 而两行之上的索引却把它们打印出来了。
  现在 `qb` 和 `fr` 是主题，`save`、`backup` 都能到备份那一条，
  核心命令也纳入了搜索范围。
- `help.lang` 从来就不在语言包里，所以帮助索引里 `!!FR lang` 的说明位置
  打印的是原始的 key。

- **`install.sh`可能写出一个没有 RCON 密码的 config.yml。**
  密码原来是在「Factorio 二进制存在吗」那个判断**里面**生成的，
  所以 `--no-server`（以及任何下载失败的运行）会得到空密码和一个不工作的 RCON。
  现在它是独立的一步，排在所有需要它的东西之前，并带 `/dev/urandom` 兜底。
- **每 66 次安装就有一次生成出坏密码。** `secrets.token_urlsafe` 产生的是
  base64url，其中 1.5% 以 `-` **开头**，而它是不加引号直接拼进命令行的：
  `--rcon-password -HjOaa2...` 会被 Factorio 的参数解析器当成另一个选项。
  现在密码只用字母数字，并且经过 `shlex.quote`。
- **启动时会拒绝两处 RCON 密码不一致的配置**，以及以短横线开头的密码。
  这两种情况原来都是无声失败，而无声意味着所有查询都失败却无从查起。

- **计算器不管存档研究到哪一步，一律回答 `assembling-machine-3`** ——
  机器列表来自 prototype，而 prototype 不知道科技树的事。
  现在会挑本势力真正造得出来的最快机器；`machines` 配置和 `machine=`
  选项都可以覆盖。
- **物品名和机器名现在按每个玩家自己的语言显示。** 原来方案是用 prototype id
  写的，而那在任何一种语言里都不是词。发进游戏的行现在是 LocalisedString，
  由 Factorio 在客户端用它自己的词条翻译；终端和 Telegram 仍是 id，
  因为那边没有 Factorio 可以渲染。

- **`!!mod refresh` 的进度行打了两遍，其中一遍还是英文。**
  核心又用英文说了一遍插件刚刚用操作者语言说过的话。
- **门户的错误是以英文进聊天的**，所以中文服务器上 `!!mod info nosuchmod`
  回的是英文。`PortalError` 现在带上翻译 key。
- `!!mod update` 和 `!!mod updates` 现在都认。

- **`!!qb make` 之后服务器卡死的死锁。** 命令处理函数原本内联在
  stdout 读取循环上执行，于是一个在等 Factorio 打印 "Saving finished"
  的处理函数，等的是一行只有它自己能读到的字。
  命令现在分发到自己的任务里；解析和事件仍然内联以保证顺序。
- **名为 `yes`、`no`、`on`、`off` 的翻译键。** YAML 把它们当布尔值，键也一样，
  于是 `common.yes` 被存成了 `common.True`，`!!server` 打印出
  `公开 common.no`。已改名，并且有测试拒绝含这种键的目录。
- `PluginServerInterface.tr()` 缺失，导致插件的翻译器落到了核心那一个上。

## 0.1.0

第一个能用的版本，基于 Factorio 2.0.77 headless 开发。

- 进程管理：启动、优雅停服、崩溃检测、自动重启。
  关服逐级升级 `/quit` → SIGINT → SIGTERM → SIGKILL，
  Ctrl-C 走同一条路，而不是把服务器留在那里跑。
- 输出解析成结构化事件，覆盖真实服务器产生的全部四种行形状。
- 可热重载的插件，按依赖排序加载，带命令树和五级权限模型。
- RCON 结构化查询：每个 Lua 查询都用 `helpers.table_to_json` 包一层，
  插件拿到的是真正的 Python 数据而不是待抓取的文本。
- 备份采用 [QuickBackupM](https://github.com/TISUnion/QuickBackupM) 的槽位模型，
  回档是一套编排好的流程，并会先把当前世界另存一份。
- 全面国际化，中英文，日志也不例外。每个插件自带自己的翻译目录。
- 统一的控制台格式和可选彩色输出，以及一份在不修改 Factorio
  任何一行输出的前提下解释它的启动检查报告。
- 十三个自带插件：`telegram_bridge`、`auto_snapshot`、`mod_manager`、
  `map_render`、`crash_doctor`、`server_utils`、`warp`、`blueprints`、
  `production`、`world_watch`、`leaderboard`、`join_motd`、`web_panel`。
