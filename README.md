# Minecraft 群服管理 · AstrBot 插件

<p align="center">
  <img src="logo.png" alt="Minecraft 群服管理 Logo" width="144" height="144">
</p>

通过群聊管理多台 Minecraft Java 版服务端：同群共享白名单，不同群独立管理，常用操作也可以在 AstrBot 网页后台完成。

**当前版本：v1.2.3** · **AstrBot：>=4.28.0,<5** · **Python：3.12+**

插件名称：`astrbot_plugin_mc_manager`

[GitHub 仓库](https://github.com/wututua/astrbot_plugin_mc_manager) · [问题反馈](https://github.com/wututua/astrbot_plugin_mc_manager/issues) · [MIT 许可证](LICENSE)

[快速开始](#快速开始) · [指令列表](#指令列表) · [权限规则](#权限规则) · [TPS 与 spark 采集器](#tps-与-spark-采集器) · [常见问题](#常见问题) · [详细文档](#详细文档)

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 按群管理多服 | 每个群绑定一组服务器，同组共用白名单，不同群彼此隔离 |
| 成员自助白名单 | 添加、分页查看、按固定编号或玩家名移除、修改玩家名 |
| 条目归属与权限 | 普通成员仅能修改和移除自己的条目，管理员可管理本群全部条目 |
| 自动同步 | 修改后写入同组各服并尝试重载；失败标记为待同步，后台持续重试 |
| 自动在线探测 | 启动、重载、保存有效配置后立即后台探测，之后周期更新状态 |
| 玩家与性能查询 | 查询在线玩家，以及 spark 提供的 TPS、Tick 耗时和 CPU 使用率 |
| 重启监控与广播 | 通过 RCON 发送 `stop`，等待外部脚本启动服务端，再向对应群通知结果 |
| 审计日志 | 管理员可查看添加、移除、修改等记录，并按操作者筛选 |
| 网页管理 | 配置服务器、群聊、管理员、指令别名、帮助菜单、回复和广播模板 |
| 数据保存 | SQLite 持久化、固定编号、白名单文件原子替换及最近 10 份备份 |

例如，同一个父目录中有五台服务端：

```text
Minecraft/
├── server1 ┐
├── server2 ├── 群 111111111：共享一份白名单
├── server3 ┘
├── server4 ┐
└── server5 ┘── 群 222222222：共享另一份白名单
```

群 1 添加玩家，只同步到 `server1`、`server2`、`server3`。群 2 只能管理和查询自己绑定的 `server4`、`server5`。每台服务器同时只能归属一个群配置组；群身份由“消息平台实例 ID + 群号”共同确定。

## 管理页面预览

入口：**AstrBot → 插件管理 → Minecraft 群服管理 → Minecraft 管理面板**。

![服务器概览与群聊切换](docs/screenshots/desktop.png)

<details>
<summary>查看服务器与群聊配置页面</summary>

![服务器与群聊配置](docs/screenshots/web-settings.png)

</details>

## 环境要求

| 项目 | 要求 |
| --- | --- |
| AstrBot | `>=4.28.0,<5`，Python `3.12+` |
| Minecraft | Java 版服务端，启用白名单和 RCON |
| 文件访问 | AstrBot 能读写各服目录中的 `whitelist.json`；Docker 部署需要挂载服务端目录 |
| 网络 | AstrBot 能连接每台服务端的 RCON；同机各服使用不同端口 |
| TPS 查询 | 服务端安装并启用 spark；部分环境需要附带的 Java 采集器 |
| 自动重启 | 启动脚本或进程管理器在服务端执行 `stop` 退出后能够重新启动它 |

插件通过“本地文件 + RCON”管理服务端。仅能访问远程 RCON、无法访问服务端文件的部署方式不满足白名单同步要求。容器中填写的服务器路径和 RCON 地址，都应以 **AstrBot 容器内实际可访问的位置**为准。

## 快速开始

### 1. 安装插件

推荐在 AstrBot 的插件安装界面填写本仓库地址：

```text
https://github.com/wututua/astrbot_plugin_mc_manager
```

也可以通过本地上传导入构建后的 `astrbot_plugin_mc_manager-1.2.3.zip`，或将源码放到 `data/plugins/astrbot_plugin_mc_manager/` 后重载。`dist/` 不纳入版本控制；仓库暂未附安装包时，可按[开发与打包](#开发与打包)自行生成。GitHub 自动生成的源码 ZIP 不是本文所指的已验证插件安装包。

### 2. 准备 Minecraft 服务端

在每台服务端的 `server.properties` 中设置：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=替换为你自己的密码
broadcast-rcon-to-ops=false
white-list=true
```

同机第二台服务器使用另一个 RCON 端口，例如 `25576`。修改后完整重启服务端，使配置生效。

RCON 密码可以直接在面板填写，也可以通过服务器配置中的 `rcon_password_env` 引用 AstrBot 进程的环境变量。填写环境变量名后，它优先于直接填写的密码；变量不存在时不会回退。

### 3. 在网页中添加服务器和群聊

打开 **Minecraft 管理面板 → 配置与菜单 → 服务器与群聊**：

1. 填写“服务端共同父目录”，例如 `D:/Minecraft` 或 `/srv/minecraft`。
2. 添加服务器，设置唯一 ID、相对目录、RCON 地址、端口、密码和 UUID 模式。
3. 添加群聊，填写群配置 ID、平台实例 ID、实际群号，并勾选该群允许管理的服务器。
4. 按需填写本群管理员账号、每人白名单上限，点击“保存并生效”。

**平台实例 ID 填群内 `/sid` 返回的 Bot ID**，不是 `aiocqhttp` 等适配器类型，也不是机器人 QQ 号。群号和管理员账号均填写实际 ID。

页面保存后立即应用；通过 AstrBot 原生插件配置页修改后，需要重载插件。如果绑定字段未填完整，管理页面仍可打开，并显示需要修正的字段。

也可以在面板的高级 JSON 中应用配置。下面是一群一服的最小示例，使用前替换路径、密码及账号信息：

```json
{
  "server_root": "D:/Minecraft",
  "servers": [
    {
      "id": "server1",
      "name": "生存服",
      "directory": "server1",
      "rcon_host": "127.0.0.1",
      "rcon_port": 25575,
      "rcon_password": "替换为实际密码",
      "online_mode": false
    }
  ],
  "groups": [
    {
      "id": "group1",
      "platform_id": "替换为 /sid 返回的 Bot ID",
      "group_id": "111111111",
      "server_ids": ["server1"],
      "admin_ids": ["123456789"],
      "max_entries_per_user": 1
    }
  ]
}
```

`online_mode` 是**插件选择玩家 UUID 的方式**：`false` 使用 Java 离线 UUID，`true` 查询正版 UUID。同组各服必须使用一致的身份体系；代理转发正版 UUID 的网络应按实际玩家 UUID 选择。这个选项不会修改服务端的 `online-mode`。

完整配置参见 [默认配置](config.default.json) 与 [两群五服示例](config.example.json)。这两个文件供参考和导入；直接修改仓库里的示例文件不会自动应用到正在运行的插件。

### 4. 验证绑定与同步

插件会自动探测服务器状态。在绑定的群聊发送 `/mc`，应收到默认菜单，同时记录本群的主动广播会话。

用普通成员账号添加一条白名单，再用另一个普通账号尝试移除，确认归属限制生效。同组各服应同步变化，其他群的服务器不受影响。

## 指令列表

以下为默认指令。`<参数>` 表示必填，`[参数]` 表示可选，输入时不需要括号。

| 指令 | 功能 | 权限 |
| --- | --- | --- |
| `/mc`、`/mc help`、`/mc 帮助` | 显示帮助菜单 | 绑定群成员 |
| `/mc 添加 <玩家名>` | 添加归属于自己的白名单 | 绑定群成员 |
| `/mc 查看 [页码]` | 查看本群白名单和固定编号 | 绑定群成员 |
| `/mc 移除 <编号或玩家名>` | 移除自己的条目 | 绑定群成员 |
| `/mc 修改 <编号或玩家名> <新玩家名>` | 修改自己的条目，保留编号与归属 | 绑定群成员 |
| `/mc 玩家 [服务器 ID]` | 查询在线玩家 | 绑定群成员 |
| `/mc tps [服务器 ID]` | 查询 spark 性能指标 | 绑定群成员 |
| `/mc 状态 [服务器 ID]` | 查询 RCON 可用状态 | 绑定群成员 |
| `/mc 服务器` | 查看本群绑定的服务器 | 绑定群成员 |
| `/mc 重启 <服务器 ID 或 all>` | 发送 `stop` 并监控重启结果 | 管理员 |
| `/mc 日志 [页码] [用户 ID]` | 查看本群操作日志，可筛选操作者 | 管理员 |
| `/mc 同步` | 立即重试本群白名单同步 | 管理员 |
| `/mc 导入` | 合并导入当前服务端文件中的条目 | 管理员 |

玩家、TPS、状态查询省略服务器时查询本群全部服务器，也可以填写 `all`。重启必须明确指定服务器或 `all`。例如：

```text
/mc 添加 Steve
/mc 查看 1
/mc 修改 1 Alex
/mc 移除 1
/mc 玩家 server1
/mc tps all
/mc 日志 1 123456789
```

`mc` 已通过 AstrBot 原生指令注册。群内发送 `/mc` 或纯文本 `mc` 均可唤起；在 AstrBot 指令管理中禁用 `mc` 后，所有自定义主指令别名也会停用。

## 权限规则

- **普通成员**可以查看本群白名单、查询本群服务器，并添加、修改、移除归属于自己的条目。默认每人最多 1 条，可按群调整。
- **本群管理员**为该群配置中 `admin_ids` 指定的账号，仅对本群生效。
- **AstrBot 管理员**需通过事件身份和当前会话 AstrBot 配置的 `admins_id` 双重核对。管理员可以管理本群全部条目，且不受每人条目数量上限限制。
- **QQ 群主、群管理员头衔**不会自动授予插件管理权限。
- **首次导入的历史条目**没有群成员归属，只能由管理员处理。
- **网页新增条目**归属于 `dashboard:<后台用户名>`；需要成员自行管理时，应让成员在群内添加。

条目归属记录的是添加者的平台账号，**不代表验证了该 Minecraft 账号的所有权**。玩家名支持 Java 版的 3～16 位字母、数字和下划线。

## 自定义指令与回复

常用设置都可以在 **配置与菜单** 中修改，无需手工编辑 JSON：服务器与群聊、管理员、常规参数、指令别名、帮助菜单、回复与广播、spark TPS 模板。高级 JSON 入口用于批量编辑。

例如，将添加指令改为 `/mc add`，同时保留中文别名：

```json
{
  "commands": {
    "prefix": "mc,minecraft",
    "add": "add,添加",
    "remove": "remove,移除"
  }
}
```

这是配置片段，请合并到现有配置。多个别名用英文逗号分隔，不能重复或包含空白；只填写 `add` 就会替换原来的中文别名。固定入口 `/mc` 始终保留，自定义主指令作为额外入口。

`messages.help` 是完整帮助菜单，可直接换成自己的文本。默认菜单使用 `{prefix}`、`{add}` 等变量，会随指令配置更新：

```text
本群 Minecraft 管理
{prefix} {add} <玩家名>：添加白名单
{prefix} {list} [页码]：查看白名单
{prefix} {remove} <编号或玩家名>：移除自己的条目
{prefix} {players}：查看在线玩家
{prefix} {tps}：查看服务器性能
```

常用回复模板：

| 配置项（位于 `messages` 下） | 常用变量 |
| --- | --- |
| `added` / `removed` | `{id}`、`{player}`、`{sync}`；添加回复另有 `{user}` |
| `modified` | `{id}`、`{old_player}`、`{player}`、`{sync}` |
| `list` / `entry` | 列表：`{page}`、`{pages}`、`{total}`、`{entries}`；条目：`{id}`、`{player}`、`{owner}` |
| `players` / `status` | `{server}`、`{result}` |
| `tps` / `tps_warning` / `tps_error` | [spark 指标与辅助变量](docs/TPS_VARIABLES.md) |
| `servers` | `{entries}` |
| `logs` / `log_entry` | 列表：`{page}`、`{pages}`、`{total}`、`{entries}`；记录：`{time}`、`{action}`、`{group}`、`{user}`、`{player}`、`{detail}` |
| `restart_started` | `{servers}` |
| `restart_success` | `{server}`、`{seconds}`、`{detail}` |
| `restart_failed` | `{server}`、`{error}` |
| `game_restart_success` | `{server}` |
| `sync` / `import` / `error` | 同步：`{sync}`；导入：`{count}`、`{sync}`；错误：`{error}` |

重启成功广播示例：`🎉 {server} 已恢复，耗时 {seconds} 秒！{detail}`。普通花括号需要写成 `{{`、`}}`，模板不支持表达式、属性访问或格式化代码。

## TPS 与 spark 采集器

默认执行 `spark tps`，将输出拆为 **19 个指标变量**：

| 指标 | 时间窗口 | 变量 |
| --- | --- | --- |
| TPS | 5 秒、10 秒、1 分钟、5 分钟、15 分钟 | `{tps_5s}`、`{tps_10s}`、`{tps_1m}`、`{tps_5m}`、`{tps_15m}` |
| Tick 耗时 | 最近 10 秒的最小值、中位数、P95、最大值 | `{mspt_10s_min}`、`{mspt_10s_med}`、`{mspt_10s_p95}`、`{mspt_10s_max}` |
| Tick 耗时 | 最近 1 分钟的最小值、中位数、P95、最大值 | `{mspt_1m_min}`、`{mspt_1m_med}`、`{mspt_1m_p95}`、`{mspt_1m_max}` |
| 系统 CPU | 10 秒、1 分钟、15 分钟 | `{cpu_system_10s}`、`{cpu_system_1m}`、`{cpu_system_15m}` |
| MC 进程 CPU | 10 秒、1 分钟、15 分钟 | `{cpu_process_10s}`、`{cpu_process_1m}`、`{cpu_process_15m}` |

Tick 耗时单位为毫秒，CPU 变量为百分比数值字符串，均不含单位。P95 表示第 95 百分位。缺失指标显示 `tps_missing_value` 配置的文字，默认“暂无数据”。

在 `messages.tps` 中自由组合，例如：

```text
[{server}] 服务器性能
TPS：{tps_5s} / {tps_1m} / {tps_15m}
Tick P95：{mspt_10s_p95} ms
系统 CPU：{cpu_system_1m}%
MC 进程 CPU：{cpu_process_1m}%{tps_warning}
```

### 选择数据来源

| `servers[].tps_source` | 行为 |
| --- | --- |
| `auto`（默认） | 优先读取新鲜且可解析的本地报告，否则尝试 RCON |
| `rcon` | 仅读取 `spark tps` 的 RCON 回复 |
| `file` | 仅读取本地采集器报告 |

部分 spark 环境的异步命令无法通过 RCON 返回完整内容。遇到“服务端返回空响应”时，可以启用仓库附带的 [Java 采集器](companion/README.md)。

### 启用 Java 采集器

将 [astrbot-spark-agent.jar](companion/astrbot-spark-agent.jar) 放入每台需要采集的 MC 服务端目录，在原有 Java 启动命令的 `-jar` 或主类之前添加 `-javaagent`。下面以 Windows 普通 JAR 启动方式为例：

```bat
@echo off
cd /d "%~dp0"
java "-javaagent:%~dp0astrbot-spark-agent.jar" -Xms2G -Xmx4G -jar server.jar nogui
```

保留自己实际使用的 Java 路径、内存参数和服务端文件名。已有自动重启循环时，将参数加入循环中的实际 Java 启动命令；其他启动方式也应确保参数传给运行 MC 的 JVM。详细步骤见[采集器文档](companion/README.md)。

完整重启服务端后，采集器每 2 秒在服务端工作目录更新 `.astrbot-spark-tps.txt`。插件服务器配置可选择 `file`，`spark_file` 保持这个文件名；报告最大有效时间由根配置 `spark_file_max_age` 控制，默认 10 秒。

采集器依赖服务端已启用 spark 并提供相应公开 API。JAR 使用 Java 8 字节码编译，具体 Minecraft 核心、spark 实现与 Java 组合仍需验证。完整变量说明、默认模板和配置示例见 [TPS 变量文档](docs/TPS_VARIABLES.md)。

## 在线探测与重启

**在线探测**：插件启动、重载及保存有效配置后，后台立即并行检查全部已配置服务器，包括暂未绑定群的服务器。首次检查显示“探测中”，之后显示“在线”或“离线或 RCON 不可用”。

探测与白名单同步共用 `sync_interval`，默认每轮间隔 30 秒，可在网页常规设置中修改。管理页面可见且未编辑配置时，每 10 秒读取最新状态；“刷新状态”会立即发起探测。这里反映的是 **RCON 可用性**，RCON 不可用不等于游戏端口一定离线。

**重启流程**：管理员执行 `/mc 重启 server1` 后，插件检查 RCON、发送 `stop`，等待旧连接断开，再检查新 RCON 连续两次能够执行玩家查询。成功或超时失败后，向该服务器所属群发送结果；成功时还可通过 `game_broadcast` 在游戏内广播。

重新启动 Java 进程由你配置的脚本或进程管理器负责，插件本身只发送 `stop`。自动重启脚本示例见[部署文档](docs/DEPLOY.md)。`restart_timeout` 默认 180 秒，可按实际启动耗时调整。广播通常需要先在对应群发送一次 `/mc`，也可配置 `broadcast_origin`。

## 数据与同步规则

- **首次接管**：合并同群各服现有的 `whitelist.json`。损坏文件或玩家名与 UUID 冲突会阻止接管；历史条目不自动分配给某位成员。
- **后续同步**：以插件数据库为准。控制台或人工修改白名单可能被后续同步覆盖；需要保留时由管理员主动执行“导入”。导入也可能重新引入旧文件中尚未删除的条目。
- **同步失败**：条目和日志先保存到数据库，再逐服同步。失败的服务器标记为待同步并持续重试，各服不保证同时完成更新。
- **固定编号**：修改玩家名保留编号，删除后编号不会复用；不同群独立编号。
- **文件备份**：每次实际修改前，在服务端 `.mcwl-backups/` 保存旧文件，最多保留 10 份。
- **插件数据**：通常保存在 `data/plugin_data/astrbot_plugin_mc_manager/mc.sqlite3`，实际目录见管理面板。升级时保留该数据目录，才能保留归属、编号、日志与广播记录。

删除群绑定或取消服务器勾选后，**保存配置才会正式释放服务器**。修改同平台的群号会保留条目与归属，并重置旧广播会话；跨平台改绑会清空用户归属。服务器转给另一群配置组时，使用新组白名单，不自动继承旧组成员。详细行为见[管理页面说明](docs/WEB_PANEL.md)。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| `/mc` 没有响应 | 确认插件已加载、AstrBot 指令管理中的 `mc` 已启用，并检查群号与平台实例 ID 是否匹配 |
| 配置不完整导致功能不可用 | 打开管理面板，根据提示修正群配置 ID、平台实例 ID、群号及服务器选择，再保存 |
| `WinError 1225`、连接被拒绝 | 检查服务端是否启动、是否开启 RCON、地址与端口是否正确；容器中的 `127.0.0.1` 指向容器自身 |
| 游戏可以进入，面板却显示离线 | 面板检测 RCON；继续检查 RCON 密码、端口、认证和网络连接 |
| spark 返回空响应 | 启用 Java 采集器，确认报告文件持续更新，插件能够读取该文件 |
| 提示没有 spark 文件或文件过期 | 检查 `-javaagent` 是否加到 MC 启动命令、工作目录、`spark_file` 路径及文件访问权限 |
| 普通成员无法移除某条白名单 | 检查条目归属；他人、历史导入和后台添加的条目需管理员处理 |
| 某账号能管理其他成员的条目 | 核对该账号是否在本群 `admin_ids` 或当前会话 AstrBot `admins_id` 中 |
| 发送重启后服务端一直没有启动 | 确认启动脚本会在 `stop` 导致的正常退出后重新启动 Java 进程 |
| 重启结果没有群广播 | 先在对应群发送 `/mc`，检查主动消息支持情况或 `broadcast_origin` 配置 |
| 删除群后仍无法选择原服务器 | 点击“保存并生效”，再检查是否仍被其他群配置组选用 |

更多排查方法见[部署文档](docs/DEPLOY.md)。反馈问题时，请提供插件与 AstrBot 版本、部署方式、服务端核心及相关日志，并去掉密码等私密信息。

## 开发与打包

在仓库根目录的 Python 3.12+ 开发环境执行：

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m ruff check main.py mc_manager tests tools
python tools/build_release.py
```

安装包输出到 `dist/astrbot_plugin_mc_manager-<版本号>.zip`，包含许可证、图标、管理页面和采集器。插件运行依赖由 AstrBot 提供；开发依赖不会作为插件运行依赖安装。

v1.2.3 基于 AstrBot 4.28.0 与 4.29.0 发布源码的完整自动化测试各 182 项通过。缺少固定版本源码、Java 或编译器时会有明确跳过项，这不等同于完整验证。真实聊天平台和 MC 部署仍需联调。

虚拟环境、固定版本框架测试、JDK 编译和浏览器截图复现步骤见[开发指南](docs/DEVELOPMENT.md)，范围见[验证记录](docs/VALIDATION.md)。

## 详细文档

| 文档 | 内容 |
| --- | --- |
| [配置字段说明](docs/CONFIGURATION.md) | 根配置、服务器、群聊与消息模板字段 |
| [开发指南](docs/DEVELOPMENT.md) | 开发环境、完整测试条件、浏览器验证、构建与发布 |
| [部署指南](docs/DEPLOY.md) | 路径与容器挂载、RCON、UUID 模式、自动重启脚本、实际联调 |
| [网页管理说明](docs/WEB_PANEL.md) | 添加服务器与群聊、编辑配置、保存草稿、改绑与释放服务器 |
| [TPS 变量与默认模板](docs/TPS_VARIABLES.md) | 19 个指标、辅助变量、输出模板、缺失值处理 |
| [Java 采集器](companion/README.md) | 启动参数、本地报告、兼容范围与重新编译 |
| [AstrBot 接口适配](docs/ASTRBOT_COMPATIBILITY.md) | 框架版本要求、接口范围与升级说明 |
| [验证记录](docs/VALIDATION.md) | 自动化测试、页面截图及验证边界 |
| [更新日志](docs/CHANGELOG.md) | 各版本功能与修复 |
| [调研记录](docs/RESEARCH.md) | 实现依据与适用边界 |

## 许可证

本项目采用 [MIT License](LICENSE)，许可证及版权声明随源码和安装包提供。AstrBot、Minecraft、spark 和其他第三方组件遵循各自许可证。
