# TPS 初始模板与机器人输出示例

[完整变量文档](TPS_VARIABLES.md) · [网页配置](WEB_PANEL.md)

这些是起始文本，均可修改。所有核心使用 messages.tps；每台服务器的 tps_output 选择 template。已有自定义模板不会自动替换，网页中选择示例后需“应用到回复模板 → 保存并生效”。不同格式的示例仅填各自真实返回的字段，不跨核心借值。

## 通用初始模板

参考指令：`spark tps`。

模板：

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

机器人输出示例（文档样例数据）：

```text
📈 服务器性能 [服务器 1]

⚡ TPS（5秒 / 10秒 / 1分钟 / 5分钟 / 15分钟）：13.24 / 13.37 / 15.95 / 19.03 / 19.67
⏱ Tick 10秒（最小 / 中位 / P95 / 最大）：69.1 / 74.3 / 79.6 / 103.2 ms
⏱ Tick 1分钟（最小 / 中位 / P95 / 最大）：31.1 / 64.6 / 77.0 / 103.5 ms
🖥 CPU（系统，10秒 / 1分钟 / 15分钟）：33 / 35 / 35 %
🖥 CPU（MC进程，10秒 / 1分钟 / 15分钟）：14 / 14 / 15 %
🧍 实体：本次回复未提供
💾 内存：本次回复未提供

ℹ️ 来源：RCON | 格式：spark | 指令：spark tps
耗时：12.4 ms | 缓存：否
```

## spark 完整指标

参考指令：`spark tps`。

模板：

```text
[{server}] spark 性能
TPS（5秒 / 10秒 / 1分钟 / 5分钟 / 15分钟）：
{tps_5s} / {tps_10s} / {tps_1m} / {tps_5m} / {tps_15m}
Tick 耗时（毫秒，最小 / 中位数 / P95 / 最大）：
最近10秒：{mspt_10s_min} / {mspt_10s_med} / {mspt_10s_p95} / {mspt_10s_max}
最近1分钟：{mspt_1m_min} / {mspt_1m_med} / {mspt_1m_p95} / {mspt_1m_max}
CPU（单位%，10秒 / 1分钟 / 15分钟）：
系统：{cpu_system_10s} / {cpu_system_1m} / {cpu_system_15m}
MC进程：{cpu_process_10s} / {cpu_process_1m} / {cpu_process_15m}{tps_warning}
```

机器人输出示例（文档样例数据）：

```text
[服务器 1] spark 性能
TPS（5秒 / 10秒 / 1分钟 / 5分钟 / 15分钟）：
13.24 / 13.37 / 15.95 / 19.03 / 19.67
Tick 耗时（毫秒，最小 / 中位数 / P95 / 最大）：
最近10秒：69.1 / 74.3 / 79.6 / 103.2
最近1分钟：31.1 / 64.6 / 77.0 / 103.5
CPU（单位%，10秒 / 1分钟 / 15分钟）：
系统：33 / 35 / 35
MC进程：14 / 14 / 15
```

## Paper / Spigot TPS

参考指令：`tps`。

模板：

```text
[{server}] TPS
1分钟：{tps_1m}
5分钟：{tps_5m}
15分钟：{tps_15m}
来源：{query_source} · {query_latency_ms} ms
```

机器人输出示例（文档样例数据）：

```text
[服务器 1] TPS
1分钟：20.0
5分钟：19.9
15分钟：19.8
来源：RCON · 12.4 ms
```

## Forge / NeoForge 维度指标

参考指令：`forge tps`。

模板：

```text
[{server}] Forge 性能
总体 TPS：{tps_overall}
总体平均 Tick：{mspt_overall} ms
主世界：{forge_overworld_tps} TPS / {forge_overworld_mspt} ms
下界：{forge_nether_tps} TPS / {forge_nether_mspt} ms
维度数：{forge_dimension_count}
{perf_dimensions}
来源：{query_source} · {query_latency_ms} ms
```

机器人输出示例（文档样例数据）：

```text
[服务器 1] Forge 性能
总体 TPS：19.98
总体平均 Tick：13.5 ms
主世界：20.0 TPS / 12.3 ms
下界：20.0 TPS / 1.2 ms
维度数：2
minecraft:overworld：TPS 20.0 · Tick 12.3 ms
minecraft:the_nether：TPS 20.0 · Tick 1.2 ms
Overall：TPS 19.98 · Tick 13.5 ms
来源：RCON · 12.4 ms
```

## 原版 tick query 指标

参考指令：`tick query`。

模板：

```text
[{server}] Tick 状态
目标速率：{perf_target_rate} tick/s（非实测 TPS）
状态：{perf_tick_state}
平均耗时：{mspt_avg} ms
P50 / P95 / P99：{mspt_p50} / {mspt_p95} / {mspt_p99} ms
来源：{query_source} · {query_latency_ms} ms
```

机器人输出示例（文档样例数据）：

```text
[服务器 1] Tick 状态
目标速率：20 tick/s（非实测 TPS）
状态：正常
平均耗时：12.3 ms
P50 / P95 / P99：11.9 / 22.4 / 31.0 ms
来源：RCON · 12.4 ms
```

## 自定义性能回复

参考指令：`custom:health`。

模板：

```text
[{server}] 性能
TPS：{perf_tps}
平均 Tick：{mspt_avg} ms
CPU：{cpu_usage}%
内存：{memory_used} {memory_used_unit} / {memory_total} {memory_total_unit}
最大内存：{memory_max} {memory_max_unit}
实体：{entities_total}（{entities_ticking} ticking / {entities_non_ticking} non-ticking）
来源：{query_source} · {query_latency_ms} ms
```

机器人输出示例（文档样例数据）：

```text
[服务器 1] 性能
TPS：19.97
平均 Tick：12.4 ms
CPU：35%
内存：1024 MB / 2048 MB
最大内存：4096 MB
实体：1339（1327 ticking / 12 non-ticking）
来源：RCON · 12.4 ms
```

## 未提供数据时

模板里的变量没有数据时显示 tps_missing_value，默认“暂无数据”。例如 Paper 的回复只有 1m/5m/15m TPS，放入 forge_nether_tps 或 mspt_p95 不会得到推测值。tick query 的 perf_target_rate 为目标速率，perf_tps 则保持缺失。

内存与实体示例仅演示能够识别包含这些字段的自定义回复，不保证 tps、spark tps 或所有核心本身返回这些信息；不会额外创建计分板或扫描系统进程。
