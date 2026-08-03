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

- **每个插件现在都是一个拥有自己 `lang/` 目录的包。**
  翻译原来共用 `plugins/lang/<id>/`，那只是因为单文件 `.py` 无处安放。
- `!!server commands true` 会被拒绝：它让每个玩家都能用 `/c`，
  这是「决定不再玩这个游戏」而不是一个服务器设置。

### 修复

- **`!!save make` 之后服务器卡死的死锁。** 命令处理函数原本内联在
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
