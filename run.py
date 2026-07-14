"""无需安装 editable 包即可从源码启动 Web 服务。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src" / "app"))

from bdpan.web import main  # noqa: E402


if __name__ == "__main__":
    main()
