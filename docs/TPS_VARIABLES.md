# TPS、服务器状态与玩家查询变量

[返回项目说明](../README.md) · [配置字段](CONFIGURATION.md) · [初始模板和输出示例](TPS_TEMPLATES.md) · [网页管理](WEB_PANEL.md)

插件将各查询方式实际取得的数据转换为变量，交给同一份可编辑机器人回复模板。当前有 **85 个变量、6 套初始模板**，含既有 spark 的 19 项指标、跨核心数值、来源/耗时、状态和玩家字段。未返回的值使用 tps_missing_value（默认“暂无数据”），不会从其他核心或历史报告补造。

## 从网页使用

1. 配置与菜单 → TPS 设置，选择查询方式和实际指令，将“TPS 回复方式”设为“机器人变量模板”。
2. 配置与菜单 → 变量文档，按数据格式/核心筛选变量，查看含义、单位和可能的数据来源。
3. 在模板预览中选择 TPS 模板，再选通用、spark、Paper/Spigot、Forge/NeoForge、原版 tick query 或自定义回复示例。
4. 编辑文字与变量，点击“应用到回复模板”，最后“保存并生效”。预览只使用该示例的数据；例如原版不会出现 spark CPU 或 Forge Overall 值。

只想保留服务端原文时，选择 raw。机器人模板模式使用 messages.tps，失败或未识别内容使用 messages.tps_error；已有自定义文本保留，不强制固定报告布局。

## 查询方式与可提供的数据

| 查询内容 | 常见指令 | 变量含义 |
| --- | --- | --- |
| spark | spark tps | tps_5s/10s/1m/5m/15m、Tick min/med/P95/max、系统/进程 CPU |
| Paper/Spigot 等时间窗口输出 | tps | 明确标注的各窗口 TPS，包含可识别的 1s/30s；缺失窗口不推断 |
| Forge/NeoForge | forge tps，或核心实际支持的自定义指令 | Overall TPS/MSPT、全部维度及主世界/下界/末地数值；不把维度平均当总体 |
| 原版 | tick query | 目标/配置 Tick 速率、运行状态、明确报告的平均耗时与百分位；目标速率不等于实测 TPS |
| 自定义性能回复 | 管理员配置的只读查询指令 | 明确标注的 TPS、MSPT、CPU、内存、实体；未知格式保留原文 |

“可能来源”说明哪类回复可提供字段，不代表每次命令一定含此值。CPU、内存和实体并非所有 TPS 指令都会返回，插件不会为补齐这些变量自动执行额外控制台命令、修改计分板或安装 agent。

## 指标变量

| 变量 | 含义 | 单位 | 可能来源 | 示例 |
| --- | --- | --- | --- | --- |
| `{tps_5s}` | 最近 5 秒的 TPS | TPS | spark、Paper/Spigot | 13.24 |
| `{tps_10s}` | 最近 10 秒的 TPS | TPS | spark、Paper/Spigot | 13.37 |
| `{tps_1m}` | 最近 1 分钟的 TPS | TPS | spark、Paper/Spigot | 15.95 |
| `{tps_5m}` | 最近 5 分钟的 TPS | TPS | spark、Paper/Spigot | 19.03 |
| `{tps_15m}` | 最近 15 分钟的 TPS | TPS | spark、Paper/Spigot | 19.67 |
| `{mspt_10s_min}` | 最近 10 秒的最小 Tick 耗时 | ms | spark | 69.1 |
| `{mspt_10s_med}` | 最近 10 秒的 Tick 耗时中位数 | ms | spark | 74.3 |
| `{mspt_10s_p95}` | 最近 10 秒的 Tick 耗时第 95 百分位 | ms | spark | 79.6 |
| `{mspt_10s_max}` | 最近 10 秒的最大 Tick 耗时 | ms | spark | 103.2 |
| `{mspt_1m_min}` | 最近 1 分钟的最小 Tick 耗时 | ms | spark | 31.1 |
| `{mspt_1m_med}` | 最近 1 分钟的 Tick 耗时中位数 | ms | spark | 64.6 |
| `{mspt_1m_p95}` | 最近 1 分钟的 Tick 耗时第 95 百分位 | ms | spark | 77.0 |
| `{mspt_1m_max}` | 最近 1 分钟的最大 Tick 耗时 | ms | spark | 103.5 |
| `{cpu_system_10s}` | 最近 10 秒的系统整体 CPU 使用率 | % | spark | 33 |
| `{cpu_system_1m}` | 最近 1 分钟的系统整体 CPU 使用率 | % | spark | 35 |
| `{cpu_system_15m}` | 最近 15 分钟的系统整体 CPU 使用率 | % | spark | 35 |
| `{cpu_process_10s}` | 最近 10 秒的 MC Java 进程 CPU 使用率 | % | spark | 14 |
| `{cpu_process_1m}` | 最近 1 分钟的 MC Java 进程 CPU 使用率 | % | spark | 14 |
| `{cpu_process_15m}` | 最近 15 分钟的 MC Java 进程 CPU 使用率 | % | spark | 15 |

