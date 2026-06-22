#!/usr/bin/env python3.11
"""
更新资金流数据到指定日期
使用 WeStock asfund 获取指定日期的全市场A股资金流向
正确映射到原始 CSV 格式并追加数据
"""

import subprocess
import csv
import json
import time
from pathlib import Path
from datetime import datetime
import pandas as pd

# 配置
DATE = "2026-06-18"
REPO_ROOT = Path(__file__).parent.parent
STOCK_PROFILE = REPO_ROOT / "market_data" / "stock_profile.csv"
OUTPUT_CSV = REPO_ROOT / "market_data" / "fund_flow_120d.csv"
BATCH_SIZE = 500  # 每批股票数量

def get_stock_codes():
    """从 stock_profile.csv 提取所有股票代码"""
    codes = []
    with open(STOCK_PROFILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)  # 跳过表头
        for row in reader:
            if row and row[0].strip():
                codes.append(row[0].strip())
    return codes

def fetch_fund_flow_batch(stock_codes, date):
    """
    使用 WeStock asfund 获取指定日期的资金流数据
    返回解析后的记录列表
    """
    # 构建命令行参数（使用 shell=True 以识别 npx）
    codes_str = ",".join(stock_codes)
    cmd = f"NODE_OPTIONS='--no-warnings' npx -y westock-data-clawhub@1.0.4 asfund {codes_str} --date {date}"
    
    print(f"  执行命令: npx westock-data-clawhub asfund ({len(stock_codes)}只股票)...")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2分钟超时
            shell=True  # 使用 shell 以识别 npx
        )
        
        if result.returncode != 0:
            print(f"  ❌ 命令执行失败: {result.stderr}")
            return []
        
        # 解析 Markdown 表格输出
        lines = result.stdout.split('\n')
        records = []
        headers = None
        data_start = False
        
        for line in lines:
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 找到表头
            if line.startswith('| code |') or line.startswith('| symbol |'):
                # 提取表头（支持 code 或 symbol 作为第一列）
                headers = [h.strip() for h in line.split('|')[1:-1]]
                data_start = True
                continue
            
            # 跳过分隔行
            if '---' in line:
                continue
            
            # 解析数据行
            if data_start and line.startswith('|'):
                values = [v.strip() for v in line.split('|')[1:-1]]
                if len(values) == len(headers):
                    record = dict(zip(headers, values))
                    records.append(record)
        
        print(f"  ✅ 解析到 {len(records)} 条记录")
        return records
        
    except subprocess.TimeoutExpired:
        print(f"  ❌ 命令超时（120秒）")
        return []
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return []

def map_to_csv_format(records, date):
    """
    将 WeStock 数据映射到原始 CSV 格式
    
    原始 CSV 格式:
    code,market,name,date,main_net,super_large_net,large_net,medium_net,small_net,symbol
    
    WeStock 返回字段（已验证 2026-06-18）:
    - code: 股票代码（如 sz000001）
    - EndDate: 日期
    - MainNetFlow: 主力净流入（main_net）
    - JumboNetFlow: 超大单净流入（super_large_net）
    - MainInFlow: 大单净流入（large_net）【注意字段名】
    - MidNetFlow: 中单净流入（medium_net）
    - SmallNetFlow: 小单净流入（small_net）
    """
    mapped_records = []
    
    for record in records:
        # 获取股票代码（WeStock 使用 'code' 字段）
        code = record.get('code', '')
        if not code:
            continue
        
        # 判断市场
        if code.startswith('sh'):
            market = 'sh'
        elif code.startswith('sz'):
            market = 'sz'
        elif code.startswith('bj'):
            market = 'bj'
        else:
            market = ''
        
        # 映射字段（严格按照 WeStock 实际返回字段名）
        # 注意：只保留数值字段，不保留 JSON 字段（如 MarginTradeInfos）
        mapped = {
            'code': code,
            'market': market,
            'name': '',  # WeStock 不返回名称，保持为空
            'date': date,
            'main_net': record.get('MainNetFlow', '0'),
            'super_large_net': record.get('JumboNetFlow', '0'),
            'large_net': record.get('MainInFlow', '0'),  # 注意：是 MainInFlow 不是 MainNetFlow
            'medium_net': record.get('MidNetFlow', '0'),
            'small_net': record.get('SmallNetFlow', '0'),
            'symbol': ''  # 保持为空
        }
        
        mapped_records.append(mapped)
    
    return mapped_records

def main():
    print("=" * 60)
    print(f"更新资金流数据: {DATE}")
    print("=" * 60)
    
    # 1. 获取股票代码列表
    print("\n[1/4] 获取股票代码列表...")
    stock_codes = get_stock_codes()
    print(f"✅ 获取到 {len(stock_codes)} 只股票")
    
    if not stock_codes:
        print("❌ 错误：无法获取股票代码")
        return
    
    # 2. 分批获取数据
    print(f"\n[2/4] 分批获取数据（每批 {BATCH_SIZE} 只）...")
    all_records = []
    total_batches = (len(stock_codes) + BATCH_SIZE - 1) // BATCH_SIZE
    
    for i in range(0, len(stock_codes), BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch = stock_codes[i:i + BATCH_SIZE]
        
        print(f"\n  批次 {batch_num}/{total_batches}: 股票 {i+1}-{min(i+BATCH_SIZE, len(stock_codes))}")
        
        records = fetch_fund_flow_batch(batch, DATE)
        all_records.extend(records)
        
        # 避免请求过快
        if batch_num < total_batches:
            time.sleep(2)
    
    print(f"\n✅ 总共获取 {len(all_records)} 条记录")
    
    if not all_records:
        print("❌ 错误：未获取到任何数据")
        return
    
    # 3. 映射到 CSV 格式
    print("\n[3/4] 映射数据格式...")
    mapped_records = map_to_csv_format(all_records, DATE)
    print(f"✅ 映射完成：{len(mapped_records)} 条记录")
    
    # 4. 追加到 CSV 文件（使用 pandas 以正确处理格式）
    print(f"\n[4/4] 追加数据到 {OUTPUT_CSV}...")
    
    # 定义列名
    fieldnames = ['code', 'market', 'name', 'date', 'main_net', 'super_large_net', 'large_net', 'medium_net', 'small_net', 'symbol']
    
    # 将新数据转换为 DataFrame
    df_new = pd.DataFrame(mapped_records, columns=fieldnames)
    
    # 读取现有数据
    if OUTPUT_CSV.exists():
        df_old = pd.read_csv(OUTPUT_CSV, encoding='utf-8-sig')
        print(f"  原有数据: {len(df_old)} 条")
        
        # 合并数据
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_combined = df_new
    
    # 去重（按 code 和 date）
    original_len = len(df_combined)
    df_combined = df_combined.drop_duplicates(subset=['code', 'date'], keep='last')
    new_len = len(df_combined)
    
    print(f"  合并后数据: {new_len} 条（删除 {original_len - new_len} 条重复）")
    
    # 保存
    df_combined.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"✅ 数据已保存到 {OUTPUT_CSV}")
    
    print("\n" + "=" * 60)
    print("✅ 更新完成！")
    print("=" * 60)
    print(f"\n请检查数据：")
    print(f"  tail -20 '{OUTPUT_CSV}'")
    print(f"\n下一步：")
    print(f"  1. 更新 A股数据下载汇总.md")
    print(f"  2. 提交到 Git 仓库")

if __name__ == "__main__":
    main()
