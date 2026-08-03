# 配置

FactorioReforge 自身的所有配置都在仓库根目录的 `config.yml` 里。
各插件自己的配置在 `config/<插件id>/`，随插件一起记在 [自带插件](plugins_zh.md)。

`./scripts/install.sh` 会给你写好一份能用的 `config.yml`。
这一页讲的是之后怎么改它，或者怎么把 FactorioReforge 指向你已有的服务器。

## 生成一份

```bash
python -m factorio_reforge init
```

会写出 `config.yml`，以及 `plugins/`、`config/`、`logs/`、`snapshots/`
几个目录。已存在的配置绝不会被覆盖。

## config.yml

```yaml
# 跑什么，在哪跑。
working_directory: server/factorio
start_command: >-
  ./bin/x64/factorio --start-server ./saves/reforge.zip
  --server-settings ./server-settings.json
  --server-adminlist ./server-adminlist.json
  --server-banlist ./server-banlist.json
  --port 34197
  --rcon-bind 127.0.0.1:27015 --rcon-password s3cret

language: zh_cn               # en | zh_cn
colour: auto                  # auto | always | never

rcon:
  enabled: true
  host: 127.0.0.1
  port: 27015
  password: s3cret            # 必须和上面 --rcon-password 一致

server:
  auto_restart: false         # 非预期退出后是否自动重启
  stop_timeout: 120           # /quit 之后等多少秒再升级手段

saves:
  current_save: server/factorio/saves/reforge.zip
  snapshot_dir: snapshots
  slot_protection: [0, 0, 0, 10800, 259200]
  auto_slot_protection: [0, 0, 0, 0, 0]

permission:
  default_level: user

plugin:
  directories: [plugins]
```

### working_directory 与 start_command

`start_command` 以 `working_directory` 为当前目录执行，所以里面写的是相对路径。
两者都指向你的 headless 安装目录。这条命令就是你自己本来会敲的那条 ——
见 [开一个 Factorio 服务器](factorio-server_zh.md#启动)。

### rcon

RCON 是所有**要拿返回值**的东西的通道：玩家列表、Lua 表达式、私聊。
`password` 必须和 `start_command` 里的 `--rcon-password` 一致，
没有别的东西能替你连上。

设成 `enabled: false` 时，需要返回值的插件会抛 `RconError`，
而不是编一个结果给你。聊天、命令、备份仍然照常工作——它们走 stdin。

### saves.auto_slot_protection

同样是一个列表，但属于自动备份那一圈槽位。自动备份和手动备份**分开**顺移，
所以一个每半小时跑一次的定时器，不会在一夜之间把某人动手前特意留的那一份
挤出列表。自动槽位用 `a` 开头寻址：`!!qb back a2`。默认不设保护期 ——
那一圈里的东西本来就不是人特意要求留下的。

### saves.slot_protection

一个以秒为单位的列表，每个备份槽位一项。**列表长度就是槽位数量。**
每一项表示该槽位里的备份多久之内不允许被挤掉。
默认的 `[0, 0, 0, 10800, 259200]` 让槽位 4 保护三小时、槽位 5 保护三天，
这样连续备份几次不会把昨天的世界冲掉。
完整模型见 [备份与回档](architecture_zh.md#备份与回档)。

### colour

`auto` 只在 stdout 是一个想要颜色的终端时上色，
所以管道给 `grep` 或日志收集器时输出仍然干净。
`NO_COLOR` 和 `TERM=dumb` 也会关掉它。`logs/reforge.log` 永远不带颜色。

### language

自带 `en` 和 `zh_cn`。运行中用 `!!FR lang set zh_cn` 切换，
它只改写这一行，立即生效，日志也跟着切。
加一门新语言见 [写插件](writing-plugins_zh.md#国际化)。

## 它拒绝启动的几种情况

启动任何东西之前会做四项检查。每一项对应的都是一种「不拦就会静默出事、
且事后代价很大」的故障，所以它们是硬性中止加一条说明，而不是一句警告。

**`start_command` 里有 `--start-server-load-latest`。**
回档替换的是 `saves.current_save`，但之后写的 autosave 更新，
于是服务器起来加载的是错的地图——表现出来就像回档静默失败了。

**`--start-server` 指向的文件不是 `saves.current_save`。**
那样回档会写到一个服务器根本不读的文件上。症状一样，成因不同，所以两条都查。

**`--rcon-port`，或者 `--rcon-bind` 绑了非本地地址。**
RCON 是明文的，能连上那个端口就等于能控制服务器，而 `--rcon-port` 监听所有网卡。
如果你确实需要远程 RCON，请走 SSH 隧道，别把它暴露出去。

**RCON 密码和 `start_command` 里的对不上。**
否则第一个症状是很久以后某个插件莫名其妙地失败。

新版本里删掉的配置项会在启动时被点名报出来，并告诉你换成了什么，
而不是被静默忽略。

## 环境变量

| 变量 | 作用 |
|---|---|
| `NO_COLOR` | 关掉颜色，遵循 [no-color.org](https://no-color.org) |
| `FORCE_COLOR` | stdout 不是终端时也强制上色 |
| `TERM=dumb` | 关掉颜色 |

## 作为服务运行

本项目刻意不自带 unit 文件——日志放哪、用哪个用户跑服务器，
这些是你机器上的决定，不该由项目替你做。一份最小的 `systemd` unit：

```ini
[Unit]
Description=FactorioReforge
After=network.target

[Service]
Type=simple
User=factorio
WorkingDirectory=/home/factorio/FactorioReforge
ExecStart=/home/factorio/FactorioReforge/.venv/bin/python -m factorio_reforge
Restart=on-failure
KillSignal=SIGINT
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
```

`KillSignal=SIGINT` 很关键：SIGINT 才是优雅路径，它会停 Factorio 并等它退出。
`TimeoutStopSec` 必须大于 `server.stop_timeout` 加上你的世界存一次档的时间，
否则 systemd 会在服务器正在写存档的时候把它 SIGKILL 掉。

没有终端时交互式控制台不可用——请改用 Telegram 或游戏内聊天来控制服务器。
