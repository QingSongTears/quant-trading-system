"""
Indicator 协议 + 注册表 — 统一指标接口 (2026-06-25)

设计目标:
  - 一处定义, 多处调用 (策略 / 评分 / 研究 / 模型训练)
  - 训练 / 推理分布一致 (同一指标同一版本, 无漂移)
  - 算法可替换 (注册表, 同名可注册 v1 / v2, 保留历史兼容)

核心抽象:
  - Indicator: 协议 (Protocol), 4 个属性 + 2 个方法
  - IndicatorResult: 算子输出 (value + params + version + meta)
  - IndicatorRegistry: 工厂 + 注册表

使用:
    from src.indicator.protocol import IndicatorRegistry

    @IndicatorRegistry.register("macd")
    class MacdIndicator:
        name = "macd"
        version = "v1"
        n_required = 35
        def compute(self, bars, **params) -> IndicatorResult: ...

    # 调用
    rsi = IndicatorRegistry.get("rsi").compute(close, period=14)
    print(rsi.value, rsi.params)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable
import logging


logger = logging.getLogger(__name__)


# ============================================================
# Indicator 协议
# ============================================================


@runtime_checkable
class Indicator(Protocol):
    """指标协议 — 任何指标类实现这 4 字段 + 1 方法即可注册

    Attributes:
        name: 指标名 (如 "macd", "rsi", "kdj")
        version: 算法版本 ("v1" / "wilder" / "tv"), 同名可有多版本
        n_required: 最小 K 线数 (不足时返 IndicatorResult(value=None, error="insufficient"))
    """
    name: str
    version: str
    n_required: int

    def compute(self, bars, **params) -> "IndicatorResult":
        """计算指标

        Args:
            bars: K 线数据 (BarData / np.ndarray / pd.Series / pd.DataFrame, 内部归一)
            **params: 指标参数 (如 period=14, fast=12, slow=26, signal=9)

        Returns:
            IndicatorResult(value, params, version, n_required, meta, error)
        """
        ...


# ============================================================
# IndicatorResult — 算子输出
# ============================================================


@dataclass
class IndicatorResult:
    """指标计算结果

    Attributes:
        name: 指标名
        value: 计算结果 (scalar / tuple / ndarray, 取决于指标)
        params: 计算时使用的参数 (回放用)
        version: 算子版本
        n_required: 最小 K 线数
        meta: 附加元数据 (子项 / 中间量 / 调试信息)
        error: 错误信息 (None = 无错)
    """
    name: str
    value: Any
    params: Dict[str, Any]
    version: str
    n_required: int
    meta: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_scalar(self) -> float:
        """取单值 (用于最末一根 K 线的指标值)"""
        if isinstance(self.value, (int, float)):
            return float(self.value)
        if isinstance(self.value, (tuple, list)) and len(self.value) == 1:
            return float(self.value[0])
        raise ValueError(
            f"IndicatorResult.value 不是 scalar, 是 {type(self.value).__name__}: {self.value!r}"
        )

    def to_tuple(self) -> tuple:
        """取多值 (macd/dif/dea/macd 这种 3 元组)"""
        if isinstance(self.value, (tuple, list)):
            return tuple(self.value)
        return (self.value,)

    def to_dict(self) -> dict:
        """序列化 (供 SQL/JSON/缓存)"""
        return {
            "name": self.name,
            "value": self.value if not hasattr(self.value, "tolist") else self.value.tolist(),
            "params": self.params,
            "version": self.version,
            "n_required": self.n_required,
            "meta": self.meta,
            "error": self.error,
        }

    def __repr__(self) -> str:
        v = self.value
        if hasattr(v, "shape"):
            v_repr = f"ndarray(shape={v.shape})"
        else:
            v_repr = repr(v)
        err = f", error={self.error!r}" if self.error else ""
        return f"IndicatorResult({self.name}@{self.version}, value={v_repr}{err})"


# ============================================================
# IndicatorRegistry — 工厂 + 注册表
# ============================================================


class IndicatorRegistry:
    """指标注册表 + 工厂

    注册: @IndicatorRegistry.register("name", version="v1")
    获取: IndicatorRegistry.get("name", version="v1")
    列出: IndicatorRegistry.list()
    """

    _indicators: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, version: str = "v1"):
        """装饰器: 把指标类注册到注册表

        Usage:
            @IndicatorRegistry.register("macd")
            class MacdIndicator:
                name = "macd"
                version = "v1"
                ...
        """
        def deco(klass: type) -> type:
            key = f"{name}@{version}"
            if key in cls._indicators:
                logger.warning(
                    "Indicator %s 重复注册, 新类 %s 覆盖旧类 %s",
                    key, klass.__name__, cls._indicators[key].__name__,
                )
            # 校验类有必需字段
            for attr in ("name", "version", "n_required"):
                if not hasattr(klass, attr):
                    raise TypeError(
                        f"指标类 {klass.__name__} 缺 {attr!r} 属性 (注册 {key} 失败)"
                    )
            if not callable(getattr(klass, "compute", None)):
                raise TypeError(
                    f"指标类 {klass.__name__} 缺 compute() 方法 (注册 {key} 失败)"
                )
            cls._indicators[key] = klass
            logger.debug("注册指标: %s -> %s", key, klass.__name__)
            return klass
        return deco

    @classmethod
    def get(cls, name: str, version: str = "v1") -> Any:
        """获取指标实例

        Returns:
            指标实例 (klass()) — 每次新建, 但指标通常是无状态对象

        Raises:
            KeyError: 未注册
        """
        key = f"{name}@{version}"
        if key not in cls._indicators:
            available = sorted(cls._indicators.keys())
            raise KeyError(
                f"未注册指标: {key!r}, "
                f"已注册 {len(available)} 个: {available[:5]}{'...' if len(available) > 5 else ''}"
            )
        klass = cls._indicators[key]
        return klass()

    @classmethod
    def has(cls, name: str, version: str = "v1") -> bool:
        """检查指标是否注册"""
        return f"{name}@{version}" in cls._indicators

    @classmethod
    def list(cls) -> list[str]:
        """列出所有已注册指标 key (格式 "name@version")"""
        return sorted(cls._indicators.keys())

    @classmethod
    def list_by_name(cls, name: str) -> list[str]:
        """列出某指标的所有版本 (格式 "name@version")"""
        return sorted(k for k in cls._indicators if k.startswith(f"{name}@"))

    @classmethod
    def clear(cls) -> None:
        """清空注册表 (仅测试用)"""
        cls._indicators.clear()


# ============================================================
# 工具: bars → close 序列 归一
# ============================================================


def to_close_series(bars) -> "pd.Series":
    """把 bars 归一成 pd.Series (close 列)

    支持输入:
        - pd.Series: 直接返 (要求是 close 序列)
        - pd.DataFrame: 取 "close" 列
        - np.ndarray: 1D 视为 close, 2D 假设列序 [open,high,low,close]
        - list[BarData] / list[dict]: 取 .close 或 ["close"]
    """
    import numpy as np
    import pandas as pd

    if isinstance(bars, pd.Series):
        return bars
    if isinstance(bars, pd.DataFrame):
        if "close" in bars.columns:
            return bars["close"]
        raise ValueError(f"DataFrame 缺 'close' 列, 有 {list(bars.columns)}")
    if isinstance(bars, np.ndarray):
        if bars.ndim == 1:
            return pd.Series(bars)
        if bars.ndim == 2 and bars.shape[1] >= 4:
            return pd.Series(bars[:, 3])  # 假设 OHLC
        raise ValueError(f"ndarray 形状不支持: {bars.shape}")
    if hasattr(bars, "__iter__"):
        # list[BarData] / list[dict]
        values = []
        for b in bars:
            if hasattr(b, "close_price"):
                values.append(b.close_price)
            elif hasattr(b, "close"):
                values.append(b.close)
            elif isinstance(b, dict):
                values.append(b["close"])
            else:
                raise ValueError(f"无法从 {type(b).__name__} 提取 close")
        return pd.Series(values)
    raise TypeError(f"bars 类型不支持: {type(bars).__name__}")


def to_ohlc_dataframe(bars) -> "pd.DataFrame":
    """把 bars 归一成 pd.DataFrame (列: open/high/low/close/volume)

    支持输入:
        - pd.DataFrame: 验证列名, 缺则补 None
        - list[BarData] / list[dict]: 构造 DataFrame
        - np.ndarray 2D: 假设列序 [open,high,low,close,volume?]
    """
    import numpy as np
    import pandas as pd

    if isinstance(bars, pd.DataFrame):
        needed = {"open", "high", "low", "close"}
        if not needed.issubset(bars.columns):
            raise ValueError(f"DataFrame 缺 {needed - set(bars.columns)} 列")
        return bars
    if isinstance(bars, np.ndarray):
        if bars.ndim != 2 or bars.shape[1] < 4:
            raise ValueError(f"ndarray 形状不支持: {bars.shape}")
        cols = ["open", "high", "low", "close"] + (
            ["volume"] if bars.shape[1] >= 5 else []
        )
        return pd.DataFrame(bars[:, : len(cols)], columns=cols)
    if hasattr(bars, "__iter__"):
        rows = []
        for b in bars:
            if hasattr(b, "open_price"):
                rows.append({
                    "open": b.open_price,
                    "high": b.high_price,
                    "low": b.low_price,
                    "close": b.close_price,
                    "volume": getattr(b, "volume", 0),
                })
            elif isinstance(b, dict):
                rows.append({
                    "open": b.get("open"),
                    "high": b.get("high"),
                    "low": b.get("low"),
                    "close": b.get("close"),
                    "volume": b.get("volume", 0),
                })
            else:
                raise ValueError(f"无法从 {type(b).__name__} 提取 OHLC")
        return pd.DataFrame(rows)
    raise TypeError(f"bars 类型不支持: {type(bars).__name__}")


__all__ = [
    "Indicator",
    "IndicatorResult",
    "IndicatorRegistry",
    "to_close_series",
    "to_ohlc_dataframe",
]
