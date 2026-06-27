"""
ADR-0010 Datafeed 统一 — 单测覆盖 (2026-06-27)

涵盖:
  - base.py get_trading_calendar 默认从 self.get_bars 推断 (无 DataRepository import)
  - base.py set_calendar_provider 注入接口
  - BaseDatafeed 新增 abstract methods:
    * get_industry_map
    * get_news_events
    * get_finance_snapshot
  - NewsEvent dataclass
  - LocalDatafeed 新接口实现 (走 stock_basic / research_report / finance_summary)
  - DataManager.business 门面 (ADR-0010 §D3 业务宽表统一入口)
"""
import sys
from datetime import date
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ── base.py 反模式终结 ──────────────────────────


def test_base_no_data_repository_import():
    """base.py 不再 import DataRepository (自己绕过自己 反模式终结)"""
    src = Path(_PROJECT_ROOT / "src/data/datafeed/base.py").read_text(encoding="utf-8")
    # 注释/字符串中提及不算,只查 import 语句
    import_lines = [
        line for line in src.splitlines()
        if line.strip().startswith(("from ", "import "))
    ]
    has_dr = any("DataRepository" in line for line in import_lines)
    assert not has_dr, f"base.py 仍 import DataRepository: {import_lines}"


def test_news_event_dataclass():
    """NewsEvent dataclass 字段完整"""
    from src.data.datafeed.base import NewsEvent
    e = NewsEvent(
        code="000001",
        date=date(2024, 1, 1),
        title="测试",
        rating="买入",
        rating_change="上调",
        institution="中信证券",
        url="http://example.com",
    )
    assert e.code == "000001"
    assert e.date == date(2024, 1, 1)
    assert e.rating == "买入"
    assert e.rating_change == "上调"
    assert e.source == "research_report"


# ── get_trading_calendar 默认从 get_bars 推断 ──────────────────────────


class _MockCalendarFeed(BaseDatafeed := __import__("src.data.datafeed", fromlist=["BaseDatafeed"]).BaseDatafeed):
    """满足 ABC 的最小 datafeed, get_bars 返回的 BarData 用于推断日历"""

    def __init__(self, probe_dates):
        super().__init__()
        self._probe_dates = probe_dates  # 上证指数 K 线的日期列表
        self._inited = True  # 跳过 init

    def get_bars(self, vt_symbol, interval="1d", start=None, end=None, count=None):
        from src.gateway import BarData
        if vt_symbol == "000001.SH":
            return [
                BarData(
                    gateway_name="MOCK",
                    symbol="000001",
                    exchange="SH",
                    datetime=__import__("datetime").datetime.combine(d, __import__("datetime").datetime.min.time()),
                    interval="1d",
                    open_price=3000.0, high_price=3000.0, low_price=3000.0,
                    close_price=3000.0, volume=0, turnover=0, open_interest=0,
                )
                for d in self._probe_dates
            ]
        return []

    def get_stock_list(self):
        return []

    def get_industry_map(self, codes):
        return {}

    def get_news_events(self, codes, start, end):
        return []

    def get_finance_snapshot(self, codes):
        return {}


def test_get_trading_calendar_inferred_from_get_bars():
    """默认 get_trading_calendar 从 000001.SH 1d 推断 (D2-B 决策)"""
    probe_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    feed = _MockCalendarFeed(probe_dates)
    cal = feed.get_trading_calendar(date(2024, 1, 1), date(2024, 1, 5))
    assert cal == probe_dates


def test_set_calendar_provider_overrides_default():
    """注入 _calendar_provider 后,优先用 provider"""
    feed = _MockCalendarFeed([])
    custom_dates = [date(2024, 5, 1), date(2024, 5, 2)]

    def my_provider(start, end):
        return custom_dates

    feed.set_calendar_provider(my_provider)
    cal = feed.get_trading_calendar(date(2024, 1, 1), date(2024, 12, 31))
    assert cal == custom_dates


# ── DataManager.business 门面 (ADR-0010 §D3) ──────────────────────────


