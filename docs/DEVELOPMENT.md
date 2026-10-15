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
├── companion/                # Java agent 源码、JAR 与启动模板
├── docs/                     # 文档和页面截图
├── tests/                    # Python、真实 TCP、框架隔离与 JVM 测试
├── tools/                    # 安装包、agent 构建和浏览器验证工具
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

普通测试使用临时目录与本机测试 TCP 服务，不会连接真实 MC 服务端或执行生产停服。框架源码或 Java 条件不足时，相应测试会明确跳过，不能把有跳过的结果称为完整验证。

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

## Java agent 编译与集成测试

安装 JDK，确保 `java` 和 `javac` 位于 PATH：

```sh
python tools/build_spark_agent.py
python -m unittest discover -s tests -p test_spark_file.py -v
```

构建输出到 `companion/astrbot-spark-agent.jar`，目标为 Java 8 字节码，内含 `META-INF/LICENSE`。真实 JVM 测试会编译仓库中的测试 spark API，验证 agent 启动、指标读取与报告写入；它不能替代真实核心和 spark 的部署验证。

无 `javac` 时，构建与测试可以使用本地 `.research/agent/ecj-3.26.0.jar`，同时需要 `java`。该开发编译器不随本仓库提供。

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

输出为 `dist/astrbot_plugin_mc_manager-<版本号>.zip`。构建会验证元数据、`pyproject.toml` 和 `mc_manager.__version__` 一致，包含根目录 CHANGELOG.md、LICENSE、图标、网页、配置、文档及采集器；从一套网页源码生成相同的 `pages/` 与 `views/`，并检查页面本地 `logo.png` 与根目录 Logo 字节一致，不包含测试、调研依赖、数据库或缓存。

如变更 agent 源码，先重编译 JAR，再构建插件 ZIP。直接从 GitHub 仓库安装时，AstrBot 使用源码中的 `pages/`；4.29 的页面发现同样保留该回退目录。

## 提交与发布

1. 更新 `metadata.yaml`、`pyproject.toml`、`mc_manager/__init__.py` 中的版本，并同步安装包测试的版本预期。
2. 在根目录 [CHANGELOG.md](../CHANGELOG.md) 顶部填写本版本的具体更新、修复和必要的升级说明，并同步 README 和验证记录。AstrBot 的“更新日志”读取插件根目录文件，不能只更新 `docs/` 或 GitHub Release 正文。构建会检查最新日志版本与元数据一致。
3. 运行适当的核心、框架、agent、页面和安装包检查。
4. 使用本仓库地址配置 AstrBot 的仓库安装或更新。
5. 只有在用户明确要求发布时才创建版本标签和 GitHub Release。Release 标题仅填写版本号，例如 `v1.4.0`；正文填写对应版本的更新内容、升级方法与验证结果。
6. 发布附件为插件 ZIP、采集器 JAR、启动示例和 `SHA256SUMS.txt`；不单独上传 LICENSE 或恢复清单。安装包包含根目录 `CHANGELOG.md` 与许可证。
7. 发布后核对 Release 正文、附件校验值及根目录日志；使用 AstrBot 正式版本的日志读取方法检查安装后的“更新日志”返回本次内容。已有旧安装包的用户需要更新或重新导入后才能读取新增文件。

仓库地址：`https://github.com/wututua/astrbot_plugin_mc_manager`。许可证为仓库根目录的 [MIT License](../LICENSE)；代码和文档一起提交，第三方依赖保持其自身许可证。
