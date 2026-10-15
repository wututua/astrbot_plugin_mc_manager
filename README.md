# Minecraft 群服管理插件

这是一个可安装到 AstrBot 的 Java 版 Minecraft 管理插件。群成员通过 `mc` 指令管理自己的白名单；同一个群绑定的多台服务端共用一份白名单，不同群彼此隔离。管理面板直接出现在 AstrBot 的插件详情页中。

支持 Python 3.12+、AstrBot **>=4.28.0,<5**。v1.2.0 按 AstrBot 4.28.0、4.28.1、4.28.2 与 4.29.0 的正式发布包检查插件使用的全部框架接口。AstrBot 4.28.0 以下请先升级机器人框架。白名单与 RCON 核心仅使用 Python 标准库。

## 已实现

- 按“平台实例 ID + 群 ID”绑定服务器集合，防止不同机器人实例之间的群号碰撞。
- 同组各服的 `whitelist.json` 同步；新增、移除、改名立即尝试重载，失败会持续重试。
- 首次接管合并保留各服已有条目；历史条目无归属，只能由管理员管理。
- 白名单固定编号、分页、成员归属、每人条目上限；改名保留编号，删除不复用编号。
- 在线玩家、TPS、服务器状态查询，可指定一台服务器或查询本群全部服务器。
- 管理员发送 `stop` 后监控旧连接断开和新 RCON 就绪；成功或失败广播到对应群。
- 重启成功可同时用 `tellraw` 广播给这台 Minecraft 服务器内的在线玩家。
- 添加、删除、修改、导入、同步与重启审计日志，记录群、用户、玩家和操作详情。
- 主指令、子指令别名、帮助菜单、各种返回文本与重启广播均可配置。
- 管理面板：状态、白名单、日志筛选、配置与模板编辑；使用 AstrBot 后台鉴权。
- SQLite 持久化、原子替换白名单、最近 10 份文件备份、损坏文件拒绝覆盖。

## 群与服务器的关系

```text
MC 服务端共同父目录
├── server1 ┐
├── server2 ├── group1 / 群 111111111
├── server3 ┘
├── server4 ┐
└── server5 ┘── group2 / 群 222222222
```

群 1 添加 Steve，只同步到 server1、server2、server3。群 2 的成员不能查询或重启群 1 的服务器。一个服务器只能归属一个群；绑定多台服务器的群内共用同一套条目和固定编号。

## 配置不完整时

如果群绑定缺少 id、platform_id 或 group_id，v1.2.0 会保留管理页面并显示具体错误，服务器管理暂时停用。打开“配置与菜单 → 服务器与群聊”，修正对应群绑定或移除空白项，再点击“保存并生效”，服务会立即启用。平台实例 ID 填群内 /sid 回复中的 Bot ID。

## 安装

1. 使用 `dist/astrbot_plugin_mc_manager-1.2.0.zip`，通过 AstrBot 插件管理中的本地上传安装；也可以把代码放到 `data/plugins/astrbot_plugin_mc_manager/`。
2. 在各服务端开启 RCON 和白名单，给每台服务端分配独立的 RCON 端口。
3. 打开插件的 **Minecraft 管理面板 → 配置与菜单 → 服务器与群聊**，在表单中填写共同父目录、添加服务器与群绑定，最后点击“保存并生效”。也可以使用 AstrBot 原生配置页。
4. 重载插件，在绑定的群内发送 `/mc`，记录本群主动广播的会话。
5. 打开插件详情 → Minecraft 管理面板，点击“刷新状态”，检查连接与同步状态。

完整默认配置为 [config.default.json](config.default.json)，两群五服示例为 [config.example.json](config.example.json)。

详细部署说明见 [docs/DEPLOY.md](docs/DEPLOY.md)。配置文件中的示例群号、平台 ID、管理员 ID、目录和密码需要替换成实际值。

## 默认指令

主指令支持 `mc` 和 `/mc`。以下表格使用 `/mc`：

