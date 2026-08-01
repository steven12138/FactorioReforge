# FactorioReforge — 分步教程

从零开始，到一个能跑、被托管、可远程控制的 Factorio 服务器。

这里的每条命令都在真实机器上跑过（Arch Linux、Factorio 2.0.77、Python 3.14）。
如果你的输出和文中不一样，那是值得停下来看的地方，别硬着头皮往下走。

**English version: [TUTORIAL.md](TUTORIAL.md)**

---

## 目录

1. [开始之前需要什么](#1-开始之前需要什么)
2. [一个脚本装好一切](#2-一个脚本装好一切)
3. [第一次启动](#3-第一次启动)
4. [从游戏里连进去](#4-从游戏里连进去)
5. [把自己设成管理员](#5-把自己设成管理员)
6. [备份与回档](#6-备份与回档)
7. [安装 mod](#7-安装-mod)
8. [用 Telegram 控制](#8-用-telegram-控制)
9. [Web 面板与地图](#9-web-面板与地图)
10. [开放到公网](#10-开放到公网)
11. [无人值守运行](#11-无人值守运行)
12. [写你的第一个插件](#12-写你的第一个插件)
13. [切换语言](#13-切换语言)
14. [出问题的时候](#14-出问题的时候)

---

## 1. 开始之前需要什么

| 需求 | 用途 | 怎么确认 |
|---|---|---|
| Linux x86-64 | headless 分发包在这里只有 Linux 版 | `uname -m` → `x86_64` |
| Python 3.11+ | 框架本体 | `python3 --version` |
| `curl`、`tar`、`xz` | 下载和解包服务端 | `curl --version` |
| 约 2 GB 空闲磁盘 | 服务端、存档、快照 | `df -h .` |
| factorio.com 账号 | 只有装 mod 或公开开服才需要 | — |

跑 headless 服务器**不需要**你拥有 Factorio。但从门户下载 mod 需要一个拥有游戏的账号。

Debian/Ubuntu 上如果 `python3 -m venv` 报错：

```bash
sudo apt install python3-venv python3-pip curl xz-utils
```

---

## 2. 一个脚本装好一切

```bash
git clone git@github.com:steven12138/FactorioReforge.git
cd FactorioReforge
./scripts/install.sh
```

这一个脚本做完下面全部的事：

1. 检查 Python 是否 3.11+，以及 `venv` 能不能用
2. 下载 Factorio headless 服务端（约 21 MB）到 `server/`
3. 写好 `server/factorio/server-settings.json` 和管理员/白名单/封禁名单
4. 在 `server/factorio/saves/reforge.zip` 创建一张新地图
5. 建 `.venv` 并安装 FactorioReforge 及可选依赖
6. **生成一个随机 RCON 密码**，写出配套的 `config.yml`
7. 校验配置，不合法就直接失败而不是让你以后再踩

预期输出（节选）：

```
==> Checking prerequisites
    Python: Python 3.14.6 at /usr/bin/python3
==> Downloading Factorio headless (stable)
==> Extracting
    Version: 2.0.77 (build 84539, linux64, headless)
==> Setting up server configuration
    generated a random RCON password
    wrote server-settings.json
==> Creating a map (this takes a moment)
    created saves/reforge.zip
==> Building the Python environment
    installed FactorioReforge and its optional extras
==> Writing FactorioReforge configuration
    wrote config.yml
==> Validating the configuration
    config.yml is valid

Setup complete.
```

常用参数：

```bash
./scripts/install.sh --yes                 # 全部用默认值，不询问
./scripts/install.sh --version 2.0.77      # 锁定 Factorio 版本
./scripts/install.sh --no-server           # 已有 headless，只装 Python 部分
./scripts/install.sh --port 34500          # 换游戏端口
./scripts/install.sh --force               # 全部重来，覆盖已有内容
```

不带 `--force` 重复执行是安全的：已经存在的东西一律保留，
你的 `config.yml` 不问过你绝不会被覆盖。

### 如果你想手动来

脚本没有任何魔法，手动等价步骤见 [README 第一部分](../README_zh.md#第一部分--把-factorio-联机服务器跑起来)。

---

## 3. 第一次启动

```bash
./scripts/run.sh
```

你应该看到插件加载，然后服务器起来：

```
[INFO] [reforge] Loaded 13 plugin(s)
[INFO] [reforge] Starting server: ./bin/x64/factorio --start-server ... --rcon-password <redacted>
[INFO] [reforge] Server started, pid=34246
[INFO] [reforge] Server startup complete
[INFO] [reforge] RCON connected to 127.0.0.1:27015

  FactorioReforge 0.1.0
  Type !!FR help for commands. Anything else goes to the Factorio console.
```

有两行最关键：

- **`Server startup complete`** —— 地图加载完了，玩家可以连了。
- **`RCON connected`** —— 查询通道通了。在这行出现之前，
  所有需要读回数据的命令（`!!stats`、`!!list`）都会告诉你 RCON 没连上。

现在在同一个终端里敲：

```
!!FR status
```

```
FactorioReforge 0.1.0 - up 12s
Server: running (pid 34246, up 12s)
RCON: connected
Plugins: 13 loaded
Backups: 0 slots in use
Online (0): -
```

**这个终端的规则：** 以 `!!` 开头的是 FactorioReforge 命令，
其余一切原样转发给 Factorio，效果等同于你在游戏聊天框里敲。
所以 `/players` 是可用的，而纯文本会广播给游戏里所有人。

用 `Ctrl-C`（会先存档再退出）或 `!!FR exit` 停止。

---

## 4. 从游戏里连进去

Factorio 客户端里：**Multiplayer → Connect to address**

| 客户端在哪 | 地址 |
|---|---|
| 同一台机器 | `127.0.0.1:34197` |
| 同一局域网 | `<服务器局域网IP>:34197` |
| 公网 | 见[第 10 节](#10-开放到公网) |

进服后 FactorioReforge 会捕捉到：

```
2026-08-02 10:05:08 [JOIN] YourName joined the game
```

同时 `join_motd` 会用实时数据给你一条欢迎语。

现在试试游戏内命令。在游戏聊天框里敲：

```
!!here
```

所有人会看到一个**可点击的坐标**，点了会在他们地图上 ping 你的位置，
同时那里会被钉上一个永久标记。

```
!!list       谁在线
!!stats      进化度、污染、科研
!!info       你的游玩时长和权限
```

---

## 5. 把自己设成管理员

这里有**两套互相独立的权限系统**，搞混是新手最常见的坑。

| 系统 | 管什么 | 怎么设 |
|---|---|---|
| Factorio 自己的管理员名单 | `/kick`、`/ban`、作弊命令 | `server/factorio/server-adminlist.json` |
| FactorioReforge 权限 | `!!` 开头的命令 | `config/permission.yml` 或 `!!FR permission set` |

**Factorio 管理员** —— 改文件然后重启：

```bash
echo '["你的Factorio用户名"]' > server/factorio/server-adminlist.json
```

**FactorioReforge 管理员** —— 在 FactorioReforge 控制台里执行
（那个控制台永远是 `owner` 级别）：

```
!!FR permission set 你的Factorio用户名 admin
```

级别是 `guest(0) user(1) helper(2) admin(3) owner(4)`，新玩家默认 `user`。查看：

```
!!FR permission list
```

---

## 6. 备份与回档

这是最值得在你真正需要它之前就搞懂的功能。

### 做一次备份

```
!!save make 大改造之前
```

```
正在存档……
已备份到 槽位 1：2026-08-02 10:12:03 由 console（24.8 MiB）—— 大改造之前
```

服务器自己把备份写成一份独立文件，FactorioReforge 等到完成消息才登记它。
备份**完全不碰实时存档**，也不需要复制 —— 早期版本用的是裸 `/server-save`，
那会覆盖掉它正要备份的那个世界。

备份遵循 [QuickBackupM](https://github.com/TISUnion/QuickBackupM) 的模型：
新备份永远进**槽位 1**，其余后移一位。被挤掉的是第一个空槽位，
或者编号最大且已过 `delete_protection` 保护期的那个 ——
如果所有槽位都还在保护期内，备份会被拒绝，而不是毁掉你想留的东西。

### 列出备份

```
!!save list
```

### 回档

回档故意做成两步：

```
!!save back 1
```

```
即将回档到 槽位 1：2026-08-02 10:12:03 由 console（24.8 MiB）—— 大改造之前
这会停止服务器并替换当前世界。60 秒内输入 '!!save confirm' 继续，'!!save abort' 取消。
```

```
!!save confirm
```

接下来按顺序发生：

1. 校验槽位里是合法 zip
2. 游戏内逐秒倒计时 —— 期间 `!!save abort` 仍然能取消
3. 停服，并等待进程真正退出
4. **把当前世界复制到 `overwrite` 槽位** —— 这样回错槽位也能救回来
5. 通过临时文件 + rename 替换存档
6. 重新启动服务器
7. 如果起不来，把 `overwrite` 里的世界放回去并明确报告

如果第 4 步失败，整个回档会被拒绝执行。
没有退路的回档是一扇单向门，这里刻意不会走进去。

回错槽位了？`!!save` 里会列出 `overwrite` 那一条 —— 那就是回档前一刻的世界。

### 自动备份

`auto_snapshot` 默认每 30 分钟一次，最后一个玩家离开时再补一次。
改 `config/auto_snapshot/config.json`，然后：

```
!!FR plugin reload auto_snapshot
```

槽位数量、以及每个槽位多久之内不会被复用，都在 `config.yml` 的
`saves.slot_protection` 里 —— 一个秒数列表，每个槽位一项。
默认让最旧的两个槽位分别保护 3 小时和 3 天。

---

## 7. 安装 mod

```
!!mod search krastorio
```

```
Searching the portal for 'krastorio'...
8 result(s):
  Krastorio 2 (Krastorio2) v2.1.2 by raiguard - 385,068 downloads
  Krastorio 2 Assets (Krastorio2Assets) v2.1.0 by raiguard - 405,910 downloads
  ...
```

当天第一次搜索会拉取完整 mod 索引（约 22500 个、13 MB、约 14 秒）并缓存下来，
之后的搜索是瞬时的。

```
!!mod info Krastorio2      详情与依赖
!!mod install flib         下载、解析依赖、启用
!!mod list                 已安装的
!!mod updates              有新版本的
!!mod remove flib
```

### 凭据

下载需要一个拥有游戏的 factorio.com 账号。插件会自动读你
`~/.factorio/player-data.json` 里的 `service-username` 和 `service-token`。
如果 FactorioReforge 跑在别的用户下，就在 `config/mod_manager/config.json` 里填。

### 它替你挡掉的三件事

**版本不匹配。** 它会问二进制的版本，只提供为该版本构建的 release。
这不是美观问题：把为 2.1 构建的 flib 0.17.2 装到 2.0.77 上，
服务器下次启动会直接以退出码 1 失败。

**Factorio 覆盖你的改动。** 运行中的服务器把 mod 列表存在内存里，
停止时把自己的版本写回 `mod-list.json`，丢弃运行期间的一切改动。
插件单独记录自己的意图，等进程真正退出后再重新应用。

**可选依赖雪崩。** 只安装必需依赖 —— `?` 和 `(?)` 前缀的条目会被跳过，
否则一个大型整合 mod 会拖进几十个无关 mod。

### 装完之后

```
!!FR server restart
```

**mod 只在启动时加载，而且每个玩家的 mod 集合必须和服务器一致。**
在有人在线的公开服上装 mod，会把所有没有这个 mod 的人挡在门外。先打个招呼。

---

## 8. 用 Telegram 控制

### 创建 bot

1. 在 Telegram 上私聊 [@BotFather](https://t.me/BotFather)
2. 发 `/newbot`，起个名字和用户名
3. 复制它给你的 token，形如 `123456789:AAF...`

### 配置

编辑 `config/telegram_bridge/config.json`：

```json
{
  "enabled": true,
  "token": "123456789:AAF-你的token",
  "allowed_chat_ids": [],
  "admin_user_ids": [],
  "owner_user_ids": [],
  "forward_chat": true,
  "forward_join_leave": true
}
```

重载：

```
!!FR plugin reload telegram_bridge
```

### 找到你的 chat id

给 bot 随便发条消息。它会无视你 —— 这是刻意的，
因为回复一个陌生对话等于向对方确认这个 bot 存在 —— 但它会把 id 记进日志：

```
[INFO] telegram_bridge ignored a message from chat 123456789; add it to allowed_chat_ids
```

把这个数字填进 `allowed_chat_ids`，把你自己的 user id 填进 `admin_user_ids`
（私聊场景下这两个是同一个数字）。再重载一次。

### 用起来

```
/status        服务器状态、在线玩家、进化度
/players
/say hello     往游戏里发消息
/save          做快照
/saves         列出快照
/rollback 3    会弹按钮要求二次确认
/restart
/mods          已安装的 mod
/modsearch bob
/modinstall flib
```

聊天是双向转发的：玩家说的话会到 Telegram，你在 Telegram 里打的字
会以 `[TG] 你的名字: ...` 出现在游戏里。

还有一些你没主动要、但正是重点的推送：

- 🔥 服务器意外退出 —— **并附带原因诊断**
- ⚠️ 进化度跨过阈值
- 🚀 火箭发射
- ♻️ 回档完成

### 权限级别

| 级别 | 谁 | 能做什么 |
|---|---|---|
| `viewer` | 允许列表里的对话中的任何人 | `/status` `/players` `/say` `/saves` |
| `admin` | `admin_user_ids` | `/save` `/rollback` `/restart` `/modinstall` |
| `owner` | `owner_user_ids` | `/cmd` —— 执行任意命令 |

所有破坏性操作都会用 inline 按钮要求二次确认。

---

## 9. Web 面板与地图

已经在 **http://127.0.0.1:8080** 上跑着了，`/api` 提供 JSON。

```
!!web
```

上面有服务器状态、在线玩家、世界统计、最近备份、蓝图库、生产曲线、
渲染出的世界地图，以及服务器日志的尾巴。

### 世界地图

```
!!map
```

按 1 像素 = 1 tile 绘制：地形、每一棵树、每一格矿、每一个建筑都在真实位置上。
Factorio 在 headless 上无法截图 —— `game.take_screenshot` 能调用但不产生文件，
因为进程里没有渲染器 —— 所以地图是用数据在这边合成的。

结果写到 `config/map_render/map.png`，Web 面板在 `/map.png` 提供，
发到 Telegram 时走**文件**而不是照片，因为 Telegram 会重新压缩照片，
而这恰好会毁掉 1 像素 1 tile 的细节。

**它是只读的，这是刻意的。** 没有停服按钮、没有回档、没有控制台。
一个没有鉴权也没有写入路径的页面，无法被利用来造成破坏。

想从别的机器访问，请在前面放一个带鉴权的反向代理。
把 `config/web_panel/config.json` 里的 `host` 改成 `0.0.0.0`，
等于把玩家名和世界状态公开给所有能访问到这个端口的人。

---

## 10. 开放到公网

### 1. 决定玩家怎么找到你

编辑 `server/factorio/server-settings.json`：

```jsonc
{
  "visibility": { "public": true, "lan": true },
  "username": "你的factorio.com用户名",
  "token": "player-data.json 里的 token",
  "game_password": "",
  "require_user_verification": true,
  "max_players": 0
}
```

`public: true` 会把你列进游戏内的服务器浏览列表，需要填账号字段。
不想被列出来就保持 `false`，直接把 IP 发给朋友。

### 2. 转发端口

**34197/UDP** —— 不是 TCP。这是最常见的错误，没有之一。

```bash
sudo ufw allow 34197/udp
```

然后在路由器上把 `34197/udp` 转发到服务器这台机器。

### 3. 千万别暴露 RCON

**27015/TCP 必须留在 localhost。** RCON 协议是明文的，
谁能连上它谁就拥有你的服务器。默认配置把它绑在 `127.0.0.1`，保持原样。

### 4. 重启并验证

```
!!FR server restart
```

找一个不在你网络里的人试连。连不上的话，先查是不是把 UDP 转成 TCP 了。

---

## 11. 无人值守运行

FactorioReforge 在前台运行，需要一个终端。要 7×24 跑，选一个：

### tmux —— 最简单

```bash
tmux new -s factorio
./scripts/run.sh
# Ctrl-B 然后 D 脱离；tmux attach -t factorio 回来
```

### systemd —— 能扛重启

`~/.config/systemd/user/factorio-reforge.service`：

```ini
[Unit]
Description=FactorioReforge
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/FactorioReforge
ExecStart=%h/FactorioReforge/.venv/bin/python -m factorio_reforge
Restart=on-failure
RestartSec=15
# FactorioReforge 收到 SIGTERM 会优雅停服，留够存档时间。
KillSignal=SIGTERM
TimeoutStopSec=120
StandardInput=null

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now factorio-reforge
systemctl --user status factorio-reforge
journalctl --user -u factorio-reforge -f
loginctl enable-linger "$USER"     # 让它在你退出登录后继续跑
```

**`StandardInput=null` 意味着你没有控制台了。** 原本要在那里敲的一切，
都得从 Telegram 或者以管理员身份在游戏聊天里发。
如果你还想要控制台，就用 tmux；或者 systemd + 依赖 Telegram ——
那个桥接本来就是为这个场景做的。

无论哪种方式，都建议在 `config.yml` 里打开崩溃自恢复：

```yaml
auto_restart_on_crash: true
crash_restart_delay: 10.0
```

---

## 12. 写你的第一个插件

新建 `plugins/hello.py`：

```python
from factorio_reforge.command.builder import Literal
from factorio_reforge.permission import PermissionLevel

PLUGIN_METADATA = {
    "id": "hello",
    "version": "1.0.0",
    "name": "Hello",
    "dependencies": {"factorio_reforge": ">=0.1.0"},
}


def on_load(server, prev):
    server.register_command(
        Literal("!!hello")
        .requires(PermissionLevel.USER)
        .runs(lambda source: source.reply("你也好"))
    )
    server.register_help_message("!!hello", "打个招呼")


async def on_player_joined(server, player, info):
    await server.say(f"欢迎，{player}！")
```

不用重启任何东西就能加载：

```
!!FR reload
```

```
Reloaded: hello
```

试试 `!!hello`。然后改文件再 `!!FR reload` —— 改动立刻生效。
（插件加载绕过了字节码缓存，所以哪怕你在同一秒内改了个不改变文件长度的地方，
也能正确重载。）

### 做点更有用的

```python
from factorio_reforge.core.errors import QueryError


async def on_player_joined(server, player, info):
    try:
        stats = await server.get_server_stats()
    except QueryError as exc:
        server.logger.warning("读不到世界状态：%s", exc)
        return
    await server.tell(
        player,
        f"进化度已经 {stats['evolution'] * 100:.1f}% 了，小心虫子。"
    )
```

`QueryError` 同时覆盖"RCON 断了"和"Lua 执行失败"，插件只需要 catch 一个。

完整 API 见 [README](../README_zh.md#写一个插件)，`plugins/` 下自带的十三个插件
都是可读的工作示例 —— `warp.py` 是其中做了实事的最小的一个。

---

## 13. 切换语言

在 `config.yml` 里设 `language`：

```yaml
language: zh_cn      # 或 en
```

然后重启，或者 `!!FR reload`。自带 `en` 和 `zh_cn`；
某个语言缺失的词条会回落到英文，所以翻译一半也不影响使用。

```
!!FR lang                  当前语言，以及各语言还缺哪些词条
!!FR lang missing zh_cn    具体缺失的键
```

要加语言，把 `factorio_reforge/lang/en.yml` 复制成 `<语言代码>.yml` 再翻译值。
缺失的键会直接显示成键名而不是空白 ——
聊天里出现一个 `save.restore.confirm` 正好告诉你该补什么。

---

## 14. 出问题的时候

### 服务器退出了，但不知道为什么

```
!!why
```

`crash_doctor` 维护着输出滚动缓冲区，会用真实的失败特征去匹配：

```
Last unexpected exit: code 1
  Cause: the mod 'flib' could not be loaded
  Detail: Incompatible Factorio version (current: 2.0, required: 2.1)
  Try: !!mod remove flib
```

如果没匹配上，它会把最后几行输出打出来，而不是瞎猜一个原因。

### 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 刚启动时 `RCON: not connected` | RCON 要等地图加载完才监听 | 等一秒；一直这样就检查 `config.yml` 的 `rcon.password` 和 `start_command` 里的 `--rcon-password` 是否一致 |
| `Could not look that up: RCON is not connected` | 查询命令在 RCON 起来之前执行了 | 同上 |
| 启动就以退出码 1 失败 | 多半是 mod 不兼容 | `!!why` |
| `Address already in use` | 上一个 Factorio 还在跑 | `pkill -f 'bin/x64/factorio'` |
| 玩家连不上 | 端口按 TCP 转发了 | 转发 **34197/UDP** |
| 玩家提示 mod 不匹配 | 他们的 mod 集合不同 | 需要完全相同的 mod 和版本 |
| 回档后加载的是错误的地图 | `start_command` 里用了 `--start-server-load-latest` | 改用 `--start-server <路径>`；FactorioReforge 遇到前者会直接拒绝启动 |
| 备份时提示没有可用槽位 | 所有槽位都还在保护期内 | `!!save del <槽位>`，或调低 `saves.slot_protection` |
| 回错槽位了 | — | `!!save` 里有 `overwrite` 一条，那是回档前一刻的世界 |
| Telegram bot 不理你 | 你的 chat id 不在允许列表 | 从日志里找到 id，加进 `allowed_chat_ids` |

### 日志

```bash
tail -f logs/reforge.log
```

RCON 密码在日志输出里是打码的，所以贴日志求助是安全的。
但 `config.yml` **不是** —— 里面是明文密码。

### 推倒重来

```bash
rm -rf .venv config.yml config/ logs/ snapshots/
./scripts/install.sh
```

这会保留 `server/` —— 也就是你的世界和 mod —— 其余全部重建。
连世界一起不要的话，把 `server/` 也删掉。

---

## 接下来看什么

- [README](../README_zh.md) —— 完整参考
- [M0-findings.md](M0-findings.md) —— 实测真实服务器得到的结论，
  包括三处官方文档说错了的地方
- `plugins/` —— 十三个可以直接读和抄的工作插件
