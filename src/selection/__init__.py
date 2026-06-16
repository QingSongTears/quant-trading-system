"""
选股模块 — 参数化多维度选股管线

用法:
    from src.selection import SelectionPipeline

    # 技术面独立选股
    pipe = SelectionPipeline(dimensions=["technical"])
    df = pipe.run("2026-06-15", top_n=20)

    # 三因子选股
    pipe = SelectionPipeline(
        dimensions=["technical", "fundamental", "fund_flow"],
        weight_preset="value"
    )
    df = pipe.run("2026-06-15", top_n=30)
"""
from .pipeline import SelectionPipeline, FilterConfig

__all__ = ["SelectionPipeline", "FilterConfig"]
