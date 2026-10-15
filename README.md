# Minecraft 群服管理

<p align="center">
  <a href="https://sg1s3.ligzs.com/tu-oss/%E5%BE%AE%E4%BF%A1%E5%9B%BE%E7%89%87_2026-10-16_020043_580.png">
    <img src="logo.png" alt="Minecraft 群服管理 Logo" width="128" height="128">
  </a>
</p>

AstrBot 的 Minecraft Java 版群服管理插件，支持多群共享服务器、白名单同步、性能查询和网页管理。

**AstrBot `>=4.28.0,<5` · Python `3.12+`**

[下载安装包](https://github.com/wututua/astrbot_plugin_mc_manager/releases/latest) · [问题反馈](https://github.com/wututua/astrbot_plugin_mc_manager/issues) · [更新日志](docs/CHANGELOG.md)

原生 / spark TPS 模式选择目前为 `main` 的未发布改动，请更新仓库源码并重载；现有 Releases 安装包尚未包含。

## 主要功能

- **群服共享**：一条配置绑定多个群，多条配置可选择同一服务器。
- **白名单管理**：添加、查看、修改、移除，固定编号、成员归属和自动同步。
- **玩家与性能**：在线玩家、状态、服务端原生 TPS 或 spark TPS / Tick 耗时 / CPU 查询。
- **重启与广播**：发送 `stop`，监控外部脚本重启，并向关联群通知结果。
- **网页配置**：服务器、群聊、管理员、指令、菜单、回复模板及操作日志。

<details>
<summary>查看管理面板</summary>

![Minecraft 管理面板](docs/screenshots/web-settings.png)

</details>

## 快速开始

1. 在 AstrBot 插件安装界面填写仓库地址，或上传 Releases 中的插件安装包：

   ```text
   https://github.com/wututua/astrbot_plugin_mc_manager
   ```

2. 在每台 Minecraft 服务端的 `server.properties` 中开启 RCON 和白名单，修改后完整重启服务端：

   ```properties
   enable-rcon=true
   rcon.port=25575
   rcon.password=替换为你的密码
   white-list=true
   ```

3. 打开 **插件详情 → Minecraft 管理面板 → 配置与菜单 → 服务器与群聊**，填写共同父目录、服务器 RCON 信息、平台实例 ID 和群号，点击“保存并生效”。平台实例 ID 使用群内 `/sid` 返回的 Bot ID。
4. 在绑定的群中发送 `/mc`，即可查看菜单并记录本群广播会话。插件会自动探测服务器状态。

AstrBot 需要能够读写各服的 `whitelist.json` 并连接 RCON。同机各服使用不同 RCON 端口；Docker 部署需要挂载服务端目录。详见[部署指南](docs/DEPLOY.md)。

## 多群与服务器共享

| 配置方式 | 效果 |
| --- | --- |
| 同一配置的群号填写 `111111111,222222222` | 两个群共用白名单、编号和管理员设置；也支持中文逗号、换行或 JSON 数组 |
| 两条配置各填一个群号，都选择 `server1` | 各组独立管理条目，`server1` 同步两组白名单的并集 |

共享服上的同名同 UUID 条目只写一次；一组移除后，另一组仍保留的条目继续生效。各组专属服务器只同步本组条目，同一实际群在同一平台实例下只对应一条配置。

## 指令列表

`<参数>` 必填，`[参数]` 可选。指令及帮助菜单均可自定义。

| 指令 | 功能 |
| --- | --- |
| `/mc`、`/mc help`、`/mc 帮助` | 查看菜单 |
| `/mc 添加 <玩家名>` | 添加自己的白名单 |
| `/mc 查看 [页码]` | 查看白名单和固定编号 |
| `/mc 移除 <编号或玩家名>` | 移除自己的条目 |
| `/mc 修改 <编号或玩家名> <新玩家名>` | 修改自己的条目 |
| `/mc 玩家 [服务器 ID]` | 查询在线玩家 |
| `/mc tps [服务器 ID]` | 按该服设置查询原生 TPS 或 spark 性能 |
| `/mc 状态 [服务器 ID]` | 查询 RCON 可用状态 |
| `/mc 服务器` | 查看本组服务器 |
| `/mc 重启 <服务器 ID 或 all>` | 重启并监控结果，限管理员 |
| `/mc 日志 [页码] [用户 ID]` | 查看或筛选日志，限管理员 |
| `/mc 同步` | 重试同步，限管理员 |
| `/mc 导入` | 导入额外磁盘条目，限管理员 |

玩家、TPS、状态查询省略服务器时查询本组全部服务器，也可填写 `all`。重启需要明确指定目标。

## 权限规则

普通成员只能修改、移除自己的条目，默认每人最多 1 条，可按配置组调整。管理员为 AstrBot 全局管理员或该组 `admin_ids` 中的账号，QQ群主和群管理员头衔不会自动授予权限。

历史导入条目由管理员管理；网页新增条目归属于后台账号。玩家名支持 Java 版的 3～16 位字母、数字和下划线，添加者归属不代表验证了 Minecraft 账号所有权。

## 自定义指令与回复

在网页“配置与菜单”中修改别名、帮助菜单和回复模板。例如将添加别名设为 `add,添加`，即可同时使用 `/mc add Steve` 和 `/mc 添加 Steve`。固定入口 `/mc` 保留。

`messages.help` 控制帮助菜单，`messages.tps` 控制性能回复，重启广播等消息也可自定义。模板及变量见[配置说明](docs/CONFIGURATION.md#回复模板变量)和 [TPS 变量表](docs/TPS_VARIABLES.md)。

## 使用提示

- **TPS**：在“配置与菜单 → TPS 设置”按服选择原生或 spark 模式并自定义指令。原生模式完整返回命令回复；仅 spark 支持变量模板，RCON 回复为空时可使用[Java 采集器](companion/README.md)。
- **重启**：插件只发送 `stop`，Java 进程重新启动由你的脚本或进程管理器负责。每个需要接收广播的群先发送一次 `/mc`。
- **同步与升级**：接管后以数据库条目为同步依据，手工改文件可能被覆盖。更新时保留原配置和插件数据目录；每次改写白名单前会保留文件备份。

## 详细文档

| 文档 | 内容 |
| --- | --- |
| [部署指南](docs/DEPLOY.md) / [网页管理](docs/WEB_PANEL.md) | 安装、RCON、群绑定、容器、重启与排查 |
| [配置字段](docs/CONFIGURATION.md) | 完整参数、指令和回复模板 |
| [TPS 变量](docs/TPS_VARIABLES.md) / [Java 采集器](companion/README.md) | 性能指标、自定义输出与启动参数 |
| [开发指南](docs/DEVELOPMENT.md) | 测试、构建与开发环境 |
| [接口适配](docs/ASTRBOT_COMPATIBILITY.md) / [验证记录](docs/VALIDATION.md) | 支持范围与测试边界 |
| [更新日志](docs/CHANGELOG.md) / [实现依据](docs/RESEARCH.md) | 版本变化与设计说明 |

配置参考：[默认配置](config.default.json) · [多群共享示例](config.example.json)。

本项目采用 [MIT License](LICENSE)。
