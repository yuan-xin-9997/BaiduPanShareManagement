# BaiduPanShareManagement

跨平台的百度网盘分享链接管理与同步 Web 服务。后端使用 Python、FastAPI 和 SQLite，
前端使用 Vue 3。系统支持多用户登录、页面级权限、目录浏览、后台同步、自动调度以及
Windows、Linux、macOS 挂载的 SMB NAS。

## 页面介绍

| 页面 | 功能 |
|---|---|
| 分享链接 | 添加、编辑、删除、刷新分享链接，逐级浏览远端目录 |
| 同步映射 | 关联远端目录与本机/SMB 目录，检测连接并执行同步 |
| 任务中心 | 查看后台任务实时状态和持久化同步历史 |
| 系统配置 | 查看脱敏运行配置，维护百度网盘 Cookie |
| 权限管理 | 管理用户密码、管理员/普通用户角色及可访问页面 |

## 目录结构

```text
src/app/bdpan/          Python 后端与构建后的前端资源
src/app/frontend/       Vue 3 + Vite 前端源码
config/app.json         系统主配置
data/app.sqlite3        SQLite 数据库（首次运行生成）
data/password.txt       登录用户来源
data/secrets.json       Cookie 与会话密钥（首次运行生成，不提交 Git）
logs/                   应用日志、进程输出和 PID
JenkinsConfig/          Jenkins 流水线
tests/                  自动化测试
```

## 安装

需要 Python 3.10 或更高版本。仅开发前端时需要 Node.js；运行构建后的服务不需要
Node.js。

```bash
conda create -n bdpan python=3.11 -y
conda activate bdpan
cd BaiduPanShareManagement
python -m pip install -e ".[web]"
```

开发或执行测试：

```bash
python -m pip install -e ".[web,dev]"
python -m pytest -q
```

修改 Vue 前端后重新构建：

```bash
cd src/app/frontend
npm install
npm run build
```

## 登录与权限

用户维护在 `data/password.txt`：

```text
username:password:role
```

`role` 只能是 `admin` 或 `user`。首次部署默认账号为：

```text
admin / admin123
```

首次登录后应立即在“权限管理”页面修改默认密码。管理员拥有全部页面权限；普通用户
可由管理员配置分享链接、同步映射、任务中心和系统配置页面权限。修改
`password.txt` 后，下次登录会自动同步到数据库。

## 配置文件

系统配置位于 `config/app.json`：

| 配置项 | 说明 |
|---|---|
| `host` / `port` | Web 监听地址与端口 |
| `timezone` | 页面和日志时区，默认 `Asia/Shanghai` |
| `database_path` | SQLite 数据库路径 |
| `password_file` | 用户文件路径 |
| `secret_file` | 百度 Cookie 和会话密钥文件 |
| `log_file` | 应用日志路径 |
| `log_retention_days` | 每日日志保留天数 |
| `task_workers` | 后台任务并发数 |
| `scheduler_poll_seconds` | 自动同步调度检查间隔 |

环境变量 `BDPAN_CONFIG` 可指向其他配置文件，`BDPAN_COOKIE` 可临时覆盖文件中的
百度网盘 Cookie。敏感信息不写入 `app.json`。

## 配置百度网盘 Cookie

登录百度网盘后，在浏览器开发者工具的 Network 请求头中复制完整 Cookie，并在
“系统配置”页面保存。Cookie 至少应包含有效的登录字段。Cookie 等同登录凭据，
不要提交到 Git 或发送给他人。

也可以使用 CLI：

```bash
bdpan config --cookie "你的百度网盘Cookie"
```

## 部署方式

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
powershell -ExecutionPolicy Bypass -File .\status.ps1
powershell -ExecutionPolicy Bypass -File .\stop.ps1
```

若 `python` 不在 PATH，可先设置 `$env:PYTHON` 为 Python 可执行文件绝对路径。

### Linux / macOS

```bash
chmod +x start.sh stop.sh status.sh
./start.sh
./status.sh
./stop.sh
```

生产环境建议将 `config/app.json` 的 `host` 设置为内网监听地址或 `0.0.0.0`，并在
HTTPS 反向代理后访问。只运行一个 Web 进程，否则自动调度可能重复执行。

### 直接启动

```bash
python run.py --config config/app.json
```

命令行参数 `--host`、`--port` 可以临时覆盖监听设置。

## 访问方式

服务默认监听 `0.0.0.0:29080`。本机访问 `http://127.0.0.1:29080`，局域网设备使用
`http://运行服务器地址:29080` 访问。公网使用时必须配置 HTTPS 和访问控制。

## 同步到 SMB NAS

应用不保存 NAS 账号密码。先由操作系统连接或挂载 SMB 3，再将映射的目标存储选择为
“NAS · SMB 挂载”。

- Windows：推荐 `\\NAS地址\共享名\目录` UNC 路径。
- Linux：先挂载到 `/mnt/nas` 等目录，再填写挂载点下的绝对路径。
- macOS：通过 Finder 连接 `smb://NAS地址/共享名`，再填写 `/Volumes/共享名/目录`。

每次同步前都会验证挂载和写权限。Linux/macOS 挂载失效时同步会停止，避免文件误写
到本机系统盘。

## 同步策略

| 策略 | 说明 |
|---|---|
| `copy_new` | 下载新增文件并替换大小不一致文件，不删除本地文件 |
| `mirror` | 当前仍只删除旧版本明确标记的占位文件，保护用户文件 |
| `ask` | CLI 可逐项确认；Web 后台任务按安全增量方式执行 |

下载先写入同目录临时文件，校验大小后原子替换。失败不会留下正式的 0 字节文件。

## CLI

```bash
bdpan add "分享链接" "提取码"
bdpan list
bdpan tree 1
bdpan refresh 1
bdpan mapping 1 "/远端目录" "/本地目录"
bdpan mappings
bdpan sync 1
bdpan sync-all
```

## 运维方式

- `logs/app.log`：应用日志，每天轮转。
- Linux/macOS 的 `logs/server.stdout.log` / `server.stderr.log`：启动进程输出。
- `logs/server.pid`：当前主进程 PID。
- Web“任务中心”：查看同步任务和错误。
- `status.ps1` / `status.sh`：检查进程和 HTTP 健康状态。

部署更新时保留整个 `data` 目录，尤其是 `password.txt`、`secrets.json` 和
`app.sqlite3`。Jenkinsfile 位于 `JenkinsConfig/Jenkinsfile`。

## 设计与需求文档

- [需求规格说明书](docs/需求规格说明书.md)
- [设计说明书](docs/设计说明书.md)
- [AGENTS 合规审计](docs/AGENTS合规审计.md)
