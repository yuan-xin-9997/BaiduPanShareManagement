# AGENTS.md 合规审计

审计日期：2026-07-14

参照项目根目录 `AGENTS.md` 和 GitHub
`yuan-xin-9997/PersonalInformationMarkdownFile` main 分支最新说明。

## 已完成

- Python + FastAPI 后端、Vue 3 + Vite 前端、SQLite 数据库。
- 代码迁移到 `src/app`，建立 `config`、`data`、`logs`、`JenkinsConfig`。
- 用户由 `data/password.txt` 维护并自动同步为 scrypt 密码哈希。
- 支持管理员、普通用户和服务端页面级授权。
- 已实现分享链接、同步映射、任务中心、系统配置、权限管理页面。
- 非敏感配置集中在 `config/app.json`；Cookie 和会话密钥单独保存且不提交 Git。
- 页面与日志使用北京时间配置。
- 应用日志按天轮转。
- `.gitignore` 明确忽略 `logs/`，但不忽略整个 `data/` 目录。
- 提供 Windows/Linux 启动、停止和状态脚本。
- 提供需求规格说明书、设计说明书、README 和 Jenkinsfile。
- PyQt6 桌面端、依赖和文档引用已删除。
- Python 测试、Vue 生产构建和真实服务登录冒烟测试通过。

## 交付前仍需执行

以下事项依赖外部 GitHub/Jenkins 状态，不属于本地代码重构：

1. 将当前全部未跟踪文件纳入 Git 并提交到 GitHub。
2. 在 Jenkins 中创建 Pipeline 任务，使用 SSH 仓库地址和
   `JenkinsConfig/Jenkinsfile`。
3. 手动触发首次构建，验证 `/opt/BaiduPanShareManagement` 部署目录。
4. 为服务配置最终内网端口和 HTTPS 反向代理域名。
5. 首次登录后修改 `admin / admin123` 默认密码。
