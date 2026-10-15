# 配置字段说明

[返回项目说明](../README.md) · [网页配置](WEB_PANEL.md) · [默认配置](../config.default.json) · [两群五服示例](../config.example.json)

推荐在网页“配置与菜单”操作。下表也适用于 AstrBot 原生配置和高级 JSON。网页保存后立即生效，原生配置页保存后需要重载。示例文件不会被插件自动监听或载入，需要导入或合并到实际配置。

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
| `spark_file_max_age` | `10` | 本地 spark 报告最大有效秒数，3～300 |
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
| `players_command` | `list` | 在线玩家和状态/就绪查询命令 |
| `tps_command` | `spark tps` | TPS 的 RCON 查询命令 |
| `tps_source` | `auto` | `auto` 优先本地报告，`rcon` 只用 RCON，`file` 只读报告 |
| `spark_file` | `.astrbot-spark-tps.txt` | 本服目录内的报告相对路径 |

不同服务器目录不能重复。查询命令是单行控制台命令，不带 `/`，不支持换行拼接。更改 UUID 模式前核对实际服务端身份体系，同组所有服务器模式必须一致。

## 群聊 `groups[]`

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `id` | 必填 | 内部配置 ID，格式与服务器 ID 相同，不是 QQ 群号 |
| `platform_id` | 必填 | `/sid` 返回的 Bot ID，即消息平台实例 ID |
| `group_id` | 必填 | 实际群号，以字符串填写 |
| `server_ids` | 必填 | 本群管理的服务器 ID 列表，至少一台 |
| `admin_ids` | `[]` | 本群服务器管理员的平台账号列表 |
| `max_entries_per_user` | `1` | 普通成员最多拥有多少条白名单，1～100 |
| `broadcast_origin` | 空 | 可选的主动广播会话，通常由群内使用 `/mc` 自动记录 |

同一平台实例的同一群不能重复绑定。每台服务器只属于一个当前配置组。群管理员列表不等于 QQ 群管理员头衔；AstrBot 全局管理员由框架身份与当前会话配置共同核实。

群号和平台可以修改，已保存的内部 ID 在网页保持固定。删除、转组、跨平台改绑的历史归属处理见[管理页面说明](WEB_PANEL.md#修改群号释放与转移服务器)。

## 指令、消息与密码保存

`commands.prefix` 设置额外主指令别名，固定 `/mc` 保留。其余键有 `help`、`add`、`list`、`remove`、`modify`、`players`、`tps`、`status`、`servers`、`restart`、`logs`、`sync`、`import`。多个别名以逗号分隔，不能重复或含空白。

`messages` 的全部默认文本见[默认配置](../config.default.json)，常用变量见 [README](../README.md#自定义指令与回复)。TPS 的 19 个指标与辅助变量见 [TPS_VARIABLES.md](TPS_VARIABLES.md)。普通花括号写为 `{{` 和 `}}`，不支持表达式与格式化代码。

网页不回传已保存密码，留空表示保留，显式清除才会清除。高级 JSON 的 `__KEEP__` 是保存接口的保留标记，不能用作新服务器的真实密码。发布示例配置时，应使用空密码、占位文字或环境变量引用。