| 指令 | 作用 | 权限 |
|---|---|---|
| `/mc`、`/mc help`、`/mc 帮助` | 帮助菜单 | 绑定群成员 |
| `/mc 添加 <玩家名>` | 添加自己的白名单 | 绑定群成员 |
| `/mc 查看 [页码]` | 查看本群白名单及固定编号 | 绑定群成员 |
| `/mc 移除 <编号或玩家名>` | 移除自己的条目 | 绑定群成员 |
| `/mc 修改 <编号或玩家名> <新玩家名>` | 改名，保留编号与归属 | 绑定群成员 |
| `/mc 玩家 [服务器 ID]` | 查询在线玩家 | 绑定群成员 |
| `/mc tps [服务器 ID]` | 查询 TPS | 绑定群成员 |
| `/mc 状态 [服务器 ID]` | 检查 RCON 是否可用 | 绑定群成员 |
| `/mc 服务器` | 查看本群服务器 ID | 绑定群成员 |
| `/mc 重启 <服务器 ID 或 all>` | 发送 stop 并监控重启 | 管理员 |
| `/mc 日志 [页码] [用户 ID]` | 查看本群日志，可筛选操作者 | 管理员 |
| `/mc 同步` | 立即重试本群白名单同步 | 管理员 |
| `/mc 导入` | 合并导入本群当前服务端文件的条目 | 管理员 |

玩家/TPS/状态省略服务器参数时查询本群全部服务器，也可显式使用 `all`。重启需要明确指定服务器或 `all`。

管理员指 AstrBot 全局管理员，或本群配置的 `admin_ids` 用户。不会自动把平台群主、群管理员头衔作为服务器管理权限。管理员可以修改或移除本群任何条目；其他成员仅可操作自己的条目。默认每人 1 条，管理员不受该数量限制。Minecraft 玩家名仅支持 Java 版 3～16 位字母、数字和下划线。

## 自定义指令与帮助

在插件配置的“自定义指令”中修改别名，多个别名使用英文逗号分隔。例如：

```json
{
  "prefix": "mc",
  "add": "add",
  "list": "list,查看",
  "remove": "remove,移除",
  "help": "help,帮助"
}
```

修改后 `/mc add Steve` 可用，`/mc 添加 Steve` 不再可用。若希望同时保留，填写 `add,添加`。别名不能重复，也不能含空白。主指令也可以改成 `minecraft`。

`messages.help` 是完整帮助文本。默认菜单使用 `{prefix}`、`{add}` 等占位符，所以别名修改后会自动更新；你也可以把整个菜单换成自己的文字。

```text
欢迎使用本群 MC 管理
{prefix} {add} <玩家名>：申请白名单
{prefix} {players}：看看谁在线
{prefix} {tps}：看看服务器是否卡顿
```

## 自定义输出与广播

在 `messages` 下修改对应消息模板：

| 模板 | 常用占位符 |
|---|---|
| `help` | `{prefix}`、所有子指令占位符，如 `{add}`、`{restart}` |
| `added` | `{id}`、`{player}`、`{user}`、`{sync}` |
| `removed` | `{id}`、`{player}`、`{sync}` |
| `modified` | `{id}`、`{old_player}`、`{player}`、`{sync}` |
| `list` | `{page}`、`{pages}`、`{total}`、`{entries}` |
| `entry` | `{id}`、`{player}`、`{owner}` |
| `players`、`tps`、`status` | `{server}`、`{result}` |
| `servers` | `{entries}` |
| `logs` | `{page}`、`{pages}`、`{total}`、`{entries}` |
| `log_entry` | `{time}`、`{action}`、`{group}`、`{user}`、`{player}`、`{detail}` |
| `restart_started` | `{servers}` |
| `restart_success` | `{server}`、`{seconds}`、`{detail}` |
| `restart_failed` | `{server}`、`{error}` |
| `game_restart_success` | `{server}` |
| `sync` | `{sync}` |
| `import` | `{count}`、`{sync}` |
| `error` | `{error}` |

文字中的普通花括号写成 `{{` 和 `}}`；模板不支持 Python 表达式、属性访问或格式化代码。服务端返回的颜色代码会被去掉。

TPS 默认使用 spark，控制台命令为 `spark tps`。19 个 TPS、Tick 耗时和 CPU 指标都可以通过变量放入 `messages.tps`。完整变量表、默认模板与空响应处理见 [docs/TPS_VARIABLES.md](docs/TPS_VARIABLES.md)。管理页面提供变量表与 TPS 模板编辑器；部分 spark 环境的 RCON 回复为空时，可以启用包内的 [本地采集器](companion/README.md)。

重启成功群广播示例：`🎉 {server} 已恢复，耗时 {seconds} 秒，大家可以上线啦！{detail}`。失败广播示例：`⚠️ {server} 启动失败，请管理员检查：{error}`。

## 管理页面