## 跨核心变量变量

| 变量 | 含义 | 单位 | 可能来源 | 示例 |
| --- | --- | --- | --- | --- |
| `{perf_format}` | 识别的格式：spark / Paper/Spigot / Forge/NeoForge / tick query / 自定义 / 未识别 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | Forge/NeoForge |
| `{perf_parse_status}` | 跨核心解析结果：已解析 / 部分 / 未解析 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 已解析 |
| `{perf_tps}` | 总体或最短可用窗口的 TPS；目标 Tick 速率不会填入 | TPS | spark、Paper/Spigot、Forge/NeoForge、自定义回复 | 19.98 |
| `{perf_tps_windows}` | 本次回复实际提供的 TPS 窗口，保持标题顺序 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 1m / 5m / 15m |
| `{perf_tps_values}` | 与窗口对应的 TPS，保留 * 标记和缺失位置 | TPS | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 20.0* / 19.9 / 19.8 |
| `{perf_mspt}` | 核心明确标注的 Tick 耗时；只写 Tick time 时不推断统计类型，不以中位数充当平均值 | ms | Forge/NeoForge、原版 tick query、自定义回复 | 12.3 |
| `{perf_target_rate}` | 原版目标/配置 Tick 速率，非实测 TPS | tick/s | 原版 tick query | 20 |
| `{perf_tick_state}` | 原版明确报告的运行状态，未知时显示缺失值 | — | 原版 tick query | 正常 |
| `{perf_dimensions}` | Forge/NeoForge 所有维度与总体明细，不跨维度计算平均 | — | Forge/NeoForge | minecraft:overworld：TPS 20 · Tick 12 ms |
| `{perf_memory}` | 回复中实际提供的内存信息；未自行探测 JVM/进程 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 1024 / 2048 MB |
| `{perf_entities}` | 回复中实际提供的实体统计；保留 ticking/non-ticking 含义 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 1327 ticking / 12 non-ticking |
| `{perf_tps_line}` | 带标签的 TPS 汇总行 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | ⚡ TPS（1m / 5m / 15m）：20.0 / 19.9 / 19.8 |
| `{perf_tick_lines}` | 带标签的 Tick 明细，可能多行 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | ⏱ Tick 平均耗时：12.3 ms |
| `{perf_cpu_lines}` | 带标签的 CPU 明细，缺失时明确说明 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 🖥 CPU（进程）：14% |
| `{perf_memory_line}` | 带标签的内存行，缺失时明确说明 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 💾 内存：本次回复未提供 |
| `{perf_entities_line}` | 带标签的实体行，缺失时明确说明 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 🧍 实体：本次回复未提供 |
| `{perf_notice}` | 解析说明，包含目标速率、星号或缺失提醒 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | ；说明：目标速率不等于实测 TPS |
| `{tps_1s}` | 回复明确标注的最近 1 秒 TPS | TPS | spark、Paper/Spigot | 19.99 |
| `{tps_30s}` | 回复明确标注的最近 30 秒 TPS | TPS | spark、Paper/Spigot | 19.95 |
| `{tps_overall}` | Forge/NeoForge 明确标注的 Overall TPS，非维度平均 | TPS | Forge/NeoForge | 19.98 |
| `{mspt_overall}` | Forge/NeoForge 明确标注的 Overall 平均 Tick 耗时 | ms | Forge/NeoForge | 13.5 |
| `{mspt_avg}` | 回复明确报告的平均 Tick 耗时；spark 中位数不填此变量 | ms | Forge/NeoForge、原版 tick query、自定义回复 | 12.3 |
| `{mspt_p50}` | tick query 明确标注的第 50 百分位 Tick 耗时 | ms | 原版 tick query | 11.9 |
| `{mspt_p95}` | tick query 明确标注的第 95 百分位 Tick 耗时 | ms | 原版 tick query | 22.4 |
| `{mspt_p99}` | tick query 明确标注的第 99 百分位 Tick 耗时 | ms | 原版 tick query | 31.0 |
| `{cpu_usage}` | 自定义回复中仅标注 CPU 的数值；不假定是进程或系统 | % | 自定义回复 | 35 |
| `{memory_used}` | 内存已用量原始数值，需配合 memory_used_unit | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 1024 |
| `{memory_used_unit}` | 内存已用量单位，保留回复原始单位 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | MB |
| `{memory_total}` | 内存总量原始数值，需配合 memory_total_unit | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 2048 |
| `{memory_total_unit}` | 内存总量单位，保留回复原始单位 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | MB |
| `{memory_max}` | 回复另行明确标注的内存最大值，不能从 total 推断 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 4096 |
| `{memory_max_unit}` | 内存最大值的原始单位 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | MB |
| `{entities_ticking}` | 实体统计明确标注的 ticking 数量 | 个 | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 1327 |
| `{entities_non_ticking}` | 实体统计明确标注的 non-ticking 数量 | 个 | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 12 |
| `{entities_total}` | 明确的实体总量，或上述两项的和，仅限回复覆盖范围 | 个 | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 1339 |
| `{forge_dimension_count}` | 回复列出的维度条数，不包含 Overall 汇总行 | 个 | Forge/NeoForge | 2 |
| `{forge_dimensions_json}` | 全部维度与 Overall 的 JSON 数组；不可解析项为 null | — | Forge/NeoForge | [{"dimension":"minecraft:overworld","tps":"20","mspt":"12.3"}] |
| `{forge_overworld_tps}` | 主世界明确报告的 TPS；识别 minecraft:overworld 或旧 Dim 0 | TPS | Forge/NeoForge | 20 |
| `{forge_overworld_mspt}` | 主世界明确报告的平均 Tick 耗时 | ms | Forge/NeoForge | 12.3 |
| `{forge_nether_tps}` | 下界明确报告的 TPS；识别 minecraft:the_nether 或旧 Dim -1 | TPS | Forge/NeoForge | 20 |
| `{forge_nether_mspt}` | 下界明确报告的平均 Tick 耗时 | ms | Forge/NeoForge | 1.2 |
| `{forge_end_tps}` | 末地明确报告的 TPS；识别 minecraft:the_end 或旧 Dim 1 | TPS | Forge/NeoForge | 19.8 |
| `{forge_end_mspt}` | 末地明确报告的平均 Tick 耗时 | ms | Forge/NeoForge | 2.5 |

