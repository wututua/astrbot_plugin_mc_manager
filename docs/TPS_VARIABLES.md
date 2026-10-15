# spark TPS 变量表与默认配置

[返回项目说明](../README.md) · [配置字段](CONFIGURATION.md) · [Java 采集器](../companion/README.md)

群聊指令仍为 `/mc tps [服务器 ID]`；控制台查询命令默认 `spark tps`。spark 的三组输出已拆成 19 个指标变量，可放在 `messages.tps` 中任意组合。

## 19 个指标变量

变量名包含时间窗口：s 是秒，m 是分钟。所有指标是数值字符串，保留命令输出的小数精度；TPS 值中的星号、颜色码会去掉。CPU 变量不包含百分号，Tick 耗时不包含 ms，所以单位可按自己的模板添加。

| 占位符 | 含义 | 单位 | 示例值 |
|---|---|---|---|
| `{tps_5s}` | 最近 5 秒的 TPS | TPS | 13.24 |
| `{tps_10s}` | 最近 10 秒的 TPS | TPS | 13.37 |
| `{tps_1m}` | 最近 1 分钟的 TPS | TPS | 15.95 |
| `{tps_5m}` | 最近 5 分钟的 TPS | TPS | 19.03 |
| `{tps_15m}` | 最近 15 分钟的 TPS | TPS | 19.67 |
| `{mspt_10s_min}` | 最近 10 秒的最小 Tick 耗时 | ms | 69.1 |
| `{mspt_10s_med}` | 最近 10 秒的 Tick 耗时中位数 | ms | 74.3 |
| `{mspt_10s_p95}` | 最近 10 秒的 Tick 耗时第 95 百分位 | ms | 79.6 |
| `{mspt_10s_max}` | 最近 10 秒的最大 Tick 耗时 | ms | 103.2 |
| `{mspt_1m_min}` | 最近 1 分钟的最小 Tick 耗时 | ms | 31.1 |
| `{mspt_1m_med}` | 最近 1 分钟的 Tick 耗时中位数 | ms | 64.6 |
| `{mspt_1m_p95}` | 最近 1 分钟的 Tick 耗时第 95 百分位 | ms | 77.0 |
| `{mspt_1m_max}` | 最近 1 分钟的最大 Tick 耗时 | ms | 103.5 |
| `{cpu_system_10s}` | 最近 10 秒的系统整体 CPU 使用率 | % | 33 |
| `{cpu_system_1m}` | 最近 1 分钟的系统整体 CPU 使用率 | % | 35 |
| `{cpu_system_15m}` | 最近 15 分钟的系统整体 CPU 使用率 | % | 35 |
| `{cpu_process_10s}` | 最近 10 秒的 MC Java 进程 CPU 使用率 | % | 14 |
| `{cpu_process_1m}` | 最近 1 分钟的 MC Java 进程 CPU 使用率 | % | 14 |
| `{cpu_process_15m}` | 最近 15 分钟的 MC Java 进程 CPU 使用率 | % | 15 |

`med` 是中位数，`p95` 是第 95 百分位（95% 的样本不超过这个耗时）。system 是系统整体 CPU，process 是运行 MC 的 Java 进程 CPU，含该进程中所有模组、插件及线程的 CPU 使用。

## 辅助变量

| 占位符 | 含义 | 示例 |
|---|---|---|
| `{server}` | 服务器显示名称 | 服务器 1 |
| `{server_id}` | 服务器配置 ID | server1 |
| `{tps_command}` | 配置的控制台命令（RCON 查询时使用） | spark tps |
| `{result}` | 去掉颜色代码后的原始响应（兼容旧模板） | TPS from last ... |
| `{tps_raw}` | 去掉颜色代码后的原始响应，与 result 相同 | TPS from last ... |
| `{tps_parse_status}` | 完整、部分或未解析 | 完整 |
| `{tps_missing_fields}` | 缺失或无效指标的变量名列表 | mspt_10s_min, mspt_10s_med |
| `{tps_warning}` | 根据 tps_warning 模板生成的缺失提示；完整时为空 | 部分 spark 数据不可用… |

