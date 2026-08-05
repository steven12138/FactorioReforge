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

- **Factorio 自己的事件，改为推送而不是轮询。** 这个项目一直建立在
  "RCON 注册不了事件钩子"这个前提上。2.0.77 实测下来是错的：`/sc` 里
  `script` 是可用的，`script.on_event` 注册的 handler 真的会触发，
  而它里面的 `print()` 会到 stdout —— 而 stdout 本来就在被解析。
  插件调 `server.request_lua_event("on_research_finished")`，
  就能在事件发生的那一 tick 收到 `on_lua_event(server, payload)`。
  科技完成提示原来最多晚两分钟，现在是即时的。
  - handler **串联，绝不替换**：普通 freeplay 存档在 `on_research_finished`
    和 `on_player_created` 上本来就挂着 handler，覆盖掉会悄无声息地破坏 scenario。
  - 每次服务器启动只装一次，次数记在 Python 侧 ——
    同一会话装两遍会让我们自己上一个 wrapper 变成"前一个 handler"，所有事件打印两遍。
  - `world_watch` 底下保留了那一轮慢轮询，因为推送送不到
    "FactorioReforge 不在线时发生的事"。

- **`version_manager` —— `!!version`。** 换服务器跑的 Factorio 版本，
  并且不因此丢掉世界。存档格式升级是一扇单向门，
  所以它长得像回档而不像安装：预备、备份、切换、验证、不行就退回去。
  - 没有任何硬编码的兼容规则。`--version` 会报出 *map input* 和 *map output*
    两个版本，存档在 `level-init.dat` 的前八个字节里带着自己的版本 ——
    所以能不能换是在停服之前判定的，而不是试出来的。
  - 各版本成为并排的目录，当前版本是一条软链，
    于是回滚就是翻一下软链 —— 而当新版本起不来、你又最不想依赖网络的时候，
    那是唯一还能用的切换方式。`!!version adopt` 就地改造现有安装，
    中途任何一步失败都会把前面几步全部撤销。
  - 下载和切换是两条命令：一条不需要停机，另一条需要停服。
  - 切换之前的世界进一个不参与轮转的固定槽位 `pre-upgrade`，
    这也正是 `!!version use <旧版本> with-save pre-upgrade`
    ——二进制和世界一起退回去——之所以能成立的原因。
  - 资料片不是隐患：`space-age`、`quality`、`elevated-rails` 都在 `data/`
    里面，跟着二进制一起换。第三方 mod 钉的是 `major.minor`，
    所以补丁级升级不可能弄坏任何一个，而这一项是会检查的。
- `!!version check <系列>` 列出一个系列里的全部版本。问 updater 而不是发布 API，
  一次请求同时拿到渠道标记**和**全部 376 个已发布 headless 版本 ——
  往回退的时候，"最新是什么"从来不是要问的问题。

### 修复

- **`crash_doctor` 的"建议"从来就没打印出来过。** 它的语言文件里 `cause` 和
  `fix` 各定义了两次 —— 一次是外层那句话，一次是各种原因对应的文本映射 ——
  而 YAML 会静默地保留后一个，于是外层那句被干掉，只剩一个裸 `fix`。
  建议文本算出来了，然后被扔掉，中英文都一样，从这个插件写出来那天起就是这样。
  新增的检查会让任何一个在任意层级重复定义键的语言文件失败。

- **烧料的机器被当成了接在电网上。** `!!ratio iron-plate 15`
  对一份全是石炉的方案答"共 48 台机器，4.32 MW"，而石炉根本不吃电；
  它们真正消耗的煤又完全没进投入清单。现在标题会说清是哪种能量，
  燃料也和矿石并列：`需要投入：iron-ore 15/s, coal 1.08/s`。
  用哪种燃料会尊重机器的 `fuel_categories` —— 2.0.77 实测：
  生物仓烧 `nutrients`、捕获虫巢烧 `food`，所以对它们来说煤是错答案而不是近似。
- 最后一条传送带被占满时会提示。"正好 1 条带"是最坏情况，不是最好情况。

- 任何一直握着 stdin 写端的东西，不再会让 FactorioReforge 退不出去。
  `run_in_executor` 没法把底下阻塞的 `readline` 取消掉，
  而 asyncio 的默认线程池又不是 daemon —— 于是解释器在等一个永远不会返回的线程：
  Factorio 停了、插件卸了、"再见"也打了，进程就那么杵着。
  是用 FIFO 当 stdin 时发现的；`tail -f | ...`
  和把 socket 接到 stdin 上的进程管理器也一样。

- **计算器把每份方案都写成了你未必有的机器。** 三个各自独立的原因，实机上全中：
  - 配置文件里还钉着旧的那份硬编码列表，而 `load_config_simple`
    从不改写已经存在的键 —— 所以把默认值改成"用你造得出来的最好的那台"
    对任何跑过一次这个插件的人都毫无作用。现在加载时会清掉那份**一模一样**的旧列表；
    被人重排过或删减过的列表则原样保留。
  - 判断"已解锁"时把回收算进去了。每台机器都有 `X-recycling` 配方、
    一开始就是启用的，而回收一台三级组装机会产出一台二级组装机 ——
    于是两级都没研究的存档报成了二级，每份方案都高了一级。
  - 某一类里一台都没解锁时，兜底给的是**全游戏最快**的那台。
    一个还在用一级组装机的存档被告知去用低温工厂。现在给的是你最先能解锁的那台。
  `!!ratio machine` 可以在聊天里查看和修改这个选择。
- **重载过的插件，旧命令还在跑。** 从来没有人调用过
  `CommandManager.unregister_plugin`，所以每次 `!!FR plugin reload`
  都会把上一版的命令树留在注册表里、而且排在前面；分发时跑的是旧模块，
  而它的 `on_unload` 刚刚把状态清空了。卸载插件同样会留下命令，
  指向一个已经不存在的模块。测试里的替身实现了这个方法 ——
  所以"满足了它"什么也证明不了，新的回归测试直接驱动真正的 CommandManager。

- `telegram_bridge` 连不上 Telegram 时会按逐步拉长的间隔重试，
  而不是打一串 traceback 然后一直死到有人重载插件。实机测得：
  api.telegram.org 在那台服务器上完全超时 —— 那是网络的问题，不是服务器出了故障。
  但被 Telegram **拒绝**的 token 仍然不重试。
- 颜色相关的测试会读开发者自己的环境变量：某个 shell 里的 `FORCE_COLOR=3`
  让 `test_disabled_when_not_a_tty` 变红，而代码是对的 ——
  强制给管道上色正是那个变量的含义。现在这个约定的两个方向都有测试，
  并且环境被隔离了。

- `!!seen` 和 `!!info` 现在报的是上次在线的**日期时间**，
  用的是管道这一侧记下来的账。Factorio 的 `player.last_online` 是一个 tick，
  把 tick 换算成时长量的是**世界**跑了多久：开着 `auto_pause` 时空服不走 tick，
  于是昨晚离开的人，在下一个玩家登录的那一刻会显示成"20 分钟前"。
- 测试套件现在会用真正的插件管理器加载每一个自带插件。
  `version_manager` 发出去的版本里伸手去拿 `server.config`，
  而那个属性在核心上、不在插件拿到的接口上；535 项测试全过，
  第一个真正跑 `on_load` 的是服务器。

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
