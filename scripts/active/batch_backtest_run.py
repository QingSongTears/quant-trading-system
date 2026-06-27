#!/usr/bin/env python3
"""
批量回测脚本 — 50支代表性股票 × 3个核心策略
直接使用 BacktestEngine，结果写入 quant.db
"""
import sys
sys.path.insert(0, 'D:/gitHub/qunat/quant-trading-system')

import importlib
import json
import time
import logging
from datetime import date
from sqlalchemy import text
from src.db.engine import get_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

from src.config import get_config, get_db_url
from src.backtest.engine import BacktestEngine
from src.models.repository import DataRepository
from src.db.sql_utils import read_sql


# ── 配置 ─────────────────────────────────────────
STRATEGIES = [
    (6, 'src.strategies.macd_signal.MACDSignalStrategy'),
    (5, 'src.strategies.ma_cross.MACrossStrategy'),
    (3, 'src.strategies.bollinger_breakout.BollingerBreakoutStrategy'),
]

START_DATE = date(2024, 6, 1)
END_DATE   = date(2026, 6, 16)
INITIAL_CAPITAL = 100_000

# ── 选股：按行业分层抽样 ──────────────────────────
def pick_stocks(engine, n=50):
    """从 data_source_meta 按行业分层取代表性股票"""
    df = read_sql("""
        SELECT d.code, s.name, s.industry
        FROM daily_price d
        JOIN stock_basic s ON d.code = s.code
        WHERE d.trade_date >= '2024-06-01'
        GROUP BY d.code
        HAVING COUNT(*) >= 120
    """, engine)
    
    if df.empty:
        # fallback: pick stocks with most data
        df = read_sql("""
            SELECT code, NULL as name, NULL as industry
            FROM daily_price
            WHERE trade_date >= '2024-06-01'
            GROUP BY code HAVING COUNT(*) >= 120
            LIMIT 50
        """, engine)
        return df['code'].tolist()
    
    # 按行业分层 + 每个行业取代码最小的
    codes = set()
    # 先跨行业
    if 'industry' in df.columns and df['industry'].notna().any():
        for ind, grp in df.groupby('industry'):
            if ind and ind not in ('', None, 'nan', 'None', '—'):
                row = grp.iloc[0]
                codes.add(row['code'])
                if len(codes) >= n:
                    break
    
    # 不够的随机补
    if len(codes) < n:
        remaining = [c for c in df['code'].tolist() if c not in codes]
        for c in remaining:
            codes.add(c)
            if len(codes) >= n:
                break
    
    return list(codes)

# ── 运行回测 ─────────────────────────────────────
def run_batch():
    config = get_config()
    db_url = get_db_url(config)
    engine = get_engine()
    
    codes = pick_stocks(engine, n=20)
    logger.info(f"入选股票: {len(codes)} 只")
    logger.info(f"策略数: {len(STRATEGIES)} 个")
    logger.info(f"预计回测数: {len(codes) * len(STRATEGIES)} 个")
    
    bt_engine = BacktestEngine()
    repo = DataRepository()
    
    results = []
    total = len(codes) * len(STRATEGIES)
    done = 0
    
    for code in codes:
        for sid, class_path in STRATEGIES:
            done += 1
            module_path, class_name = class_path.rsplit('.', 1)
            strategy_name = class_name
            
            try:
                mod = importlib.import_module(module_path)
                strat_cls = getattr(mod, class_name)
                
                report = bt_engine.run(
                    strategy_class=strat_cls,
                    stock_code=code,
                    start_date=START_DATE,
                    end_date=END_DATE,
                    initial_capital=INITIAL_CAPITAL,
                )
                
                # 写入 DB
                db_dict = report.to_db_dict(sid)
                # 插入 stock_name
                db_dict['stock_name'] = report.stock_name
                
                results.append(db_dict)
                
                status = f"[{done}/{total}] {report.stock_name}({code}) {strategy_name}: "
                if report.total_return is not None:
                    status += f"收益={report.total_return:+.2f}% "
                if report.sharpe_ratio is not None:
                    status += f"夏普={report.sharpe_ratio:.2f} "
                if report.total_trades is not None:
                    status += f"交易={report.total_trades}次"
                logger.info(status)
                
                # 每20条批量写入一次
                if len(results) >= 20:
                    _flush_results(engine, results)
                    results.clear()
                
            except Exception as e:
                logger.error(f"[{done}/{total}] {code} {strategy_name}: {e}")
                # 记一条失败记录
                results.append({
                    'strategy_id': sid,
                    'stock_code': code,
                    'stock_name': '',
                    'start_date': START_DATE,
                    'end_date': END_DATE,
                    'initial_capital': INITIAL_CAPITAL,
                    'final_equity': 0,
                    'total_return': None,
                    'annual_return': None,
                    'sharpe_ratio': None,
                    'max_drawdown': None,
                    'win_rate': None,
                    'profit_factor': None,
                    'total_trades': 0,
                    'annual_volatility': None,
                    'calmar_ratio': None,
                    'benchmark_return': None,
                    'excess_return': None,
                    'equity_curve': '[]',
                    'trades_detail': '[]',
                    'monthly_returns': '{}',
                    'cost_config': '{}',
                })
                continue
            
            time.sleep(0.05)  # 微延时，避免 CPU 打满
    
    # 写入剩余
    if results:
        _flush_results(engine, results)
    
    logger.info("=" * 50)
    logger.info("批量回测完成！")
    _print_summary(engine)


def _flush_results(engine, results):
    """批量插入 backtest_result 表"""
    from datetime import datetime
    for r in results:
        cols = list(r.keys()) + ['created_at']
        vals = list(r.values()) + [datetime.now()]
        placeholders = ','.join([':' + c for c in cols])
        col_names = ','.join(cols)
        sql = f'INSERT OR REPLACE INTO backtest_result ({col_names}) VALUES ({placeholders})'
        with engine.begin() as conn:
            conn.execute(text(sql), dict(zip(cols, vals)))


def _print_summary(engine):
    """打印汇总"""
    summary = read_sql('''
        SELECT sc.name, COUNT(*) as cnt, 
               ROUND(AVG(br.total_return), 2) as avg_ret,
               ROUND(AVG(br.sharpe_ratio), 2) as avg_sharpe,
               ROUND(AVG(br.win_rate), 1) as avg_wr
        FROM backtest_result br
        JOIN strategy_config sc ON br.strategy_id = sc.id
        GROUP BY sc.name
        ORDER BY AVG(br.total_return) DESC
    ''', engine)
    logger.info("\n=== 策略汇总 ===")
    logger.info(summary.to_string())


if __name__ == '__main__':
    run_batch()
