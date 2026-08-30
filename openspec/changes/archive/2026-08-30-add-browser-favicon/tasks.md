## Tasks

- [x] 1.1 新增 `frontend/public/favicon.svg` 与 `favicon.ico`（16/32 像素内嵌 PNG）。
- [x] 1.2 `index.html` 增加 favicon `<link>` 引用。
- [x] 1.3 `web.py` 新增 `/favicon.ico`、`/favicon.svg` 路由。
- [x] 1.4 新增测试 `test_favicon_served_for_browser_tab`，全量测试通过。
- [x] 1.5 重新构建前端，web_static 产物包含图标与引用。
- [x] 1.6 更新 README、需求规格说明书、设计说明书。
- [x] 1.7 提交代码并推送到 GitHub main 分支（提交 769848a）。
- [x] 1.8 部署验证：Jenkins 30 分钟轮询触发器自动构建并部署成功；手工 API 触发因
      会话中无 Jenkins 凭据未执行。线上 `/favicon.ico`、`/favicon.svg` 均返回 200，
      页面含 favicon 引用。
