# spark 本地采集器（可选）

`astrbot-spark-agent.jar` 是一个小型 Java agent，从服务端 JVM 内已安装的 spark 的公开 API 读取 TPS、Tick 耗时及 CPU 数据，每 2 秒把报告原子写入服务端当前工作目录的 `.astrbot-spark-tps.txt`。采集通过本地文件传递，依赖 Java 标准库和已安装 spark 提供的公开 API。

如果你的服务端能通过 RCON 完整返回 `spark tps`，直接使用机器人插件即可。若 RCON 返回空文本，可以使用这个采集器；它依赖 spark 已安装并提供 `me.lucko.spark.api.SparkProvider` API。

## 使用

把 `astrbot-spark-agent.jar` 放在 MC 服务端共同父目录，例如 `D:/Minecraft/astrbot-spark-agent.jar`。每台服务端的启动脚本需要先切换到各自服务端目录，再添加 `-javaagent` 参数：

```bat
cd /d "%~dp0"
java -javaagent:D:/Minecraft/astrbot-spark-agent.jar -Xms2G -Xmx4G -jar server.jar nogui
```

Linux 示例：

```sh
cd -- "$(dirname -- "$0")" || exit 1
java -javaagent:/srv/minecraft/astrbot-spark-agent.jar -Xms2G -Xmx4G -jar server.jar nogui
```

如果已有自动重启循环，只需给循环内的 Java 启动命令追加上述参数。采集器线程是 daemon，不会在服务端退出后阻止进程结束。

机器人配置默认是：

```json
{
  "tps_source": "auto",
  "tps_command": "spark tps",
  "spark_file": ".astrbot-spark-tps.txt"
}
```

这些字段填写在对应的 `servers` 条目中。`auto` 优先使用新鲜且可解析的本地报告，否则尝试 RCON。也可以用 `file` 只读取本地数据，或用 `rcon` 只读取命令回复。根配置 `spark_file_max_age` 默认 10 秒；超过这个时间的数据不会被当作当前指标。

启动成功后可以看到：

```text
[AstrBotSparkAgent] spark API ready; metrics are sampled every 2 seconds
```

每台服的工作目录中会出现 `.astrbot-spark-tps.txt`。机器人需要有该文件的读取权限；容器部署时该文件也需要位于机器人可读取的服务端挂载目录内。

## 自定义文件位置

Java agent 参数可以直接指定文件路径：

```text
-javaagent:D:/Minecraft/astrbot-spark-agent.jar=logs/astrbot-spark-tps.txt
```

文件的父目录需提前存在，同时把该服 `spark_file` 改成 `logs/astrbot-spark-tps.txt`。机器人拒绝读取跨出该服目录的路径。

## 格式和验证范围

采集器按 spark 的相同时间窗口读取公开统计值；TPS 输出 2 位小数、Tick 耗时 1 位小数、CPU 转换为百分比。不可用数值输出 N/A，机器人显示配置的缺失文字，不会补造 0 或 20。

JAR 编译为 Java 8 字节码，可被 Java 8 及更新 JVM 加载。本工作区使用真实 Java 8 JVM和与 spark 公开 API 形状一致的测试类验证了 agent 加载、跨接口反射、daemon 生命周期、原子写入和全部 19 个样例值。没有在你的真实 MC 核心、spark 实现、模组组合或启动脚本上运行；部署后需确认上述 ready 日志与文件更新。

如要停用采集器，移除启动命令的 `-javaagent` 参数后重新启动服务端，并按需要将 `tps_source` 改为 `rcon`。

## 重编译

源码在 `src/astrbot/spark/SparkAgent.java`。从插件源码工作区执行 `python tools/build_spark_agent.py`，需要 JDK 的 javac，或已有 `.research/agent/ecj-3.26.0.jar` 开发编译器。用户安装时可以直接使用已编译的 JAR。
