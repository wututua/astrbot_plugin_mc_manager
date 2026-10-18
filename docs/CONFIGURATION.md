# 配置字段说明

[返回项目说明](../README.md) · [网页配置](WEB_PANEL.md) · [默认配置](../config.default.json) · [多群共享五服示例](../config.example.json)

推荐在网页“配置与菜单”操作。下表也适用于 AstrBot 原生配置和高级 JSON。网页保存后立即生效，原生配置页保存后需要重载。示例文件不会被插件自动监听或载入，需要导入或合并到实际配置。

新增运维参数、通知重试、查询保护及分级权限见[运维说明](OPERATIONS.md)，默认值始终以[完整默认配置](../config.default.json)为准。

## 根配置

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `server_root` | 空 | 服务端共同父目录，以 AstrBot 实际可访问路径为准 |
| `servers` | `[]` | 服务器列表 |
| `groups` | `[]` | 按平台实例和群号绑定的配置组 |
| `page_size` | `10` | 白名单和聊天日志每页条数，1～100 |
| `sync_interval` | `30` | 后台每轮在线探测、同步与广播重试后的等待秒数，5～3600 |
| `rcon_timeout` | `5` | 单次 RCON 操作超时秒数，1～30 |
| `restart_timeout` | `180` | 重启监控总超时秒数，5～3600 |
| `poll_interval` | `3` | 重启就绪检查间隔秒数，1～30 |
| `game_broadcast` | `true` | 重启成功后是否在对应游戏服务器广播 |
| `tps_missing_value` | `暂无数据` | TPS 缺失指标文字，1～32 个字符，单行 |
| `commands` | 见默认配置 | 主指令和子指令别名 |
| `messages` | 见默认配置 | 帮助、回复、错误、日志和广播模板 |

整数不能填写布尔值；布尔值使用 JSON 的 `true` / `false`。`servers`、`groups` 应为对象数组，`commands`、`messages` 应为对象。原生模板里的 `__template_key` 是表单标记，不是业务 ID。

## 服务器 `servers[]`

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `id` | 必填 | 唯一 ID，1～64 位字母、数字、下划线或横线 |
| `name` | 与 ID 相同 | 页面和消息显示名称 |
| `directory` | 与 ID 相同 | `server_root` 内的相对目录，不能指向根目录本身或越界 |
| `rcon_host` | `127.0.0.1` | AstrBot 可访问的 RCON 地址 |
| `rcon_port` | `25575` | RCON 端口，1～65535，各服地址与端口组合不能重复 |
| `rcon_password` | 空 | 直接填写的 RCON 密码 |
| `rcon_password_env` | 空 | 密码环境变量名，非空时优先读取，缺失不会回退 |
| `online_mode` | `false` | `false` 离线 UUID，`true` 查询正版 UUID |
| `whitelist_overwrite` | `false` | 是否允许直接覆盖异常白名单；开启后先备份原文件，再按插件管理的名单重建，详见下节 |
| `players_command` | `list` | 在线玩家和状态/就绪查询命令 |
| `tps_auto_detect` | `false` | 优先执行配置的指令，无有效回复或不支持时尝试 tps、forge tps、tick query，并缓存成功指令 |
| `tps_output` | `template` | template：将各格式实际数据填入 messages.tps；raw：完整原文 |
| `spark_log_fallback` | `true` | spark RCON 无有效报告时回读本次新增日志，无需 Java agent；原生自定义 spark tps 同样支持 |
| `spark_log_path` | `logs/latest.log` | 本服内控制台日志相对路径 |
| `spark_log_encoding` | `utf-8` | utf-8 或 gb18030 |
| `spark_log_wait_seconds` | `3` | 新报告等待秒数，1～15，受查询总超时限制 |
| `tps_command` | `spark tps` | 填写服务端实际支持的单行查询命令，如 tps、forge tps、tick query；不带 / |

查询指令配置片段，合并到对应 `servers[]` 条目：

```json
{
  "tps_command": "tps"
}
```

查询统一通过 RCON 执行，自动识别返回格式；`tps_output` 决定使用变量模板或原文，模板和已有自定义指令保持独立。旧配置中的 `tps_mode` 自动移除：未填写查询指令的旧 native 配置补为 `tps`，旧 spark 或无该字段的配置补为 `spark tps`；已经填写的指令保持原样。核心是否支持具体命令取决于实际安装的服务端。

不同服务器目录不能重复。查询命令是单行控制台命令，不带 `/`，不支持换行拼接。更改 UUID 模式前核对实际服务端身份体系，同组所有服务器模式必须一致。

