"""
历史数据下载引擎
================

数据来源:
  - 主数据源: AKShare (akshare.readthedocs.io) — 东方财富/新浪财经公开接口
  - 补充数据源: WeStock Data (腾讯自选股接口) — 实时行情 & 技术指标
  - 备选数据源: Baostock (baostock.com) — 免费证券数据

所有下载的数据均为交易所公开行情数据，未经任何修改或模拟。
"""
from __future__ import annotations
import time
import logging
from datetime import date, datetime
from typing import Callable

import akshare as ak
import pandas as pd
from sqlalchemy.orm import Session
from tqdm import tqdm

from ..config import get_config
from ..models.repository import DataRepository

logger = logging.getLogger(__name__)


class DataDownloader:
    """
    数据下载引擎
    
    支持三种下载模式:
    1. 全量下载: 下载全市场近N年所有日线数据
    2. 增量更新: 仅下载数据库中缺失的最新数据
    3. 单股下载: 下载指定股票的日线数据
    """

    # 市场前缀映射
    MARKET_PREFIX = {
        "SH": "sh", "SZ": "sz", "BJ": "bj"
    }

    def __init__(self):
        config = get_config()
        self.repo = DataRepository()
        self.start_date = config["data"]["download"]["start_date"]
        self.interval = config["data"]["download"]["request_interval"]
        self.max_retries = config["data"]["download"]["max_retries"]
        self.timeout = config["data"]["download"]["timeout"]
        self.markets = config["data"]["markets"]
        self.progress_callback: Callable | None = None

    # ===== 股票列表获取 =====

    def fetch_stock_list(self) -> pd.DataFrame:
        """
        获取全市场A股列表
        数据来源: AKShare stock_info_a_code_name() → 东方财富
        """
        logger.info("正在获取全市场A股列表...")
        try:
            df = ak.stock_info_a_code_name()
            df.columns = ["code", "name"]

            # 判断市场
            def _get_market(c):
                if c.startswith("6"):
                    return "SH"
                elif c.startswith("0") or c.startswith("3"):
                    return "SZ"
                elif c.startswith("4") or c.startswith("8"):
                    return "BJ"
                return "OTHER"

            df["market"] = df["code"].apply(_get_market)
            df = df[df["market"] != "OTHER"]
            logger.info(f"获取到 {len(df)} 只股票")
            return df
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            raise

    # ===== 全量下载 =====

    def download_full(self, progress_callback: Callable | None = None) -> dict:
        """
        全量下载全市场历史日线数据
        
        流程:
        1. 获取全市场股票列表
        2. 逐只下载近N年日线数据
        3. 写入 SQLite 数据库
        4. 下载沪深300基准数据
        5. 输出完整性校验报告
        """
        self.progress_callback = progress_callback
        config = get_config()
        start = config["data"]["download"]["start_date"]
        end = date.today().strftime("%Y%m%d")

        # Step 1: 获取股票列表
        stock_df = self.fetch_stock_list()
        total = len(stock_df)

        # Step 2: 初始化数据库
        self.repo.init_database()

        session = self.repo.get_session()
        failed_list = []
        success_count = 0

        try:
            # 先写入所有股票基本信息
            for _, row in stock_df.iterrows():
                self.repo.upsert_stock_basic(
                    session, row["code"], row["name"], row["market"]
                )
            session.commit()

            # Step 3: 逐只下载日线数据
            logger.info(f"开始下载全市场日线数据，共 {total} 只股票...")

            for idx, (_, row) in enumerate(stock_df.iterrows()):
                code = row["code"]
                name = row["name"]

                try:
                    records = self._download_stock_history(code, start, end)
                    if records:
                        self._batch_write_daily(session, records)
                        success_count += 1
                except Exception as e:
                    failed_list.append({"code": code, "name": name, "error": str(e)})
                    logger.warning(f"下载失败 [{code} {name}]: {e}")

                # 进度回调
                if progress_callback:
                    progress_callback(idx + 1, total, code, name)

                # 请求间隔
                if idx < total - 1:
                    time.sleep(self.interval)

                # 每 100 只提交一次
                if (idx + 1) % 100 == 0:
                    session.commit()
                    logger.info(f"进度: {idx + 1}/{total} ({(idx + 1) / total * 100:.1f}%)")

            session.commit()

            # Step 4: 下载基准数据
            self._download_benchmark(session)

            # Step 5: 记录下载元信息
            self.repo.save_download_meta(session, {
                "source_name": "AKShare",
                "download_time": datetime.now(),
                "date_start": datetime.strptime(start, "%Y%m%d").date(),
                "date_end": date.today(),
                "stock_count": total,
                "record_count": self.repo.get_data_coverage()["total_records"],
                "status": "completed",
                "error_log": str(failed_list) if failed_list else None
            })
            session.commit()

        except Exception as e:
            session.rollback()
            logger.error(f"全量下载异常: {e}")
            raise
        finally:
            session.close()

        # Step 6: 输出统计
        result = {
            "total_stocks": total,
            "success_count": success_count,
            "failed_count": len(failed_list),
            "failed_list": failed_list[:20],  # 最多显示 20 条
            "coverage": success_count / total * 100 if total > 0 else 0,
        }

        logger.info(f"下载完成: 成功 {success_count}/{total} ({result['coverage']:.1f}%)")
        return result

    # ===== 增量更新 =====

    def download_incremental(self, progress_callback: Callable | None = None) -> dict:
        """
        增量更新: 仅下载每只股票缺失的最新交易日数据
        """
        self.progress_callback = progress_callback
        stock_df = self.fetch_stock_list()
        total = len(stock_df)

        session = self.repo.get_session()
        updated_count = 0
        failed_list = []

        try:
            for idx, (_, row) in enumerate(stock_df.iterrows()):
                code = row["code"]
                latest = self.repo.get_latest_date(code)

                if latest:
                    start_date_str = (latest + pd.Timedelta(days=1)).strftime("%Y%m%d")
                else:
                    start_date_str = get_config()["data"]["download"]["start_date"]

                end_date_str = date.today().strftime("%Y%m%d")

                # 如果起始日期 >= 今天，跳过
                if datetime.strptime(start_date_str, "%Y%m%d").date() >= date.today():
                    continue

                try:
                    records = self._download_stock_history(code, start_date_str, end_date_str)
                    if records:
                        self._batch_write_daily(session, records)
                        updated_count += 1
                except Exception as e:
                    failed_list.append({"code": code, "name": row["name"], "error": str(e)})

                if progress_callback:
                    progress_callback(idx + 1, total, code, row["name"])

                if idx < total - 1:
                    time.sleep(self.interval)

                if (idx + 1) % 100 == 0:
                    session.commit()

            session.commit()

            self.repo.save_download_meta(session, {
                "source_name": "AKShare",
                "download_time": datetime.now(),
                "date_end": date.today(),
                "stock_count": updated_count,
                "status": "completed"
            })
            session.commit()

        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

        return {
            "updated_count": updated_count,
            "failed_count": len(failed_list),
            "failed_list": failed_list[:20]
        }

    # ===== 内部方法 =====

    def _download_stock_history(self, code: str, start: str, end: str) -> list[dict]:
        """
        下载单只股票历史日线数据
        数据来源: AKShare stock_zh_a_hist(symbol, period, start_date, end_date, adjust)
        返回: [{code, trade_date, open, high, low, close, volume, amount, pct_change, turnover}, ...]
        """
        for attempt in range(self.max_retries):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start,
                    end_date=end,
                    adjust="qfq"  # 前复权
                )

                if df is None or df.empty:
                    return []

                records = []
                for _, row in df.iterrows():
                    records.append({
                        "code": code,
                        "trade_date": pd.Timestamp(row["日期"]).date(),
                        "open": float(row["开盘"]),
                        "high": float(row["最高"]),
                        "low": float(row["最低"]),
                        "close": float(row["收盘"]),
                        "volume": int(row["成交量"]),
                        "amount": float(row["成交额"]),
                        "pct_change": float(row["涨跌幅"]) if pd.notna(row.get("涨跌幅")) else None,
                        "turnover": float(row["换手率"]) if pd.notna(row.get("换手率")) else None,
                    })

                return records

            except Exception as e:
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    raise e

        return []

    def _batch_write_daily(self, session: Session, records: list[dict]):
        """批量写入日线数据（使用 OR 忽略重复）"""
        if not records:
            return
        self.repo.batch_insert_daily(session, records)

    def _download_benchmark(self, session: Session):
        """
        下载沪深300基准数据
        数据来源: AKShare stock_zh_index_daily(symbol="sh000300")
        """
        logger.info("下载沪深300基准数据...")
        try:
            df = ak.stock_zh_index_daily(symbol="sh000300")
            records = []
            for _, row in df.iterrows():
                records.append({
                    "index_code": "sh000300",
                    "trade_date": pd.Timestamp(row["date"]).date(),
                    "close": float(row["close"]),
                    "pct_change": float(row.get("pct_chg", 0)) if pd.notna(row.get("pct_chg")) else None
                })
            self.repo.batch_insert_daily(session, records)
            logger.info(f"基准数据下载完成: {len(records)} 条")
        except Exception as e:
            logger.warning(f"基准数据下载失败: {e}")


def run_download(mode: str = "full", progress_callback=None) -> dict:
    """
    便捷下载入口
    mode: "full" | "incremental"
    """
    downloader = DataDownloader()

    if mode == "full":
        return downloader.download_full(progress_callback)
    elif mode == "incremental":
        return downloader.download_incremental(progress_callback)
    else:
        raise ValueError(f"未知下载模式: {mode}")
