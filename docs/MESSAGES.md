# 全部机器人消息自定义

在管理网页的 **设置 → 回复与广播** 编辑，支持按分类和文字、模板名、变量搜索。点击变量插入光标处，修改后点击 **保存并生效**。每项都可单独恢复默认。帮助、TPS 和消息互通页的专用编辑器与这里同步显示当前输入。也可直接修改配置中的 `messages` 对象。

配置缺少新模板时会自动补用默认文字，已有自定义内容会保留。完整默认配置见 [config.default.json](../config.default.json)，机器可读目录见 [messages.variables.json](../messages.variables.json)。

## 模板与变量规则

- `{变量名}` 插入本次操作数据。下面的表列出每个模板实际使用的动态值；命令别名变量对所有模板可用。TPS 的完整数值变量另见 [TPS_VARIABLES.md](TPS_VARIABLES.md)。
- `{prefix}` 自带 `/`；子指令变量 `{diagnose}`、`{backup}`、`{cancel}`、`{requests}`、`{approve}`、`{reject}`、`{mine}`、`{help}`、`{add}`、`{list}`、`{remove}`、`{modify}`、`{players}`、`{tps}`、`{status}`、`{servers}`、`{restart}`、`{logs}`、`{sync}`、`{import}` 使用配置的第一个别名。
- `{{`、`}}` 输出普通花括号。变量只替换一次，玩家输入和服务端原文中的花括号不会再次展开。不支持对象属性、下标、格式表达式或执行代码。
- 只想改外层回复时修改 `added`、`status`、`tps` 等；要改内部明细，再修改 `sync_*`、`perf_*`、`query_*`、`error_*` 等子模板。模板不能按名字引用其他模板，使用表中给出的结果变量即可。
- 留空表示该模板输出空文字。外层回复为空时不发送；内部片段为空时隐藏该段（外层模板自己写的分隔符/换行仍然保留）。重启任务和审核等操作仍会正常执行。待发送通知为空时直接确认消费，不会反复重试。
- 原文模式也有 `raw_tps` 外层模板，默认 `{result}`，可加标题或只保留部分文字；服务端返回的原文没有结构化的逐句替换。系统异常详情、历史日志记录、玩家聊天和死亡原文属于数据，可在外层模板中保留或省略对应变量。
- `error_unknown` 控制未归类的系统异常详情（默认 `{error}`）；`internal_error` 控制意外内部异常的友好提示。配置待修正时仍尽量保留有效的消息模板。
- `game_restart_countdown` 和 `game_restart_success` 控制游戏广播。`bridge_to_game` 可使用 `§` 染色码；通过固定 JSON tellraw 发送，模板不会变成 RCON 指令。
- 修改文案不会改变权限、条目归属、告警判断、RCON 命令或重启成功条件。AstrBot 自身的系统提示和日志不属于本插件消息模板。
- 已写入通知队列的文字在入队时确定；修改模板影响之后产生的通知。日志详情在操作发生时记录，修改 `audit_*` 影响新日志，旧日志可用 `log_detail` 控制是否显示。

## 修改示例

```json
{
  "messages": {
    "added": "✅ 已为 {player} 开通白名单（#{id}）\n{sync}",
    "sync_success": "{server}：白名单已更新",
    "error": "⚠️ {error}",
    "error_entry_owner": "这条白名单不属于你，请联系管理员。",
    "query_offline": "{error}",
    "restart_success": "{server} 已恢复，耗时 {seconds} 秒。{detail}",
    "perf_memory_line": "内存情况：{perf_memory}"
  }
}
```

这是部分配置示例，将这些键合并到已有 `messages` 中即可。

