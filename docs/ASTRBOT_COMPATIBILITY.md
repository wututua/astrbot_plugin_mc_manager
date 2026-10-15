# AstrBot 接口适配与升级

当前插件 v1.3.0 声明支持 **AstrBot `>=4.28.0,<5`、Python `3.12+`**。4.28.0 是最低版本，4.29.0 是已验证的另一固定版本；该范围不表示所有未来 4.x 版本都经过测试，也不包含 AstrBot 5.x。

## 接口范围

| 接口 | 当前使用方式 |
| --- | --- |
| 插件元数据 | `metadata.yaml` 的必填字段、`astrbot_version` 与 `repo` |
| 基类与日志 | `Star(context, config)`、`self.logger` |
| 生命周期 | `initialize` 注册网页 API 并启动后台任务；`terminate` 等待任务结束并清理本实例路由 |
| 原生命令 | `filter.command("mc")` 注册，前置 `CustomFilter` 匹配完整主指令与自定义别名 |
| 身份与隔离 | 平台实例、群号和用户 ID 均来自事件接口，群隔离同时比较平台实例与群号 |
| 全局管理员 | 严格布尔 `event.is_admin()` 与 `Context.get_config(event.unified_msg_origin)` 中的 `admins_id` 同时匹配 |
| 会话与广播 | `unified_msg_origin`、`MessageChain().message()`、`Context.send_message()` |
| 数据目录 | `StarTools.get_data_dir`，保留已有持久化目录 |
| 配置保存 | `AstrBotConfig.save_config_async`，检查保存结果；取消时等待实际保存完成 |
| 网页接口 | `astrbot.api.web.request/json_response/error_response`，结合后台鉴权与身份检查 |
| 平台选项 | 通过平台管理器 `get_insts()` 枚举实例，只返回 ID、类型和名称 |
| 页面 SDK | 优先 `AstrBotPluginView`，兼容 `AstrBotPluginPage`，使用 `ready/apiGet/apiPost` |

全局身份读取失败时不授予全局管理权限；本群 `admin_ids` 仍按群配置校验。API Key 的管理员降权标记不会被普通事件角色覆盖。消息角色标记本身不够授予管理权限。

框架暂未提供与 `register_web_api` 配对的公开注销方法；插件卸载时按回调归属清理本实例的 `registered_web_apis` 项，不删除其他插件路由。此行为依赖已检查的容器形状，需要在框架升级时继续回归。

## 网页源码与安装包

仓库维护 `pages/console` 一套页面。正式构建包生成：

```text
astrbot_plugin_mc_manager/
├── logo.png
├── pages/console/index.html
└── views/console/index.html
```

CSS 与 JavaScript 同样复制并逐文件验证一致。4.28 使用 `pages`；4.29 优先 `views` 并保留 `pages` 回退。安装包只发现一个 console 页面。源码仓库安装使用 `pages` 即可。

## 验证矩阵

| AstrBot | 验证范围 |
| --- | --- |
| 4.28.0 | 核心、公开接口、真实命令注册与唤醒、Web API、安装器、页面发现的完整自动化回归 |
| 4.28.1 / 4.28.2 | 固定发布包的接口与适配检查，历史命令、Web API 和 ZIP 导入回归 |
| 4.29.0 | 完整回归，额外核对 views 页面发现、SDK 及 Web API 依赖变化 |

浏览器测试使用对应正式包的官方 SDK，模拟后端与父页面响应。完整 AstrBot 进程、真实聊天平台和 MC 服务端仍需实际联调。具体数量和边界见 [VALIDATION.md](VALIDATION.md)。历史版本的 4.26/4.27 记录只保留在更新日志，不属于当前支持范围。

## 升级步骤

1. 确认 AstrBot 至少 4.28.0，保留原插件配置与 `data/plugin_data/astrbot_plugin_mc_manager/`。
2. 通过仓库更新，或导入构建的插件 ZIP。
3. 重载插件并刷新后台页面，确认图标和页面正常。
4. 等待自动探测，在绑定群发送 `/mc`、玩家与 TPS 查询，并用两个账号核对归属权限。
5. 如修改了群号，在新群发送 `/mc` 重新记录广播会话。

插件名称、数据库位置和内部配置 ID 保持稳定。v1.3.0 会自动迁移旧绑定表，保留已有条目、编号、归属和日志，增加多群会话与共享关系。升级不会自动替换已有自定义模板；群服转移和归档规则见 [WEB_PANEL.md](WEB_PANEL.md)。
