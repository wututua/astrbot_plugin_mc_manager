# 开发、验证与发布

[项目说明](../README.md) · [验证范围](VALIDATION.md) · [接口兼容](ASTRBOT_COMPATIBILITY.md)

## 仓库结构

```text
astrbot_plugin_mc_manager/
├── main.py                   # AstrBot 事件、网页 API、配置与生命周期
├── metadata.yaml             # 插件名称、版本、框架要求与仓库地址
├── CHANGELOG.md              # AstrBot 直接读取的更新日志
├── _conf_schema.json         # AstrBot 配置表单
├── mc_manager/               # 白名单、持久化、RCON、spark 与业务逻辑
├── pages/console/            # 网页源码，构建包额外生成同内容的 views/
├── docs/                     # 文档和页面截图
├── tests/                    # Python、真实 TCP 与框架隔离测试
├── tools/                    # 安装包与浏览器验证工具
├── logo.png                  # AstrBot 自动识别的插件图标
└── LICENSE                   # MIT 许可证
```

`.research/`、`dist/`、`output/`、缓存、虚拟环境和运行数据库不提交。`.research/` 用于可选的框架源码、测试依赖和本地验证产物，不是插件运行依赖。不要将真实密码、生产配置或数据库提交到仓库。

## Python 开发环境

在仓库根目录执行，Python 需要 3.12+：

```sh
python -m venv .venv
```

激活环境后安装开发依赖：

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m ruff check main.py mc_manager tests tools
python -m compileall -q main.py mc_manager tests tools
```

PowerShell 激活命令为 `.venv/Scripts/Activate.ps1`；Linux/macOS 为 `. .venv/bin/activate`。也可以直接使用虚拟环境里的 Python，不必修改系统 Python。

普通测试使用临时目录与本机测试 TCP 服务，不会连接真实 MC 服务端或执行生产停服。框架源码或开发依赖条件不足时，相应测试会明确跳过，不能把有跳过的结果称为完整验证。

## 固定 AstrBot 版本验证

框架测试隔离调用官方发布源码，不启动完整 AstrBot。可以在另一临时目录用 `pip download --no-deps astrbot==4.28.0` 获取 wheel 并解压，或使用对应正式标签的仓库源码。将 `MC_TEST_ASTRBOT_RELEASE` 指向包含 `api/`、`core/`、`dashboard/` 的 **astrbot 包目录**，不是其父目录。

PowerShell 示例：

```powershell
$env:MC_TEST_ASTRBOT_RELEASE = 'D:/test-deps/astrbot-4.28.0/astrbot'
python -m unittest discover -s tests -v
```

Linux 示例：

```sh
MC_TEST_ASTRBOT_RELEASE=/tmp/astrbot-4.28.0/astrbot python -m unittest discover -s tests -v
```

不设置变量时优先寻找 `.research/release/astrbot`；部分 Web 测试也会检查已安装的 AstrBot。`requirements-dev.txt` 提供 Web 测试所需的 FastAPI 等依赖。正式支持范围以 `metadata.yaml` 为准，验证矩阵见 [VALIDATION.md](VALIDATION.md)。

## 网页验证

需要 Node.js 和 Playwright，Python 插件运行不依赖 Node.js。默认将浏览器测试依赖放到忽略目录：

```sh
npm install --prefix .research/browser playwright
npx --prefix .research/browser playwright install chromium
node --check pages/console/app.js
node --check pages/console/settings.js
node tools/test_ui.cjs
```

默认使用 Playwright 管理的 Chromium。可以通过 `MC_TEST_CHROMIUM` 指定浏览器可执行文件，通过 `MC_TEST_PLAYWRIGHT_MODULE` 指定已有 Playwright 模块路径。

设置 `MC_TEST_ASTRBOT_RELEASE` 后，浏览器测试会加载对应正式包的 `dashboard/plugin_page_bridge.js`；否则使用测试 bridge。后端和父页面交互使用模拟响应，不是完整 AstrBot 服务。测试覆盖 320 / 390 / 768 / 1024 / 1440 像素宽度下的四个功能页与六个配置分类，并验证 Logo 加载和蓝色主题。脚本会更新 `docs/screenshots/` 的桌面/手机截图；提交前检查截图变化。

## 构建安装包

在仓库根目录执行：

```sh
python tools/build_release.py
```

输出为 `dist/astrbot_plugin_mc_manager-<版本号>.zip`。构建会验证元数据、`pyproject.toml` 和 `mc_manager.__version__` 一致，包含根目录 CHANGELOG.md、LICENSE、图标、网页、配置与文档；从一套网页源码生成相同的 `pages/` 与 `views/`，并检查页面本地 `logo.png` 与根目录 Logo 字节一致，不包含测试、调研依赖、数据库或缓存。

当前不包含 Java agent、JAR 或 JVM 构建任务。直接从 GitHub 仓库安装时，AstrBot 使用源码中的 `pages/`；4.29 的页面发现同样保留该回退目录。

## 自动检查

.github/workflows/ci.yml 配置 Windows/Linux 与 AstrBot 4.28.0/4.29.0 回归、两套官方 SDK 浏览器检查，共 6 个矩阵任务，无需安装 JDK。工作流在 main 推送、Pull Request 和手动运行时触发，只有仓库读取权限；同一分支的新运行会取消旧运行。每个任务设置超时，网页失败时保留 7 天截图。运行结果可通过 README 的 CI 徽章或仓库 Actions 页面查看；手动执行选择 CI → Run workflow。

tools/fetch_astrbot.py 下载固定官方 wheel 并提取接口源码；tools/validate_docs.py 检查相对链接、JSON 和凭据模式。运维测试使用临时目录、模拟 RCON、真实 SQLite 和官方 Web API，恢复测试会注入失败验证回滚。

## 提交与发布

1. 更新 `metadata.yaml`、`pyproject.toml`、`mc_manager/__init__.py` 中的版本，并同步安装包测试的版本预期。
2. 在根目录 [CHANGELOG.md](../CHANGELOG.md) 顶部填写本版本的具体更新、修复和必要的升级说明，并同步 README 和验证记录。AstrBot 的“更新日志”读取插件根目录文件，不能只更新 `docs/` 或 GitHub Release 正文。构建会检查最新日志版本与元数据一致。
3. 运行适当的核心、框架、页面和安装包检查。
4. 使用本仓库地址配置 AstrBot 的仓库安装或更新。
5. 只有在用户明确要求发布时才创建版本标签和 GitHub Release。Release 标题仅填写版本号，例如 `v1.5.0`；正文填写对应版本的更新内容、升级方法与验证结果。
6. 发布附件为插件 ZIP 和 `SHA256SUMS.txt`；不单独上传 LICENSE 或恢复清单。安装包包含根目录 `CHANGELOG.md` 与许可证。
7. 发布后核对 Release 正文、附件校验值及根目录日志；使用 AstrBot 正式版本的日志读取方法检查安装后的“更新日志”返回本次内容。已有旧安装包的用户需要更新或重新导入后才能读取新增文件。

仓库地址：`https://github.com/wututua/astrbot_plugin_mc_manager`。许可证为仓库根目录的 [MIT License](../LICENSE)；代码和文档一起提交，第三方依赖保持其自身许可证。