## 常用回复

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `backup_created` | 已创建插件数据备份：{result}<br>请在管理网页下载，备份包含配置凭据。 | `{result}`：本次完整结果或原始回复 |
| `alert` | ⚠️ [{server}] {alert_type}：{alert_detail} | `{server}`：服务器显示名称<br>`{alert_type}`：告警类型<br>`{alert_detail}`：告警明细 |
| `alert_recovery` | ✅ [{server}] {alert_type} 已恢复：{alert_detail} | `{server}`：服务器显示名称<br>`{alert_type}`：告警类型<br>`{alert_detail}`：告警明细 |
| `help` | Minecraft 管理菜单<br>{prefix} {add} <玩家名>：添加白名单<br>{prefix} {mine}：查看自己的白名单<br>{prefix} {remove} [编号或玩家名]：移除自己的白名单<br>{prefix} {modify} [编号或原名] <新玩家名>：修改自己的白名单<br>{prefix} {players} [服务器]：在线玩家<br>{prefix} {tps} [服务器]：服务器性能<br>{prefix} {help} all：全部指令 | `{prefix}`：主指令，包含 /<br>`{add}`：对应子指令配置中的第一个别名<br>`{mine}`：对应子指令配置中的第一个别名<br>`{remove}`：对应子指令配置中的第一个别名<br>`{modify}`：对应子指令配置中的第一个别名<br>`{players}`：对应子指令配置中的第一个别名<br>`{tps}`：对应子指令配置中的第一个别名<br>`{help}`：对应子指令配置中的第一个别名 |
| `added` | 已添加 #{id} {player}，所属用户：{user}<br>{sync} | `{id}`：白名单或申请固定编号<br>`{player}`：MC 玩家名<br>`{user}`：操作用户 ID<br>`{sync}`：按同步行模板生成的结果 |
| `removed` | 已移除 #{id} {player}<br>{sync} | `{id}`：白名单或申请固定编号<br>`{player}`：MC 玩家名<br>`{sync}`：按同步行模板生成的结果 |
| `modified` | 已修改 #{id} {old_player} → {player}<br>{sync} | `{id}`：白名单或申请固定编号<br>`{old_player}`：修改前的玩家名<br>`{player}`：MC 玩家名<br>`{sync}`：按同步行模板生成的结果 |
| `list` | 本群白名单（第 {page}/{pages} 页，共 {total} 条）<br>{entries} | `{page}`：当前页码<br>`{pages}`：总页数<br>`{total}`：总条数<br>`{entries}`：按行模板生成的明细 |
| `entry` | #{id} {player} · 所属：{owner} | `{id}`：白名单或申请固定编号<br>`{player}`：MC 玩家名<br>`{owner}`：条目所属用户 |
| `servers` | 本群服务器：<br>{entries} | `{entries}`：按行模板生成的明细 |
| `error` | MC 操作失败：{error} | `{error}`：失败原因，可由对应错误模板生成 |
| `sync` | {sync} | `{sync}`：按同步行模板生成的结果 |
| `import` | 已导入 {count} 条未托管条目（无归属，限管理员管理）。<br>{sync} | `{count}`：条目数量<br>`{sync}`：按同步行模板生成的结果 |
| `help_full` | Minecraft 管理菜单<br>{prefix} {add} <玩家名>：添加自己的白名单<br>{prefix} {list} [页码]：查看固定编号的白名单<br>{prefix} {remove} <编号或玩家名>：移除自己的条目<br>{prefix} {modify} <编号或玩家名> <新玩家名>：修改自己的条目<br>{prefix} {players} [服务器]：查看在线玩家<br>{prefix} {tps} [服务器]：查询 TPS<br>{prefix} {status} [服务器]：查看状态<br>{prefix} {servers}：查看本群服务器<br>{prefix} {restart} <服务器或 all>：重启（管理员）<br>{prefix} {logs} [页码]：操作日志（管理员）<br>{prefix} {sync}：重试同步（管理员）<br>{prefix} {import}：合并导入现有白名单（管理员）<br>{prefix} {mine}：查看自己的条目<br>{prefix} {diagnose} [服务器]：诊断（管理员）<br>{prefix} {requests}：查看待审核申请<br>{prefix} {approve} <申请编号>：通过申请（管理员）<br>{prefix} {reject} <申请编号> [原因]：拒绝申请（管理员）<br>{prefix} {cancel} <服务器>：取消倒计时（重启管理员）<br>{prefix} {backup}：创建插件备份（全局管理员） | `{prefix}`：主指令，包含 /<br>`{add}`：对应子指令配置中的第一个别名<br>`{list}`：对应子指令配置中的第一个别名<br>`{remove}`：对应子指令配置中的第一个别名<br>`{modify}`：对应子指令配置中的第一个别名<br>`{players}`：对应子指令配置中的第一个别名<br>`{tps}`：对应子指令配置中的第一个别名<br>`{status}`：对应子指令配置中的第一个别名<br>`{servers}`：对应子指令配置中的第一个别名<br>`{restart}`：对应子指令配置中的第一个别名<br>`{logs}`：对应子指令配置中的第一个别名<br>`{sync}`：对应子指令配置中的第一个别名<br>`{import}`：对应子指令配置中的第一个别名<br>`{mine}`：对应子指令配置中的第一个别名<br>`{diagnose}`：对应子指令配置中的第一个别名<br>`{requests}`：对应子指令配置中的第一个别名<br>`{approve}`：对应子指令配置中的第一个别名<br>`{reject}`：对应子指令配置中的第一个别名<br>`{cancel}`：对应子指令配置中的第一个别名<br>`{backup}`：对应子指令配置中的第一个别名 |
| `choose_own_entry` | 你有多条白名单，请指定要操作的编号：<br>{entries}<br>移除：{prefix} {remove} <编号><br>修改：{prefix} {modify} <编号> <新玩家名> | `{entries}`：按行模板生成的明细<br>`{prefix}`：主指令，包含 /<br>`{remove}`：对应子指令配置中的第一个别名<br>`{modify}`：对应子指令配置中的第一个别名 |
| `own_entry_missing` | 你还没有自己的白名单。可使用 {prefix} {add} <玩家名> 添加。 | `{prefix}`：主指令，包含 /<br>`{add}`：当前上下文中的数据 |
| `choose_restart_server` | 请选择服务器，或明确填写 all 操作本组全部服务器：<br>{entries}<br>{prefix} {restart} <服务器 ID 或 all> | `{entries}`：按行模板生成的明细<br>`{prefix}`：主指令，包含 /<br>`{restart}`：对应子指令配置中的第一个别名 |
| `usage_add` | 用法：{prefix} {add} <玩家名><br>例如：{prefix} {add} Steve | `{prefix}`：主指令，包含 /<br>`{add}`：对应子指令配置中的第一个别名 |
| `usage_modify` | 用法：{prefix} {modify} [编号或原名] <新玩家名><br>只有一条自己的白名单时可省略编号。 | `{prefix}`：主指令，包含 /<br>`{modify}`：对应子指令配置中的第一个别名 |
| `usage_help` | 发送 {prefix} 查看常用菜单，或发送 {prefix} {help} all 查看全部指令。 | `{prefix}`：主指令，包含 /<br>`{help}`：对应子指令配置中的第一个别名 |
| `empty_logs` | 暂无日志 | 纯文本；可添加命令别名变量 |
| `empty_mine` | 暂无自己的条目 | 纯文本；可添加命令别名变量 |
| `server_entry` | {server_id} · {server} | `{server_id}`：服务器配置 ID<br>`{server}`：服务器显示名称 |
| `sync_restarting` | {server}：重启中，待同步 | `{server}`：服务器显示名称 |
| `sync_success` | {server}：已同步 | `{server}`：服务器显示名称 |
| `sync_pending` | {server}：待同步（{error}；{detail}） | `{server}`：服务器显示名称<br>`{error}`：失败原因，可由对应错误模板生成<br>`{detail}`：附加说明或操作详情 |
| `sync_disk_ready` | 白名单文件已就绪，重载成功或服务端下次启动后生效 | 纯文本；可添加命令别名变量 |
| `sync_disk_failed` | 白名单文件未更新，修复后可重试同步 | 纯文本；可添加命令别名变量 |
| `alert_type_rcon` | RCON 不可用 | 纯文本；可添加命令别名变量 |
| `alert_type_disk` | 磁盘空间不足 | 纯文本；可添加命令别名变量 |
| `alert_type_tps` | TPS 过低 | 纯文本；可添加命令别名变量 |
| `alert_disk_detail` | 插件数据、临时目录或服务端所在盘剩余 {free_mb} MB | `{free_mb}`：剩余磁盘空间，MB |
| `alert_tps_current_detail` | 本次返回的 TPS={perf_tps} | `{perf_tps}`：性能/查询变量，详见 TPS 变量文档 |
| `alert_tps_detail` | 最近 1 分钟 TPS={perf_tps} | `{perf_tps}`：性能/查询变量，详见 TPS 变量文档 |
| `alert_status_online` | 在线 | 纯文本；可添加命令别名变量 |
| `alert_status_offline` | 离线或 RCON 不可用 | 纯文本；可添加命令别名变量 |
| `alert_type_other` | {alert_type} | `{alert_type}`：告警类型 |
| `empty_whitelist` | 暂无白名单 | 纯文本；可添加命令别名变量 |
| `sync_restart_reason` | 重启中，完成后自动同步 | 纯文本；可添加命令别名变量 |

## 重启与广播

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `restart_countdown` | ⏳ {server} 将在 {seconds} 秒后重启，请及时保存进度。 | `{server}`：服务器显示名称<br>`{seconds}`：耗时、等待或倒计时秒数 |
| `restart_cancelled` | 已取消 {server} 的重启倒计时。 | `{server}`：服务器显示名称 |
| `restart_started` | 正在重启 {servers}，将向本群广播结果。 | `{servers}`：目标服务器名称列表 |
| `restart_success` | ✅ {server} 重启成功，耗时 {seconds} 秒。{detail} | `{server}`：服务器显示名称<br>`{seconds}`：耗时、等待或倒计时秒数<br>`{detail}`：附加说明或操作详情 |
| `restart_failed` | ❌ {server} 重启失败：{error} | `{server}`：服务器显示名称<br>`{error}`：失败原因，可由对应错误模板生成 |
| `game_restart_success` | 服务器 {server} 已重启成功。 | `{server}`：服务器显示名称 |
| `restart_interrupted` | 插件监控曾中断，无法确认重启结果；请查询服务器状态。 | 纯文本；可添加命令别名变量 |
| `restart_pending_sync` |  白名单待同步：{error} | `{error}`：失败原因，可由对应错误模板生成 |
| `restart_game_failed` |  游戏内广播失败：{error} | `{error}`：失败原因，可由对应错误模板生成 |
| `restart_timeout` | 重启超时：旧连接未断开或新服务器未就绪。 | 纯文本；可添加命令别名变量 |
| `game_restart_countdown` | {result} | `{result}`：本次完整结果或原始回复 |
| `restart_cancelled_detail` | 管理员在发送 stop 前取消倒计时 | 纯文本；可添加命令别名变量 |
| `restart_ready_detail` | 新 RCON 连续两次可执行查询 | 纯文本；可添加命令别名变量 |
| `restart_waiting` | 旧连接已断开，新服务器尚未就绪。 | 纯文本；可添加命令别名变量 |