## 查询信息变量

| 变量 | 含义 | 单位 | 可能来源 | 示例 |
| --- | --- | --- | --- | --- |
| `{query_source}` | 本次实际来源：RCON / 服务端新增日志；后者仍由本次 RCON 命令触发 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | RCON |
| `{query_command}` | 本次实际执行的命令 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | forge tps |
| `{query_latency_ms}` | 实际采集耗时，包含排队时间；缓存命中保留原耗时 | ms | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 12.4 |
| `{query_checked_at}` | 采集完成时间，ISO 8601 / UTC | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 2026-10-17T02:00:00+00:00 |
| `{query_cached}` | 是否使用短缓存：是 / 否 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 否 |
| `{query_age_seconds}` | 距离本次结果采集完成的时间 | s | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 0.2 |
| `{query_error}` | 失败原因；成功时为空 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 服务端返回空响应 |
| `{query_warning}` | 补充说明；正常时为空 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 在线人数未能解析 |
| `{rcon_online}` | 本次是否验证 RCON：是 / 否 / 未验证 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 是 |
| `{tps_source}` | spark 查询来源：rcon / log / unavailable | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | log |

## 辅助变量

| 变量 | 含义 | 单位 | 可能来源 | 示例 |
| --- | --- | --- | --- | --- |
| `{server}` | 服务器显示名称 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 服务器 1 |
| `{server_id}` | 服务器配置 ID | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | server1 |
| `{tps_command}` | 配置的控制台命令（RCON 查询时使用） | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | spark tps |
| `{result}` | 当前可读结果；spark/玩家为原始回复，状态为状态说明（兼容旧模板） | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | TPS from last ... |
| `{tps_raw}` | 去掉颜色代码后的原始响应，与 result 相同 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | TPS from last ... |
| `{tps_parse_status}` | spark：完整/部分/未解析；其他格式：已解析/部分/未解析 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 完整 |
| `{tps_missing_fields}` | 缺失或无效指标的变量名列表 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | mspt_10s_min, mspt_10s_med |
| `{tps_warning}` | 根据 tps_warning 模板生成的缺失提示；完整时为空 | — | spark、Paper/Spigot、Forge/NeoForge、原版 tick query、自定义回复、状态/玩家 | 部分 spark 数据不可用… |

