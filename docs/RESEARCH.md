# 调研记录与实现选择

调研使用 AstrBot 官方仓库、PyPI 发布包及 PaperMC 官方资料。查询时 PyPI 返回 AstrBot **4.28.2**，对应发布包要求 Python 3.12+。针对用户实际使用的 AstrBot 4.26.3，后续下载其正式发布包补充验证。v1.0.2 的适用版本在 metadata.yaml 中修正为 >=4.26.3,<5。

## AstrBot 4.26.3 兼容性补充

- 依据 PyPI 的 4.26.3 固定版本发布包，Python 要求同样是 >=3.12。
- `astrbot/api/web.py`、`api/star/__init__.py`、`api/event/__init__.py`、`api/event/filter/__init__.py`、`dashboard/plugin_page_bridge.js` 与已检查的 4.28.2 版本内容一致。
- 检查 4.26.3 的 `register_web_api()`、`send_message()`、`StarTools.get_data_dir()` 与事件身份方法，插件所需的调用方式可用。
- 4.26.3 仍扫描 `pages/<名称>/index.html`；原始页面发现方法可以找到包内的 console 页面。
- 安装器内部命名是 `PluginUpdator.unzip_file`，4.28.2 内部命名为 `_PluginUpdater._extract_plugin_archive`。插件本身不依赖这些内部名称，测试会选择对应的原始导入方法验证压缩包。
- 使用 4.26.3 的 `_validate_astrbot_version_specifier()` 复现旧范围的拒绝信息，并验证新范围接受 4.26.3 与 4.28.2，拒绝 5.0.0。
- 这次无需修改插件业务代码或移除管理页面，原因是公开接口已经兼容，之前的最低版本声明过高。

固定版本来源： https://pypi.org/pypi/astrbot/4.26.3/json

## AstrBot 插件接口

已检查 4.28.2 发布包中的：

- `astrbot/api/event/filter/__init__.py`：CustomFilter 与 custom_filter 公开导出。使用自定义过滤器解析配置的主指令与子指令，避免装饰器写死命令别名。
- `astrbot/core/platform/astr_message_event.py`：`get_platform_id()` 是平台实例 ID，区别于适配器类型；群隔离同时比较实例 ID 和群 ID。`is_admin()` 是 AstrBot 管理员身份。
- `astrbot/core/star/star_tools.py`：`StarTools.get_data_dir()` 返回插件专用持久化目录。
- `astrbot/core/star/context.py`：`register_web_api(route, handler, methods, desc)` 注册插件扩展 API；`send_message(origin, MessageChain)` 用于主动广播，并返回是否找到可发送的平台。
- `astrbot/api/web.py`：公开的 `request`、`json_response`、`error_response` 接口，避免直接依赖内部 FastAPI 请求对象。
- `astrbot/dashboard/services/plugin_page_service.py`：4.28.2 扫描 `pages/<page_name>/index.html`，返回 HTML 时插入 bridge SDK。
- `astrbot/dashboard/plugin_page_bridge.js`：页面使用 `window.AstrBotPluginPage.ready()`、`apiGet()`、`apiPost()`；后台替页面执行鉴权请求。
- `astrbot/dashboard/api/plugins.py`：插件扩展请求在管理员鉴权与插件权限检查后调用注册的 handler。插件的页面 API 额外要求公开请求上下文中的 `username`。

官方 master 分支的文档已使用 `views/` 和 `AstrBotPluginView`，同时说明兼容 `pages/` 和 `AstrBotPluginPage`。本插件以实际发布包的 pages 结构为依据，前端兼容两种 bridge 全局名称。

官方依据：

- https://github.com/AstrBotDevs/AstrBot
- https://pypi.org/pypi/astrbot/json
- https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/guides/plugin-pages.md
- https://github.com/AstrBotDevs/AstrBot/blob/master/docs/zh/dev/star/guides/plugin-config.md
- https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/api/web.py

## Minecraft 控制方式

- 本地 `whitelist.json` 是服务端读取的白名单文件。同步后通过控制台 `whitelist reload` 使运行中的服务器重载。
- 玩家查询默认 `list`，TPS 默认 `tps`。TPS 并非所有核心都提供，按服务器设置命令，不从延迟、日志或网络 ping 推算 TPS。
- `enable-rcon`、`rcon.port`、`rcon.password` 开启控制台连接；各实例的端口独立。
- RCON 请求使用长度前缀、请求 ID、类型和双 NUL 结束。客户端使用 readexactly 处理拆包，合并同 ID 多包后再解码 UTF-8。
- 空命令在某些服务端不保证产生响应，因此使用只读 `time query gametime` 作为单包响应界标，保证长查询输出不会截断。服务端需兼容 Java 原生 RCON 与该标准命令。
- 重启只发送 `stop`。旧连接断开后，新 RCON 连续两次执行查询通过才判断启动成功。它证明控制台就绪，不等同于所有第三方模组业务都已恢复。
- 游戏内广播用 `tellraw @a` 的 JSON 文本组件，避免把自定义广播文字拼成可执行子命令。

官方依据：

- https://docs.papermc.io/paper/reference/server-properties/
- https://docs.papermc.io/paper/reference/commands/
- https://github.com/Mojang/brigadier

## UUID 与数据归属

离线模式按 `OfflinePlayer:<name>` UTF-8 字符串的 MD5、Java nameUUIDFromBytes 版本/变体位规则计算 UUID；与测试向量 Notch = b50ad385-829d-3141-a216-7e7d7539ba7f 一致。正版模式调用 Mojang profile lookup，成功后使用服务返回的标准玩家名和 UUID。

多个服务端必须采用同一 UUID 身份体系。代理转发网络需要管理员按实际玩家 UUID 选择插件模式；不能只看后端 online-mode=false 就认定玩家使用离线 UUID。

白名单初始来源是同组所有服务端文件的并集，后续来源是插件数据库，避免已删除条目从一个短时离线的旧文件复活。导入历史条目不自动认领；它们由管理员管理。聊天用户对自己条目的归属来自平台用户 ID，并非 Minecraft 账号所有权认证。

## 验证范围

- 用标准库 unittest 与临时服务端目录验证白名单、群隔离、权限、持久化、故障重试和重启通知。
- 用真实本机 TCP 测试 RCON 认证响应、多包/拆包、UTF-8、超时与 stop 后断线。
- 对插件适配层使用 4.26.3 与 4.28.2 发布包的公开 Web 请求/响应实现做接口检查，原始安装器与页面发现方法也做了验证。
- 管理页面通过模拟官方 bridge 做桌面和手机浏览器验证；模拟数据不等于真实 AstrBot 已部署。
- 本工作区没有用户真实平台实例、MC 服务端、RCON 密码或启动脚本，因此没有发送真实停服命令。部署后的真实联调按 DEPLOY.md 进行。

## 可视化配置接口

v1.0.4 通过受 AstrBot 管理员鉴权的原有 config API 提供 schema、平台摘要和历史绑定。平台选项读取已有 Context.platform_manager.platform_insts 的元数据，仅返回 ID、类型与显示名称，不返回平台配置。页面保存携带内存中的随机版本标识，阻止旧页面覆盖新配置；内部业务仍由 Settings 与 Store 验证群和服务器归属。该接口在 4.26.3 与 4.28.2 的发布包公开 Web API 上进行了适配层测试。