## 申请审核

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `requests` | 待审核申请<br>{entries} | `{entries}`：按行模板生成的明细 |
| `request_created` | 白名单申请 #{id} 已提交：{player}，等待管理员审核。 | `{id}`：白名单或申请固定编号<br>`{player}`：MC 玩家名 |
| `request_approved` | 已通过申请 #{id}：{player}<br>{sync} | `{id}`：白名单或申请固定编号<br>`{player}`：MC 玩家名<br>`{sync}`：按同步行模板生成的结果 |
| `request_rejected` | 已拒绝申请 #{id}：{detail} | `{id}`：白名单或申请固定编号<br>`{detail}`：附加说明或操作详情 |
| `empty_requests` | 暂无待审核申请 | 纯文本；可添加命令别名变量 |
| `default_rejection` | 管理员拒绝 | 纯文本；可添加命令别名变量 |
| `request_entry` | #{id} · {request_kind} · {player} · {user} · {time} | `{id}`：白名单或申请固定编号<br>`{request_kind}`：申请类型（可修改 request_kind_*）<br>`{player}`：MC 玩家名<br>`{user}`：操作用户 ID<br>`{time}`：操作或申请时间 |
| `request_kind_add` | add | 纯文本；可添加命令别名变量 |
| `request_kind_modify` | modify | 纯文本；可添加命令别名变量 |

## 状态与查询

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `players` | [{server}] 在线玩家：{result} | `{server}`：服务器显示名称<br>`{result}`：本次完整结果或原始回复 |
| `tps` | 📈 服务器性能 [{server}]<br><br>{perf_tps_line}<br>{perf_tick_lines}<br>{perf_cpu_lines}<br>{perf_entities_line}<br>{perf_memory_line}{perf_notice}<br><br>ℹ️ 来源：{query_source} \| 格式：{perf_format} \| 指令：{query_command}<br>耗时：{query_latency_ms} ms \| 缓存：{query_cached} | `{server}`：服务器显示名称<br>`{perf_tps_line}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_tick_lines}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_cpu_lines}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_entities_line}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_memory_line}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_notice}`：性能/查询变量，详见 TPS 变量文档<br>`{query_source}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_format}`：性能/查询变量，详见 TPS 变量文档<br>`{query_command}`：性能/查询变量，详见 TPS 变量文档<br>`{query_latency_ms}`：性能/查询变量，详见 TPS 变量文档<br>`{query_cached}`：性能/查询变量，详见 TPS 变量文档 |
| `tps_warning` | <br>部分 spark 数据不可用：{tps_missing_fields}<br>原始输出：<br>{tps_raw} | `{tps_missing_fields}`：性能/查询变量，详见 TPS 变量文档<br>`{tps_raw}`：性能/查询变量，详见 TPS 变量文档 |
| `tps_error` | [{server}] TPS 查询失败或无法解析：<br>原始输出：<br>{tps_raw} | `{server}`：服务器显示名称<br>`{tps_raw}`：性能/查询变量，详见 TPS 变量文档 |
| `status` | [{server}] {result} | `{server}`：服务器显示名称<br>`{result}`：本次完整结果或原始回复 |
| `players_none` | 无 | 纯文本；可添加命令别名变量 |
| `source_unavailable` | 未取得数据 | 纯文本；可添加命令别名变量 |
| `source_log` | 服务端新增日志 | 纯文本；可添加命令别名变量 |
| `status_online` | 在线（RCON 查询成功） | 纯文本；可添加命令别名变量 |
| `status_offline` | 离线或 RCON 不可用 | 纯文本；可添加命令别名变量 |
| `status_invalid` | RCON 可达，查询指令异常 | 纯文本；可添加命令别名变量 |
| `status_unknown` | 查询失败，状态未确认 | 纯文本；可添加命令别名变量 |
| `status_restarting` | 重启监控中 | 纯文本；可添加命令别名变量 |
| `query_timeout` | 查询超时（含排队，超过 {seconds} 秒），请稍后重试或调整查询总超时。 | `{seconds}`：耗时、等待或倒计时秒数 |
| `query_empty` | 服务端返回空响应 | 纯文本；可添加命令别名变量 |
| `query_spark_empty` | 服务端返回空响应；spark 报告可能只输出到控制台。请开启 spark 新增日志回读并确认日志路径，或改用核心支持的原生 TPS 命令。{detail} | `{detail}`：附加说明或操作详情 |
| `query_unsupported` | 服务端不支持查询命令，或命令权限/参数无效 | 纯文本；可添加命令别名变量 |
| `query_unsupported_result` | {error}：{result} | `{error}`：失败原因，可由对应错误模板生成<br>`{result}`：本次完整结果或原始回复 |
| `query_offline` | 离线或 RCON 不可用：{error} | `{error}`：失败原因，可由对应错误模板生成 |
| `query_native_failure` | 未找到可用的 TPS 查询指令<br>{entries} | `{entries}`：按行模板生成的明细 |
| `query_command_failure` | {query_command}：{error} | `{query_command}`：性能/查询变量，详见 TPS 变量文档<br>`{error}`：失败原因，可由对应错误模板生成 |
| `query_empty_short` | 空响应 | 纯文本；可添加命令别名变量 |
| `query_unparsed` | TPS 回复格式未识别，保留原文 | 纯文本；可添加命令别名变量 |
| `query_players_unparsed` | 在线人数未能解析，可使用 players_raw 查看原始回复 | 纯文本；可添加命令别名变量 |
| `query_log_warning` | spark 日志回读：{error} | `{error}`：失败原因，可由对应错误模板生成 |
| `raw_tps` | {result} | `{result}`：本次完整结果或原始回复 |
| `raw_tps_append` | {result}<br><br>原始输出：<br>{tps_raw} | `{result}`：本次完整结果或原始回复<br>`{tps_raw}`：性能/查询变量，详见 TPS 变量文档 |
| `query_log_acquired` | spark 报告通过本次命令之后新增的控制台日志取得，无需 Java 采集器。 | 纯文本；可添加命令别名变量 |
| `source_rcon` | RCON | 纯文本；可添加命令别名变量 |