入口是 AstrBot 插件详情页中的 **Minecraft 管理面板**。安装包同时提供内容一致的 `pages/console/index.html` 和 `views/console/index.html`，由对应版本的 AstrBot 加载，页面通过相应正式版本的官方 bridge 调用插件 API，使用 AstrBot 后台鉴权。新版优先使用 AstrBotPluginView，4.28 系列使用 AstrBotPluginPage。

页面支持按群切换、玩家/TPS 查询、白名单添加/修改/移除、同步重试、导入、重启以及操作日志筛选。配置使用可视化表单：添加服务器、添加群聊、勾选服务器、管理员账号、常规设置、指令别名、菜单、回复与广播、spark TPS 都可直接修改；高级 JSON 保留为批量编辑入口。保存后立即生效，密码留空可以保留原密码，草稿状态和多页面版本检查避免误覆盖。详细操作见 [docs/WEB_PANEL.md](docs/WEB_PANEL.md)。

页面新增条目归属于 `dashboard:<后台用户名>`。后台管理员可以管理本群所有条目；如需要让群成员自行移除，请让成员在群里添加自己的条目。

## 数据与同步规则

插件数据目录通常是 `data/plugin_data/astrbot_plugin_mc_manager/`，实际路径会在面板配置页显示。`mc.sqlite3` 保存白名单、编号计数、归属、操作日志、广播会话、重启状态和待发送广播。更新插件不应删除该目录。

首次接管每个群时先读取全部服务端文件，成功后合并写入数据库；同名不同 UUID、同 UUID 不同名字、损坏文件都会阻止初始接管。已有玩家不会被强行赋予某个群成员。

完成初始接管后，数据库是同步依据。请通过插件改白名单；控制台或人工文件修改会在后续同步中被覆盖。主动执行“导入”才会再次合并文件内容，可能重新引入已删除但尚未同步的条目。

添加/移除/改名先在同一数据库事务中提交条目、日志和待同步标记，再逐台服务端同步。多服不能保证跨机器的原子提交；某服失败时会明确显示待同步，并默认每 30 秒重试。离线服文件可先写入，下次启动读取；在线服通过 `whitelist reload` 重载。

每次实际修改文件前，在该服务端 `.mcwl-backups/` 保存旧版本，最多保留 10 份。损坏的 `whitelist.json` 会保留原状并提示管理员处理。

群配置 ID 用于关联本配置组的白名单与日志，在表单中保持固定。QQ 群号和平台实例可以修改：同平台更换群号时保留该组白名单与编号，旧日志仍记录原群号，广播会话和旧待发送通知会重置；跨平台修改时玩家保留、用户归属清空，由管理员管理。删除群绑定或取消服务器勾选并保存后会释放服务器。转给其他配置组时使用新组白名单，不自动导入旧组成员；旧组数据与日志归档保留。

## 重启判断

插件不启动 Java 进程。它只发送一次 `stop`，由你的启动脚本负责重新启动服务端。监控先保留旧 RCON 连接等待断开，再建立新连接；新连接连续两次能执行玩家查询才判定成功，默认总等待时间 180 秒。

成功与失败通知会进入持久化发送队列，只发送到对应群的广播会话。发送失败在后续同步周期重试；这是至少一次投递，极端情况下可能重复通知。插件在监控期间被卸载或重载，下一次加载会广播“监控中断，结果未确认”，不会冒充已确认成功。

本插件确认的是新 RCON 可以执行查询；地图、特定模组或代理网络的全部功能仍应按实际服务端情况检查。

## 验证与打包

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py mc_manager
node --check pages/console/app.js
python -m pip install -r requirements-dev.txt
python tools/build_release.py
```

测试覆盖群隔离、归属限制、管理员权限、稳定编号、持久化、日志、初始合并、文件损坏、离线同步、重启成功/失败/超时、消息重试，以及真实 TCP 上的 RCON 认证、拆包、多包 UTF-8 和连接断开。v1.2.0 的验证包含框架公开接口、异步配置保存、初始化与卸载、群消息、主动广播、网页 SDK、服务器/群表单、spark 与既有权限功能，管理页面还做了桌面/手机尺寸的浏览器检查；详情与截图见 [docs/VALIDATION.md](docs/VALIDATION.md)。

调研依据和适用边界见 [docs/RESEARCH.md](docs/RESEARCH.md)。本工作区没有正在运行的 AstrBot 实例和真实 Minecraft 服务端，因此上线前需要按部署文档完成实际环境联调。

升级接口的具体范围、版本矩阵和保留数据说明见 [docs/ASTRBOT_COMPATIBILITY.md](docs/ASTRBOT_COMPATIBILITY.md)。
