# Add Browser Favicon

## Why

系统在浏览器标签页中没有图标，标签页显示默认的空白图标，多标签使用时不易辨识。
需要为系统添加专属的浏览器标签页图标（favicon），并与现有品牌风格保持一致。

## What Changes

- 新增 `src/app/frontend/public/` 目录，存放 `favicon.svg`（现代浏览器矢量图标）
  与 `favicon.ico`（内嵌 16/32 像素位图的兼容回退）。
- `index.html` 通过 `<link rel="icon">` 同时引用两者，现代浏览器优先使用 SVG。
- 后端 `web.py` 新增 `/favicon.ico`、`/favicon.svg` 路由，直接从 `web_static`
  提供图标文件，无需登录即可访问。
- 更新 README、需求规格说明书、设计说明书中的相关描述。
- 新增测试 `test_favicon_served_for_browser_tab` 覆盖图标路由与页面引用。

## Capabilities

### Modified Capabilities

- `web-ui`: 浏览器标签页显示系统专属图标。

## Impact

- `src/app/frontend/public/`（新增）
- `src/app/frontend/index.html`
- `src/app/bdpan/web.py`
- `src/app/bdpan/web_static/`（构建产物）
- `tests/test_web.py`
- `README.md`、`docs/需求规格说明书.md`、`docs/设计说明书.md`
