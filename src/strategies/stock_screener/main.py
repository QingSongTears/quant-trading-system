#!/usr/bin/env python3
"""
A股波段选股系统 — 主入口
用法:
  python main.py              # 运行完整策略
  python main.py --quick      # 快速模式（减少数据拉取）
  python main.py --codes 600519,000858  # 指定代码检测
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# 确保项目路径
sys.path.insert(0, str(Path(__file__).parent))

from strategy import StockScreenerStrategy


def main():
    parser = argparse.ArgumentParser(description="A股波段选股系统")
    parser.add_argument("--quick", action="store_true", help="快速模式")
    parser.add_argument("--codes", type=str, help="指定股票代码（逗号分隔）")
    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════╗
║      A股波段选股策略 v0.1                      ║
║      EMA20/60 金叉 + 回踩买点 + 量化检测       ║
╚══════════════════════════════════════════════╝
    """)

    strategy = StockScreenerStrategy()
    result = strategy.run()

    if result is not None and not result.empty:
        print("\n✅ 策略运行完成!")
        print(f"📊 推荐 {len(result)} 只标的，请结合盘面判断入场时机")
    else:
        print("\n⚠️ 今日无符合条件的标的，继续等待机会")
        print("提示：市场情绪低迷时出信号少是正常的，保持耐心")


if __name__ == "__main__":
    main()
