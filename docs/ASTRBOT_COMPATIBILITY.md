# AstrBot 4.28+ 接口适配说明

v1.1.0 的最低支持版本为 **AstrBot 4.28.0**，元数据声明 `>=4.28.0,<5`。Python 要求为 3.12+。本次范围是插件实际使用的全部 AstrBot 接口，包括聊天、网页、配置和生命周期。

## 核对范围

| 接口 | 适配方式 |
|---|---|
| 插件加载与版本 | `metadata.yaml` 的必填字段、名称、版本范围、ZIP 根目录与正式安装器 |
| 插件基类与日志 | `Star(context, config)`，使用 `self.logger` 写插件独立日志 |
| 初始化与卸载 | `initialize` 中启动服务并注册 Web API；`terminate` 结束后台任务并清理当前实例路由 |
| 指令入口 | 公开 `filter.CustomFilter` 和 `filter.custom_filter`，保持可配置命令别名 |
| 群与用户身份 | `get_platform_id/get_group_id/get_sender_id/get_sender_name/is_admin`；群隔离继续比较平台实例 ID 和群号 |
| 广播会话 | `unified_msg_origin`，保持框架消息会话格式 |
| 消息发送 | `MessageChain().message(text)`，通过 `Context.send_message` 投递 |
| 插件数据目录 | `StarTools.get_data_dir`，保留 `plugin_data/astrbot_plugin_mc_manager` |
| 配置保存 | `AstrBotConfig.save_config_async`，检查是否提交成功；取消时等待正在进行的磁盘保存结束 |
| Web 请求/响应 | `astrbot.api.web.request/json_response/error_response`，保持管理员身份校验 |
| 平台选择 | 通过平台管理器 `get_insts()` 获取实例，仅展示 ID、适配器和名称 |
| 管理页面 | 4.28 的 pages 路径和 4.29 的 views 路径均随包提供，内容来自同一套源码 |
| 页面 SDK | 优先 `window.AstrBotPluginView`，兼容 `window.AstrBotPluginPage`；调用 ready/apiGet/apiPost |

路由注册使用公开的 `register_web_api`。AstrBot 当前没有配对的公开 unregister 方法，因此卸载时按回调归属清理 `registered_web_apis`，只移除当前插件实例的项目；此容器形状已在目标版本中检查。平台枚举仍通过 Context 提供的平台管理器访问，其 `get_insts` 方法也有对应版本的检查。

## 页面打包

工作区维护 `pages/console` 一套源码。构建脚本把相同内容写入 ZIP 的：

```text
astrbot_plugin_mc_manager/
  pages/console/index.html
  views/console/index.html
```

CSS 与 JavaScript 同样复制。构建会逐文件核对两套入口内容一致。4.28 发现 pages；4.29 优先发现 views，不会展示两份相同的面板。

## 升级

1. 先将 AstrBot 升级到 4.28.0 或更新的 4.x 正式版本。
2. 导入 `astrbot_plugin_mc_manager-1.1.0.zip` 更新插件。
3. 重载插件并刷新管理页面。
4. 检查服务器状态，在绑定群发送 `/mc` 和 `/mc tps` 验证。

插件名称、数据库路径、服务器 ID、群 ID 和消息模板字段保持不变。更新时保留现有配置和插件数据目录，已有白名单归属、编号与日志继续使用。

## 版本验证

- 4.28.0：公开接口检查、实际 Web API 实现、安装器与页面发现检查，以及完整业务回归。
- 4.28.1、4.28.2：同一套接口、Web API 和 ZIP 导入验证。
- 4.29.0：检查新增的 views 页面根目录、官方页面 SDK 和 Web API 依赖，并运行完整业务回归。
- 浏览器使用正式发布包中的官方 SDK，执行页面初始化握手和 API 请求转发；后端响应使用受控测试数据。
- 对 4.28.0 以下及 5.0.0，框架版本检查会按声明拒绝加载。

验证针对明确列出的正式版本；未来 4.x 仍遵循本声明的版本范围，但若公开接口再变化，需要追加实际验证。工作区没有启动完整 AstrBot 和真实 Minecraft 服务端，部署联调步骤见 DEPLOY.md。

## 复现

设置 `MC_TEST_ASTRBOT_RELEASE` 为指定正式发布包解压后的 `astrbot` 目录：

```powershell
$env:MC_TEST_ASTRBOT_RELEASE = 'D:/release/astrbot'
python -m unittest discover -s tests -v
node tools/test_ui.cjs
```

接口检查直接使用目标包的 Web API 实现和原始框架方法，测试中的其余框架对象由替身提供。浏览器测试需要本工作区开发用 Playwright 和 Chromium；接口测试需 FastAPI、PyYAML 和 packaging。
