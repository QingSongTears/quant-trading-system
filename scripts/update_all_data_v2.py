#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
update_all_data_v2.py — 统一数据更新编排脚本 (2026-06-25 重写)

按依赖关系编排所有增量更新脚本，确保:
  1. 先取数 (CSV 层) → 再入库 (DB 层) → 最后派生 (Parquet/导出)
  2. 每一步均可独立运行，幂等安全
  3. 失败不中断后续任务 (SKIP & CONTINUE)

阶段:
  Phase 1: 取数 (CSV 层)
    1.1 K线日线           — incremental_update.py --types kline
    1.2 融资融券           — incremental_update.py --types margin
    1.3 大宗交易           — incremental_update.py --types block
    1.4 资金流向           — incremental_update.py --types fund_flow
    1.5 龙虎榜             — update_dragon_tiger.py
    1.6 技术指标(DB→CSV)   — update_tech_indicators_csv.py
    1.7 超图热点           — update_hot_reason.py
    1.8 股东户数           — update_holder_num.py
    1.9 快照类             — update_snapshots.py

  Phase 2: 入库 (DB 层)
    2.1 CSV→DB 桥接       — sync_csv_to_db.py
    2.2 公告导出           — update_announcements.py --export

  Phase 3: 派生 (Parquet)
    3.1 Parquet 重建       — rebuild_parquet.py

用法:
  python scripts/update_all_data_v2.py                            # 全部
  python scripts/update_all_data_v2.py --phase 1                  # 仅取数
  python scripts/update_all_data_v2.py --phase 1,2                # 取数+入库
  python scripts/update_all_data_v2.py --task kline,margin        # 指定任务
  python scripts/update_all_data_v2.py --dry-run                  # 预览
  python scripts/update_all_data_v2.py --start 2026-06-19 --end 2026-06-24  # 指定日期