## TPS 摘要与标签

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `parse_complete` | 已解析 | 纯文本；可添加命令别名变量 |
| `parse_partial` | 部分 | 纯文本；可添加命令别名变量 |
| `parse_failed` | 未解析 | 纯文本；可添加命令别名变量 |
| `perf_tps_windows_line` | ⚡ TPS（{windows}）：{values} | `{windows}`：本次实际提供的统计窗口<br>`{values}`：与窗口/标签对应的数值 |
| `perf_tps_overall_line` | ⚡ TPS（总体/实测）：{perf_tps} | `{perf_tps}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_tps_dimensions_line` | ⚡ TPS：按维度提供，未报告总体 TPS | 纯文本；可添加命令别名变量 |
| `perf_tps_target_line` | ⚡ 实测 TPS：未提供；目标/配置速率：{perf_target_rate} tick/s；状态：{perf_tick_state} | `{perf_target_rate}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_tick_state}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_tps_unparsed_line` | ⚡ TPS：未解析（保留原始回复） | 纯文本；可添加命令别名变量 |
| `perf_dimension_entry` | {dimension}：TPS {perf_tps} · Tick {perf_mspt} ms | `{dimension}`：核心返回的维度名称<br>`{perf_tps}`：性能/查询变量，详见 TPS 变量文档<br>`{perf_mspt}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_dimensions_line` | 🌍 维度与总体：<br>{entries} | `{entries}`：按行模板生成的明细 |
| `perf_tick_empty` | ⏱ Tick：本次回复未提供 | 纯文本；可添加命令别名变量 |
| `perf_cpu_empty` | 🖥 CPU：本次回复未提供 | 纯文本；可添加命令别名变量 |
| `perf_memory_line` | 💾 内存：{perf_memory} | `{perf_memory}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_entities_line` | 🧍 实体：{perf_entities} | `{perf_entities}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_missing` | 本次回复未提供 | 纯文本；可添加命令别名变量 |
| `perf_tick_spark_line` | ⏱ Tick {window}（{labels}）：{values} ms | `{window}`：统计窗口<br>`{labels}`：统计标签<br>`{values}`：与窗口/标签对应的数值 |
| `perf_cpu_spark_line` | 🖥 CPU（{scope}，{windows}）：{values} % | `{scope}`：CPU 指标范围<br>`{windows}`：本次实际提供的统计窗口<br>`{values}`：与窗口/标签对应的数值 |
| `perf_tick_average_line` | ⏱ Tick 平均耗时：{mspt_avg} ms | `{mspt_avg}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_tick_percentile_line` | ⏱ Tick 百分位：{entries} | `{entries}`：按行模板生成的明细 |
| `perf_tick_custom_line` | ⏱ Tick 耗时（原始标注）：{perf_mspt} ms | `{perf_mspt}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_cpu_custom_line` | 🖥 CPU（原始标注）：{cpu_usage} % | `{cpu_usage}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_entities_summary` | {entities_ticking} ticking / {entities_non_ticking} non-ticking | `{entities_ticking}`：性能/查询变量，详见 TPS 变量文档<br>`{entities_non_ticking}`：性能/查询变量，详见 TPS 变量文档 |
| `perf_memory_summary` | {memory_used} {memory_used_unit} / {memory_total} {memory_total_unit}{detail} | `{memory_used}`：性能/查询变量，详见 TPS 变量文档<br>`{memory_used_unit}`：性能/查询变量，详见 TPS 变量文档<br>`{memory_total}`：性能/查询变量，详见 TPS 变量文档<br>`{memory_total_unit}`：性能/查询变量，详见 TPS 变量文档<br>`{detail}`：附加说明或操作详情 |
| `perf_notice_star` | 已保留服务端的 * 标记；不将其解释为零值或缺失。 | 纯文本；可添加命令别名变量 |
| `perf_notice_multiple` | 返回内容包含多份 TPS 报告，未跨报告合并数值。 | 纯文本；可添加命令别名变量 |
| `perf_notice_target` | 目标/配置 Tick 速率不等于实测 TPS，未将其填入 TPS 变量。 | 纯文本；可添加命令别名变量 |
| `perf_notice_partial` | 部分指标未能解析，已保留原始回复。 | 纯文本；可添加命令别名变量 |
| `perf_notice_query` | 查询说明：{error} | `{error}`：失败原因，可由对应错误模板生成 |
| `perf_notice` | <br>说明：{entries} | `{entries}`：按行模板生成的明细 |
| `perf_label_min` | 最小 | 纯文本；可添加命令别名变量 |
| `perf_label_median` | 中位 | 纯文本；可添加命令别名变量 |
| `perf_label_max` | 最大 | 纯文本；可添加命令别名变量 |
| `perf_label_system` | 系统 | 纯文本；可添加命令别名变量 |
| `perf_label_process` | MC进程 | 纯文本；可添加命令别名变量 |
| `perf_window_1s` | 1s | 纯文本；可添加命令别名变量 |
| `perf_window_5s` | 5秒 | 纯文本；可添加命令别名变量 |
| `perf_window_10s` | 10秒 | 纯文本；可添加命令别名变量 |
| `perf_window_30s` | 30s | 纯文本；可添加命令别名变量 |
| `perf_window_1m` | 1分钟 | 纯文本；可添加命令别名变量 |
| `perf_window_5m` | 5分钟 | 纯文本；可添加命令别名变量 |
| `perf_window_15m` | 15分钟 | 纯文本；可添加命令别名变量 |
| `perf_state_frozen` | 冻结 | 纯文本；可添加命令别名变量 |
| `perf_state_sprinting` | 加速 | 纯文本；可添加命令别名变量 |
| `perf_state_overloaded` | 过载 | 纯文本；可添加命令别名变量 |
| `perf_state_normal` | 正常 | 纯文本；可添加命令别名变量 |
| `perf_format_unknown` | 未识别 | 纯文本；可添加命令别名变量 |
| `perf_format_custom` | 自定义 | 纯文本；可添加命令别名变量 |
| `parse_full` | 完整 | 纯文本；可添加命令别名变量 |
| `perf_format_spark` | spark | 纯文本；可添加命令别名变量 |
| `perf_format_paper` | Paper/Spigot | 纯文本；可添加命令别名变量 |
| `perf_format_forge` | Forge/NeoForge | 纯文本；可添加命令别名变量 |
| `perf_format_tick` | tick query | 纯文本；可添加命令别名变量 |
| `perf_label_p95` | P95 | 纯文本；可添加命令别名变量 |
| `perf_percentile_entry` | {percentile} {value} ms | `{percentile}`：回复中标注的百分位名称<br>`{value}`：本次校验的值 |

## 消息互通

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `bridge_to_game` | §b[群 {group}] §f{nickname}§7: §r{message} | `{group}`：群号 / 群 ID<br>`{nickname}`：群昵称<br>`{message}`：聊天/事件原文 |
| `bridge_chat` | [{server}] <{player}> {message} | `{server}`：服务器显示名称<br>`{player}`：MC 玩家名<br>`{message}`：聊天/事件原文 |
| `bridge_join` | [{server}] {player} 加入了游戏 | `{server}`：服务器显示名称<br>`{player}`：MC 玩家名 |
| `bridge_leave` | [{server}] {player} 离开了游戏 | `{server}`：服务器显示名称<br>`{player}`：MC 玩家名 |
| `bridge_death` | [{server}] {message} | `{server}`：服务器显示名称<br>`{message}`：聊天/事件原文 |

