#!/usr/bin/env python3
"""
Backfill totalShare and liqaShare in finance_summary.csv
using share_structure.csv as the source.

For each row in finance_summary.csv where totalShare or liqaShare is NaN,
look up the same code in share_structure.csv and fill in the value.
"""

import pandas as pd
import sys

FINANCE_CSV = r'E:\work\work\quant-trading-system\market_data\finance_summary.csv'
SHARE_STRUCT_CSV = r'E:\work\work\quant-trading-system\market_data\share_structure.csv'
OUTPUT_CSV = FINANCE_CSV  # overwrite in place


def main():
    # Load share_structure as lookup table (keep last occurrence per code)
    print("Loading share_structure.csv ...")
    ss = pd.read_csv(SHARE_STRUCT_CSV)
    print(f"  share_structure rows: {len(ss)}")

    # Build lookup dict: code -> (totalShare, liqaShare)
    # Drop rows where both are NaN
    ss = ss.dropna(subset=['totalShare', 'liqaShare'], how='all')
    # If duplicate codes, keep the last one
    ss = ss.drop_duplicates(subset='code', keep='last')
    ss_lookup = ss.set_index('code')[['totalShare', 'liqaShare']].to_dict('index')
    print(f"  unique codes in share_structure: {len(ss_lookup)}")

    # Load finance_summary
    print("Loading finance_summary.csv ...")
    fs = pd.read_csv(FINANCE_CSV)
    total_rows = len(fs)
    ts_before = fs['totalShare'].notna().sum()
    ls_before = fs['liqaShare'].notna().sum()
    print(f"  finance_summary rows: {total_rows}")
    print(f"  totalShare filled before: {ts_before}/{total_rows} ({100*ts_before/total_rows:.1f}%)")
    print(f"  liqaShare filled before: {ls_before}/{total_rows} ({100*ls_before/total_rows:.1f}%)")

    # Identify rows needing backfill
    ts_missing_mask = fs['totalShare'].isna()
    ls_missing_mask = fs['liqaShare'].isna()
    ts_missing_count = ts_missing_mask.sum()
    ls_missing_count = ls_missing_mask.sum()
    print(f"  totalShare missing: {ts_missing_count}")
    print(f"  liqaShare missing: {ls_missing_count}")

    # Backfill totalShare
    ts_filled = 0
    for idx in fs.index[ts_missing_mask]:
        code = fs.at[idx, 'code']
        if code in ss_lookup and pd.notna(ss_lookup[code]['totalShare']):
            fs.at[idx, 'totalShare'] = ss_lookup[code]['totalShare']
            ts_filled += 1

    # Backfill liqaShare
    ls_filled = 0
    for idx in fs.index[ls_missing_mask]:
        code = fs.at[idx, 'code']
        if code in ss_lookup and pd.notna(ss_lookup[code]['liqaShare']):
            fs.at[idx, 'liqaShare'] = ss_lookup[code]['liqaShare']
            ls_filled += 1

    print(f"\nBackfill results:")
    print(f"  totalShare backfilled: {ts_filled}/{ts_missing_count}")
    print(f"  liqaShare backfilled: {ls_filled}/{ls_missing_count}")

    ts_after = fs['totalShare'].notna().sum()
    ls_after = fs['liqaShare'].notna().sum()
    print(f"  totalShare filled after: {ts_after}/{total_rows} ({100*ts_after/total_rows:.1f}%)")
    print(f"  liqaShare filled after: {ls_after}/{total_rows} ({100*ls_after/total_rows:.1f}%)")

    # Write output
    print(f"\nWriting to {OUTPUT_CSV} ...")
    fs.to_csv(OUTPUT_CSV, index=False)
    print("Done.")

    # Summary
    print(f"\n=== SUMMARY ===")
    print(f"totalShare: {ts_before} -> {ts_after} (+{ts_after - ts_before})")
    print(f"liqaShare: {ls_before} -> {ls_after} (+{ls_after - ls_before})")


if __name__ == '__main__':
    main()
