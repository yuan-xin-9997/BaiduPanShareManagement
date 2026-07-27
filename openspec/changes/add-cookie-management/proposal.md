## Why

系统目前只维护一份全局百度网盘 Cookie，无法在多个账号凭据之间切换，也无法让不同分享链接使用各自适用的 Cookie；当 Cookie 失效时，管理员也缺少直观的状态与时间信息来定位问题。增加多 Cookie 管理及分享链接关联能力，可以提升多账号场景下的可用性，并降低凭据失效后的排查成本。

## What Changes

- 在系统配置中支持添加、编辑、删除和查看多条百度网盘 Cookie，并为每条 Cookie 设置便于识别的名称。
- 保存 Cookie 的添加时间、最近校验时间与当前有效状态，敏感 Cookie 内容仅以脱敏形式展示。
- 支持主动校验 Cookie，并在实际访问百度网盘失败时更新相应 Cookie 的有效状态。
- 分享链接可关联一条 Cookie；新增或编辑分享链接时可选择 Cookie，后续刷新、浏览和同步使用该关联 Cookie。
- 为已有单 Cookie 配置和现有分享链接提供兼容迁移，避免升级后丢失凭据或中断现有任务。
- 更新需求规格说明书、设计说明书、README 和部署流水线相关校验。

## Capabilities

### New Capabilities

- `cookie-management`: 管理多条百度网盘 Cookie、展示其元数据与有效状态，并将分享链接关联到指定 Cookie。

### Modified Capabilities

无。

## Impact

- 后端 SQLite 数据模型、幂等迁移、Cookie 安全存储、百度网盘客户端创建方式及分享链接/任务相关 API。
- Vue 系统配置页和分享链接新增、编辑、列表交互。
- 现有 `data/secrets.json` 单 Cookie 数据需要安全迁移；API 响应与日志不得泄露 Cookie 原文。
- 单元测试、API 冒烟测试、前端构建，以及 README、需求/设计文档和 Jenkins 流水线验证。
