# spark 本地采集器（可选）

[返回项目说明](../README.md) · [TPS 变量表](../docs/TPS_VARIABLES.md) · [部署指南](../docs/DEPLOY.md)

`astrbot-spark-agent.jar` 从运行 Minecraft 的 JVM 内读取已启用 spark 的公开 API，每 2 秒将 TPS、Tick 耗时和 CPU 报告原子写入工作目录的 `.astrbot-spark-tps.txt`。服务端能通过 RCON 完整返回 `spark tps` 时，可直接使用机器人插件，无需采集器。

该采集器仅用于 `tps_mode: "spark"`。选择原生 TPS 模式时，插件只执行 RCON 查询，不读取本地报告。

## 环境与文件位置

采集器依赖服务端存在可用的 `me.lucko.spark.api.SparkProvider`。它不会自行安装 spark，也不从机器人进程或其他服务器采集指标。

将 [astrbot-spark-agent.jar](astrbot-spark-agent.jar) 放在每台需要采集的 MC 服务端目录。Java 启动时先切换到该服目录，再给该 JVM 添加 `-javaagent`。也可以多服共用一个只读 JAR，但各服工作目录必须独立。

## Windows 普通 JAR 启动

以下文件放在 MC 服务端目录，保留实际 Java 路径、内存和服务端文件名：

```bat
@echo off
cd /d "%~dp0"
java "-javaagent:%~dp0astrbot-spark-agent.jar" -Xms2G -Xmx4G -jar server.jar nogui
```

如需在 `stop` 后自动重启，可使用 [start-with-spark-agent.example.bat](start-with-spark-agent.example.bat)，先修改其中的 `MC_JAVA`、`MC_JAR`、`MC_XMS`、`MC_XMX`。

## Linux 普通 JAR 启动

```sh
#!/usr/bin/env bash
cd -- "$(dirname -- "$0")" || exit 1
java "-javaagent:$(pwd)/astrbot-spark-agent.jar" -Xms2G -Xmx4G -jar server.jar nogui
```

也可使用独立的 [Linux 启动模板](start-with-spark-agent.example.sh)，通过环境变量修改 MC_JAVA、MC_JAR、MC_XMS、MC_XMX 和 MC_RESTART_DELAY。在服务端目录运行 bash start-with-spark-agent.example.sh 即可。

已有自动重启循环时，把参数加入循环内的实际 Java 启动命令，不要另起一个单独的采集 JVM。

## 参数文件与其他启动方式

如果启动命令包含 `@user_jvm_args.txt`，可在该文件中增加一行：

```text
-javaagent:astrbot-spark-agent.jar
```

相对路径以 Java 工作目录为准。或者将带正确路径的 `-javaagent` 放在原 Java 命令的 `-jar` / 主类之前。Forge、NeoForge 等核心应保留其原有参数文件、类路径与引导方式；不要直接把启动命令替换成上述普通 JAR 示例。

修改后完整重启 MC 服务端，热重载机器人插件不会给已运行的 JVM 加载 agent。

## 插件设置与成功标志

在对应 `servers` 条目配置：

```json
{
  "tps_source": "file",
  "tps_command": "spark tps",
  "spark_file": ".astrbot-spark-tps.txt"
}
```

`file` 只读取报告；默认 `auto` 优先使用新鲜且可解析的报告，否则回退 RCON；`rcon` 忽略本地报告。根配置 `spark_file_max_age` 默认 10 秒，超过后不作为当前数据使用。

成功后 MC 控制台会出现：

```text
[AstrBotSparkAgent] spark API ready; metrics are sampled every 2 seconds
```

确认 `.astrbot-spark-tps.txt` 每 2 秒更新，且 AstrBot 能读取。Docker 部署时，报告文件需要位于挂载给 AstrBot 的服务端目录内。指标暂不可用时会记录诊断信息，不会伪造 TPS。

## 自定义报告位置

参数等号后可指定文件路径：

```text
-javaagent:D:/Minecraft/astrbot-spark-agent.jar=logs/astrbot-spark-tps.txt
```

报告父目录需提前存在，再将服务器 `spark_file` 改为 `logs/astrbot-spark-tps.txt`。插件只允许读取本服目录内的相对路径，不能通过该字段跨服读取。

## 兼容范围与排查

JAR 编译目标为 Java 8 字节码。已在真实 Java 8 JVM 和与 spark API 形状一致的测试类上验证 agent 加载、反射读取、后台线程生命周期、原子写入与全部 19 个样例值。字节码版本并不保证所有 Java、MC 核心、spark 或模组组合都兼容；这些实际组合仍需部署验证。

| 现象 | 检查事项 |
| --- | --- |
| 找不到或无法打开 JAR | `-javaagent` 路径、空格引用与读取权限 |
| 报告未生成 | 参数是否传给 MC JVM、服务端工作目录、spark 是否启用及 API 是否可用 |
| 报告过期 | MC 是否仍运行、控制台是否报采集错误、机器人配置的路径是否正确 |
| 报告存在但查询失败 | AstrBot 文件权限、Docker 挂载、报告内容和有效期 |

停用时移除 Java 命令或参数文件中的 `-javaagent`，完整重启 MC，并按需把 `tps_source` 改为 `rcon`。

## 源码与编译

源码为 [SparkAgent.java](src/astrbot/spark/SparkAgent.java)。在仓库根目录执行：

```sh
python tools/build_spark_agent.py
```

优先使用 PATH 中 JDK 的 `javac`，输出覆盖 `companion/astrbot-spark-agent.jar`；也支持开发环境已有的 ECJ 编译器，见[开发指南](../docs/DEVELOPMENT.md)。JAR 内包含本项目 MIT 许可证；spark 本身遵循其自身许可证。
