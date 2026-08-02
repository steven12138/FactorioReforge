# Factorio 实测笔记 —— 实机采样得到的结论

环境：Factorio **2.0.77** (build 84539, linux64, headless)、Arch Linux、Python 3.14.6。
原始采样见 `factorio_output_samples.txt`，探测脚本 `scripts/probe_stdout.py`。

**English: [factorio-notes.md](factorio-notes.md)**

---

## 1. stdout 缓冲：不是问题 ✅

计划里把「C++ 程序全缓冲」列为最高风险，实测**不成立**。用最朴素的
`asyncio.create_subprocess_exec(stdout=PIPE)`：

```
0.01s  第一行到达
0.57s  changing state ... to(InGame)
6.02s  Players (0):        ← 6.00s 时写入 /players，两帧内响应
9.02s  [CHAT] <server>: hello from probe
```

Factorio 自己按行 flush。**不需要 pty，也不需要 `stdbuf -oL`。**
`core/process.py` 直接用 asyncio 管道，pty 分支根本不用实现。

## 2. stdin EOF 不会关服 ⚠️（与假设相反）

计划里写的是「stdin EOF → Factorio 自杀，可以当作兜底停服手段」。2.0.77 实测：

```
6.01s  Error InterruptibleStdioStream.cpp:55: Got EOF on stdin; closing
26.0s  >>> STILL ALIVE 20s after stdin EOF   ← 服务器还在跑
```

只打印一条 Error，进程继续运行。这比原假设**更糟**：关掉 stdin 既杀不掉服务器，
又永久失去了唯一的命令通道。

**修正**：stdin 全程保持打开，绝不 close。停服顺序改为
`/quit` → `SIGINT` → `SIGTERM` → `SIGKILL`。SIGINT 实测是优雅的，会先存档：

```
16.011 Received SIGINT, shutting down
16.011 Quitting: signal.
16.011 Info MainLoop.cpp:437: Saving map as .../probe.zip
16.030 Info MainLoop.cpp:448: Saving progress: 100.000000%
16.528 Goodbye
```

## 3. stdout 是**四**种格式，不是两种 ⚠️

计划里的双正则方案不够用。实测：

| # | 形态 | 样例 |
|---|---|---|
| A | 引擎日志，带等级 + 源码位置 | `   0.578 Info ServerMultiplayerManager.cpp:808: updateTick(926) changing state from(CreatingGame) to(InGame)` |
| B | 引擎日志，**只有运行秒数**，无等级/源码位置 | `   0.577 Hosting game at IP ADDR:({0.0.0.0:34199})`<br>`   0.543 Loading map /.../probe.zip: 863501 bytes.`<br>`  16.011 Received SIGINT, shutting down` |
| C | 游戏事件，带日期 + `[TAG]` | `2026-08-02 02:16:35 [CHAT] <server>: hello from probe` |
| D | **裸回执，零前缀** | `Players (0):` / `2.0.77` / `7 seconds` |

D 类是 stdin 命令的回执。它和普通文本在形态上完全无法区分，
因此解析器的策略必须是**按 C → A → B 顺序尝试，全不匹配则归入 D
（`COMMAND_RESPONSE`）**，而不是把「解析失败」当成错误。

B 类会让「必须含等级和源码位置的 fullmatch」直接失败 ——
A 里的 `Info xxx.cpp:NN:` 部分是可选的，正则必须写成可选分组。

## 4. 已确认的锚点字符串

- 启动完成：`changing state from(CreatingGame) to(InGame)`
- 监听成功：`Hosting game at IP ADDR:({0.0.0.0:34199})`
- RCON 就绪：`Starting RCON interface at IP ADDR:(...)`
- 关服完成：`Goodbye`
- 自己发的聊天回声：`[CHAT] <server>: ...` ← Telegram 桥接防死循环靠它

存档完成有**两种**形态，两种都必须认，否则快照会白等到超时：

- `/server-save` 走 AppManager：`Info AppManager.cpp:419: Saving finished`
- 关服时的存档走 MainLoop：`Info MainLoop.cpp:448: Saving progress: 100.000000%`

## 5. 后续实测追加的结论

以下不属于最初的 M0，但同属「文档说的和实际不一样」，记在一起：

- **`game.table_to_json` 在 2.0 已被移除**，是 `helpers.table_to_json`。
  所有结构化查询都依赖它。
- **`force.get_evolution_factor()` 现在要传 surface**；
  `force.item_production_statistics` 变成了 `force.get_item_production_statistics(surface)`。
- **`[gps=x,y,surface]` 富文本是可点击的**，点了会在所有人地图上 ping 该位置。
  最初以为「Factorio 聊天不可点击」，是错的。
- **Source RCON 的哨兵技巧对 Factorio 无效** —— 它不会回应空命令，
  等哨兵回显会永久挂起。正确做法是读匹配 id 的第一个包，再短暂续读多包响应。
- **`add_chart_tag` 在未探索区域也能成功**，虽然官方文档说需要先探索。
- **Factorio 退出时用内存里的状态覆盖 `mod-list.json`**，
  丢弃运行期间的一切改动。这就是 `mod_manager` 必须单独记录意图、
  并在进程真正退出后重放的原因。

## 6. 未能自动验证的部分

`[JOIN]` / `[LEAVE]` / `[DEATH]` / `[KICK]` 需要真实客户端连接才能触发，本轮没有采到。
它们与已验证的 `[CHAT]` 同属 C 类格式，解析器按统一的 `[TAG] content` 正则处理，
tag 表做成开放集合（未知 tag 不报错，降级为 GENERAL_INFO 并只 warn 一次）。
等实际有玩家进服后补采样，再回填单测。