## 消息互通测试

`tests/test_bridge.py` 覆盖日志样例、轮转/截断/半行、多字节边界、路径约束、颜色到 JSON 的转换、RCON 包长度、消息作用域、各级开关、冷却/去重、队列限制及任务生命周期；`tests/test_plugin.py` 使用两套官方 SDK 的真实过滤器与 Web API 验证群监听和配置保存。浏览器验证消息互通表单、模板、开关保存和手机布局。网络目标使用假 RCON 和假群发送器，不会向真实群或 MC 服务端发送测试消息。

## 日志规范与审核说明

运行时代码统一使用 AstrBot `Star` 自带的 `self.logger`。入口 `main.py` 创建业务服务时，通过必填的 `logger` 关键字参数传递此实例；普通初始化、网页保存配置、备份恢复与失败回滚共用这一约定。`mc_manager/operations.py` 的后台异常和 `mc_manager/bridge.py` 的消息投递调试信息都调用该实例，不导入标准库日志模块，不自行创建 logger、处理器或日志文件。

`tests/logger_stub.py` 提供只记录内存调用的 `unittest.mock.Mock`。独立服务测试、命令运行器、插件适配器、官方安装器及接口契约测试均使用该替身，不输出日志或创建日志文件。接口契约测试中的 `"logging"` 命名空间是提供给原样编译的 AstrBot 官方 Star 方法的纯测试替身，不是导入 Python 标准库日志模块，也不是插件运行逻辑。

`tests/test_logging_policy.py` 用 AST 检查插件、测试及工具源码中没有标准库日志导入。行为回归同时检查后台任务异常调用插件 logger、消息投递失败记录、服务重建继承 Star logger，以及安装包排除 `tests/`、`tools/`、`.research/`。

可在重新送审时说明：