"""
import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT / "scripts"
PYTHON = "C:/Users/aini7/.workbuddy/binaries/python/versions/3.13.12/python.exe"

# ============ 任务定义 ============

PHASE_TASKS = {
    1: [  # 取数层
        {"name": "K线日线", "script": "incremental_update.py",
         "args": lambda ctx: ["--types", "kline", "--start", ctx["start"], "--end", ctx["end"]]},
        {"name": "融资融券", "script": "incremental_update.py",
         "args": lambda ctx: ["--types", "margin", "--start", ctx["start"], "--end", ctx["end"]]},
        {"name": "大宗交易", "script": "incremental_update.py",
         "args": lambda ctx: ["--types", "block", "--start", ctx["start"], "--end", ctx["end"]]},
        {"name": "资金流向", "script": "incremental_update.py",
         "args": lambda ctx: ["--types", "fund_flow", "--start", ctx["start"], "--end", ctx["end"]]},
        {"name": "龙虎榜", "script": "update_dragon_tiger.py",
         "args": lambda ctx: ["--start", ctx["start"], "--end", ctx["end"]]},
        {"name": "技术指标CSV", "script": "update_tech_indicators_csv.py",
         "args": lambda ctx: []},
        {"name": "同花顺热点", "script": "update_hot_reason.py",
         "args": lambda ctx: ["--start", ctx["start"], "--end", ctx["end"]] if ctx.get("start") else ["--pull", "--export"]},
        {"name": "股东户数", "script": "update_holder_num.py",
         "args": lambda ctx: ["--pull"],
         "slow": True,
         "note": "约 30-60 分钟 (全市场~5200只 × 每批200只)"},
        {"name": "快照刷新", "script": "update_snapshots.py",
         "args": lambda ctx: [],
         "note": "profile + quote + dividend + calendar + sector + report + shares"},
    ],
    2: [  # 入库层
        {"name": "CSV→DB桥接", "script": "sync_csv_to_db.py",
         "args": lambda ctx: []},
        {"name": "公告导出", "script": "update_announcements.py",
         "args": lambda ctx: ["--export"]},
    ],
    3: [  # 派生层
        {"name": "Parquet重建", "script": "rebuild_parquet.py",
         "args": lambda ctx: [],
         "note": "约 5-10 分钟"},
    ],
}


def run_script(script_name: str, args: list[str], timeout_min: int = 30) -> bool:
    """运行一个 Python 脚本，返回是否成功"""
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        print(f"  ❌ 脚本不存在: {script_path}")
        return False

    cmd = [PYTHON, str(script_path)] + args
    cmd_str = " ".join(cmd)
    print(f"  ▶ {cmd_str}")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=False, text=True, timeout=timeout_min * 60)
        elapsed = time.time() - t0
        if r.returncode == 0:
            print(f"  ✅ 完成 ({elapsed:.1f}s)")
            return True
        else:
            print(f"  ❌ 失败 (exit={r.returncode}, {elapsed:.1f}s)")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ⏰ 超时 (>{timeout_min}min)")
        return False
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=str, default="all",
                        help="阶段: 1(取数) / 2(入库) / 3(派生) / all / 1,2 / 2,3")
    parser.add_argument("--task", type=str, help="指定任务名(逗号分隔)")
    parser.add_argument("--start", type=str, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="预览")
    parser.add_argument("--skip-slow", action="store_true", help="跳过慢任务(股东户数等)")
    parser.add_argument("--skip-snapshot", action="store_true", help="跳过快照刷新")
    args = parser.parse_args()

    # 日期上下文
    today = date.today().strftime("%Y-%m-%d")
    ctx = {
        "start": args.start or (date.today() - timedelta(days=7)).strftime("%Y-%m-%d"),
        "end": args.end or today,
    }

    # 确定阶段
    if args.phase == "all":
        phases = [1, 2, 3]
    else:
        phases = [int(p.strip()) for p in args.phase.split(",") if p.strip().isdigit()]

    print("=" * 70)
    print(f"  统一数据更新 v2 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  日期: {ctx['start']} ~ {ctx['end']}")
    print(f"  阶段: {phases}")
    print(f"  {'[DRY-RUN] ' if args.dry_run else ''}{'[SKIP-SLOW] ' if args.skip_slow else ''}")
    print("=" * 70)

    if args.dry_run:
        for phase in phases:
            print(f"\n--- Phase {phase} ---")
            for task in PHASE_TASKS[phase]:
                skip_reason = ""
                if args.skip_slow and task.get("slow"):
                    skip_reason = " [SKIP-SLOW]"
                note = f" — {task.get('note', '')}" if task.get("note") else ""
                print(f"  {task['name']}: {task['script']}{skip_reason}{note}")
        return 0

    # 执行
    global_t0 = time.time()
    total_success = 0
    total_fail = 0
    total_skip = 0

    if args.task:
        # 指定任务模式: 忽略阶段
        task_names = set(t.strip() for t in args.task.split(","))
        for phase in sorted(PHASE_TASKS.keys()):
            for task in PHASE_TASKS[phase]:
                if task["name"] not in task_names:
                    continue
                print(f"\n{'─' * 60}")
                print(f"  [{task['name']}] ({task['script']})")
                skip = (args.skip_slow and task.get("slow"))
                if skip:
                    print(f"  ⏭ SKIP (慢任务)")
                    total_skip += 1
                    continue
                if run_script(task["script"], task["args"](ctx)):
                    total_success += 1
                else:
                    total_fail += 1
    else:
        # 阶段模式
        for phase in phases:
            print(f"\n{'=' * 60}")
            print(f"  Phase {phase}")
            print(f"{'=' * 60}")

            for task in PHASE_TASKS[phase]:
                print(f"\n{'─' * 50}")
                print(f"  [{task['name']}] ({task['script']})")
                if task.get("note"):
                    print(f"  ℹ {task['note']}")

                skip = False
                if args.skip_slow and task.get("slow"):
                    print(f"  ⏭ SKIP (慢任务, --skip-slow)")
                    total_skip += 1
                    skip = True
                if args.skip_snapshot and task["script"] == "update_snapshots.py":
                    print(f"  ⏭ SKIP (快照刷新, --skip-snapshot)")
                    total_skip += 1
                    skip = True

                if not skip:
                    if run_script(task["script"], task["args"](ctx)):
                        total_success += 1
                    else:
                        total_fail += 1

    # 总结
    total_elapsed = time.time() - global_t0
    print()
    print("=" * 70)
    print(f"  完成! ✅{total_success}  ❌{total_fail}  ⏭{total_skip}")
    print(f"  总耗时: {total_elapsed/60:.1f}min ({total_elapsed:.0f}s)")
    print("=" * 70)

    return 1 if total_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
