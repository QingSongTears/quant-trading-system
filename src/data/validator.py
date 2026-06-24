"""
数据质量校验工具
================

提供数据完整性、一致性和异常检测功能:
- check_coverage: 数据覆盖率检查
- check_gaps: 缺失日期段检测
- check_nulls: 关键字段空值率检查
- check_price_anomalies: 价格异常检测 (close=0, 涨跌幅超阈值)
- fill_pct_change: 从 close 价格自动计算并回填 pct_change

用法:
    from src.data.validator import DataValidator
    v = DataValidator()
    report = v.run_all()
    v.print_report(report)

    # 或单独使用
    v.check_coverage()
    v.fill_pct_change()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from sqlalchemy import text

from ..config import get_config, get_db_url
from ..models.repository import DataRepository

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    """数据校验报告"""
    coverage: dict = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)
    nulls: dict = field(default_factory=dict)
    anomalies: list[dict] = field(default_factory=list)
    pct_change_status: str = ""
    summary: list[str] = field(default_factory=list)

    def add_issue(self, msg: str):
        self.summary.append(msg)


class DataValidator:
    """数据质量校验器"""

    def __init__(self):
        self.repo = DataRepository()
        config = get_config()
        from sqlalchemy import create_engine
        self.engine = create_engine(get_db_url(config), echo=False)

    def check_coverage(self) -> dict:
        """
        检查数据覆盖率:
        - 股票总数
        - 日线记录总数
        - 数据日期区间
        - 基准数据行数
        - 有数据的股票占比
        """
        result = {}
        with self.engine.connect() as conn:
            result["total_stocks"] = conn.execute(
                text("SELECT COUNT(*) FROM stock_basic")
            ).scalar() or 0

            result["total_daily_records"] = conn.execute(
                text("SELECT COUNT(*) FROM daily_price")
            ).scalar() or 0

            date_range = conn.execute(
                text("SELECT MIN(trade_date), MAX(trade_date) FROM daily_price")
            ).fetchone()
            result["date_min"] = str(date_range[0]) if date_range[0] else None
            result["date_max"] = str(date_range[1]) if date_range[1] else None

            result["benchmark_rows"] = conn.execute(
                text("SELECT COUNT(*) FROM benchmark_data")
            ).scalar() or 0

            # 有日线数据的股票数
            stocks_with_data = conn.execute(
                text("SELECT COUNT(DISTINCT code) FROM daily_price")
            ).scalar() or 0
            result["stocks_with_data"] = stocks_with_data
            if result["total_stocks"] > 0:
                result["coverage_pct"] = round(
                    stocks_with_data / result["total_stocks"] * 100, 1
                )
            else:
                result["coverage_pct"] = 0

        return result

    def check_gaps(self, min_gap_days: int = 5) -> list[dict]:
        """
        检测缺失日期段（交易日连续缺失 >= min_gap_days 天）

        注: 排除周末和节假日，只检测异常的交易日缺失。
        简化方案: 检测同一只股票内相邻 trade_date 间隔 > min_gap_days 的情况。
        """
        gaps = []
        sql = text("""
            SELECT code, trade_date AS prev_date,
                   (SELECT MIN(dp2.trade_date)
                    FROM daily_price dp2
                    WHERE dp2.code = dp.code AND dp2.trade_date > dp.trade_date
                   ) AS next_date
            FROM daily_price dp
            GROUP BY code, trade_date
            HAVING next_date IS NOT NULL
              AND julianday(next_date) - julianday(prev_date) > :min_gap
            ORDER BY code, prev_date
            LIMIT 100
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"min_gap": min_gap_days}).fetchall()
            for row in rows:
                gaps.append({
                    "code": row[0],
                    "prev_date": str(row[1]),
                    "next_date": str(row[2]),
                    "gap_days": (row[2] - row[1]).days if hasattr(row[2] - row[1], 'days') else None,
                })
        return gaps

    def check_nulls(self) -> dict:
        """
        检查关键字段的空值率

        检查字段: open, high, low, close, volume, pct_change
        """
        result = {}
        fields = ["open", "high", "low", "close", "volume", "pct_change"]
        with self.engine.connect() as conn:
            total = conn.execute(
                text("SELECT COUNT(*) FROM daily_price")
            ).scalar() or 0
            result["total_rows"] = total

            if total == 0:
                for f in fields:
                    result[f] = {"null_count": 0, "null_pct": 0}
                return result

            for f in fields:
                null_count = conn.execute(
                    text(f"SELECT COUNT(*) FROM daily_price WHERE {f} IS NULL")
                ).scalar() or 0
                result[f] = {
                    "null_count": null_count,
                    "null_pct": round(null_count / total * 100, 2),
                }

        return result

    def check_price_anomalies(self) -> list[dict]:
        """
        检测价格异常:
        - close <= 0
        - 单日涨跌幅 > 11%（主板正常涨停 10%，超过说明数据可能有误）
        - volume < 0
        """
        anomalies = []
        with self.engine.connect() as conn:
            # close <= 0
            rows = conn.execute(text("""
                SELECT code, trade_date, close, 'close_le_zero' AS issue
                FROM daily_price
                WHERE close <= 0
                LIMIT 50
            """)).fetchall()
            for row in rows:
                anomalies.append({
                    "code": row[0], "date": str(row[1]),
                    "value": row[2], "issue": row[3],
                })

            # pct_change 绝对值 > 11%
            rows = conn.execute(text("""
                SELECT code, trade_date, pct_change, 'extreme_pct_change' AS issue
                FROM daily_price
                WHERE pct_change IS NOT NULL AND ABS(pct_change) > 11
                LIMIT 50
            """)).fetchall()
            for row in rows:
                anomalies.append({
                    "code": row[0], "date": str(row[1]),
                    "value": row[2], "issue": row[3],
                })

            # volume < 0
            rows = conn.execute(text("""
                SELECT code, trade_date, volume, 'negative_volume' AS issue
                FROM daily_price
                WHERE volume < 0
                LIMIT 50
            """)).fetchall()
            for row in rows:
                anomalies.append({
                    "code": row[0], "date": str(row[1]),
                    "value": row[2], "issue": row[3],
                })

        return anomalies

    def fill_pct_change(self, batch_size: int = 10000) -> dict:
        """
        从 close 价格自动计算并回填 daily_price.pct_change

        公式: pct_change = (close_today / close_prev - 1) * 100

        只更新 pct_change IS NULL 的行。
        分批更新，避免一次性大事务。
        """
        updated = 0
        with self.engine.begin() as conn:
            # 获取所有有数据的股票代码
            codes = [row[0] for row in conn.execute(
                text("SELECT DISTINCT code FROM daily_price WHERE pct_change IS NULL")
            ).fetchall()]

            if not codes:
                return {"updated": 0, "message": "所有 pct_change 已有值，无需更新"}

            logger.info(f"需要更新 pct_change 的股票: {len(codes)} 只")

            for i, code in enumerate(codes):
                # 获取该股票所有日线数据，按日期排序
                rows = conn.execute(text("""
                    SELECT id, trade_date, close
                    FROM daily_price
                    WHERE code = :code
                    ORDER BY trade_date ASC
                """), {"code": code}).fetchall()

                if len(rows) < 2:
                    continue

                # 计算 pct_change
                updates = []
                prev_close = None
                for row in rows:
                    row_id, trade_date, close = row
                    if prev_close and prev_close > 0 and close:
                        pct = round((close / prev_close - 1) * 100, 4)
                        updates.append((pct, row_id))
                    prev_close = close

                # 批量更新
                for pct, row_id in updates:
                    conn.execute(
                        text("UPDATE daily_price SET pct_change = :pct WHERE id = :id"),
                        {"pct": pct, "id": row_id},
                    )
                updated += len(updates)

                if (i + 1) % 500 == 0:
                    logger.info(f"pct_change 回填进度: {i + 1}/{len(codes)} 只股票")

        msg = f"pct_change 回填完成: 更新 {updated} 行"
        logger.info(msg)
        return {"updated": updated, "message": msg}

    def run_all(self) -> ValidationReport:
        """运行所有检查，返回汇总报告"""
        report = ValidationReport()

        logger.info("开始数据质量检查...")

        # 1. 覆盖率
        report.coverage = self.check_coverage()
        if report.coverage["benchmark_rows"] == 0:
            report.add_issue("benchmark_data 表为空（0 行），需运行 python scripts/import_benchmark_akshare.py")
        if report.coverage["coverage_pct"] < 90:
            report.add_issue(
                f"数据覆盖率仅 {report.coverage['coverage_pct']}% "
                f"({report.coverage['stocks_with_data']}/{report.coverage['total_stocks']})"
            )

        # 2. 空值率
        report.nulls = self.check_nulls()
        pct_null = report.nulls.get("pct_change", {}).get("null_pct", 0)
        if pct_null > 50:
            report.add_issue(f"pct_change 空值率 {pct_null}%，建议运行 fill_pct_change()")
        for f in ["open", "high", "low", "close"]:
            if report.nulls.get(f, {}).get("null_pct", 0) > 1:
                report.add_issue(f"字段 {f} 空值率 {report.nulls[f]['null_pct']}%，可能存在数据问题")

        # 3. 价格异常
        report.anomalies = self.check_price_anomalies()
        if report.anomalies:
            report.add_issue(f"发现 {len(report.anomalies)} 条价格异常记录")

        # 4. pct_change 状态
        if pct_null > 50:
            report.pct_change_status = f"需回填 ({pct_null}% 空值)"
        else:
            report.pct_change_status = "正常"

        logger.info(f"数据质量检查完成: {len(report.summary)} 个问题")
        return report

    @staticmethod
    def print_report(report: ValidationReport):
        """打印可读报告"""
        print("\n" + "=" * 60)
        print("数据质量检查报告")
        print("=" * 60)

        c = report.coverage
        print(f"\n[覆盖率]")
        print(f"  股票总数:       {c.get('total_stocks', 'N/A')}")
        print(f"  有数据的股票:   {c.get('stocks_with_data', 'N/A')} ({c.get('coverage_pct', 0)}%)")
        print(f"  日线记录总数:   {c.get('total_daily_records', 0):,}")
        print(f"  数据日期区间:   {c.get('date_min', 'N/A')} ~ {c.get('date_max', 'N/A')}")
        print(f"  基准数据行数:   {c.get('benchmark_rows', 0):,}")

        print(f"\n[空值率]")
        for f in ["open", "high", "low", "close", "volume", "pct_change"]:
            info = report.nulls.get(f, {})
            pct = info.get("null_pct", 0)
            cnt = info.get("null_count", 0)
            marker = " ***" if pct > 1 else ""
            print(f"  {f:15s}  {cnt:>10,} 行 ({pct:>5.1f}%){marker}")

        print(f"\n[价格异常]")
        if report.anomalies:
            for a in report.anomalies[:10]:
                print(f"  {a['code']} {a['date']} {a['issue']}: {a['value']}")
            if len(report.anomalies) > 10:
                print(f"  ... 还有 {len(report.anomalies) - 10} 条")
        else:
            print("  无异常")

        print(f"\n[pct_change 状态] {report.pct_change_status}")

        if report.summary:
            print(f"\n[问题汇总]")
            for i, msg in enumerate(report.summary, 1):
                print(f"  {i}. {msg}")
        else:
            print(f"\n  所有检查通过")

        print("=" * 60)
