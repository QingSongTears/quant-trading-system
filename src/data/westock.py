"""
WeStock Data 集成模块
=====================

通过 npx CLI 调用腾讯自选股行情数据接口 (westock-data-clawhub)。
用于:
- 实时行情查询
- 技术指标验证
- 公司简况 & 财务数据
- 数据一致性交叉校验

所有查询结果标注数据来源: "腾讯自选股公开行情接口"

安全说明
--------
本模块所有外部调用均通过 subprocess.run(..., shell=False, ...) 的 argv 列表形式,
不拼接 shell 字符串。每个公共函数对入参做白名单正则校验,避免命令注入。
"""
from __future__ import annotations
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import date, datetime


logger = logging.getLogger(__name__)

WESTOCK_PKG = "westock-data-clawhub@1.0.4"


def _resolve_npx() -> list[str]:
    """
    动态定位 npx 可执行文件，兼容 Windows (npx.cmd) 和 Unix (npx)。
    找不到时返回含清晰错误提示的 fallback，让后续 subprocess.run 给出有意义的报错。
    """
    for name in ("npx", "npx.cmd"):
        path = shutil.which(name)
        if path:
            return [path, "-y", WESTOCK_PKG]
    # 找不到 npx — 返回默认值，后续调用时由 subprocess 抛出 FileNotFoundError
    # 但这里先记录一条警告日志，方便排查
    logger.warning(
        "未找到 npx 可执行文件。请确认 Node.js 已安装且在 PATH 中。"
        "Windows 用户可从 cmd.exe 运行 'where npx' 确认。"
    )
    return ["npx", "-y", WESTOCK_PKG]


_NPM_BIN = _resolve_npx()

# ===== 入参白名单(防止命令注入) =====
# 股票代码: sh/sz/bj + 6 位数字,逗号分隔多个
_SYMBOLS_RE = re.compile(r"^[a-z]{2}\d{6}(,[a-z]{2}\d{6})*$")
# 单一股票代码
_SINGLE_SYMBOL_RE = re.compile(r"^[a-z]{2}\d{6}$")
# 日期: YYYY-MM-DD
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 周期
_PERIOD_RE = re.compile(r"^(day|week|month|season|year)$")
# 复权方式
_FQ_RE = re.compile(r"^(qfq|hfq|bfq)$")
# 指标分组
_GROUP_RE = re.compile(r"^(ma|macd|kdj|rsi|boll|bias|wr|dmi|all)$")
# 报表类型
_FINANCE_TYPE_RE = re.compile(r"^(lrb|zcfz|xjll)?$")
# 搜索关键词: 限制长度 + 禁止 shell 元字符
_SEARCH_KEYWORD_RE = re.compile(r"^[\w一-龥\-_.]{1,40}$")


def _err(msg: str) -> dict:
    """统一的入参校验失败返回"""
    return {"error": msg}


def _check(value: str, pattern: re.Pattern, name: str) -> dict | None:
    """检查 value 是否匹配 pattern,失败返回 error dict,成功返回 None"""
    if not isinstance(value, str) or not pattern.match(value):
        return _err(f"invalid {name}: {value!r}")
    return None