## 群聊 `groups[]`

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `id` | 必填 | 内部配置 ID，格式与服务器 ID 相同，不是 QQ 群号 |
| `platform_id` | 必填 | `/sid` 返回的 Bot ID，即消息平台实例 ID |
| `group_id` | 必填 | 一个或多个实际群号，支持逗号、中文逗号、换行或 JSON 数组 |
| `server_ids` | 必填 | 本群管理的服务器 ID 列表，至少一台 |
| `admin_ids` | `[]` | 本群服务器管理员的平台账号列表 |
| `max_entries_per_user` | `1` | 普通成员最多拥有多少条白名单，1～100 |
| `broadcast_origin` | 空 | 可选的主动广播会话，通常由群内使用 `/mc` 自动记录 |

同一平台实例的同一实际群只对应一条配置；一条配置可以包含多个群。多条配置可以选择同一服务器，服务器同步所有关联配置组白名单的并集。群管理员列表不等于 QQ 群管理员头衔；AstrBot 全局管理员由框架身份与当前会话配置共同核实。

同一配置内的多个群共用白名单、编号、管理员和每人上限，不同配置组保留独立条目。共享服的同名同 UUID 条目会合并写入；一组移除后其他组仍保留的条目不会被误删。

群号和平台可以修改，已保存的内部 ID 在网页保持固定。删除、转组、跨平台改绑的历史归属处理见[管理页面说明](WEB_PANEL.md#修改群号释放与转移服务器)。

## 指令、消息与密码保存

`commands.prefix` 设置额外主指令别名，固定 `/mc` 保留。其余键有 `help`、`add`、`list`、`remove`、`modify`、`players`、`tps`、`status`、`servers`、`restart`、`logs`、`sync`、`import`。多个别名以逗号分隔，不能重复或含空白。

`messages` 的逐项默认文字、内部片段、错误和变量说明见[全部消息模板](MESSAGES.md)，可在网页“回复与广播”分类搜索、修改和插入变量。完整配置见[默认配置](../config.default.json)，常用模板变量见下文。TPS 的 19 个 spark 指标、跨核心数值和辅助变量见 [TPS_VARIABLES.md](TPS_VARIABLES.md)。普通花括号写为 `{{` 和 `}}`，不支持表达式与格式化代码。

网页不回传已保存密码，留空表示保留，显式清除才会清除。高级 JSON 的 `__KEEP__` 是保存接口的保留标记，不能用作新服务器的真实密码。发布示例配置时，应使用空密码、占位文字或环境变量引用。

## 回复模板变量

| 配置项（位于 `messages` 下） | 常用变量 |
| --- | --- |
| `added` / `removed` | `{id}`、`{player}`、`{sync}`；添加回复另有 `{user}` |
| `modified` | `{id}`、`{old_player}`、`{player}`、`{sync}` |
| `list` / `entry` | 列表：`{page}`、`{pages}`、`{total}`、`{entries}`；条目：`{id}`、`{player}`、`{owner}` |
| `players` / `status` | `{server}`、`{result}` |
| `tps` / `tps_warning` / `tps_error` | [spark 指标与辅助变量](TPS_VARIABLES.md) |
| `servers` | `{entries}` |
| `logs` / `log_entry` | 列表：`{page}`、`{pages}`、`{total}`、`{entries}`；记录：`{time}`、`{action}`、`{group}`、`{user}`、`{player}`、`{detail}` |
| `restart_started` | `{servers}` |
| `restart_success` | `{server}`、`{seconds}`、`{detail}` |
| `restart_failed` | `{server}`、`{error}` |
| `game_restart_success` | `{server}` |
| `sync` / `import` / `error` | 同步：`{sync}`；导入：`{count}`、`{sync}`；错误：`{error}` |

模板只支持变量替换；字面花括号写为 `{{`、`}}`，不支持表达式或属性访问。完整默认文本见[默认配置](../config.default.json)，TPS 指标详见 [TPS_VARIABLES.md](TPS_VARIABLES.md)。

## 服务器绑定列表的填写

群配置的 server_ids 推荐使用独立数组，例如 `"server_ids": ["server1", "server2"]`。服务器唯一 ID 与服务端目录名是不同字段。原生配置列表建议每项添加一个 ID。

main 中的修复兼容 `"server_ids": ["server1,server2"]`、中文逗号和换行，网页保存时会规范为独立数组。v1.5.0 原安装包遇到单项内逗号可能提示“服务器 server1,server2 不存在”，可在高级 JSON 中改为上述独立数组，应用到表单后保存；也可编辑群聊并重新勾选对应服务器。真正未知的 ID 和同组重复 ID 仍会拒绝保存。

## 消息互通（可选）

在网页“配置与菜单 → 消息互通”同时开启总开关和目标群开关，可设置两个转发方向、聊天/进退服/死亡事件、转发前缀和模板。服务器编辑窗口可设置本服内日志路径与编码；Docker 需挂载日志目录。群 → 游戏依赖 RCON，游戏 → 群读取新增日志，无需额外消息推送模组。详见[消息互通说明与变量表](MESSAGE_BRIDGE.md)。

## 白名单直接覆盖开关

位置：**配置与菜单 → 服务器与群聊 → 编辑服务器 → 允许直接覆盖白名单文件**。每台服务器独立设置，默认关闭；保存后立即生效。

- 关闭：现有 `whitelist.json` 损坏、格式或编码异常时拒绝覆盖，保留原文件并等待处理。
- 开启：遇到上述异常时，用插件数据库中该服关联配置组的有效白名单并集重建文件；原始字节先备份到本服 `.mcwl-backups/whitelist-时间.json`，最近 10 份备份按既有规则保留。
- 备份失败、目录异常、白名单或备份目录为链接时仍拒绝覆盖。文件访问权限问题需要先解决；此开关不绕过权限或文件系统保护。
- 首次接管时，开启此开关的服务器允许跳过无法解析的原文件，其他有效文件仍正常导入。原文件中未能导入的条目不会自动恢复，需要从备份核对、修复后再人工导入。手动 `/mc 导入` 与诊断仍严格校验文件。
- 正常白名单内容一致时不会重复写入或创建多余备份。关闭开关可恢复默认保护；成员归属、固定编号与群权限规则保持原样。

在现有目标 `servers[]` 条目内添加：

```json
{
  "whitelist_overwrite": true
}
```

不要用此片段替换完整的服务器配置。针对已添加但待同步的玩家，打开目标服开关并保存后，可由管理员发送 `/mc 同步` 重试，无需重复添加。

同步失败提示区分两个阶段：“白名单文件未更新”表示尚未写入成功；“白名单文件已就绪”表示磁盘内容已准备好，但 RCON 重载尚未成功，重载完成或服务端下次启动后生效。

## 查询策略与变量文档

- `query_timeout_seconds`：默认 `0` 自动使用 `max(5, 2 × rcon_timeout + 2 + 本服 spark 日志等待)` 秒，设置为 1～300 可自定义单服完整采集时限，包含排队及探测。
- `tps_capability_cache_seconds`：默认 `600` 秒，0 关闭可用命令缓存；仅原生自动探测生效，与 `query_cache_seconds` 的短结果缓存不同。
- `servers[].tps_auto_detect`：默认 `false`，可在“TPS 设置”或编辑服务器时开启，不再受查询方式限制。关闭时只执行配置的指令。
- 状态/玩家回复可使用人数、名单、实际来源、实际命令、耗时、采集时间和缓存变量；当前查询通过 RCON 发令，spark 无回复时可回读新增控制台日志，无需 Java 采集器。旧回复不自动覆盖。

网页“变量文档”提供 85 个查询相关变量的分类、适用范围、说明、单位、样例、插入及模板预览。完整变量与边界见[查询变量说明](TPS_VARIABLES.md)。

## 已取消的采集器配置

旧 tps_source、spark_file、spark_file_max_age 不再控制查询，兼容旧配置时忽略，保存或恢复时清理。此前安装过 Java agent 的部署需从 MC 启动参数中移除对应 -javaagent 项并重启 MC。详见[迁移说明](TPS_VARIABLES.md#取消采集器和旧配置迁移)。

控制台有报告但网页为空的处理方法见[新增日志回读说明](TPS_VARIABLES.md#控制台有报告但-rcon-返回空响应)。它不恢复旧 agent 指标文件功能；只读取发令后新增的本服日志。

## 各核心 TPS 数据变量与初始文本

查询指令与机器人模板分开设置。模板模式下，Paper/Spigot 的时间窗口、Forge/NeoForge 的 Overall 与维度、原版目标速率与百分位，以及自定义回复中明确提供的内存/实体都能使用各自变量；没有返回的字段显示缺失值。网页提供 6 套初始模板，可先预览再应用到 messages.tps，详见[模板及实际输出示例](TPS_TEMPLATES.md)。

## 日常配置入口

推荐从[快速上手](QUICK_START.md)开始。网页自动生成内部 ID，并将连接/权限/性能等低频选项收进“更多选项”；这些选项仍保留在完整配置中。新增、编辑弹窗只提供保存与取消，测试连接只运行 list。默认 help 为常用菜单，help_full 为完整菜单；原有自定义文字保留。

## 自动读取服务端参数

快速配置从 server.properties 读取 enable-rcon、rcon.port、rcon.password、online-mode 和 white-list。新增服务器使用 spark tps + tps_auto_detect=true，回复使用现有初始模板；原已配置服务器继续沿用原参数，不被文件中的值覆盖。未开启 RCON/无密码/重复端口等结果不可直接导入，未开启白名单会提示。

该流程不增加配置暂存功能，不修改游戏文件或启动脚本。读取到的密码仅保留在后台短期扫描结果，显式保存后按既有插件配置和备份权限管理。目录解析、上限、版本和文件变更检查详见[快速上手](QUICK_START.md)。