def test_data_manager_business_returns_data_repository():
    """DataManager.business 返回 DataRepository 实例 (业务宽表统一入口)"""
    from src.data.manager import DataManager
    from src.models.repository import DataRepository
    dm = DataManager()
    assert isinstance(dm.business, DataRepository)


# ── check_legacy.py 黑名单规则 ──────────────────────────


def test_check_legacy_blacklist_patterns():
    """check_legacy.py 加了 3 条 datafeed 黑名单 pattern (ADR-0010)"""
    from dev_tools.hooks import check_legacy
    # 黑名单目录
    assert "src/strategies/" in check_legacy.DATAFEED_BLACKLIST_DIRS
    assert "src/scoring/" in check_legacy.DATAFEED_BLACKLIST_DIRS
    assert "src/selection/" in check_legacy.DATAFEED_BLACKLIST_DIRS
    assert "src/web/routes/" in check_legacy.DATAFEED_BLACKLIST_DIRS
    # 黑名单 pattern (3 条:DataRepository / pd.read_sql / sql_utils.read_sql)
    assert len(check_legacy.DATAFEED_BLACKLIST_PATTERNS) == 3


def test_check_legacy_detects_data_repository_import():
    """check_file 应检测业务模块直接 import DataRepository"""
    from pathlib import Path
    from dev_tools.hooks import check_legacy
    # 测试文件需在黑名单目录下 (src/strategies/),用临时文件 + ROOT 路径
    test_dir = _PROJECT_ROOT / "src/strategies/_test_check_legacy_dr"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / "test_dr.py"
    try:
        test_file.write_text("from src.models.repository import DataRepository\n"
                              "repo = DataRepository()\n", encoding="utf-8")
        errors = check_legacy.check_file(test_file)
        assert any("DataRepository" in e and "ADR-0010" in e for e in errors), \
            f"未检测到 ADR-0010 黑名单: {errors}"
    finally:
        if test_file.exists():
            test_file.unlink()
        if test_dir.exists():
            test_dir.rmdir()


def test_check_legacy_detects_pd_read_sql():
    """check_file 应检测业务模块 pd.read_sql 调用"""
    from pathlib import Path
    from dev_tools.hooks import check_legacy
    test_dir = _PROJECT_ROOT / "src/strategies/_test_check_legacy_pd"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / "test_pd.py"
    try:
        test_file.write_text("import pandas as pd\n"
                              "df = pd.read_sql(sql, engine)\n", encoding="utf-8")
        errors = check_legacy.check_file(test_file)
        assert any("pd.read_sql" in e and "ADR-0010" in e for e in errors), \
            f"未检测到 pd.read_sql 黑名单: {errors}"
    finally:
        if test_file.exists():
            test_file.unlink()
        if test_dir.exists():
            test_dir.rmdir()


def test_check_legacy_detects_sql_utils_read_sql():
    """check_file 应检测业务模块 import sql_utils.read_sql"""
    from pathlib import Path
    from dev_tools.hooks import check_legacy
    test_dir = _PROJECT_ROOT / "src/strategies/_test_check_legacy_rs"
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / "test_rs.py"
    try:
        test_file.write_text("from ..db.sql_utils import read_sql\n"
                              "df = read_sql(sql, engine)\n", encoding="utf-8")
        errors = check_legacy.check_file(test_file)
        assert any("read_sql" in e and "ADR-0010" in e for e in errors), \
            f"未检测到 read_sql 黑名单: {errors}"
    finally:
        if test_file.exists():
            test_file.unlink()
        if test_dir.exists():
            test_dir.rmdir()


def test_check_legacy_ignores_data_layer():
    """data/manager.py 等 data 层不受 ADR-0010 黑名单约束"""
    from pathlib import Path
    from dev_tools.hooks import check_legacy
    src_data_test = _PROJECT_ROOT / "src/data/__test_check_legacy.py"
    try:
        src_data_test.write_text("from src.models.repository import DataRepository\n",
                                  encoding="utf-8")
        errors = check_legacy.check_file(src_data_test)
        # data/ 不在黑名单,应无 ADR-0010 错误
        assert not any("ADR-0010" in e for e in errors), \
            f"data/ 误报: {errors}"
    finally:
        if src_data_test.exists():
            src_data_test.unlink()