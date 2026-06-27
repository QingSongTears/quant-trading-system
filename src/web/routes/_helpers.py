"""
共享辅助模块 (v2.1.2 #85 拆 api.py)

提供所有 /api/* 路由模块复用的:
  - get_repo():  业务宽表访问入口 (ADR-0010 §D3)
  - _safe_float: numpy float / NaN / None → 原生 Python
  - _safe_json:  numpy 类型 + NaN 安全序列化为 JSON 字符串
  - _parse_md_table: westock CLI 返回的 markdown 表格 → list[dict]
  - _to_westock_code: 6 位股票代码自动加 sh/sz/bj 前缀
  - _f: 字符串数字 (含千分位逗号) → float 或 None
"""
from __future__ import annotations

import json
import logging

from ...data import get_data_manager

logger = logging.getLogger(__name__)


# ===== 业务宽表访问 (ADR-0010 §D3) =====
def get_repo():
    """统一从 data 层获取业务宽表访问入口 (ADR-0010 §D3)

    通过 DataManager.business 门面访问,避免 web 直接 import DataRepository
    (check_legacy.py 黑名单:strategies/scoring/selection/web 禁 import DataRepository)。
    """
    return get_data_manager().business


# ===== 序列化辅助 (处理 np.nan / np.float64) =====
def _safe_float(v):
    """numpy float / NaN / None → 原生 Python"""
    if v is None:
        return None
    try:
        import math
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _safe_json(v):
    """numpy 类型 + NaN 安全序列化为 JSON 字符串"""
    if v is None:
        return "[]"
    if isinstance(v, str):
        return v  # 已经是 JSON 字符串
    try:
        import math
        import numpy as np

        def _conv(o):
            if isinstance(o, dict):
                return {k: _conv(val) for k, val in o.items()}
            if isinstance(o, (list, tuple)):
                return [_conv(x) for x in o]
            if isinstance(o, (np.integer,)):
                return int(o)
            if isinstance(o, (np.floating,)):
                f = float(o)
                if math.isnan(f) or math.isinf(f):
                    return None
                return f
            if isinstance(o, float):
                if math.isnan(o) or math.isinf(o):
                    return None
                return o
            return o

        cleaned = _conv(v)
        return json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
    except Exception:
        return "[]"


# ===== westock / 行情辅助 =====
def _parse_md_table(text: str) -> list[dict]:
    """westock CLI 返回 markdown 表格 → list[dict]

    格式: | col1 | col2 |... \\n | --- | --- |...\\n | v1 | v2 |...
    """
    if not text or not isinstance(text, str):
        return []
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for ln in lines[2:]:  # 跳过分隔行
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append({h: cells[i] for i, h in enumerate(header)})
    return rows


def _to_westock_code(code: str) -> str:
    """600519 → sh600519, 000001 → sz000001, 6 位 sh/sz/bj 自动加前缀"""
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if len(code) == 6:
        if code.startswith(("5", "6", "9")):
            return "sh" + code
        if code.startswith(("0", "1", "2", "3")):
            return "sz" + code
        if code.startswith(("4", "8")):
            return "bj" + code
    return code


def _f(v):
    """字符串数字 (含千分位逗号) → float 或 None"""
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None
