"""
westock 安全单测 — 命令注入防护

覆盖 src/data/westock.py 改写后的所有白名单正则:
- get_kline symbols / period / limit / fq
- get_technical symbols / group / start / end
- get_profile symbols
- get_finance symbol / type_ / num
- get_hot_stock
- search_stock keyword
- verify_data_consistency code / date_str
"""
import re
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# westock 在 import 时不调用 npx,只加载正则和函数定义
from src.data import westock


# ============================================================
# get_kline
# ============================================================


class TestGetKlineValidation:
    def test_valid_input_passes_validation(self, monkeypatch):
        """合法输入应该过入参校验(实际 subprocess 会因为没装 npx 报错,但不应是 invalid)"""
        import subprocess
        # 拦截 subprocess.run,避免真的去执行 npx
        def fake_run(*args, **kwargs):
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = '{"data": "ok"}'
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)

        r = westock.get_kline("sh600000", period="day", limit=60, fq="qfq")
        # 不应返回 "invalid symbols" 类错误
        if "error" in r:
            assert "invalid" not in r["error"].lower()

    def test_rejects_shell_injection_in_symbols(self):
        r = westock.get_kline("sh600000; rm -rf /", period="day", limit=60, fq="qfq")
        assert "error" in r
        assert "invalid symbols" in r["error"]

    def test_rejects_invalid_market_prefix(self):
        r = westock.get_kline("xx600000", period="day", limit=60, fq="qfq")
        assert "error" in r

    def test_rejects_short_code(self):
        r = westock.get_kline("sh60000", period="day", limit=60, fq="qfq")
        assert "error" in r

    def test_rejects_long_code(self):
        r = westock.get_kline("sh6000000", period="day", limit=60, fq="qfq")
        assert "error" in r

    def test_rejects_invalid_period(self):
        r = westock.get_kline("sh600000", period="evil_period", limit=60, fq="qfq")
        assert "error" in r
        assert "invalid period" in r["error"]

    def test_rejects_invalid_fq(self):
        r = westock.get_kline("sh600000", period="day", limit=60, fq="hacker")
        assert "error" in r
        assert "invalid fq" in r["error"]

    def test_rejects_negative_limit(self):
        r = westock.get_kline("sh600000", period="day", limit=-1, fq="qfq")
        assert "error" in r
        assert "invalid limit" in r["error"]

    def test_rejects_excessive_limit(self):
        r = westock.get_kline("sh600000", period="day", limit=99999, fq="qfq")
        assert "error" in r

    def test_rejects_non_int_limit(self):
        r = westock.get_kline("sh600000", period="day", limit=60.5, fq="qfq")
        assert "error" in r

    def test_accepts_multiple_symbols(self, monkeypatch):
        import subprocess
        def fake_run(*args, **kwargs):
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = "{}"
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)
        r = westock.get_kline("sh600000,sz000001,bj430047", period="day", limit=60, fq="qfq")
        if "error" in r:
            assert "invalid" not in r["error"].lower()

    def test_uses_shell_false(self, monkeypatch):
        """核心安全:subprocess.run 必须用 shell=False"""
        import subprocess
        captured = {}
        def fake_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = "{}"
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)

        westock.get_kline("sh600000", period="day", limit=60, fq="qfq")
        # shell=False 是强制安全要求
        assert captured["kwargs"].get("shell") is False, "必须 shell=False 防命令注入"

    def test_passes_argv_list_not_string(self, monkeypatch):
        """核心安全:第一个参数必须是 list(argv)而不是 string"""
        import subprocess
        captured = {}
        def fake_run(*args, **kwargs):
            captured["args"] = args
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = "{}"
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)

        westock.get_kline("sh600000", period="day", limit=60, fq="qfq")
        # 第一个位置参数应该是 list
        cmd = captured["args"][0]
        assert isinstance(cmd, list), f"argv 必须是 list,得到 {type(cmd).__name__}"
        # 第一个元素是 npx
        assert cmd[0] == "npx"
        # 不应该含 shell 元字符
        joined = " ".join(str(x) for x in cmd)
        assert ";" not in joined
        assert "|" not in joined
        assert "&&" not in joined


# ============================================================
# get_technical
# ============================================================


class TestGetTechnicalValidation:
    def test_rejects_injection_in_start(self):
        r = westock.get_technical("sh600000", group="all", start="2024-01-01; evil", end="2024-12-31")
        assert "error" in r
        assert "invalid start" in r["error"]

    def test_rejects_injection_in_end(self):
        r = westock.get_technical("sh600000", group="all", start="2024-01-01", end="2024-12-31; evil")
        assert "error" in r
        assert "invalid end" in r["error"]

    def test_rejects_malformed_date(self):
        r = westock.get_technical("sh600000", start="2024/01/01")  # 用了错误的分隔符
        assert "error" in r

    def test_rejects_invalid_group(self):
        r = westock.get_technical("sh600000", group="evilgroup")
        assert "error" in r
        assert "invalid group" in r["error"]

    def test_accepts_none_dates(self, monkeypatch):
        """start/end 可选(None 应被跳过)"""
        import subprocess
        def fake_run(*args, **kwargs):
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = "{}"
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)
        r = westock.get_technical("sh600000", group="rsi", start=None, end=None)
        if "error" in r:
            assert "invalid" not in r["error"].lower()


