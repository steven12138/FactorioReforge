# 开一个 Factorio 联机服务器

这一页只讲 Factorio headless 服务器本身，不涉及 FactorioReforge。
已经在跑服务器了就直接看 [配置](configuration_zh.md)；
想要手把手的版本，[新手教程](TUTORIAL_zh.md) 讲同样的内容，
但一路带着 FactorioReforge。

## 三种联机方式

| 方式 | 适合 | 说明 |
|---|---|---|
| 客户端里 Host | 临时和朋友玩一局 | 主机退出即结束，也没法被任何东西托管 |
| **headless 专用服务端** | 长期开的服 | 无图形、无音频，纯命令行 |
| 公开列表 | 让陌生人找到你 | `visibility.public` 会把你注册到官方 matching server |

只有 headless 服务器能被托管，本项目其余部分都建立在这上面。

## 安装 headless 服务端

headless 是**独立于游戏客户端的另一份下载**，不需要账号，也不含 DLC 数据。

```bash
mkdir -p server && cd server
curl -L -o factorio-headless.tar.xz "https://factorio.com/get-download/stable/headless/linux64"
tar -xJf factorio-headless.tar.xz
./factorio/bin/x64/factorio --version
```

别和 `~/.factorio/` 混在一起——那是你游戏客户端的目录，
混用意味着客户端一更新就可能改掉你服务器跑的东西。

```
factorio/
├── bin/x64/factorio               可执行文件
├── data/                          基础游戏数据，以及 server-settings.example.json
├── saves/                         存档，每个一个 .zip
├── mods/                          服务端 mod
└── config/config.ini
```

玩家的 **mod 和 DLC 必须和服务器完全一致**，否则连不上。

## 创建地图

```bash
cd factorio
./bin/x64/factorio --create ./saves/reforge.zip
```

可以加 `--map-gen-settings ./map-gen-settings.json`
和 `--map-settings ./map-settings.json`，两者在 `data/` 里都有示例。

## server-settings.json

```bash
cp data/server-settings.example.json ./server-settings.json
```

真正要关心的字段：

```jsonc
{
  "name": "我的服务器",
  "description": "",
  "max_players": 0,                    // 0 = 不限

  "visibility": { "public": false, "lan": true },
  "username": "",                      // public 时必填，factorio.com 账号
  "token": "",                         // 在 ~/.factorio/player-data.json 里

  "game_password": "",
  "require_user_verification": true,   // 校验玩家的 factorio.com 账号

  "allow_commands": "admins-only",     // true | false | admins-only
  "autosave_interval": 10,             // 分钟
  "autosave_slots": 5,
  "auto_pause": true,                  // 无人时暂停世界
  "non_blocking_saving": true          // 存档时不卡住游戏
}
```

**`allow_commands: true` 意味着每个玩家都能用 `/c`**，
那会把存档**永久**标记为作弊并彻底关掉成就。`admins-only` 才是正常选择。
FactorioReforge 拒绝从聊天里把它改成 `true`，并在它已经打开时于启动检查中警告。

**`non_blocking_saving: true`** 只要是有人真在玩的服就该开。
不开的话，每次存档所有人都会卡住整整一次存档的时间。

旁边还有三个名单文件，各是一个玩家名字符串数组：

```bash
echo '["你的_factorio_名字"]' > server-adminlist.json
echo '[]' > server-whitelist.json
echo '[]' > server-banlist.json
```

## 启动

```bash
./bin/x64/factorio \
  --start-server ./saves/reforge.zip \
  --server-settings ./server-settings.json \
  --server-adminlist ./server-adminlist.json \
  --server-banlist  ./server-banlist.json \
  --port 34197 \
  --rcon-bind 127.0.0.1:27015 --rcon-password 'CHANGE_ME'
```

