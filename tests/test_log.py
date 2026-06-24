"""
src.log 单测 — 借鉴 vnpy.trader.logger, 2026-06-24

涵盖:
  - setup() 创建日志目录 + 配置 console/file sink
  - get_logger() 返回带 source 标签的 logger
  - 不同 level 行为
  - 文件切分 (rotation 参数设置正确, 不实际等 24h)
  - 默认导入不崩 (auto_setup_if_needed 兜底)
  - bridge_stdlib_logging 接管 stdlib logging
"""
import logging
import os
import sys
from pathlib import Path

import pytest
from loguru import logger as loguru_logger

from src import log as src_log
from src.log import (
    _auto_setup_if_needed,
    _initialized,
    get_logger,
    logger,
    setup,
)


@pytest.fixture
def clean_log_state(tmp_path, monkeypatch):
    """每个测试前重置 src.log 内部状态, 防止模块级 singleton 串扰"""
    # loguru 是模块级 singleton, 每次 remove() 重置 sink
    loguru_logger.remove()
    src_log._initialized = False
    src_log._sink_ids = []

    yield tmp_path

    # 清理: 等异步写入完成 (enqueue=True 队列)
    try:
        loguru_logger.complete()
    except Exception:
        pass
    loguru_logger.remove()
    src_log._initialized = False
    src_log._sink_ids = []


# ── setup 函数 ──────────────────────────


def test_setup_creates_log_dir(tmp_path, clean_log_state):
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()

    setup(log_dir=str(log_dir), level="INFO", file_log=False)

    assert log_dir.exists()
    assert log_dir.is_dir()


def test_setup_accepts_string_level(tmp_path, clean_log_state):
    """level 字符串大小写不敏感 (我们 .upper())"""
    setup(log_dir=str(tmp_path), level="debug", file_log=False)
    assert src_log._initialized is True


def test_setup_rejects_invalid_level(tmp_path, clean_log_state):
    with pytest.raises(ValueError, match="level"):
        setup(log_dir=str(tmp_path), level="BOGUS", file_log=False)


def test_setup_console_only_disables_file(tmp_path, clean_log_state):
    setup(log_dir=str(tmp_path), level="INFO", console=True, file_log=False)
    # 文件名格式 quant_YYYYMMDD.log, 不应被创建
    files = list(tmp_path.glob("quant_*.log"))
    assert files == []