这些指标变量只在 TPS 回复、TPS 缺失提示和 TPS 失败回复中填充。其他消息的变量见 [README](../README.md#自定义指令与回复)。

## 默认回复模板

`config.default.json`、`config.example.json` 和 AstrBot 原生配置 schema 都已提供下列默认模板：

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

对用户的样例，正常返回如下：

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

## 自定义回复

编辑插件配置的 `messages.tps`，例如：

```text
[{server}]
TPS：{tps_5s} / {tps_1m} / {tps_15m}
Tick P95：{mspt_10s_p95} ms
系统 CPU：{cpu_system_1m}%
MC 进程 CPU：{cpu_process_1m}%
{tps_warning}
```

也可在管理面板“配置与菜单”中使用专门的 TPS 模板编辑框；下方可以展开完整变量表，修改会同步到完整 JSON。点击“使用 spark 默认配置”会把本配置的各服 `tps_command` 改为 `spark tps` 并恢复上述默认模板，之后点击“保存并生效”。

默认配置文件用途：

- [config.default.json](../config.default.json)：完整默认配置，新部署使用；服务端与群绑定为空，需要填写实际信息。
- [config.example.json](../config.example.json)：包含两群五服、RCON 端口和环境变量密码引用的完整配置示例。
- [tps.variables.json](../tps.variables.json)：机器可读的变量清单，包含名称、说明、单位与示例值。

已有部署请保留原有服务器目录、群绑定、密码与权限配置，修改 TPS 相关字段即可。已有的自定义模板不会在普通升级时自动重置。旧模板中的 `{result}` 仍能显示原始输出。

## 缺失值和失败处理

- `tps_missing_value` 默认 `暂无数据`，可以改为 `--` 等单行文字。
- 全部 19 个指标可用时，`{tps_parse_status}` 是 `完整`，`{tps_warning}` 为空。
- 部分指标可用时，缺失位置显示缺失文字；`messages.tps_warning` 默认显示缺失变量名及原始输出，可以自定义或留空。
- 没有解析出任何指标时，用 `messages.tps_error` 显示失败及原始响应。
- 连接失败、未知命令、空响应、NaN 或缺失数据不会被自动变成正常的 20 TPS 或 0% CPU。
- 时间窗口按输出标题映射，统计项按 min/med/95%ile/max 标题映射；不完整的数据行不会错位填入其他窗口。

模板只支持 `{变量}` 替换；字面花括号写成 `{{` 和 `}}`，不支持属性访问、计算或 Python 的小数格式表达式。

## spark 的 RCON 回复为空时

部分 spark 环境异步执行指令，而 RCON 的响应缓冲在命令返回时结束，可能拿不到之后的 spark 输出。spark 的 [issue 119](https://github.com/lucko/spark/issues/119) 记录了这个限制。等待同一个 RCON 缓冲不能可靠修复它。

插件因此支持三种 `servers[].tps_source`：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 优先读取有效的本地 spark API 报告；没有有效报告时尝试 RCON 命令 |
| `rcon` | 只读 `tps_command` 的控制台回复 |
| `file` | 只读本地采集器报告 |

可选采集器是包内 `companion/astrbot-spark-agent.jar`。在每台服启动命令中添加 `-javaagent:…/astrbot-spark-agent.jar`，服务端 JVM 会读取已安装 spark 的公开 API，每 2 秒更新本服 `.astrbot-spark-tps.txt`。配置中的 `spark_file` 与此文件对应。

完整安装步骤见 [采集器说明](../companion/README.md)。它不会自动修改你的启动脚本。报告超过 `spark_file_max_age`（默认 10 秒）后会拒绝使用，停机或采集失败后不会持续返回旧数据。

## 调研依据

- spark 指令实现： https://github.com/lucko/spark/blob/master/spark-common/src/main/java/me/lucko/spark/common/command/modules/HealthModule.java
- spark 公共 API： https://github.com/lucko/spark/blob/master/spark-api/src/main/java/me/lucko/spark/api/Spark.java
- 统计窗口： https://github.com/lucko/spark/blob/master/spark-api/src/main/java/me/lucko/spark/api/statistic/StatisticWindow.java
- RCON 限制记录： https://github.com/lucko/spark/issues/119
