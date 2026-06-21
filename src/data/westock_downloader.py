"""
WeStock 数据下载器 — 桥接模块
==============================
兼容旧版 import: from src.data.westock_downloader import WestockDownloader

⚠️ 状态: 桩模块 (stub) — 实际未实现,会抛 NotImplementedError。
历史原因: WeStock 数据源依赖腾讯自选股 CLI (`npx westock`),
跨平台/网络稳定性差,生产已统一改用 DataDownloader (AKShare)。

调用方应直接使用 `DataDownloader`,或检测到 ImportError/NotImplementedError 后 fallback。
"""
from __future__ import annotations
import logging
from typing import Callable

logger = logging.getLogger(__name__)


# 保留旧行为标记,让调用方可以检测
class WestockDownloaderNotImplemented(NotImplementedError):
    """WestockDownloader 桩模块未实现的明确错误类型 — 便于调用方 fallback"""
    pass


class WestockDownloader:
    """WeStock 数据下载器 — ⚠️ 桩模块,所有方法抛 NotImplementedError

    历史接口(为兼容旧 import 保留),但实际:
    - __init__: 抛 NotImplementedError
    - download_full / download_incremental: 抛 NotImplementedError

    调用方应直接使用 DataDownloader (AKShare)。
    保留类是为了让 from src.data.westock_downloader import WestockDownloader 不立即
    ImportError — 业务代码可以捕获 NotImplementedError 后回退。
    """

    def __init__(self):
        raise WestockDownloaderNotImplemented(
            "WestockDownloader 是桩模块,未实现。"
            "请直接使用 src.data.downloader.DataDownloader (AKShare)。"
            "详见 PR1 commit 1.1。"
        )

    def download_full(self, progress_callback: Callable | None = None) -> dict:
        # 仅为接口兼容保留 — 正常路径下不会到这里 (因为 __init__ 已抛错)
        raise WestockDownloaderNotImplemented(
            "WestockDownloader.download_full 未实现,请使用 DataDownloader"
        )

    def download_incremental(self, progress_callback: Callable | None = None) -> dict:
        raise WestockDownloaderNotImplemented(
            "WestockDownloader.download_incremental 未实现,请使用 DataDownloader"
        )