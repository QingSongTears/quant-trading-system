"""
批量拉上交所总股本/流通股本 — 巨潮 cninfo
带超时 + 并发线程池
"""
import akshare as ak
import csv
import time
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("share_sh")

OUT_PATH = Path("E:/work/work/quant-trading-system/market_data/share_structure.csv")


def fetch_one_cninfo(code6: str) -> tuple[str, str, str]:
    """巨潮查一只股票的 (code6, totalShare, liqaShare)，单位万股→股"""
    try:
        df = ak.stock_share_change_cninfo(
            symbol=code6,
            start_date="20200101",
            end_date="20251231"
        )
        if len(df) > 0:
            latest = df.iloc[-1]
            total_wan = latest.get("总股本")
            liqa_wan = latest.get("已流通股份")
            total = ""
            liqa = ""
            if total_wan and str(total_wan) != "nan":
                try:
                    total = f"{float(total_wan) * 10000:.0f}"
                except (ValueError, TypeError):
                    pass
            if liqa_wan and str(liqa_wan) != "nan":
                try:
                    liqa = f"{float(liqa_wan) * 10000:.0f}"
                except (ValueError, TypeError):
                    pass
            return (code6, total, liqa)
    except Exception as e:
        logger.debug("  cninfo 失败 %s: %s", code6, str(e)[:60])
    return (code6, "", "")


def fetch_sh_parallel(codes: list[tuple[str, str]], workers: int = 5) -> list[dict]:
    """并发查上交所"""
    rows = []
    completed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one_cninfo, code6): (code6, name) for code6, name in codes}

        for future in as_completed(futures):
            code6, name = futures[future]
            try:
                _, total, liqa = future.result(timeout=30)
            except Exception:
                total, liqa = "", ""

            bs_code = f"sh.{code6}"
            rows.append({
                "code": bs_code,
                "code6": code6,
                "name": name,
                "totalShare": total,
                "liqaShare": liqa,
            })
            completed += 1
            if completed % 100 == 0:
                elapsed = time.time() - t0
                rate = completed / elapsed if elapsed else 0
                eta = (len(codes) - completed) / rate if rate else 0
                filled = sum(1 for r in rows[-100:] if r["totalShare"])
                logger.info("  %d/%d  累计填充:%d  %.1f只/s  ETA %.1fmin",
                            completed, len(codes), filled, rate, eta / 60)

    return rows


def main():
    # 1. 深交所
    logger.info("=== 深交所 ===")
    sz_rows = []
    try:
        df = ak.stock_info_sz_name_code(symbol="A股列表")
        logger.info("深交所: %d 只", len(df))
        for _, r in df.iterrows():
            code6 = str(r.get("A股代码", "")).strip()
            if not code6 or not code6.isdigit():
                continue
            total_str = str(r.get("A股总股本", "")).replace(",", "").strip()
            liqa_str = str(r.get("A股流通股本", "")).replace(",", "").strip()
            sz_rows.append({
                "code": f"sz.{code6}",
                "code6": code6,
                "name": str(r.get("A股简称", "")).strip(),
                "totalShare": total_str if total_str and total_str != "nan" else "",
                "liqaShare": liqa_str if liqa_str and liqa_str != "nan" else "",
            })
    except Exception as e:
        logger.error("深交所失败: %s", str(e)[:200])

    # 2. 北交所
    logger.info("=== 北交所 ===")
    bj_rows = []
    try:
        df = ak.stock_info_bj_name_code()
        logger.info("北交所: %d 只", len(df))
        for _, r in df.iterrows():
            code6 = str(r.get("证券代码", "")).strip()
            if not code6 or not code6.isdigit():
                continue
            total = r.get("总股本", "")
            liqa = r.get("流通股本", "")
            bj_rows.append({
                "code": f"bj.{code6}",
                "code6": code6,
                "name": str(r.get("证券简称", "")).strip(),
                "totalShare": f"{int(total)}" if total and str(total) != "nan" else "",
                "liqaShare": f"{int(liqa)}" if liqa and str(liqa) != "nan" else "",
            })
    except Exception as e:
        logger.error("北交所失败: %s", str(e)[:200])

    # 3. 上交所（巨潮并发）
    logger.info("=== 上交所 (巨潮 cninfo, 5并发) ===")
    sh_codes = []
    for symbol in ["主板A股", "科创板"]:
        try:
            df = ak.stock_info_sh_name_code(symbol=symbol)
            for _, r in df.iterrows():
                code6 = str(r.get("证券代码", "")).strip()
                if code6 and code6.isdigit():
                    sh_codes.append((code6, str(r.get("证券简称", "")).strip()))
        except Exception as e:
            logger.error("上交所 %s 列表失败: %s", symbol, str(e)[:200])
    logger.info("上交所: %d 只", len(sh_codes))

    sh_rows = fetch_sh_parallel(sh_codes, workers=5)

    # 合并
    all_rows = sz_rows + bj_rows + sh_rows
    logger.info("=" * 60)
    logger.info("总计: %d 条 (深:%d + 北:%d + 沪:%d)", len(all_rows), len(sz_rows), len(bj_rows), len(sh_rows))
    t_filled = sum(1 for r in all_rows if r["totalShare"])
    l_filled = sum(1 for r in all_rows if r["liqaShare"])
    if all_rows:
        logger.info("totalShare 填充: %d/%d (%.1f%%)", t_filled, len(all_rows), t_filled / len(all_rows) * 100)
        logger.info("liqaShare 填充: %d/%d (%.1f%%)", l_filled, len(all_rows), l_filled / len(all_rows) * 100)

    # 保存
    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "code6", "name", "totalShare", "liqaShare"])
        w.writeheader()
        w.writerows(all_rows)
    logger.info("保存到: %s", OUT_PATH)


if __name__ == "__main__":
    main()
