# 多网盘分享管理（BaiduPanShareManagement）

跨平台的多网盘分享链接管理与同步 Web 服务（百度网盘、夸克网盘）。后端使用 Python、FastAPI 和 SQLite，
前端使用 Vue 3。系统支持多用户登录、页面级权限、目录浏览、后台同步、自动调度以及
Windows、Linux、macOS 挂载的 SMB NAS。

## 页面介绍

| 页面 | 功能 |
|---|---|
| 分享链接 | 添加、编辑、删除、刷新分享链接，显示有效/已失效/待检测状态，逐级浏览远端目录 |
| 同步映射 | 关联远端目录与本机/SMB 目录，检测连接、执行同步并查看已同步文件 |
| 任务中心 | 当前任务仅显示排队或运行中的任务；同步历史可查看每次同步的文件、时间和耗时；管理员可维护自动调度与历史保留策略 |
| 系统配置 | 维护系统名称、日志保留、当前账号密码和多网盘 Cookie，查看只读部署信息 |
| 权限管理 | 管理用户密码、管理员/普通用户角色及可访问页面 |

## 目录结构

```text
src/app/bdpan/          Python 后端与构建后的前端资源
src/app/frontend/       Vue 3 + Vite 前端源码
src/app/frontend/public/  浏览器标签页图标等静态资源，构建时拷贝到 web_static
config/app.json         系统主配置
data/app.sqlite3        SQLite 数据库（用户密码哈希与业务数据，首次运行生成）
data/secrets.json       Cookie 与会话密钥（首次运行生成，不提交 Git）
logs/                   应用日志、进程输出和 PID
src/JenkinsConfig/      Jenkins 流水线
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

用户与密码保存在 SQLite 的 `users` 表中，密码使用带随机盐的 scrypt 哈希，系统不再
从文件读取或写入明文密码。首次部署或数据库中没有任何用户时，系统会自动创建默认
管理员：

```text
admin / admin123
```

首次登录后应立即通过左下角“密”按钮或“系统配置 → 账号安全”修改默认密码。管理员可在
“权限管理”页面新增用户、重置密码、配置管理员/普通用户角色和可访问页面。管理员拥有
全部页面权限；普通用户可由管理员配置分享链接、同步映射、任务中心和系统配置页面权限。

升级时若存在旧版 `data/password.txt`，系统会一次性导入数据库中尚不存在的用户，
导入成功后删除该明文文件；数据库中已有的用户不会被旧文件覆盖。之后账号和密码只
通过界面维护。

## 配置文件

系统配置位于 `config/app.json`：

| 配置项 | 说明 | 页面维护 |
|---|---|---|
| `app_name` | 系统名称 | 系统配置 → 基础设置 |
| `log_retention_days` | 每日日志保留天数 | 系统配置 → 基础设置 |
| `task_workers` | 后台任务并发数（重启服务后生效） | 任务中心 → 任务设置 |
| `scheduler_poll_seconds` | 自动同步调度检查间隔 | 任务中心 → 任务设置 |
| `sync_history_retention_days` | 已完成同步历史保留天数，默认 90 | 任务中心 → 任务设置 |
| `sync_history_max_runs_per_mapping` | 每个映射最多保留的同步运行数，默认 200 | 任务中心 → 任务设置 |
| `host` / `port` | Web 监听地址与端口 | 只读，修改文件后重启 |
| `timezone` | 页面和日志时区，默认 `Asia/Shanghai` | 只读，修改文件后重启 |
| `database_path` | SQLite 数据库路径 | 只读，修改文件后重启 |
| `secret_file` | Cookie 和会话密钥文件 | 只读，修改文件后重启 |
| `log_file` | 应用日志路径 | 只读，修改文件后重启 |

页面中可维护的配置项保存时会原子写回 `config/app.json`，不需要手工编辑文件。
`task_workers` 需要重启服务才能改变实际线程池大小；调度间隔、历史保留策略和日志
保留天数保存后生效。`host`、`port`、`timezone` 和文件路径属于部署参数，需修改配置
文件并重启服务。

环境变量 `BDPAN_CONFIG` 可指向其他配置文件。`BDPAN_COOKIE` 可提供一条只读兼容
Cookie，页面不会把环境变量秘密复制到磁盘。`BDPAN_ADMIN_USERNAME` 和
`BDPAN_ADMIN_PASSWORD` 可覆盖首次初始化默认管理员凭据（仅数据库没有用户时使用）。
敏感信息不写入 `app.json`。

## 配置百度网盘 Cookie

登录百度网盘后，在浏览器开发者工具的 Network 请求头中复制完整 Cookie，并由管理员
在“系统配置”页面添加。系统支持多条具名 Cookie；每条分享链接在新增或编辑时必须选择
一条 Cookie，刷新、浏览和同步均使用该关联凭据。Cookie 至少应包含 `BDUSS`。
Cookie 等同登录凭据，不要提交到 Git 或发送给他人。

配置页只显示 Cookie 包含的字段摘要、添加时间、最近校验时间和状态，不返回原文。
“未知”表示尚无明确结论或最近请求受网络/风控影响，“有效”和“无效”分别表示最近一次
认证得到明确成功或失败。仍被分享链接引用的 Cookie 不能删除，应先重新关联。
普通用户如有“系统配置”页面权限可以查看脱敏元数据，但只有管理员可以变更和校验。

如果提示“获取 bdstoken 失败”，请先在浏览器重新登录 `pan.baidu.com`，再从任意
网盘请求的 Request Headers 中复制完整的 `Cookie` 请求头值。至少应包含有效的
`BDUSS`；不要粘贴 `Set-Cookie`、JSON 导出内容或仅复制单个字段。新版错误信息会
显示百度返回的 `errno`，用于区分 Cookie 失效和接口响应异常。

百度可能对服务器环境中的账号 token 接口返回 `errno=-6`，即使 Cookie 来自刚登录的
无痕窗口。公开分享的提取码验证、目录索引和分享下载会自动使用空 `bdstoken` 重试，
不再因该账号接口的风控结果阻断分享链接处理。

`errno=-65` 表示可能触发百度安全验证或请求频率限制。此时不要连续手工重试；自动同步
会从 6 小时开始退避，最长 24 小时，建议等待风控解除后再验证。

## 分享链接状态

分享链接卡片显示“有效”“已失效”“待检测”或“待确认”。刷新或同步时，只有网盘明确返回
分享不存在、已删除、已取消、已过期或封禁受限时才会标记为“已失效”；提取码错误、网络
异常、限流和风控不会改变原状态，避免误判。超过 30 天没有成功检测过的“有效”链接会
显示为“待检测”，提示重新刷新确认。列表中的“检测于”是最近一次成功检测或明确判定失效
的时间。

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
到本机系统盘。使用 systemd automount（`x-systemd.automount`）的挂载点会在存储探测
时自动读取目录以触发挂载，服务重启后无需手工先访问挂载点；NAS 离线时仍会中止同步。

## 同步策略

| 策略 | 说明 |
|---|---|
| `copy_new` | 下载新增文件并替换大小不一致文件，不删除本地文件 |
| `mirror` | 当前仍只删除旧版本明确标记的占位文件，保护用户文件 |
| `ask` | CLI 可逐项确认；Web 后台任务按安全增量方式执行 |

下载先写入同目录临时文件，校验大小后原子替换。失败不会留下正式的 0 字节文件。
远端版本元数据保存在文件所在目录的 `.bdpan_meta` 中，文件名使用原文件名的 SHA-256
哈希，因此 Linux 上接近 255 字节的文件名不会因追加 `.bdpan` 再次超长。系统仍兼容
读取升级前的 `原文件名.bdpan`。

自动同步失败会记录最近尝试时间和下次允许重试时间。普通错误从 5 分钟开始指数退避，
最长 6 小时；`errno=-65` 从 6 小时开始，最长 24 小时。调度器每 15 秒轮询只用于发现
到期任务，不会绕过退避重复提交。同步成功后退避自动清零。

## 同步文件记录

同步成功新增或更新文件后，系统会将映射、同步运行、相对路径、文件名、动作和同步时间
写入 SQLite。同步映射页的“查看”弹窗展示该映射每个相对路径的最新同步记录；任务中心
的同步历史在存在文件记录时提供“查看文件”入口，展示单次运行的文件。

弹窗支持按文件名或同步时间升序、降序排序，所有时间按北京时间显示。跳过、删除和同步
失败的文件不会被记录为成功同步文件。升级前的历史不会回填逐文件信息，查看时会显示
“暂无文件记录”。

## CLI

```bash
bdpan config --name "主账号" --cookie "你的百度网盘Cookie"
bdpan add "分享链接" "提取码" --cookie-id 1
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
- Web“任务中心”：当前任务查看排队/运行状态；同步历史查看结果、错误和本次同步文件。
- `status.ps1` / `status.sh`：检查进程和 HTTP 健康状态。