## 服务器状态变量

| 变量 | 含义 | 单位 | 可能来源 | 示例 |
| --- | --- | --- | --- | --- |
| `{status}` | 本次状态查询结果，不等同于游戏端口检测 | — | 状态/玩家 | 在线（RCON 查询成功） |
| `{players_online}` | list 回复中的在线人数；无法解析时不填 0 | 人 | 状态/玩家 | 2 |
| `{players_max}` | list 回复中的人数上限 | 人 | 状态/玩家 | 20 |
| `{players_names}` | list 回复中的玩家列表；明确 0 人时为 无 | — | 状态/玩家 | Steve, Alex |
| `{players_raw}` | 去除颜色码后的原始玩家查询回复 | — | 状态/玩家 | There are 2 of a max of 20 players online: Steve, Alex |

## 初始机器人模板

默认模板中的 perf_*_line 是便于起步的组合变量；每一个数值也有单独变量，可以删除组合行、改用 tps_overall、mspt_p95、memory_used 等字段自由排版。

```text
📈 服务器性能 [{server}]

{perf_tps_line}
{perf_tick_lines}
{perf_cpu_lines}
{perf_entities_line}
{perf_memory_line}{perf_notice}

ℹ️ 来源：{query_source} | 格式：{perf_format} | 指令：{query_command}
耗时：{query_latency_ms} ms | 缓存：{query_cached}
```

各核心的纯数值模板及渲染示例见[初始模板文档](TPS_TEMPLATES.md)。这六套模板、样例数据与网页使用同一个定义，机器可读目录位于 [tps.variables.json](../tps.variables.json)。

## 数值和单位规则

- 19 个 spark 指标保持原有名称。CPU 数值不含 %，耗时单位为 ms；med/P50 是中位数，不能充当平均值。
- perf_tps 取明确的 Overall/实测标量，或最短可用 TPS 时间窗口。需要固定窗口请用 tps_1m 等变量；原版目标速率不会填入 perf_tps。
- perf_mspt 保留核心原始 Tick 耗时标注。只有明确写“平均”或 Forge Mean 时才填 mspt_avg；Spark min/med/P95/max 不会被当作平均耗时。
- tps_overall/mspt_overall 仅用 Forge Overall；没有 Overall 时为空，不对维度求平均。forge_dimension_count 不包含 Overall；forge_dimensions_json 给出所有行的 dimension、tps、mspt，数值用字符串保留原始精度，无法解析项为 null。
- 常用维度别名仅匹配 minecraft:overworld、minecraft:the_nether、minecraft:the_end，以及旧 Dim 0/-1/1。其他维度保存在完整列表和 JSON 中。
- memory_used/total/max 各自配套原始单位。total 不能冒充 max，KB/MB/GB 与 KiB/MiB/GiB 不做未经声明的混换；这些字段也不代表另行探测了 JVM 进程 RSS。
- entities_total 为明确总量或 ticking+non-ticking 之和，范围仅限回复覆盖区域；未提供的计数不填 0。
- perf_tps_values 保留原文的 * 标记，单独数值变量不带 *；插件不凭标记推断其原因。