## 权限与操作错误

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `error_setup_shared_group` | 群号已经属于共享配置（{groups}）。请填写该配置的完整群号列表，或使用手动配置调整，避免改变其他群的服务器权限。 | `{groups}`：现有共享配置中的完整群号列表 |
| `error_setup_timeout` | 目录读取超时，请检查挂载或缩小扫描范围后重试。 | 纯文本；可添加命令别名变量 |
| `error_setup_root` | 请填写 AstrBot 可访问的服务端目录；可以填写共同父目录或单台服务端目录。 | 纯文本；可添加命令别名变量 |
| `error_setup_path` | 扫描路径包含链接或超出所选目录，请检查目录。 | 纯文本；可添加命令别名变量 |
| `error_setup_properties` | server.properties 无法读取、文件类型异常、过大或读取期间已改变。 | 纯文本；可添加命令别名变量 |
| `error_setup_limit` | 目录条目过多或服务端超过 64 个，请缩小扫描范围。 | 纯文本；可添加命令别名变量 |
| `error_setup_expired` | 扫描结果已过期或配置已经变化，请重新扫描。 | 纯文本；可添加命令别名变量 |
| `error_setup_selection` | 请选择有效且已配置 RCON 的服务器。 | 纯文本；可添加命令别名变量 |
| `error_setup_existing` | 已有配置结构需要修复，请先使用手动配置处理。 | 纯文本；可添加命令别名变量 |
| `error_setup_different_root` | 已有服务器位于其他共同父目录，快速配置不会迁移现有路径；请使用原目录或手动配置。 | 纯文本；可添加命令别名变量 |
| `error_setup_platform` | 请选择消息机器人；没有可选项时填写群内 /sid 返回的 Bot ID。 | 纯文本；可添加命令别名变量 |
| `error_setup_group` | 请填写要绑定的群号。 | 纯文本；可添加命令别名变量 |
| `error_setup_host` | 请填写有效的 RCON 地址。 | 纯文本；可添加命令别名变量 |
| `error_setup_changed` | 扫描后的 server.properties 已改变，请重新扫描后保存。 | 纯文本；可添加命令别名变量 |
| `error_setup_group_overlap` | 这些群号属于不同配置组，不能自动合并，请分开配置。 | 纯文本；可添加命令别名变量 |
| `error_connection_failed` | 连接失败：{error}。请检查 RCON 是否开启、地址端口及密码。 | `{error}`：失败原因，可由对应错误模板生成 |
| `error_connection_request` | 请选择有效的服务器配置后再测试连接。 | 纯文本；可添加命令别名变量 |
| `error_connection_reply` | RCON 已连接，但玩家查询无有效回复，请检查玩家查询命令。原始回复：{result} | `{result}`：本次完整结果或原始回复 |
| `error_main_001` | 配置待修正：{config_error} 请打开管理面板修正后保存。 | `{config_error}`：配置问题详情 |
| `error_main_002` | 插件尚未初始化或已卸载。 | 纯文本；可添加命令别名变量 |
| `error_main_003` | 请先登录 AstrBot 管理后台。 | 纯文本；可添加命令别名变量 |
| `error_main_004` | 请选择一个已配置的群。 | 纯文本；可添加命令别名变量 |
| `error_main_005` | 配置保存被更新的配置覆盖，请重新载入后再试。 | 纯文本；可添加命令别名变量 |
| `error_main_006` | 请求必须是 JSON 对象。 | 纯文本；可添加命令别名变量 |
| `error_main_007` | 不支持该操作。 | 纯文本；可添加命令别名变量 |
| `error_main_008` | 参数格式无效。 | 纯文本；可添加命令别名变量 |
| `error_main_009` | 请求需要 config 对象。 | 纯文本；可添加命令别名变量 |
| `error_main_010` | 配置已被其他页面修改，请先重新载入配置后再保存。 | 纯文本；可添加命令别名变量 |
| `error_main_011` | 有服务器正在重启，等待重启监控结束后再保存配置。 | 纯文本；可添加命令别名变量 |
| `error_main_012` | 新服务器需要填写密码或密码环境变量。 | 纯文本；可添加命令别名变量 |
| `error_main_013` | 配置变更未完成，原配置回滚写盘也失败。{detail} 当前实例仍使用原配置，但磁盘配置可能不同；修复存储问题后请重新保存并核对配置。 | `{detail}`：附加说明或操作详情 |
| `error_bridge_001` | 消息日志路径超出本服目录。 | 纯文本；可添加命令别名变量 |
| `error_bridge_002` | 消息日志路径必须指向普通文件。 | 纯文本；可添加命令别名变量 |
| `error_bridge_003` | 群消息发送失败。 | 纯文本；可添加命令别名变量 |
| `error_group_unbound` | 本群未绑定 MC 服务器，请联系管理员配置。 | 纯文本；可添加命令别名变量 |
| `error_group_only` | MC 功能仅允许在绑定的群聊中使用。 | 纯文本；可添加命令别名变量 |
| `error_unknown_command` | 未知子指令，请发送主指令查看帮助。 | 纯文本；可添加命令别名变量 |
| `error_operations_001` | 清理预览已失效，请重新预览。 | 纯文本；可添加命令别名变量 |
| `error_unloading` | 插件正在卸载。 | 纯文本；可添加命令别名变量 |
| `error_operations_002` | 无法确认发送者身份。 | 纯文本；可添加命令别名变量 |
| `error_operations_003` | 指令参数数量不正确，请查看帮助。 | 纯文本；可添加命令别名变量 |
| `error_operations_004` | 导入文本不能超过 1 MB。 | 纯文本；可添加命令别名变量 |
| `error_operations_005` | 每次导入应包含 1～500 条玩家记录。 | 纯文本；可添加命令别名变量 |
| `error_operations_006` | 待确认导入过多，请稍后重试。 | 纯文本；可添加命令别名变量 |
| `error_operations_007` | 导入预览已失效，请重新预览。 | 纯文本；可添加命令别名变量 |
| `error_operations_008` | 请先修正预览中的错误。 | 纯文本；可添加命令别名变量 |
| `error_operations_009` | 请选择 1～500 条记录。 | 纯文本；可添加命令别名变量 |
| `error_operations_010` | 批量操作或归属无效。 | 纯文本；可添加命令别名变量 |
| `error_operations_011` | 查询过于频繁，请等待 {value1} 秒。 | `{value1}`：当前模板中对应位置的动态数据 |
| `error_operations_012` | 插件完整备份仅允许已核实的 AstrBot 全局管理员或后台管理员创建。 | 纯文本；可添加命令别名变量 |
| `error_operations_013` | 未找到本配置组待审核的申请。 | 纯文本；可添加命令别名变量 |
| `error_operations_014` | 无法解析导入内容。 | 纯文本；可添加命令别名变量 |
| `error_operations_015` | 白名单已变化，请重新预览后导入。 | 纯文本；可添加命令别名变量 |
| `error_operations_016` | 没有此服务器的重启管理权限。 | 纯文本；可添加命令别名变量 |
| `error_operations_017` | 只能取消尚未发送 stop 的重启倒计时。 | 纯文本；可添加命令别名变量 |
| `error_operations_018` | 导入格式应为 json、csv 或 text。 | 纯文本；可添加命令别名变量 |
| `error_operations_019` | 归属用户 ID 无效。 | 纯文本；可添加命令别名变量 |
| `error_operations_020` | 导入内容存在重复玩家。 | 纯文本；可添加命令别名变量 |
| `error_operations_021` | 导入 UUID 与当前身份模式不一致。 | 纯文本；可添加命令别名变量 |
| `error_operations_022` | 申请人的白名单数量已达到上限。 | 纯文本；可添加命令别名变量 |
| `error_operations_023` | 原条目或归属已变化，请拒绝旧申请并重新提交。 | 纯文本；可添加命令别名变量 |
| `error_operations_024` | 已有同名条目使用不同 UUID。 | 纯文本；可添加命令别名变量 |
| `error_operations_025` | 该玩家已存在，无法重复通过。 | 纯文本；可添加命令别名变量 |
| `error_operations_026` | 新的玩家名或 UUID 已存在。 | 纯文本；可添加命令别名变量 |
| `error_operations_store_001` | 未找到当前配置组的通知。 | 纯文本；可添加命令别名变量 |
| `error_operations_store_002` | 你已有待审核的申请，请等待管理员处理。 | 纯文本；可添加命令别名变量 |
| `error_operations_store_003` | 日期格式应为 ISO 8601，例如 2026-10-16T00:00:00+08:00。 | 纯文本；可添加命令别名变量 |
| `error_rcon_001` | RCON 命令包含无效字符。 | 纯文本；可添加命令别名变量 |
| `error_rcon_002` | RCON 命令过长。 | 纯文本；可添加命令别名变量 |
| `error_rcon_003` | RCON 返回了无效包长度。 | 纯文本；可添加命令别名变量 |
| `error_rcon_004` | RCON 返回包缺少结束符。 | 纯文本；可添加命令别名变量 |
| `error_rcon_005` | RCON 认证响应无效。 | 纯文本；可添加命令别名变量 |
| `error_rcon_006` | 未配置 RCON 密码，或密码环境变量为空。 | 纯文本；可添加命令别名变量 |
| `error_rcon_007` | RCON 响应编号或类型不匹配。 | 纯文本；可添加命令别名变量 |
| `error_rcon_008` | RCON 响应超过 1 MB。 | 纯文本；可添加命令别名变量 |
| `error_rcon_009` | RCON 认证失败，请检查密码。 | 纯文本；可添加命令别名变量 |
| `error_admin_required` | 该功能仅允许 AstrBot 管理员或本群配置的管理员使用。 | 纯文本；可添加命令别名变量 |
| `error_server_target` | 服务器不存在、名称不唯一或不属于本群，请使用服务器 ID。 | 纯文本；可添加命令别名变量 |
| `error_entry_owner` | 只能修改或移除自己的白名单；历史导入条目需管理员处理。 | 纯文本；可添加命令别名变量 |
| `error_service_001` | 页码超出范围，目前共 {pages} 页。 | `{pages}`：总页数 |
| `error_service_002` | 无法确认发送者身份，请使用可识别账号的群聊消息重试。 | 纯文本；可添加命令别名变量 |
| `error_command_arguments` | 指令参数数量不正确，请发送主指令查看帮助。 | 纯文本；可添加命令别名变量 |
| `error_service_003` | 启动就绪查询没有有效响应，请检查 players_command 配置。 | 纯文本；可添加命令别名变量 |
| `error_service_004` | 没有目标服务器的重启权限，请核对本组管理员及服务器负责人设置。 | 纯文本；可添加命令别名变量 |
| `error_restart_busy` | 目标服务器已有重启任务，请等待当前任务结束。 | 纯文本；可添加命令别名变量 |
| `error_service_005` | 尚无本群广播会话；请先在群内使用 MC 指令，或配置 broadcast_origin。 | 纯文本；可添加命令别名变量 |
| `error_service_006` | 等待服务器启动超时：{last_error} | `{last_error}`：最近一次启动检查错误 |
| `error_service_007` | 玩家 {value1} 在各服使用不同 UUID，导入已停止。 | `{value1}`：当前模板中对应位置的动态数据 |
| `error_service_008` | 同一 UUID 对应多个玩家名，导入已停止。 | 纯文本；可添加命令别名变量 |
| `error_service_009` | 每人最多添加 {max_entries} 条，请先修改或移除已有条目。 | `{max_entries}`：每人白名单数量上限 |
| `error_service_010` | 该玩家名或 UUID 已在本群白名单中。 | 纯文本；可添加命令别名变量 |
| `error_service_011` | 白名单重载失败：{response} | `{response}`：服务端原始回复 |
| `error_service_012` | 条目归属已变化或权限不足，请重新查看白名单。 | 纯文本；可添加命令别名变量 |
| `error_entry_missing` | 未找到该白名单条目；编号按群固定，删除后不会复用。 | 纯文本；可添加命令别名变量 |
| `error_store_001` | 共享服务器 {sid} 的玩家名与 UUID 冲突，请管理员核对各组条目。 | `{sid}`：涉及的服务器 ID |
| `error_store_002` | 该玩家名或 UUID 已在本群或共享服务器中存在冲突，请管理员核对。 | 纯文本；可添加命令别名变量 |
| `internal_error` | 内部错误，请管理员检查 AstrBot 日志和同步状态。 | 纯文本；可添加命令别名变量 |
| `error_unknown` | {error} | `{error}`：失败原因，可由对应错误模板生成 |