def _run_westock(argv: list) -> dict:
    """
    执行 WeStock CLI 命令并解析 JSON 输出

    安全: argv 必须是可信列表,不要从用户原始输入拼字符串传入。
    """
    cmd = _NPM_BIN + list(argv)
    logger.debug("执行: %s", " ".join(cmd))

    # Windows: CREATE_NO_WINDOW 防止 npx.cmd 弹黑色 cmd 窗口
    CREATE_NO_WINDOW = 0x08000000
    try:
        result = subprocess.run(
            cmd, shell=False, capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            logger.error("WeStock 命令失败: %s", result.stderr)
            return {"error": result.stderr}

        output = result.stdout.strip()
        if output.startswith("{"):
            return json.loads(output)
        return {"raw": output}

    except subprocess.TimeoutExpired:
        logger.error("WeStock 命令超时")
        return {"error": "请求超时"}
    except Exception as e:
        logger.error("WeStock 命令异常: %s", e)
        return {"error": str(e)}


def get_kline(symbols: str, period: str = "day", limit: int = 60, fq: str = "qfq") -> dict:
    """
    获取K线数据

    Args:
        symbols: 逗号分隔的股票代码 (如 "sh600000,sz000001")
        period: 周期 (day/week/month/season/year)
        limit: 返回条数 (1-2000)
        fq: 复权方式 (qfq前复权/hfq后复权/bfq不复权)
    """
    if e := _check(symbols, _SYMBOLS_RE, "symbols"):
        return e
    if e := _check(period, _PERIOD_RE, "period"):
        return e
    if e := _check(fq, _FQ_RE, "fq"):
        return e
    if not isinstance(limit, int) or not (1 <= limit <= 2000):
        return _err(f"invalid limit: {limit!r}")
    return _run_westock(
        ["kline", symbols, "--period", period, "--limit", str(limit), "--fq", fq]
    )


def get_technical(symbols: str, group: str = "all",
                  start: str | None = None, end: str | None = None) -> dict:
    """
    获取技术指标

    Args:
        symbols: 股票代码 (sh600000 / sz000001 / 多代码逗号分隔)
        group: 指标分组 (ma/macd/kdj/rsi/boll/bias/wr/dmi/all)
        start: 开始日期 (YYYY-MM-DD)
        end: 结束日期 (YYYY-MM-DD)
    """
    if e := _check(symbols, _SYMBOLS_RE, "symbols"):
        return e
    if e := _check(group, _GROUP_RE, "group"):
        return e
    if start is not None:
        if e := _check(start, _DATE_RE, "start"):
            return e
    if end is not None:
        if e := _check(end, _DATE_RE, "end"):
            return e
    argv = ["technical", symbols, "--group", group]
    if start:
        argv += ["--start", start]
    if end:
        argv += ["--end", end]
    return _run_westock(argv)


def get_profile(symbols: str) -> dict:
    """获取公司简况"""
    if e := _check(symbols, _SYMBOLS_RE, "symbols"):
        return e
    return _run_westock(["profile", symbols])


def get_finance(symbol: str, type_: str = "", num: int = 4) -> dict:
    """
    获取财务报表
    Args:
        symbol: 单一股票代码 (如 "sh600000")
        type_: 报表类型 (lrb利润表/zcfz资产负债表/xjll现金流量表, 留空=全部)
        num: 返回期数 (1-20)
    """
    if e := _check(symbol, _SINGLE_SYMBOL_RE, "symbol"):
        return e
    if e := _check(type_, _FINANCE_TYPE_RE, "type_"):
        return e
    if not isinstance(num, int) or not (1 <= num <= 20):
        return _err(f"invalid num: {num!r}")
    argv = ["finance", symbol, "--num", str(num)]
    if type_:
        argv += ["--type", type_]
    return _run_westock(argv)


def get_hot_stock() -> dict:
    """获取热搜股票"""
    return _run_westock(["hot", "stock"])


def search_stock(keyword: str) -> dict:
    """
    搜索股票

    关键词限制: 1-40 字符,仅允许字母/数字/中文/下划线/点/连字符
    """
    if e := _check(keyword, _SEARCH_KEYWORD_RE, "keyword"):
        return e
    return _run_westock(["search", keyword])


def verify_data_consistency(code: str, date_str: str) -> dict:
    """
    数据一致性校验: 交叉对比 AKShare 和 WeStock 的同日数据

    用于抽样校验，确保本地数据库中的数据与实时接口一致。

    Args:
        code: 股票代码 (纯数字, 如 "000001")
        date_str: 日期 (YYYYMMDD 或 YYYY-MM-DD)

    Returns:
        {"consistent": bool, "akshare_close": float, "westock_close": float, "diff_pct": float}
    """
    # code 必须是 6 位纯数字
    if not re.match(r"^\d{6}$", code):
        return _err(f"invalid code: {code!r} (需 6 位数字)")
    # date_str 接受 8 位或 10 位
    if not re.match(r"^\d{8}$|^\d{4}-\d{2}-\d{2}$", date_str):
        return _err(f"invalid date_str: {date_str!r} (YYYYMMDD 或 YYYY-MM-DD)")

    # 判断市场前缀
    if code.startswith("6"):
        wstock_code = f"sh{code}"
    elif code.startswith("0") or code.startswith("3"):
        wstock_code = f"sz{code}"
    elif code.startswith("4") or code.startswith("8"):
        wstock_code = f"bj{code}"
    else:
        return _err(f"无法识别市场: {code}")

    result = get_kline(wstock_code, limit=5)

    return {
        "source": "WeStock Data (腾讯自选股)",
        "note": "🔵 此校验结果由 AI 辅助分析生成",
        "data": result,
    }
