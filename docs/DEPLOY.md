# 部署与首次联调

本文适用于插件 v1.4.0、AstrBot `>=4.28.0,<5`、Python `3.12+`。首次使用可先阅读 [README](../README.md)，网页操作见[管理页面说明](WEB_PANEL.md)。

## 1. 准备文件访问与目录

插件通过服务端目录中的 `whitelist.json` 和 RCON 管理 Minecraft Java 版服务端。AstrBot 需要能够读写这些目录；可以与服务端运行在同机，也可以通过可信文件挂载访问。仅连接远程 RCON 不足以完成白名单同步。

```text
D:/Minecraft/
├── server1/whitelist.json
├── server2/whitelist.json
└── server3/whitelist.json
```

`server_root` 填 `D:/Minecraft`，各服 `directory` 填 `server1`、`server2` 等相对目录。子目录必须留在共同父目录内，服务器目录需要提前存在。缺少 `whitelist.json` 时按空白名单处理；文件损坏时保留原状并提示错误。

Docker 示例：宿主机 `/srv/minecraft` 以读写方式挂载到 AstrBot 容器的 `/minecraft`，插件填写 `/minecraft`。RCON 地址也应从容器内可达；容器内的 `127.0.0.1` 指容器自身。Linux 下 AstrBot 用户应能读写服务端目录，Java 用户应能读取替换后的白名单文件。

## 2. 开启 RCON 和白名单

各服 `server.properties` 示例：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=替换为你的密码
broadcast-rcon-to-ops=false
white-list=true
```

同机不同服务端分配不同 RCON 端口，例如 `25575`、`25576`、`25577`。修改后完整重启服务端一次。RCON 连接不提供传输加密，应通过本机、可信内网或受保护的隧道访问，避免直接暴露到公网。

可以填写 `rcon_password`，也可以通过 `rcon_password_env` 引用 AstrBot 进程的环境变量。填写了环境变量名后，它优先于直接填写的密码；环境变量不存在不会回退。

Windows PowerShell 示例，在启动 AstrBot 的同一环境设置：

```powershell
$env:MC_RCON_1 = '替换为 server1 的密码'
$env:MC_RCON_2 = '替换为 server2 的密码'
```

Linux / Docker 应在实际运行 AstrBot 的服务或容器环境传入变量。在宿主机终端设置变量，不代表已有容器会自动获得该变量。

## 3. 安装与绑定群聊

在 AstrBot 的插件安装界面填写仓库地址：

```text
https://github.com/wututua/astrbot_plugin_mc_manager
```

也可导入由 `tools/build_release.py` 生成的安装包。GitHub 的源码 ZIP 与构建的插件 ZIP 用途不同；本地上传优先使用构建包。

打开 **Minecraft 管理面板 → 配置与菜单 → 服务器与群聊**：

1. 设置服务端共同父目录，添加服务器及其 RCON 信息。
2. 添加群聊，设置唯一配置 ID、平台实例 ID、一个或多个实际群号；可与其他配置组勾选相同服务器。
3. 勾选该群管理的服务器，填写本群管理员账号与每人条目上限。
4. 点击“保存并生效”。原生插件配置页保存后需要重载插件。

群配置片段：

```json
{
  "id": "group1",
  "platform_id": "替换为 /sid 返回的 Bot ID",
  "group_id": "111111111,111111112",
  "server_ids": ["server1", "server2", "server3"],
  "admin_ids": ["123456789"],
  "max_entries_per_user": 1,
  "broadcast_origin": ""
}
```

`platform_id` 来自群内 `/sid` 返回的 Bot ID，不是适配器类型或机器人 QQ 号。群号可用逗号或换行填写多个，高级 JSON 也支持数组；用户 ID 使用字符串。内部 `id` 与实际群号是不同字段，已保存的内部配置 ID 保持固定。

完整配置见[默认配置](../config.default.json)与[多群共享五服示例](../config.example.json)。配置不完整时仍可打开网页，按提示修正空白群绑定后保存。

## 4. 选择正确的 UUID 模式

`servers[].online_mode=false` 使用 Java 离线 UUID；`true` 通过 Mojang 查询正版 UUID，需要 AstrBot 能访问查询服务。同群各服必须采用同一身份体系。

该字段只控制插件的 UUID 选择，不修改 `server.properties`。代理转发正版 UUID 的网络应按实际玩家 UUID 选择，不能只依据后端的 `online-mode=false`。玩家名支持 Java 版的 3～16 位字母、数字和下划线。白名单归属表示添加者的平台账号，不验证其 Minecraft 账号所有权。

## 5. 在线探测与 spark TPS

插件启动、重载和保存有效配置后立即在后台并行探测全部已配置服务器。每轮按 `sync_interval` 重新探测，默认间隔 30 秒。管理页面在非配置编辑页每 10 秒读取缓存；“刷新状态”立即主动探测。

“在线”表示 RCON 查询可用；“离线或 RCON 不可用”可能由游戏进程停止、端口不可达或认证失败等原因导致，不等同于游戏端口一定离线。

TPS 可按服在网页“TPS 设置”选择 `spark` 或 `native`。spark 默认命令为 `spark tps`，需要服务端启用 spark；原生模式默认 `tps`，也可改成该核心提供的查询指令，完整返回文本且不使用变量模板。RCON 返回空响应时使用[可选 Java 采集器](../companion/README.md)。所有指标与可配置文本见 [TPS 变量表](TPS_VARIABLES.md)。

## 6. 自动重启与广播

插件只通过 RCON 发送 `stop`。重新启动 Java 进程由你的脚本或进程管理器完成，且必须在服务端工作目录启动。下面适用于普通服务端 JAR，保留自己的 Java、内存和文件名。

Windows `start.bat`：

```bat
@echo off
cd /d "%~dp0"
:restart
java -Xms2G -Xmx4G -jar server.jar nogui
echo Server stopped, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto restart
```

Linux `start.sh`：

```sh
#!/usr/bin/env bash
cd -- "$(dirname -- "$0")" || exit 1
while true; do
  java -Xms2G -Xmx4G -jar server.jar nogui
  sleep 5