## 配置与文件错误

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `error_backups_001` | 备份数据库完整性检查未通过。 | 纯文本；可添加命令别名变量 |
| `error_backups_002` | 备份数据库包含不支持的对象。 | 纯文本；可添加命令别名变量 |
| `error_backups_003` | 备份中的后续编号无效。 | 纯文本；可添加命令别名变量 |
| `error_backups_004` | 备份目录不能是链接。 | 纯文本；可添加命令别名变量 |
| `error_backups_005` | 备份文件名无效。 | 纯文本；可添加命令别名变量 |
| `error_backups_006` | 备份文件路径无效。 | 纯文本；可添加命令别名变量 |
| `error_backups_007` | 备份不存在或超过大小限制。 | 纯文本；可添加命令别名变量 |
| `error_backups_008` | 备份压缩包不能超过 32 MB。 | 纯文本；可添加命令别名变量 |
| `error_backups_009` | 待确认恢复过多，请稍后再试。 | 纯文本；可添加命令别名变量 |
| `error_backups_010` | 恢复预览已失效，请重新预览。 | 纯文本；可添加命令别名变量 |
| `error_backups_011` | 备份数据库结构不受支持：{table} | `{table}`：备份数据库表名 |
| `error_backups_012` | 备份中的固定编号、归属或配置组数据无效。 | 纯文本；可添加命令别名变量 |
| `error_backups_013` | 备份中存在重复白名单条目。 | 纯文本；可添加命令别名变量 |
| `error_backups_014` | 插件数据库超过备份大小限制，请先导出并清理旧日志。 | 纯文本；可添加命令别名变量 |
| `error_backups_015` | 配置超过 1 MB。 | 纯文本；可添加命令别名变量 |
| `error_backups_016` | 压缩备份超过 32 MB，请先清理日志。 | 纯文本；可添加命令别名变量 |
| `error_backups_017` | 备份格式、数据库或校验信息无效。 | 纯文本；可添加命令别名变量 |
| `error_backups_018` | 上传的备份超过大小限制。 | 纯文本；可添加命令别名变量 |
| `error_backups_019` | 恢复预览已过期。 | 纯文本；可添加命令别名变量 |
| `error_backups_020` | 预览后的备份发生变化。 | 纯文本；可添加命令别名变量 |
| `error_backups_021` | 备份仅允许 database.sqlite3、config.json、backup.json 三个文件。 | 纯文本；可添加命令别名变量 |
| `error_backups_022` | 备份解压大小超过限制。 | 纯文本；可添加命令别名变量 |
| `error_backups_023` | 这不是受支持的 MC 管理插件数据备份。 | 纯文本；可添加命令别名变量 |
| `error_backups_024` | 备份校验值不一致。 | 纯文本；可添加命令别名变量 |
| `error_backups_025` | 备份上传编码无效。 | 纯文本；可添加命令别名变量 |
| `error_config_001` | {label} 必须使用 JSON 布尔值 true 或 false。 | `{label}`：当前校验/统计项目名称 |
| `error_config_002` | Java 版玩家名必须为 3～16 位字母、数字或下划线。 | 纯文本；可添加命令别名变量 |
| `error_config_003` | 群号必须是文本、数字或群号列表，多个群用逗号或换行分隔。 | 纯文本；可添加命令别名变量 |
| `error_config_004` | {label} 必须是 {minimum}～{maximum} 之间的整数。 | `{label}`：当前校验/统计项目名称<br>`{minimum}`：最小允许值<br>`{maximum}`：最大允许值 |
| `error_config_005` | 服务器 ID 必须是文本或 ID 列表，多个 ID 用逗号或换行分隔。 | 纯文本；可添加命令别名变量 |
| `error_config_006` | 群号列表只能包含文本或数字。 | 纯文本；可添加命令别名变量 |
| `error_config_007` | 配置必须为 JSON 对象。 | 纯文本；可添加命令别名变量 |
| `error_config_008` | TPS 缺失值文字必须为 1～32 字符的单行文本。 | 纯文本；可添加命令别名变量 |
| `error_config_009` | 至少配置一个主指令。 | 纯文本；可添加命令别名变量 |
| `error_config_010` | 服务器 ID 格式无效；请填写服务器唯一 ID，多个 ID 用逗号或换行分隔。 | 纯文本；可添加命令别名变量 |
| `error_config_011` | 单个群号不能包含空白或控制字符。 | 纯文本；可添加命令别名变量 |
| `error_config_012` | {value1} 必须是 JSON 对象数组。 | `{value1}`：当前模板中对应位置的动态数据 |
| `error_config_013` | {value1} 必须为 JSON 对象。 | `{value1}`：当前模板中对应位置的动态数据 |
| `error_config_014` | 主指令不能包含 / 或空白。 | 纯文本；可添加命令别名变量 |
| `error_config_015` | 指令 {action} 至少需要一个别名。 | `{action}`：日志操作类型 |
| `error_config_016` | 消息模板 {value1} 必须为不超过 16000 字符的文本。 | `{value1}`：当前模板中对应位置的动态数据 |
| `error_config_017` | 服务器 ID 必须唯一，且只含字母、数字、下划线或横线。 | 纯文本；可添加命令别名变量 |
| `error_config_018` | 请先设置 server_root（服务器共同父目录）。 | 纯文本；可添加命令别名变量 |
| `error_config_019` | 服务器 {sid} 的目录必须在 server_root 内，且不能重复。 | `{sid}`：涉及的服务器 ID |
| `error_config_020` | 不同服务器不能使用相同的 RCON 地址和端口。 | 纯文本；可添加命令别名变量 |
| `error_config_022` | 服务器 {sid} 的 TPS 输出格式必须为 template 或 raw。 | `{sid}`：涉及的服务器 ID |
| `error_config_023` | 服务器 {sid} 的 RCON 地址或查询命令无效。 | `{sid}`：涉及的服务器 ID |
| `error_config_024` | 服务器重启负责人应使用 平台实例ID:用户ID 格式。 | 纯文本；可添加命令别名变量 |
| `error_config_025` | 消息互通日志必须是本服目录内的相对文件路径。 | 纯文本；可添加命令别名变量 |
| `error_config_026` | 消息互通日志编码必须为 utf-8 或 gb18030。 | 纯文本；可添加命令别名变量 |
| `error_config_027` | spark 日志必须是本服目录内的相对文件路径。 | 纯文本；可添加命令别名变量 |
| `error_config_028` | spark 日志编码必须为 utf-8 或 gb18030。 | 纯文本；可添加命令别名变量 |
| `error_config_029` | 第 {group_index} 条群绑定缺少：{value2}。请编辑该群绑定，或删除未使用的空白项。 | `{group_index}`：当前上下文中的数据<br>`{value2}`：当前模板中对应位置的动态数据 |
| `error_config_030` | 第 {group_index} 条群绑定的配置 ID 格式无效，只能使用 1～64 位字母、数字、下划线或横线。 | `{group_index}`：当前上下文中的数据 |
| `error_config_031` | 第 {group_index} 条群绑定的配置 ID {gid} 重复，请使用不同 ID 或删除重复项。 | `{group_index}`：当前上下文中的数据<br>`{gid}`：当前上下文中的数据 |
| `error_config_032` | 同一平台下的群不能重复绑定到多条配置；可在同一配置填写多个群号。 | 纯文本；可添加命令别名变量 |
| `error_config_033` | 群 {gid} 的服务器集合为空或重复。 | `{gid}`：当前上下文中的数据 |
| `error_config_034` | 群 {gid} 的服务器必须使用相同 UUID 模式。 | `{gid}`：当前上下文中的数据 |
| `error_config_035` | 群消息转发前缀必须为不超过 32 字符且不含空白的文本，可留空。 | 纯文本；可添加命令别名变量 |
| `error_config_036` | 子指令别名重复或包含空白：{value} | `{value}`：本次校验的值 |
| `error_config_037` | 消息模板 {value1} 使用了无效的占位符或花括号。 | `{value1}`：当前模板中对应位置的动态数据 |
| `error_config_038` | 群 {gid} 绑定的服务器 ID {sid} 不存在；请先添加该服务器，或编辑群聊重新勾选服务器。 | `{gid}`：当前上下文中的数据<br>`{sid}`：涉及的服务器 ID |
| `error_dashboard_001` | 导出格式应为 json 或 csv。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_002` | 恢复会覆盖配置和数据库，需要明确确认。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_003` | 不支持的运维操作。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_004` | 配置已变化，请重新预览备份。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_005` | 服务器正在重启，请等待监控结束再恢复。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_006` | 操作请求必须是 JSON 对象。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_007` | 请先修正配置，或从已有备份恢复。 | 纯文本；可添加命令别名变量 |
| `error_dashboard_008` | 批量修改需要明确确认。 | 纯文本；可添加命令别名变量 |
| `error_files_001` | 服务器目录在配置后变为链接，已拒绝访问。 | 纯文本；可添加命令别名变量 |
| `error_files_002` | 服务器目录不存在：{id} | `{id}`：白名单或申请固定编号 |
| `error_files_003` | whitelist.json 不能是指向其他位置的链接。 | 纯文本；可添加命令别名变量 |
| `error_files_004` | 该正版玩家不存在。 | 纯文本；可添加命令别名变量 |
| `error_files_005` | 正版 UUID 查询返回了不同的玩家名。 | 纯文本；可添加命令别名变量 |
| `error_files_006` | 正版 UUID 查询失败（HTTP {code}），请稍后重试。 | `{code}`：HTTP 状态码 |
| `error_files_007` | 正版 UUID 查询失败，请检查联网后重试。 | 纯文本；可添加命令别名变量 |
| `error_files_008` | 白名单文件超过 8 MB。 | 纯文本；可添加命令别名变量 |
| `error_files_009` | 服务器 {id} 的白名单损坏或无法读取，已拒绝覆盖。 | `{id}`：白名单或申请固定编号 |
| `error_files_010` | 白名单存在重复玩家名或 UUID，请先人工检查。 | 纯文本；可添加命令别名变量 |
| `error_files_011` | 白名单备份失败，已拒绝覆盖：{exc} | `{exc}`：底层异常详情 |
| `error_spark_log_001` | spark 日志目录在配置后变为链接，请检查服务端目录。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_002` | spark 日志路径超出本服目录或指向链接。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_003` | spark 日志尚未记录查询起点。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_004` | spark 日志报告过长，已拒绝解析。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_005` | spark 日志路径必须指向普通文件。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_006` | 未找到本服日志 {relative}，请确认路径和日志目录挂载。 | `{relative}`：日志相对路径 |
| `error_spark_log_007` | 查询期间 spark 日志已轮转或截断，请重试；不会读取替换文件的历史报告。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_008` | 查询期间 spark 日志内容已重写，请重试。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_009` | 本次 spark 查询期间新增日志超过 256 KB，已停止读取，请稍后重试。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_010` | 本次等待窗口出现多份 spark 报告，无法可靠归属，请避免同时手动查询后重试。 | 纯文本；可添加命令别名变量 |
| `error_spark_log_011` | RCON 已返回，但 {value1} 秒内未发现本次 spark 的新增日志报告。请确认日志路径、编码或增加等待时间。 | `{value1}`：当前模板中对应位置的动态数据 |