> 已将运行时后台任务和消息互通的日志统一改为 AstrBot Star 提供的 self.logger；所有服务创建路径显式传入该实例。运行时代码与开发测试均已移除标准库日志导入和自建 logger。开发测试使用仅记录内存调用的日志替身，tests、tools 与调研目录不包含在插件安装包中。

## 查询与变量回归

`tests/test_monitoring.py` 使用异步 RCON 替身与查询锁验证超时、去重、来源、缓存、玩家解析与模板数据隔离。`tps.variables.json` 与 `spark.variables_table()` 必须一致，指标仍为 19 项，新增查询/状态变量按 scopes 标记。浏览器验证变量筛选、插入、预览的文本安全和模板直接保存；预览不查询真实服务器。

## 无采集器回归

`tests/test_no_collector.py` 检查旧文件采集配置兼容、RCON 不读取遗留报告、空回复明确提示、旧模板占位符兼容、RCON 诊断/告警以及源码和 CI 的组件清理。官方 Web API 测试覆盖保存和恢复旧配置；安装包测试拒绝包含 Java 采集器或 Java 文件。无 Java/JDK 环境也可运行全部 Python 检查。

## 配置存储故障回归

`tests/test_config_storage.py` 使用两套官方 SDK 的原始 snapshot/write 方法，在 fsync 阶段注入 ENOSPC，验证旧文件字节与实例保留、临时文件清理、仅一次保存调用，以及后续重试能够提交。另覆盖 Windows 39/112、配额和 SQLite FULL、必要回滚、回滚失败的状态提示、初始化后容量错误及取消传播。模拟不会填满真实磁盘，也不清理主机文件。

## spark 异步控制台回归

`tests/test_spark_log.py` 使用截图中的 Forge spark-worker 格式验证新日志定位、星号/颜色/编码、线程隔离、重复报告、日志变更、范围和字节上限、取消、并发合并及模板来源。另启本机真实 TCP RCON 测试端，先回空包，再异步追加临时日志，验证完整查询；不连接生产 Minecraft。官方网页 API 测试验证选项保存和带变量的查询输出。

## 跨核心变量与初始模板

`mc_manager/performance.py` 解析已标注数据；`variables_table()` 给出 85 个变量的 scopes/formats，`template_examples()` 给出 6 套初始文本与一致的预览值，网页接口与 tps.variables.json 同步。tests/test_performance.py 验证各核心数值、零值、缺失、单位、维度范围、原版目标速率区分和可编辑模板；官方 Web API 验证原生 Forge 数值的保存/查询，浏览器验证按核心筛选和示例不跨格式补值。

## 维护消息模板

插件自有错误和内部片段定义在 `mc_manager/message_catalog.py`；顶层模板与分类/字段元数据位于 `mc_manager/config.py`。运行 `python tools/sync_message_docs.py` 同步默认配置、原生配置 schema、`messages.variables.json` 与 `docs/MESSAGES.md`。修改时保留公开键名，新增模板使用缺省合并以保留用户文案。

`MessageText`、`QueryResult` 和 `MCError` 保留内部默认值，输出时才解析自定义文字，避免根据显示文案判断权限、在线状态或告警条件。新增回复请使用结构化模板，不要按中文原文全局替换。测试会检查模板目录一致性及运行时异常是否使用显式键。

## 连接测试与操作简化

operations POST 的 test_connection 仅向选中的 RCON 发送固定 list，支持未保存服务器；后台身份认证和生命周期租约与其他操作一致。__KEEP__ 仅能解析已存在 ID 的密码，响应不返回凭据，不修改配置、编号、白名单或数据库。可读的连接异常转换为显式消息模板。

群内省略条目参数只推断当前组内、当前认证用户唯一的一条归属记录；后续仍运行原有权限检查、审核分支和带 owner 条件的 SQL。多条需明确选择，单服重启以外不隐式扩大服务器范围。

## 自动初始化

`mc_manager/onboarding.py` 负责有界目录扫描、Java Properties 常见格式解析、敏感字段不参与 repr/公共响应、限额/过期扫描结果，以及配置候选合并。operations 的 discover_servers 只读文件；config POST 的 quick_setup 仅接受扫描 token、所选目录键、机器人/群号和可选新服地址。保存前重新读取指纹，并绑定后台账号和配置修订版本。

候选配置仍进入原有 Settings 校验、框架原子保存、服务切换和回滚路径，没有另建写盘流程。已配置服务器参数保持不变；现有群必须使用完整群号列表才能扩展服务器，多个独立配置组不自动合并。新服开启只读 TPS 候选探测，未隐式赋予群内用户管理权限。
