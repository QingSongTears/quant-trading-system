#!/usr/bin/env python3.11
"""
并行下载资金流历史数据
同时运行 N 个 asfund 进程（不同日期），2分钟=5天
比串行快5倍
"""
import subprocess, csv, time, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# 配置
CODES_FILE = "/tmp/stock_profile.csv"
OUTPUT = "/workspace/quant-trading-system/market_data/fund_flow_120d.csv"
PARALLEL = 10  # 同时跑10个日期，速度拉满

# 读取股票代码
codes = []
with open(CODES_FILE) as f:
    next(csv.reader(f))
    for row in csv.reader(f):
        if row and row[0].strip(): codes.append(row[0].strip())

CODES_STR = ",".join(codes)
print(f"股票: {len(codes)}", flush=True)

# 读取已有数据，确定已下载的日期
existing_dates = set()
if os.path.exists(OUTPUT):
    with open(OUTPUT, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) >= 4:
                existing_dates.add(row[3])

# 生成所有交易日历（2023-01 ~ 2024-01）
all_dates = []
dt = datetime(2023, 1, 3)
end = datetime(2024, 1, 31)
while dt <= end:
    ds = dt.strftime("%Y-%m-%d")
    if dt.weekday() < 5 and ds not in existing_dates:
        all_dates.append(ds)
    dt += timedelta(days=1)

print(f"待下载: {len(all_dates)} 天", flush=True)
total = len(all_dates)

def download_day(date_str):
    """下载某一天的资金流数据"""
    cmd = f"NODE_OPTIONS='--no-warnings' npx -y westock-data-clawhub@1.0.4 asfund '{CODES_STR}' --date {date_str}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        hd = None; rows = []; in_t = False
        for l in lines:
            if l.startswith("| code ") or l.startswith("| symbol "):
                hd = [h.strip() for h in l.split("|")[1:-1]]; in_t = True; continue
            if "---" in l: continue
            if in_t and l.startswith("|"):
                vs = [v.strip() for v in l.split("|")[1:-1]]
                if len(vs) == len(hd): rows.append(dict(zip(hd, vs)))
        return date_str, rows
    except Exception as e:
        return date_str, []

# 并行下载: 每批5天
done_count = 0
t0 = time.time()

for batch_start in range(0, len(all_dates), PARALLEL):
    batch = all_dates[batch_start:batch_start + PARALLEL]
    
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        futures = {pool.submit(download_day, d): d for d in batch}
        results = []
        for f in as_completed(futures):
            ds, rows = f.result()
            results.append((ds, rows))
    
    # 写入结果
    for ds, rows in results:
        if rows:
            with open(OUTPUT, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                for r in rows:
                    c = r.get("code", r.get("symbol", ""))
                    if not c: continue
                    m = c[:2] if len(c) >= 2 else ""
                    w.writerow([c, m, "", ds,
                        r.get("MainNetFlow","0"), r.get("JumboNetFlow","0"),
                        r.get("MainInFlow","0"), r.get("MidNetFlow","0"),
                        r.get("SmallNetFlow","0"), c])
    
    done_count += len(batch)
    elapsed = time.time() - t0
    avg_per_min = done_count / elapsed * 60 if elapsed > 0 else 0
    remain_min = (total - done_count) / avg_per_min if avg_per_min > 0 else 0
    print(f"[{done_count}/{total}] {batch[0]}~{batch[-1]} | 速率:{avg_per_min:.0f}天/分 | 剩余:{remain_min:.0f}分", flush=True)

print(f"\n✅ 全部完成! 总耗时: {(time.time()-t0)/60:.1f}分", flush=True)