## 日志与诊断

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `diagnostics` | MC 诊断<br>{result} | `{result}`：本次完整结果或原始回复 |
| `logs` | 本群操作日志（第 {page}/{pages} 页，共 {total} 条）<br>{entries} | `{page}`：当前页码<br>`{pages}`：总页数<br>`{total}`：总条数<br>`{entries}`：按行模板生成的明细 |
| `log_entry` | {time} \| {action} \| 群 {group} \| 用户 {user} \| {player} \| {detail} | `{time}`：操作或申请时间<br>`{action}`：日志操作类型<br>`{group}`：群号 / 群 ID<br>`{user}`：操作用户 ID<br>`{player}`：MC 玩家名<br>`{detail}`：附加说明或操作详情 |
| `diagnostic_server` | {server}（{server_id}）：{status}；目录可写={writable}；白名单可读={readable} | `{server}`：服务器显示名称<br>`{server_id}`：服务器配置 ID<br>`{status}`：性能/查询变量，详见 TPS 变量文档<br>`{writable}`：目录写入检查结果（可修改 boolean_*）<br>`{readable}`：白名单读取检查结果（可修改 boolean_*） |
| `diagnostic_worker` | 后台 {worker}：{worker_state}；最近错误={error} | `{worker}`：后台任务名称<br>`{worker_state}`：后台任务状态（可修改 worker_state_*）<br>`{error}`：失败原因，可由对应错误模板生成 |
| `log_detail` | {detail} | `{detail}`：附加说明或操作详情 |
| `audit_initial_import_1` | 保留合并 {count} 条原有白名单 | `{count}`：条目数量 |
| `audit_restart_requested_1` | 发送 stop，等待启动脚本重启 | 纯文本；可添加命令别名变量 |
| `audit_import_1` | 合并导入 {count} 条 | `{count}`：条目数量 |
| `audit_add_1` | #{id}; UUID={uuid}; 同步到 {server_ids} | `{id}`：白名单或申请固定编号<br>`{uuid}`：玩家 UUID<br>`{server_ids}`：目标服务器 ID 列表 |
| `audit_remove_1` | #{id}; 原归属={owner} | `{id}`：白名单或申请固定编号<br>`{owner}`：条目所属用户 |
| `audit_remove_2` | 修改 #{id}，原归属={owner} | `{id}`：白名单或申请固定编号<br>`{owner}`：条目所属用户 |
| `audit_add_2` | 修改 #{id}，归属={owner} | `{id}`：白名单或申请固定编号<br>`{owner}`：条目所属用户 |
| `audit_event_1` | 申请 #{id} | `{id}`：白名单或申请固定编号 |
| `audit_event_2` | 申请 #{id}；申请人={owner}；{detail} | `{id}`：白名单或申请固定编号<br>`{owner}`：条目所属用户<br>`{detail}`：附加说明或操作详情 |
| `audit_restart_cancelled_1` | 发送 stop 前取消 | 纯文本；可添加命令别名变量 |
| `audit_event_3` | #{id}；原归属={old_owner}；新归属={new_owner} | `{id}`：白名单或申请固定编号<br>`{old_owner}`：原所属用户 ID<br>`{new_owner}`：新所属用户 ID 或移除标记 |
| `worker_state_starting` | starting | 纯文本；可添加命令别名变量 |
| `worker_state_running` | running | 纯文本；可添加命令别名变量 |
| `worker_state_waiting` | waiting | 纯文本；可添加命令别名变量 |
| `worker_state_paused` | paused | 纯文本；可添加命令别名变量 |
| `worker_state_retrying` | retrying | 纯文本；可添加命令别名变量 |
| `action_initial_import` | initial_import | 纯文本；可添加命令别名变量 |
| `action_add` | add | 纯文本；可添加命令别名变量 |
| `action_remove` | remove | 纯文本；可添加命令别名变量 |
| `action_import` | import | 纯文本；可添加命令别名变量 |
| `action_sync` | sync | 纯文本；可添加命令别名变量 |
| `action_restart_requested` | restart_requested | 纯文本；可添加命令别名变量 |
| `action_restart_cancelled` | restart_cancelled | 纯文本；可添加命令别名变量 |
| `action_restart_success` | restart_success | 纯文本；可添加命令别名变量 |
| `action_restart_failed` | restart_failed | 纯文本；可添加命令别名变量 |
| `action_backup` | backup | 纯文本；可添加命令别名变量 |
| `action_request_add` | request_add | 纯文本；可添加命令别名变量 |
| `action_request_modify` | request_modify | 纯文本；可添加命令别名变量 |
| `action_request_approved` | request_approved | 纯文本；可添加命令别名变量 |
| `action_request_rejected` | request_rejected | 纯文本；可添加命令别名变量 |
| `audit_unbind_group` | 移除群绑定；共享服务器保留其他群的绑定，历史白名单和日志保留 | 纯文本；可添加命令别名变量 |
| `audit_bind_group` | 新增群绑定：{server_ids} | `{server_ids}`：目标服务器 ID 列表 |
| `audit_restore_group` | 恢复已归档配置组，保留该组原有白名单 | 纯文本；可添加命令别名变量 |
| `audit_change_group` | 群号/平台 {old_platform}:{old_group} → {platform}:{group}；白名单和编号保留，失效广播会话重置 | `{old_platform}`：原平台实例 ID<br>`{old_group}`：原群号<br>`{platform}`：当前平台实例 ID<br>`{group}`：群号 / 群 ID |
| `audit_change_platform` | ；跨平台条目归属清空，由管理员管理 | 纯文本；可添加命令别名变量 |
| `audit_move_server` | 配置组 {scope_id} 接入共享/曾托管服务器 {server_id}，使用本组数据库条目，不自动导入其他组成员 | `{scope_id}`：配置组 ID<br>`{server_id}`：服务器配置 ID |
| `audit_bulk_add` | 管理员批量导入；归属={owner} | `{owner}`：条目所属用户 |
| `audit_restore` | 从插件数据备份恢复；安全备份={backup} | `{backup}`：备份文件名称 |
| `action_bind_group` | bind_group | 纯文本；可添加命令别名变量 |
| `action_unbind_group` | unbind_group | 纯文本；可添加命令别名变量 |
| `action_change_group` | change_group | 纯文本；可添加命令别名变量 |
| `action_move_server` | move_server | 纯文本；可添加命令别名变量 |
| `action_bulk_add` | bulk_add | 纯文本；可添加命令别名变量 |
| `action_bulk_remove` | bulk_remove | 纯文本；可添加命令别名变量 |
| `action_bulk_transfer` | bulk_transfer | 纯文本；可添加命令别名变量 |
| `action_restore` | restore | 纯文本；可添加命令别名变量 |

## 通用文字

| 配置键（messages 下） | 初始默认文字 | 变量说明 |
| --- | --- | --- |
| `owner_imported` | 历史导入 | 纯文本；可添加命令别名变量 |
| `no_error` | 无 | 纯文本；可添加命令别名变量 |
| `yes` | 是 | 纯文本；可添加命令别名变量 |
| `no` | 否 | 纯文本；可添加命令别名变量 |
| `unverified` | 未验证 | 纯文本；可添加命令别名变量 |
| `owner_imported_managed` | 历史导入（管理员管理） | 纯文本；可添加命令别名变量 |
| `boolean_true` | True | 纯文本；可添加命令别名变量 |
| `boolean_false` | False | 纯文本；可添加命令别名变量 |
| `boolean_unknown` | None | 纯文本；可添加命令别名变量 |
| `owner_removed` | 已移除 | 纯文本；可添加命令别名变量 |
