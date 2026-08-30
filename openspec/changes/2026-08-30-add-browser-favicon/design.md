## Context

前端由 Vite 构建，构建产物输出到 `src/app/bdpan/web_static` 并随仓库提交。此前
`index.html` 没有 favicon 引用，浏览器请求 `/favicon.ico` 会落到无匹配路由。
后端目前仅挂载 `/assets` 静态目录，`/` 返回 index.html。

## Decisions

### 1. 图标设计

采用与登录页品牌标识一致的视觉：蓝色渐变（`#6b8af2 → #3f5bd4`）圆角方块为底，
白色云朵承载蓝色上传箭头，呼应"百度网盘分享/同步"的核心语义。SVG 源文件手工
绘制；ICO 内嵌 16/32 像素 PNG 位图（PNG-in-ICO 格式，现代浏览器均支持），
保证小尺寸下清晰可辨。

### 2. 引用方式

`index.html` 中按推荐顺序声明：

```html
<link rel="icon" href="/favicon.ico" sizes="32x32" />
<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
```

旧浏览器取 ICO 回退，现代浏览器命中 SVG 并可随主题缩放。

### 3. 提供方式

图标文件放在 `frontend/public/`，Vite 构建时原样拷贝到 `web_static` 根目录。
后端新增两条显式路由 `/favicon.ico`、`/favicon.svg` 返回对应文件，不引入
根目录静态挂载，避免改变现有 API 404 行为。浏览器自动探测 `/favicon.ico`
时无需登录即可命中，属于静态资源，不暴露敏感信息。

## Risks / Trade-offs

- [ICO 手工打包] → 采用 PNG-in-ICO 标准格式并经 sips 渲染验证，兼容性风险低。
- [构建产物随仓库提交] → 与现有 web_static 管理方式一致，不引入额外流程。
