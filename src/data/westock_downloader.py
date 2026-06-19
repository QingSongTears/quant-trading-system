"""
WeStock 数据下载器 — 桥接模块
==============================
兼容旧版 import: from src.data.westock_downloader import WestockDownloader
"""
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WestockDownloader:
    """WeStock 数据下载器 — 兼容 DataDownloader 接口的桩模块"""

    def __init__(self):
        logger.info("WestockDownloader 初始化")

    def download_full(self, progress_callback: Optional[Callable] = None) -> dict:
        logger.info("WeStock 全量下载")
        if progress_callback:
            progress_callback(1, 1, "", "westock 就绪")
        return {"status": "ok", "source": "WeStock-Data", "records": 0}

    def download_incremental(self, progress_callback: Optional[Callable] = None) -> dict:
        logger.info("WeStock 增量下载")
        if progress_callback:
            progress_callback(1, 1, "", "westock 就绪")
        return {"status": "ok", "source": "WeStock-Data", "records": 0}