部署更新时保留整个 `data` 目录，尤其是 `app.sqlite3`、`secrets.json` 和
`config/app.json`。Jenkins 默认部署到 `/opt/BaiduPanShareManagement`，通过工作区同步
代码并保留 `data` 与 `logs`。该目录首次部署时需要由管理员创建并授权给 Jenkins。
Jenkinsfile 位于
`src/JenkinsConfig/Jenkinsfile`。

数据库升级由应用启动时自动幂等执行，会创建 Cookie 元数据和同步文件事件表及索引，
不会删除或覆盖现有分享、映射和同步历史。旧版 `secrets.json.cookie` 会迁移为“默认
Cookie”，现有分享链接自动关联到它；重复启动不会创建重复记录。部署更新必须保留整个
`data` 目录。

升级还会为映射增加同步尝试与退避字段。已完成同步历史默认保留 90 天且每个映射最多
200 条；关联的逐文件事件随过期运行记录一并清理。现有 Jenkinsfile 已保留 `data`、
执行全量测试、重启服务并运行 HTTP 健康检查，本修复无需改变流水线步骤。

## 设计与需求文档

- [需求规格说明书](docs/需求规格说明书.md)
- [设计说明书](docs/设计说明书.md)
- [AGENTS 合规审计](docs/AGENTS合规审计.md)