| 参数 | 作用 |
|---|---|
| `--start-server FILE` | 加载指定的那个世界 |
| `--start-server-load-latest` | 加载最新的存档——见下面的警告 |
| `--start-server-load-scenario [MOD/]NAME` | 从场景开始 |
| `--server-settings` / `--server-adminlist` / `--server-whitelist` / `--server-banlist` | 上面那些文件 |
| `--port N` | 游戏端口，**UDP**，默认 34197 |
| `--rcon-bind ADDR:PORT` | RCON 监听——永远要带上地址 |
| `--rcon-port N` | RCON 监听**所有网卡**；请用 `--rcon-bind` |
| `--console-log FILE` | 另存一份控制台输出，含聊天 |
| `--mod-directory PATH` | 用别处的 mod 目录 |

> **`--start-server-load-latest` 和备份不能共存。** 回档写的是你的世界文件，
> 但之后写的 autosave **更新**，于是服务器起来加载的是错的地图——
> 看起来就像回档静默失败了。FactorioReforge 带这个参数会拒绝启动。

> **用 `--rcon-bind 127.0.0.1:27015`，不要用 `--rcon-port 27015`。**
> RCON 是明文不加密的，能连上那个端口**就等于**能控制服务器，
> 而 `--rcon-port` 监听所有网卡，包括你的公网口。
> FactorioReforge 遇到 `--rcon-port`、或者 `--rcon-bind`
> 绑了非本地地址，都会拒绝启动。

## 网络

- 游戏端口是 **34197/UDP**，不是 TCP。公网开服要做端口转发并放行防火墙
  （`sudo ufw allow 34197/udp`）。
- RCON 是 **27015/TCP**，只该待在 localhost。
- 局域网：`visibility.lan` 让服务器出现在同网段的多人游戏列表里，两边都不用配。
- 公网：`visibility.public` 加上 `username` 和 `token`
  会把你登记到官方 matching server，它同时负责 NAT 打洞。
- 直连：**多人游戏 → 连接到地址 → `IP:34197`**。

## 服务端控制台

**标准输入就是游戏内的聊天框。** 你打的任何一行都会以 `<server>`
的身份说出去；以 `/` 开头的则是以服务器身份执行的命令。

| | |
|---|---|
| `/players` `/admins` `/version` `/time` `/seed` | 查询 |
| `/promote` `/demote` `/kick` `/ban` `/unban` `/mute` `/whitelist add\|remove` | 管理玩家 |
| `/server-save [名字]` | 立即存档；带名字则存到**另一个**文件 |
| `/quit` | 存档并干净地关服 |
| `/sc <lua>` | 静默执行 Lua，不打作弊标记 |
| `/c <lua>` | 执行 Lua，**并把存档永久标记为作弊** |

标准输出是两种格式混在一起的——如果你要解析它，这一点很重要：

```
   0.001 2026-08-02 14:02:11; Factorio 2.0.77 (build 84115, linux64, headless)
   1.234 Info ServerMultiplayerManager.cpp:791: updateTick(4) changing state from(CreatingGame) to(InGame)
2026-08-02 14:02:31 [JOIN] Alice joined the game
2026-08-02 14:02:48 [CHAT] Alice: hello
2026-08-02 14:03:02 [DEATH] Bob was killed by small-biter
```

引擎行是 `<启动至今秒数> <级别> <文件>:<行号>: <内容>`，
游戏事件行是 `<日期> <时间> [TAG] <内容>`，TAG 包括 `JOIN` `LEAVE`
`CHAT` `SHOUT` `DEATH` `KICK` `BAN` `COMMAND` `WARNING`。
实际上还有两种形状，[Factorio 实测笔记](factorio-notes_zh.md) 记了全部四种。

## 存档与回档

自动存档在 `saves/_autosave1.zip` … `_autosave5.zip` 之间循环覆盖。
**它们不是备份**：五个档、每十分钟一个，意味着你想撤销的那个失误
不到一小时就被覆盖没了。

**Factorio 不能在运行中换世界。** 回档只能是：停服 → 放好文件 → 重新启动。
正是这个物理约束，让 FactorioReforge 的回档是一套编排好的流程而不是一次文件拷贝
——见 [回档](architecture_zh.md#备份与回档)。

手动做备份的话，`/server-save <名字>` 是好用的那个：
它写出一份**独立完整**的存档，同时不动正在跑的世界。
