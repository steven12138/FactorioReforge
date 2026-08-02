# 自带插件

`plugins/` 里自带十三个插件，每个都是一个包，
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

备份属于框架而不是插件——`!!save` 永远都在。
槽位模型和一次回档具体做了什么，见 [备份与回档](architecture_zh.md#备份与回档)。

## auto_snapshot

定时备份，以及最后一名玩家离开时备份。

无人在线时定时器会跳过。开着 `auto_pause` 的话，空服的世界根本没动过，
那些备份会是一模一样的重复，白白占掉本该放真实历史的槽位。

```jsonc
{
  "interval_minutes": 60,
  "on_last_player_leave": true,
  "comment": "auto"
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

服务端共享蓝图库。`!!bp save <名字>` 把你周围的区域做成蓝图，
`!!bp get <名字>` 把它放进别人的物品栏。

完全在服务端通过一个临时物品栏完成，所以客户端不需要装任何东西。
蓝图字符串在存入时就校验，畸形的蓝图在保存那一刻被拒绝，
而不是等到有人来取的时候才失败。

```jsonc
{ "radius": 32, "manage_permission": "user" }
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