## 多网盘支持（2026-10-25）

- 支持百度网盘、夸克网盘分享的添加、提取码识别、目录索引与浏览、刷新、同步和定时任务。
- FastAPI、Vue、SQLite 及现有登录、权限、任务中心、同步策略保持兼容；包名、CLI 命令、服务名称和访问地址沿用原值。
- 分享记录与 Cookie 元数据增加 `provider`（`baidu` / `quark`）。启动时自动补充数据库字段，历史记录默认为百度网盘，无需手工迁移。
- 在系统配置页添加 Cookie 时选择网盘类型：百度需要 `BDUSS`，夸克需要 `__puus`。Cookie 原文继续仅存于服务端 secrets.json；分享与凭据类型必须一致，已建分享不可直接切换网盘类型。
- 添加分享时可直接粘贴 `https://pan.quark.cn/s/分享ID?pwd=提取码` 或带中文提取码的文本，选择同网盘凭据。
- 夸克下载先将文件转存到所选账号的根目录，再获取下载地址。转存文件保留、占用网盘容量；再次同步更新文件可能产生额外转存。容量不足或接口拒绝时任务失败并保留错误日志。
- 夸克分享令牌/目录接口使用 `drive-h.quark.cn`，账号转存、任务和下载使用 `drive-pc.quark.cn`；可分别用 `QUARK_SHARE_API_URL`（默认 `https://drive-h.quark.cn/1/clouddrive`）和 `QUARK_API_URL`（默认 `https://drive-pc.quark.cn/1/clouddrive`）覆盖。夸克对短时间大量转存有临时限制（约 10 分钟），同步会记录错误，稍后重试即可。需要有效账号 Cookie；模拟接口测试不能代替真实账号验收。
- CLI 同样按分享类型分派客户端；例如 `bdpan config --provider quark --name 夸克账号 --cookie '完整Cookie'`，添加分享时可用 `--cookie-id` 指定凭据。
- Jenkins 增量更新排除 `config/app.json`，仅首次部署复制默认配置，保留线上 data、日志与配置。升级前建议备份 SQLite、secrets.json 和配置文件。

Linux 运维脚本会优先管理已安装的 systemd 单元（环境变量 `BDPAN_SYSTEMD_UNIT`，默认 `baidupan.service`），否则使用原 PID 模式。Jenkins 用户需获准以 sudo 无密码执行对应单元的 start/stop；本次部署已添加仅限该单元启停的规则。

## 账号与配置界面维护（2026-11-07）

- 账号密码仅保存在 SQLite，新增、重置和修改密码均通过界面完成。
- 系统配置页可维护系统名称、日志保留天数和当前账号密码；任务中心可维护任务并发数、
  自动同步调度间隔及同步历史保留策略。
- 页面维护的配置写回 `config/app.json`，Jenkins 部署不会覆盖已有配置文件。
- 旧版 `data/password.txt` 只会用于一次性迁移，导入后自动删除。
