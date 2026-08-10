## Why

映射#2 在定时同步时，`get_share_download_url` 方法在检测到百度返回风控密文响应后，仅重试 4 次（总等待 ~35 秒）便放弃，抛出 `-1` 错误导致任务失败进入 6 小时退避。百度风控往往是间歇性的，较短的重试窗口容易在"差一点就恢复"时提前放弃，增加不必要的退避等待。扩大重试窗口可以让系统在单次调度内捕捉更多恢复机会，减少因风控抖动导致的频繁退避。

## What Changes

- 将 `get_share_download_url` 中风控密文重试次数从 4 次提升至 8 次，总等待时间从 ~35 秒延长至 ~25 分钟。
- 更新 `test_raises_risk_control_after_retries_exhausted` 测试用例中的断言以反映新的重试次数。
- 更新设计说明书中的风控退避策略说明。
- 更新需求规格说明书中任务失败处理的描述。

## Capabilities

### Modified Capabilities

- `share-sync`: 提升分享文件下载时风控响应的容忍度，减少因间歇性风控导致的无效退避。

## Impact

- `src/app/bdpan/client.py` 中 `risk_retries` 参数值调整。
- 相关单元测试断言更新。
- 文档中风控处理策略描述的澄清。