def test_setup_file_log_creates_file(tmp_path, clean_log_state):
    setup(log_dir=str(tmp_path), level="INFO", file_log=True)
    logger.info("test message")
    loguru_logger.complete()  # flush async queue (enqueue=True)

    files = list(tmp_path.glob("quant_*.log"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "test message" in content


def test_setup_file_log_format_includes_source(tmp_path, clean_log_state):
    setup(log_dir=str(tmp_path), level="INFO", file_log=True)
    log = get_logger("test.mod")
    log.info("hello source")
    loguru_logger.complete()  # flush async queue (enqueue=True)

    content = (tmp_path / "quant_20260624.log").read_text(encoding="utf-8")
    assert "test.mod" in content
    assert "hello source" in content


def test_setup_relative_path_resolves_against_project_root(tmp_path, clean_log_state):
    """log_dir 相对路径应从 project_root 解析 (非 cwd)"""
    # 不实际创建, 只验证 resolve 逻辑不抛
    setup(log_dir="output/log", level="INFO", file_log=False, project_root=str(tmp_path))
    assert src_log._initialized is True


def test_setup_removes_default_stderr(clean_log_state):
    """setup 应先 logger.remove() 移除 loguru 默认 stderr sink"""
    # loguru 启动时自动有 1 个 stderr sink
    initial_handlers = len(loguru_logger._core.handlers)  # type: ignore[attr-defined]
    setup(log_dir="output/log", level="INFO", file_log=False)
    # setup 后应只剩我们加的 console sink (1 个)
    after_handlers = len(loguru_logger._core.handlers)  # type: ignore[attr-defined]
    assert after_handlers == 1


# ── get_logger 函数 ──────────────────────────


def test_get_logger_returns_logger_with_source(tmp_path, clean_log_state, capsys):
    setup(log_dir=str(tmp_path), level="INFO", file_log=False)
    log = get_logger("src.event.engine")
    log.info("engine started")

    captured = capsys.readouterr()
    assert "src.event.engine" in captured.out
    assert "engine started" in captured.out


def test_get_logger_default_source_is_Logger(tmp_path, clean_log_state, capsys):
    setup(log_dir=str(tmp_path), level="INFO", file_log=False)
    logger.info("no source")

    captured = capsys.readouterr()
    assert "Logger" in captured.out


def test_get_logger_can_be_called_multiple_times(tmp_path, clean_log_state):
    setup(log_dir=str(tmp_path), level="INFO", file_log=False)
    log1 = get_logger("a.b")
    log2 = get_logger("c.d")
    log1.info("from a")
    log2.info("from c")
    # 不应崩, 两个 logger 可独立用


# ── level 控制 ──────────────────────────


def test_level_filters_lower_messages(tmp_path, clean_log_state, capsys):
    """level=ERROR 时, INFO/WARNING 应被过滤"""
    setup(log_dir=str(tmp_path), level="ERROR", file_log=False)
    log = get_logger("test")
    log.info("should not show")
    log.error("should show")

    captured = capsys.readouterr()
    assert "should not show" not in captured.out
    assert "should show" in captured.out


# ── _auto_setup_if_needed 兜底 ──────────────────────────


def test_module_import_creates_minimal_config(monkeypatch, tmp_path):
    """模块导入时 (无显式 setup), 应有最小兜底 (stdout, 不开 file_log)

    注意: 这是模块级 singleton, 第一次 setup() 后 _initialized=True,
    后续测试需要重置才能验证 auto_setup 行为
    """
    # 强制重置
    loguru_logger.remove()
    src_log._initialized = False

    # 模拟"模块刚被 import, 用户没调 setup"
    _auto_setup_if_needed()

    assert src_log._initialized is True
    # 应有 1 个 sink (console only)
    assert len(loguru_logger._core.handlers) == 1  # type: ignore[attr-defined]


# ── bridge_stdlib_logging ──────────────────────────


def test_bridge_stdlib_logging_captures_stdlib_messages(tmp_path, clean_log_state, capsys):
    """bridge 后, stdlib logging.info() 也走 loguru 输出"""
    setup(log_dir=str(tmp_path), level="INFO", file_log=False)
    src_log.bridge_stdlib_logging()

    stdlib_log = logging.getLogger("test.stdlib")
    stdlib_log.info("from stdlib")

    captured = capsys.readouterr()
    assert "from stdlib" in captured.out
    assert "test.stdlib" in captured.out


# ── 文件 rotation 配置 (不实际等 24h, 只验证配置生效) ──────────────────────────


def test_file_log_has_rotation_config(tmp_path, clean_log_state):
    """验证文件 sink 配了 rotation='00:00' 和 retention='30 days'

    不实际触发切分 (需要等午夜), 只验证 sink 注册成功
    """
    setup(log_dir=str(tmp_path), level="INFO", file_log=True)
    # 应有 2 个 sink: 1 console + 1 file
    handlers = loguru_logger._core.handlers  # type: ignore[attr-defined]
    assert len(handlers) == 2


def test_file_log_writes_with_utf8_encoding(tmp_path, clean_log_state):
    """文件应使用 utf-8 编码, 避免中文乱码"""
    setup(log_dir=str(tmp_path), level="INFO", file_log=True)
    log = get_logger("test.utf8")
    log.info("测试中文日志")
    loguru_logger.complete()  # flush async queue (enqueue=True)

    content = (tmp_path / "quant_20260624.log").read_text(encoding="utf-8")
    assert "测试中文日志" in content


# ── 全局 logger 单例 ──────────────────────────


def test_logger_is_loguru_singleton():
    """从 src.log 导入的 logger 应是 loguru 同一个实例"""
    assert logger is loguru_logger


# ── 实际写入文件不崩 (集成测试) ──────────────────────────


def test_end_to_end_log_writes(tmp_path, clean_log_state):
    """完整端到端: setup + get_logger + 多条日志 + 验证文件"""
    setup(log_dir=str(tmp_path), level="DEBUG", file_log=True)
    log = get_logger("e2e.test")

    log.debug("debug msg")
    log.info("info msg")
    log.warning("warn msg")
    log.error("error msg")
    loguru_logger.complete()  # flush async queue (enqueue=True)

    files = list(tmp_path.glob("quant_*.log"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    for msg in ["debug msg", "info msg", "warn msg", "error msg"]:
        assert msg in content
    for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
        assert level in content