## 未识别、缺失和模板语法

只支持 {变量}，字面花括号写 {{ 和 }}；不支持属性访问、表达式或 :.2f。字段全部缺失/格式未识别时用 tps_error，默认保留完整原始回复；原文模式与长模板回复不受旧 6000/16000 字符截断限制，仍保留 RCON 总响应保护。较长群消息继续分段发送。

部分已识别数值不可解析时保留缺失位置，不把后续窗口的值向前移；默认附上原文以便排查。没有提供 CPU/内存等额外类型，不等于该核心永远不支持它。

## 超时、缓存与实际来源

query_timeout_seconds 包括排队、锁等待、连接和探测；0 自动使用 max(5, 2×rcon_timeout+2+本服 spark 日志等待) 秒，可自定义为 1～300 秒。query_cache_seconds 是短结果缓存；tps_capability_cache_seconds 是原生可用指令缓存，默认 600 秒。原生自动探测优先自定义命令，再尝试 tps、forge tps、tick query；网络错误不盲目重复探测。

每条结果携带独立的来源、命令、耗时、采集时间、缓存状态，避免多服之间互相覆盖。缓存命中时保留原采集耗时，用 query_age_seconds 表示结果年龄。状态/玩家复用同一次 list 请求，但与 TPS 的数值字段分开。

## 控制台有报告但 RCON 返回空响应

spark 的异步报告可能只写控制台日志。无需 Java 采集器，可在“TPS 设置 → 对应服务器 → spark 无回复时：新增日志回读”开启本次新增日志读取。

| 每服配置项 | 默认 | 作用 |
| --- | --- | --- |
| spark_log_fallback | true | 仅无有效 spark RCON 回复时等待日志 |
| spark_log_path | logs/latest.log | 本服内相对路径，AstrBot 需有读取权限 |
| spark_log_encoding | utf-8 | 可选 gb18030，以实际文件编码为准 |
| spark_log_wait_seconds | 3 | 1～15 秒，总体仍受查询超时限制 |

发令前记录日志末尾，只读之后新增的本服报告；旧日志、跨线程混用、检测到的多份报告、轮转/截断/重写都会被拒绝或提示重试。每次最多扫描 256 KB。日志没有请求 ID，只能按新增位置、线程和短窗口关联，请避免其他面板同时手动查询。只在屏幕显示而不写日志文件时无法回读，Docker 需挂载日志目录。

成功时 query_source 为“服务端新增日志”、tps_source 为 log，RCON 连接/认证失败不会被日志掩盖。执行 spark tps 时可取得报告，无需额外选择查询方式，是否模板化由 tps_output 决定。

## 取消采集器和旧配置迁移

不附带 Java agent、JAR 或独立指标文件采集。旧 tps_source/spark_file/spark_file_max_age 配置被忽略，保存或恢复时清理；旧 tps_file_age_seconds 占位符仅返回缺失值，不再宣传为可用指标。变量 tps_source 本身保留，值为 rcon/log/unavailable。

旧 MC 启动脚本若含指向 astrbot-spark-agent.jar 的 -javaagent 项，移除该项并完整重启 Minecraft；只重载 AstrBot 不能停止已加载 agent。插件不改写启动脚本、不删除服务端旧文件。

## 默认配置文件

- [config.default.json](../config.default.json)：初始机器人模板与根配置默认值。
- [config.example.json](../config.example.json)：多服、多群和共享服务器示例。
- [tps.variables.json](../tps.variables.json)：85 个变量、来源元信息和 6 套可编辑示例。
