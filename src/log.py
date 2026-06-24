"""
统一日志 — 借鉴 vnpy.trader.logger, 2026-06-24

设计:
  - 用 loguru 替代 stdlib logging (更现代, 彩色终端, 结构化)
  - 双 sink:
      1. stdout (彩色, 适合开发)
      2. 文件 (按日切分, 适合长跑/多策略调试)
  - 支持 extra 字段: gateway_name / strategy_name 等
  - 默认 log 目录: <project_root>/output/log/

用法:
    from src.log import logger
    logger.info("hello")
    logger.bind(strategy_name="V6").info("策略启动")

    # 初始化时调一次 (run.py / web 启动)
    from src.log import setup
    setup(log_dir="output/log", level="DEBUG", file_log=False)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger as _loguru_logger


# ── 默认配置 ──────────────────────────

# 1. 全局 logger (兼容 vnpy 风格用法)
logger = _loguru_logger


# 2. 日志格式 (借鉴 vnpy: 时间 + 级别 + 来源 + 消息)
_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
    "| <level>{level: <8}</level> "
    "| <cyan>{extra[source]: <16}</cyan> "
    "| <level>{message}</level>"
)

_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} "
    "| {level: <8} "
    "| {extra[source]: <16} "
    "| {message}"
)


# 3. 日志级别映射
_LEVEL_MAP = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


# 4. 默认 sink 状态
_initialized: bool = False
_sink_ids: list = []  # 记录 sink id, 便于 remove_all 后重配


# ── setup 函数 ──────────────────────────


def setup(
    log_dir: str = "output/log",
    level: str = "INFO",
    console: bool = True,
    file_log: bool = True,
    project_root: Optional[str] = None,
) -> None:
    """
    初始化日志配置 (建议在程序启动时调用一次)

    Args:
        log_dir: 日志目录, 相对或绝对路径
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        console: 是否输出到 stdout
        file_log: 是否输出到 daily-rotate 文件
        project_root: 项目根目录, 默认从 src.log 所在目录向上推 1 层
    """
    global _initialized

    # 1. 先移除所有现有 handler (loguru 默认有 stderr)
    logger.remove()

    # 2. 计算项目根
    if project_root is None:
        # src/log.py → <project_root>/src/log.py
        project_root = Path(__file__).resolve().parent.parent
    else:
        project_root = Path(project_root)

    # 3. 解析日志目录
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = project_root / log_path
    log_path.mkdir(parents=True, exist_ok=True)

    # 4. 校验 level
    level_upper = level.upper()
    if level_upper not in _LEVEL_MAP:
        raise ValueError(
            f"level 必须为 {_LEVEL_MAP.keys()} 之一, 收到: {level!r}"
        )

    # 5. 添加 console sink
    if console:
        sink_id = logger.add(
            sys.stdout,
            level=level_upper,
            format=_LOG_FORMAT,
            colorize=True,
        )
        _sink_ids.append(sink_id)

    # 6. 添加文件 sink (按日切分)
    if file_log:
        today = datetime.now().strftime("%Y%m%d")
        filename = f"quant_{today}.log"
        file_path = log_path / filename

        sink_id = logger.add(
            str(file_path),
            level=level_upper,
            format=_FILE_FORMAT,
            encoding="utf-8",
            rotation="00:00",      # 每天 0 点切分新文件
            retention="30 days",   # 保留 30 天
            enqueue=False,         # 同步写, 简单可靠 (A 股场景不需要异步)
        )
        _sink_ids.append(sink_id)

    # 6.5 配置默认 source extra
    logger.configure(extra={"source": "Logger"})

    _initialized = True
    logger.info(f"日志系统初始化完成 (level={level_upper}, dir={log_path})")


def get_logger(source: str = "Logger"):
    """
    获取带 source 标签的 logger 实例 (类似 vnpy logger.bind(gateway_name=...))

    Args:
        source: 来源标识, 建议传 __name__ (模块名) 或 gateway_name

    Returns:
        loguru logger 实例, 调用 .info/.warning 等方法时自动带 source

    Example:
        log = get_logger("src.event.engine")
        log.info("engine started")  # 自动带 "src.event.engine" tag
    """
    # 若未调过 setup, 给一个最小默认配置 (避免丢日志)
    if not _initialized:
        setup(file_log=False)
    return logger.bind(source=source)


# ── 自动初始化 (模块导入时) ──────────────────────────


def _auto_setup_if_needed() -> None:
    """
    模块导入时尝试一次默认配置 (仅 console, 不开 file_log)

    设计:
      - 业务模块 import src.log 后即可用 logger
      - 若程序入口 (run.py) 调了 setup(), 这里 skip
      - 若没调, 给一个 stdout 兜底, 防日志丢失
    """
    if _initialized:
        return
    setup(file_log=False)


_auto_setup_if_needed()


# ── 兼容 stdlib logging 的 bridge (供 vnpy 风格代码调用) ──────────────────────────


class StdLibLoguruBridge:
    """
    让 stdlib logging 也能走 loguru 输出 (兼容 vnpy 风格代码)

    解决: 有些第三方库 (如 sqlalchemy, requests) 用 stdlib logging,
          默认输出风格不一致, 用此 bridge 统一走 loguru
    """

    def __init__(self) -> None:
        import logging
        self._stdlib_logger = logging.getLogger()

    def emit(self, record) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == __file__:
            frame = frame.f_back
            depth += 1

        # 把 stdlib logger 名作为 source 标签, 方便追溯来源
        logger.bind(source=record.name).opt(
            depth=depth, exception=record.exc_info,
        ).log(level, record.getMessage())


def bridge_stdlib_logging() -> None:
    """
    把 stdlib logging 桥接到 loguru (一次性调用, 在 setup() 之后)

    Example:
        from src.log import setup, bridge_stdlib_logging
        setup()
        bridge_stdlib_logging()  # 之后 sqlalchemy/requests 等日志也走 loguru
    """
    import logging
    handler = logging.Handler()
    handler.emit = StdLibLoguruBridge().emit  # type: ignore[assignment]
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)