done
```

使用采集器时，将 `-javaagent` 加入循环内的 Java 命令；可直接参考 [Windows 采集器启动模板](../companion/start-with-spark-agent.example.bat)。使用进程管理器时，需确认 `stop` 导致的正常退出也会触发重启。

管理员执行 `/mc 重启 server1` 后，插件等待旧连接断开，再检查新 RCON 连续两次可以执行玩家查询，才判断重启成功。`restart_timeout` 默认 180 秒，`poll_interval` 默认 3 秒。成功只表示控制台查询已就绪，不能保证所有模组的业务已恢复。

先在每个需要广播的群发送一次 `/mc` 分别记录广播会话，或填写 `broadcast_origin`。群广播依赖适配器主动发送能力；该公开接口不能识别某些适配器内部的静默发送失败。`messages.restart_success`、`messages.restart_failed` 可自定义群消息；`game_broadcast` 控制成功后的游戏内广播。

## 7. 联调顺序

1. 等待自动探测，确认各服状态；按需点击“刷新状态”。
2. 在两个绑定群分别发送 `/mc`，确认帮助菜单与会话记录。
3. 群 1 成员添加测试玩家，检查只同步本群服务器。
4. 另一个普通成员尝试按编号和玩家名移除，应被拒绝；原添加者可正常修改和移除。
5. 管理员查看日志，确认群号、操作者、玩家名和操作类型正确。
6. 查询玩家和 TPS；若 RCON TPS 为空，确认采集器报告持续更新。
7. 配置自动重启后执行 `/mc 重启 server1`，验证结果广播到该服所有关联组中已记录会话的群；不包含该服的群不会收到。
8. 暂停自动启动脚本，验证超过超时后返回失败。
9. 让一台服暂时离线，修改白名单后检查“待同步”，恢复后确认自动重试。

首次导入时，一台服务器的原有文件只由最先接管它的配置组导入，其他组不会自动认领其中成员；各自独立的首次接管服务器仍正常导入。不同组共享服务器时使用各组数据库条目的并集。

首次导入的条目没有成员归属。测试成员自助管理时，应由管理员移除测试条目，再让成员在群内自行添加。

## 8. 升级、改绑与排查

升级时保留现有配置与 `data/plugin_data/astrbot_plugin_mc_manager/`。数据库保存编号、归属、日志与广播记录；删除它会失去这些数据。每次实际改写白名单前，服务端 `.mcwl-backups/` 最多保存 10 份旧文件。

删除群绑定或取消服务器选择后保存，只移除该组与服务器的关联，其他配置组可以继续使用。新增共享绑定时采用关联组白名单并集；完全转给另一组时不自动导入旧组成员。改绑规则见[管理页面说明](WEB_PANEL.md)。

| 现象 | 检查位置 |
| --- | --- |
| `/mc` 无响应 | 插件加载日志、原生指令是否启用、平台实例与群号是否一致 |
| RCON 拒绝连接 / WinError 1225 | 进程、端口、RCON 是否开启、容器地址及网络 |
| Errno 28 / No space left on device | 检查 AstrBot、服务端和临时目录所在盘；释放空间后查看诊断中的后台任务恢复状态 |
| 重启成功但通知长期未到达 | 运维与数据 → 通知队列，检查失败原因后手动重试 |
| 后台任务显示异常后重试 | 运维与数据 → 诊断，检查最近错误、磁盘、目录权限和数据库状态 |
| RCON 认证失败 | 实际密码与环境变量是否可读 |
| 本地目录不存在或无法写入 | 容器挂载、共同父目录、相对目录与文件权限 |
| 普通成员能管理他人条目 | 是否仍运行旧版；该账号是否列入本群或 AstrBot 管理员配置 |
| 历史条目无法自行移除 | 历史条目无归属，由管理员处理 |
| spark 文件缺失、过期或不可解析 | Java 启动参数、spark API、工作目录、`spark_file`、读取权限 |
| 管理员无法重启 | 本群 `admin_ids` 或当前会话 AstrBot `admins_id` 配置，以及可核实的事件身份 |
| 重启后没有广播 | 广播会话、平台主动发送能力、重试队列 |
| 升级后仍显示旧图标或页面 | 重载插件并刷新 AstrBot 后台 |

反馈问题请在仓库创建 Issue，附插件/AstrBot 版本、部署方式、核心与 Java 版本、复现步骤及脱敏日志。
