# 部署与首次联调

## 1. 服务器目录

插件需要直接读写 Minecraft 服务端目录，AstrBot 必须在同一台机器上运行，或通过文件挂载获得相同目录的可写访问。这里只接入本地文件与 RCON，没有额外的远程代理程序。

Windows 示例：

```text
D:/Minecraft/
  server1/whitelist.json
  server2/whitelist.json
  server3/whitelist.json
  server4/whitelist.json
  server5/whitelist.json
```

`server_root` 填 `D:/Minecraft`。各服务器 `directory` 填 `server1`、`server2` 等相对目录。可以使用更深的子目录，但路径必须留在共同父目录内。服务器文件夹需要提前存在，插件不会创建假服务端目录。没有 `whitelist.json` 时按空白名单处理。

Docker 例子：把宿主机 `/srv/minecraft` 以读写方式挂载到 AstrBot 容器的 `/minecraft`，插件填写 `/minecraft`。RCON 地址从容器内看必须可达；容器内的 `127.0.0.1` 指的是容器自身。

Linux 上 AstrBot 用户需有读写服务端目录和白名单文件的权限，Java 用户也需能读取替换后的白名单文件。插件会尽量保留原文件权限与所有者。

## 2. 开启 RCON 与白名单

每个 Java 服务端的 `server.properties` 设置：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=替换成你的密码
broadcast-rcon-to-ops=false
white-list=true
```

同机不同服分别使用 25575、25576、25577、25578、25579。编辑后先手动完整重启服务端一次，让 RCON 生效。RCON 使用明文连接，应限制在本机、可信内网或受保护的隧道内。

`rcon_password` 可以直接填写，或通过 `rcon_password_env` 引用启动 AstrBot 时传入的环境变量。如果填写了环境变量名，它优先，变量不存在会报密码未配置，不会回退到另一个密码。

PowerShell 在启动 AstrBot 的同一个环境中设置：

```powershell
$env:MC_RCON_1 = '你的 server1 RCON 密码'
$env:MC_RCON_2 = '你的 server2 RCON 密码'
```

设置 `online_mode=false` 时使用 Java 离线 UUID；设置为 `true` 时查询 Mojang 正版 UUID，必须能联网。同一群下各服需要使用同一模式。该设置是插件的 UUID 选择方式，不会修改服务器 `server.properties`。代理转发正版 UUID 的服务网络应按实际玩家 UUID 选择，而不能仅根据后端 `online-mode` 字段判断。

## 3. 安装与填写配置

安装 `astrbot_plugin_mc_manager-1.0.1.zip` 后，在 AstrBot 插件配置页逐项填写。完整示例是根目录 `config.example.json`，可复制到管理面板的配置编辑器后按实际值修改。

群配置示例：

```json
{
  "id": "group1",
  "platform_id": "你的消息平台实例ID",
  "group_id": "111111111",
  "server_ids": ["server1", "server2", "server3"],
  "admin_ids": ["123456789"],
  "max_entries_per_user": 1,
  "broadcast_origin": ""
}
```

`platform_id` 来自 AstrBot 的“消息平台”实例设置。它不是 `aiocqhttp`、`telegram` 等适配器名称。群号和用户 ID 用字符串，不能用显示名称。

保存 AstrBot 原生配置后重载插件；面板内保存配置会立即应用。首次接管会读取并合并原有白名单，所以开始前确认各服现有条目都是这个群应该保留的成员。

## 4. 配置自动重启脚本

你可以使用自己的脚本，重点是 Java 进程退出后再次启动，工作目录必须是对应服务端文件夹。

Windows `start.bat` 示例（放在每个服务端文件夹）：

```bat
@echo off
cd /d "%~dp0"
:loop
java -Xms2G -Xmx4G -jar server.jar nogui
echo Server stopped, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
```

Linux `start.sh` 示例：

```sh
#!/usr/bin/env bash
cd -- "$(dirname -- "$0")" || exit 1
while true; do
  java -Xms2G -Xmx4G -jar server.jar nogui
  sleep 5
done
```

这里是部署示例，插件本身不会执行 shell、修改脚本或运行 Java。若使用 systemd 等进程管理工具，需确认手动 `stop` 导致的正常退出也会触发重启，例如按需要使用 `Restart=always`。

群内重启广播依赖对应平台适配器支持主动发送。建议先按以下步骤实际验证广播；`context.send_message` 的返回值表示找到了平台，部分适配器内部的静默发送失败无法由该公开接口判定。

## 5. 实际联调顺序

1. 管理面板“刷新状态”确认每台服务端 RCON 在线。
2. 在两个群分别发送 `/mc`，确保各自出现帮助菜单。
3. 群 1 的测试成员添加 Steve，确认 server1～3 的文件一致，server4～5 没有改变。
4. 群 2 添加另一个测试名，确认只作用于 server4～5。
5. 用另一个普通成员尝试移除 Steve，应该拒绝；管理员能查看添加记录。
6. 修改 Steve 的名称，确认编号保持不变；移除后再添加，确认编号递增。
7. 分别测试 `/mc 玩家` 和 `/mc tps`。如果 TPS 命令不存在，为这台服填写其核心或插件实际支持的命令。
8. 用管理员 `/mc 重启 server1`，确认旧进程退出、脚本重新启动、群 1 收到成功广播，群 2 没有收到。
9. 暂停自动启动脚本再测一次，超过 `restart_timeout` 后群 1 应收到失败广播。
10. 暂时让一台服离线，添加/移除后检查“待同步”；恢复 RCON 后确认重试清除。

首次接管导入的测试玩家没有归属，请用管理员移除后由测试成员重新添加，以测试“只能删除自己的条目”。

## 6. 常见问题

| 现象 | 排查 |
|---|---|
| 提示本群未绑定 | 群号是否匹配；平台是否填写实例 ID；是否保存并重载 |
| 文件已变，在线服仍未生效 | 查看待同步原因，核对 RCON，执行 `/mc 同步` |
| RCON 认证失败 | 密码、环境变量、服务端重启后配置是否生效 |
| 某服一直提示目录不存在 | 容器挂载路径、相对目录、读写权限 |
| 白名单损坏 | 先备份当前文件，从 `.mcwl-backups` 选择正确版本人工恢复再同步 |
| 游戏内未限制进入 | `white-list=true` 是否生效；代理或模组是否使用特殊白名单机制 |
| TPS 返回未知命令 | 这台服是否有 TPS 命令；配置 `tps_command` |
| 管理员不能重启 | AstrBot 全局管理员是否正确，或该群 `admin_ids` 是否包含用户 ID |
| 面板重启提示没有会话 | 在对应群发送一次 `/mc`；或填写实际的 `broadcast_origin` |
| 重启成功但没有广播 | 群会话是否有效；查看待发送广播数与 AstrBot 日志 |
| 面板入口未出现 | AstrBot 版本、插件启用状态、`pages/console/index.html` 是否随包安装 |
| 旧群 ID 改绑被拒绝 | 旧 ID 绑定着历史数据，新增群需使用新 ID；不要删除数据库规避归属保护 |

## 7. 备份与恢复

更新插件时保留插件数据目录。停用插件后再复制 `mc.sqlite3`，如 WAL/SHM 文件仍存在则一并复制；在仍有运行实例时，应使用 SQLite 的 backup API 取得一致快照。

服务端 `.mcwl-backups` 只是文件备份，不包含群成员归属、编号和审计日志；完整恢复需要插件数据库。数据库丢失后重新初始导入只能恢复玩家条目，不能恢复原操作者归属与固定编号。