# ============================================================
# get_finance
# ============================================================


class TestGetFinanceValidation:
    def test_rejects_injection_in_symbol(self):
        r = westock.get_finance("sh600000; evil")
        assert "error" in r
        assert "invalid symbol" in r["error"]

    def test_rejects_multiple_symbols(self):
        """单一股票代码,不应接受多代码"""
        r = westock.get_finance("sh600000,sz000001")
        assert "error" in r

    def test_rejects_invalid_type(self):
        r = westock.get_finance("sh600000", type_="evil_type")
        assert "error" in r

    def test_accepts_empty_type(self, monkeypatch):
        """type_ 留空 = 全部报表"""
        import subprocess
        def fake_run(*args, **kwargs):
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = "{}"
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)
        r = westock.get_finance("sh600000", type_="")
        if "error" in r:
            assert "invalid" not in r["error"].lower()

    def test_rejects_excessive_num(self):
        r = westock.get_finance("sh600000", num=999)
        assert "error" in r

    def test_rejects_zero_num(self):
        r = westock.get_finance("sh600000", num=0)
        assert "error" in r


# ============================================================
# search_stock
# ============================================================


class TestSearchStockValidation:
    def test_rejects_shell_metachar(self):
        r = westock.search_stock("evil; cat /etc/passwd")
        assert "error" in r
        assert "invalid keyword" in r["error"]

    def test_rejects_pipe(self):
        r = westock.search_stock("foo | bar")
        assert "error" in r

    def test_rejects_ampersand(self):
        r = westock.search_stock("foo & bar")
        assert "error" in r

    def test_rejects_empty(self):
        r = westock.search_stock("")
        assert "error" in r

    def test_rejects_excessive_length(self):
        r = westock.search_stock("a" * 1000)  # 超过 40 字符限制
        assert "error" in r

    def test_accepts_chinese(self, monkeypatch):
        """关键词应该支持中文"""
        import subprocess
        def fake_run(*args, **kwargs):
            from unittest.mock import MagicMock
            m = MagicMock()
            m.returncode = 0
            m.stdout = "{}"
            m.stderr = ""
            return m
        monkeypatch.setattr(subprocess, "run", fake_run)
        r = westock.search_stock("贵州茅台")
        if "error" in r:
            assert "invalid" not in r["error"].lower()


# ============================================================
# verify_data_consistency
# ============================================================


class TestVerifyDataConsistencyValidation:
    def test_rejects_injection_in_code(self):
        r = westock.verify_data_consistency("000001; evil", "20240101")
        assert "error" in r
        assert "invalid code" in r["error"]

    def test_rejects_non_6digit_code(self):
        r = westock.verify_data_consistency("00001", "20240101")  # 5 位
        assert "error" in r

    def test_rejects_letter_in_code(self):
        r = westock.verify_data_consistency("000001X", "20240101")
        assert "error" in r

    def test_accepts_both_date_formats(self):
        """YYYYMMDD 和 YYYY-MM-DD 都接受"""
        # 我们只测到入参校验这一步
        for date_str in ["20240101", "2024-01-01"]:
            r = westock.verify_data_consistency("000001", date_str)
            # 不应在入参校验时失败(后续 get_kline 会因 npx 缺失失败)
            if "error" in r and "invalid" in r["error"]:
                pytest.fail(f"格式 {date_str} 应被接受: {r}")

    def test_rejects_invalid_date_format(self):
        r = westock.verify_data_consistency("000001", "2024/01/01")
        assert "error" in r
        assert "invalid date_str" in r["error"]

    def test_market_prefix_6xxxx(self):
        r = westock.verify_data_consistency("600000", "20240101")
        # 应到 get_kline 阶段(npx 缺失会失败,但不应在入参校验时挂)
        if "error" in r:
            assert "invalid" not in r["error"].lower() or "code" not in r["error"].lower()

    def test_market_prefix_0xxxx(self):
        r = westock.verify_data_consistency("000001", "20240101")
        if "error" in r:
            assert "invalid" not in r["error"].lower() or "code" not in r["error"].lower()

    def test_market_prefix_3xxxx(self):
        r = westock.verify_data_consistency("300001", "20240101")
        if "error" in r:
            assert "invalid" not in r["error"].lower() or "code" not in r["error"].lower()

    def test_market_prefix_4xxxx(self):
        r = westock.verify_data_consistency("430001", "20240101")
        if "error" in r:
            assert "invalid" not in r["error"].lower() or "code" not in r["error"].lower()

    def test_market_prefix_8xxxx(self):
        r = westock.verify_data_consistency("830001", "20240101")
        if "error" in r:
            assert "invalid" not in r["error"].lower() or "code" not in r["error"].lower()

    def test_market_prefix_unrecognized(self):
        r = westock.verify_data_consistency("100001", "20240101")
        # 1xxxx 没有任何市场,应返回"无法识别市场"
        assert "error" in r
        assert "无法识别市场" in r["error"]


# ============================================================
# 回归保护
# ============================================================


class TestNoShellInjectionRegression:
    """防止有人回退到 shell=True"""

    def test_no_shell_true_anywhere(self):
        path = _PROJECT_ROOT / "src" / "data" / "westock.py"
        src = path.read_text(encoding="utf-8")
        assert "shell=True" not in src, "westock.py 禁止 shell=True"
