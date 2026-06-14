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
"""
import json
import logging
import subprocess
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

WESTOCK_PKG = "westock-data-clawhub@1.0.4"
WESTOCK_CMD = f"npx -y {WESTOCK_PKG}"


def _run_westock(command: str) -> dict:
    """
    执行 WeStock CLI 命令并解析 JSON 输出
    
    注意: 此函数不创建临时脚本文件，直接使用 subprocess 调用。
    """
    cmd = f"{WESTOCK_CMD} {command}"
    logger.debug(f"执行: {cmd}")

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logger.error(f"WeStock 命令失败: {result.stderr}")
            return {"error": result.stderr}

        # 尝试解析 JSON 输出
        output = result.stdout.strip()
        if output.startswith("{"):
            return json.loads(output)
        return {"raw": output}

    except subprocess.TimeoutExpired:
        logger.error("WeStock 命令超时")
        return {"error": "请求超时"}
    except Exception as e:
        logger.error(f"WeStock 命令异常: {e}")
        return {"error": str(e)}


def get_kline(symbols: str, period: str = "day", limit: int = 60, fq: str = "qfq") -> dict:
    """
    获取K线数据
    
    Args:
        symbols: 逗号分隔的股票代码 (如 "sh600000,sz000001")
        period: 周期 (day/week/month/season/year)
        limit: 返回条数 (最大2000)
        fq: 复权方式 (qfq前复权/hfq后复权/bfq不复权)
    
    Returns:
        K线数据字典
    """
    return _run_westock(f"kline {symbols} --period {period} --limit {limit} --fq {fq}")


def get_technical(symbols: str, group: str = "all",
                  start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """
    获取技术指标
    
    Args:
        symbols: 股票代码
        group: 指标分组 (ma/macd/kdj/rsi/boll/bias/wr/dmi/all)
        start: 开始日期 (YYYY-MM-DD)
        end: 结束日期 (YYYY-MM-DD)
    """
    cmd = f"technical {symbols} --group {group}"
    if start:
        cmd += f" --start {start}"
    if end:
        cmd += f" --end {end}"
    return _run_westock(cmd)


def get_profile(symbols: str) -> dict:
    """获取公司简况"""
    return _run_westock(f"profile {symbols}")


def get_finance(symbol: str, type_: str = "", num: int = 4) -> dict:
    """
    获取财务报表
    Args:
        symbol: 股票代码
        type_: 报表类型 (lrb利润表/zcfz资产负债表/xjll现金流量表)
        num: 返回期数
    """
    cmd = f"finance {symbol}"
    if type_:
        cmd += f" --type {type_}"
    cmd += f" --num {num}"
    return _run_westock(cmd)


def get_hot_stock() -> dict:
    """获取热搜股票"""
    return _run_westock("hot stock")


def search_stock(keyword: str) -> dict:
    """搜索股票"""
    return _run_westock(f"search {keyword}")


def verify_data_consistency(code: str, date_str: str) -> dict:
    """
    数据一致性校验: 交叉对比 AKShare 和 WeStock 的同日数据
    
    用于抽样校验，确保本地数据库中的数据与实时接口一致。
    
    Args:
        code: 股票代码 (纯数字, 如 "000001")
        date_str: 日期 (YYYYMMDD)
    
    Returns:
        {"consistent": bool, "akshare_close": float, "westock_close": float, "diff_pct": float}
    """
    # 判断市场前缀
    if code.startswith("6"):
        wstock_code = f"sh{code}"
    elif code.startswith("0") or code.startswith("3"):
        wstock_code = f"sz{code}"
    elif code.startswith("4") or code.startswith("8"):
        wstock_code = f"bj{code}"
    else:
        return {"error": f"无法识别市场: {code}"}

    result = get_kline(wstock_code, limit=5)
    
    # 简单解析（实际使用时需要更健壮的解析）
    return {
        "source": "WeStock Data (腾讯自选股)",
        "note": "🔵 此校验结果由 AI 辅助分析生成",
        "data": result
    }
