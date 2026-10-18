"""Synchronize the public message catalog, defaults, schema and variable reference."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mc_manager.config import ALIASES, MESSAGE_CATEGORIES, MESSAGES, message_templates  # noqa: E402
from mc_manager.message_catalog import EXTRA_MESSAGES  # noqa: E402


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def synchronize():
    catalog = message_templates()
    schema_path = ROOT / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    items = schema["messages"]["items"]
    for row in catalog:
        key, default = row["key"], row["default"]
        fields = "；".join("{" + item["name"] + "} " + item["description"] for item in row["variables"])
        spec = items.setdefault(
            key, {"description": row["category_label"] + " · " + default.replace("\n", " ")[:48], "type": "text"}
        )
        spec["default"] = default
        spec["hint"] = (
            "本模板变量：" + fields if fields else "纯文本，无必需变量。"
        ) + " 也支持主/子指令变量；花括号写为 {{ 或 }}。留空可隐藏此段；完整回复留空则不发送。"
    dump(schema_path, schema)
    for name in ("config.default.json", "config.example.json"):
        path = ROOT / name
        config = json.loads(path.read_text(encoding="utf-8"))
        if name == "config.default.json":
            config["messages"] = dict(MESSAGES)
        else:
            for key, value in MESSAGES.items():
                if key in EXTRA_MESSAGES:
                    config.setdefault("messages", {})[key] = value
                else:
                    config.setdefault("messages", {}).setdefault(key, value)
        dump(path, config)
    dump(ROOT / "messages.variables.json", {"templates": catalog})

    def cell(value):
        return value.replace("|", "\\|").replace("\n", "<br>")

    doc = [
        "# 全部机器人消息自定义",
        "",
        "在管理网页的 **设置 → 回复与广播** 编辑，支持按分类和文字、模板名、变量搜索。点击变量插入光标处，修改后点击 **保存并生效**。每项都可单独恢复默认。帮助、TPS 和消息互通页的专用编辑器与这里同步显示当前输入。也可直接修改配置中的 `messages` 对象。",
        "",
        "配置缺少新模板时会自动补用默认文字，已有自定义内容会保留。完整默认配置见 [config.default.json](../config.default.json)，机器可读目录见 [messages.variables.json](../messages.variables.json)。",
        "",
        "## 模板与变量规则",
        "",
        "- `{变量名}` 插入本次操作数据。下面的表列出每个模板实际使用的动态值；命令别名变量对所有模板可用。TPS 的完整数值变量另见 [TPS_VARIABLES.md](TPS_VARIABLES.md)。",
        "- `{prefix}` 自带 `/`；子指令变量 `"
        + "`、`".join("{" + key + "}" for key in ALIASES)
        + "` 使用配置的第一个别名。",
        "- `{{`、`}}` 输出普通花括号。变量只替换一次，玩家输入和服务端原文中的花括号不会再次展开。不支持对象属性、下标、格式表达式或执行代码。",
        "- 只想改外层回复时修改 `added`、`status`、`tps` 等；要改内部明细，再修改 `sync_*`、`perf_*`、`query_*`、`error_*` 等子模板。模板不能按名字引用其他模板，使用表中给出的结果变量即可。",
        "- 留空表示该模板输出空文字。外层回复为空时不发送；内部片段为空时隐藏该段（外层模板自己写的分隔符/换行仍然保留）。重启任务和审核等操作仍会正常执行。待发送通知为空时直接确认消费，不会反复重试。",
        "- 原文模式也有 `raw_tps` 外层模板，默认 `{result}`，可加标题或只保留部分文字；服务端返回的原文没有结构化的逐句替换。系统异常详情、历史日志记录、玩家聊天和死亡原文属于数据，可在外层模板中保留或省略对应变量。",
        "- `error_unknown` 控制未归类的系统异常详情（默认 `{error}`）；`internal_error` 控制意外内部异常的友好提示。配置待修正时仍尽量保留有效的消息模板。",
        "- `game_restart_countdown` 和 `game_restart_success` 控制游戏广播。`bridge_to_game` 可使用 `§` 染色码；通过固定 JSON tellraw 发送，模板不会变成 RCON 指令。",
        "- 修改文案不会改变权限、条目归属、告警判断、RCON 命令或重启成功条件。AstrBot 自身的系统提示和日志不属于本插件消息模板。",
        "- 已写入通知队列的文字在入队时确定；修改模板影响之后产生的通知。日志详情在操作发生时记录，修改 `audit_*` 影响新日志，旧日志可用 `log_detail` 控制是否显示。",
        "",
        "## 修改示例",
        "",
        "```json",
        json.dumps(
            {
                "messages": {
                    "added": "✅ 已为 {player} 开通白名单（#{id}）\n{sync}",
                    "sync_success": "{server}：白名单已更新",
                    "error": "⚠️ {error}",
                    "error_entry_owner": "这条白名单不属于你，请联系管理员。",
                    "query_offline": "{error}",
                    "restart_success": "{server} 已恢复，耗时 {seconds} 秒。{detail}",
                    "perf_memory_line": "内存情况：{perf_memory}",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "这是部分配置示例，将这些键合并到已有 `messages` 中即可。",
    ]
    for category, label in MESSAGE_CATEGORIES.items():
        rows = [row for row in catalog if row["category"] == category]
        doc += ["", "## " + label, "", "| 配置键（messages 下） | 初始默认文字 | 变量说明 |", "| --- | --- | --- |"]
        for row in rows:
            variables = (
                "<br>".join("`{" + item["name"] + "}`：" + item["description"] for item in row["variables"])
                or "纯文本；可添加命令别名变量"
            )
            doc.append("| `" + row["key"] + "` | " + cell(row["default"]) + " | " + cell(variables) + " |")
    (ROOT / "docs/MESSAGES.md").write_text("\n".join(doc) + "\n", encoding="utf-8")
    print(f"Synchronized {len(catalog)} message templates.")


if __name__ == "__main__":
    synchronize()
