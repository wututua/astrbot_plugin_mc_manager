# 版本记录

## v1.0.1

- 修复 metadata.yaml 缺少 AstrBot 必填字段 desc，导致 ZIP 上传后加载失败的问题。
- 打包前验证 name、desc、version、author 都是非空字符串，short_desc 不替代 desc。
- 检查元数据、pyproject.toml 和核心版本号一致，并自动生成对应版本的 ZIP 文件名。
- 添加 6 项安装包回归测试，包含实际 AstrBot 发布包的元数据检查和解压流程验证。

## v1.0.0

- 首次实现群服白名单管理、RCON 查询、重启监控、审计日志和管理页面。
- 此版 ZIP 缺少 desc 字段，请使用 v1.0.1 安装包。
