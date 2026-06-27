"""
db_optimize.py — SQLite 数据库优化脚本

执行步骤 (可单独开关):
  --analyze    收集统计信息 (快速, 推荐定期跑)
  --reindex    重建索引 (中等耗时, 索引膨胀时跑)
  --vacuum     碎片整理 (耗时, 需要磁盘空间 = db 大小, 推荐月度)
  --check      仅检查 (打印页面/碎片/索引状态, 不修改)

默认全跑, 顺序: analyze → reindex → vacuum

⚠️ VACUUM 会锁库, 建议在停服时跑 (或先 --backup 再 --vacuum)

用法:
    python scripts/db_optimize.py --check              # 只看不改
    python scripts/db_optimize.py --analyze            # 收集统计
    python scripts/db_optimize.py --reindex --vacuum   # 重索引+碎片
    python scripts/db_optimize.py --all --yes          # 全跑, 无确认
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "quant.db"


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def get_size(db_path: Path) -> int:
    return db_path.stat().st_size if db_path.exists() else 0


def check_status(db_path: Path) -> dict:
    """只读模式拉取 page_size / freelist / 索引等元信息"""
    if not db_path.exists():
        return {"error": f"db not found: {db_path}"}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        cur = conn.execute("PRAGMA page_size")
        page_size = cur.fetchone()[0]
        cur = conn.execute("PRAGMA page_count")
        page_count = cur.fetchone()[0]
        cur = conn.execute("PRAGMA freelist_count")
        freelist = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'")
        idx_count = cur.fetchone()[0]
        cur = conn.execute("PRAGMA integrity_check")
        integrity = cur.fetchone()[0]
        return {
            "db_size": page_size * page_count,
            "page_size": page_size,
            "page_count": page_count,
            "freelist_pages": freelist,
            "freelist_pct": (freelist / page_count * 100) if page_count else 0,
            "index_count": idx_count,
            "integrity": integrity,
        }
    finally:
        conn.close()


def run_analyze(db_path: Path) -> float:
    t0 = time.time()
    conn = sqlite3.connect(str(db_path), timeout=60)
    try:
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()
    return time.time() - t0


def run_reindex(db_path: Path) -> float:
    t0 = time.time()
    conn = sqlite3.connect(str(db_path), timeout=300)
    try:
        # REINDEX 重建所有索引 (sqlite_master 中 type='index' 的)
        conn.execute("REINDEX")
        conn.commit()
    finally:
        conn.close()
    return time.time() - t0


def run_vacuum(db_path: Path) -> float:
    """VACUUM 会重建整个 db 文件, 需要 2x 磁盘空间"""
    t0 = time.time()
    # 不能在只读/事务中, 必须新连接
    conn = sqlite3.connect(str(db_path), timeout=600, isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return time.time() - t0


def confirm(msg: str) -> bool:
    try:
        ans = input(f"{msg} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 数据库优化")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--check", action="store_true", help="仅检查状态, 不修改")
    parser.add_argument("--analyze", action="store_true", help="ANALYZE 收集统计")
    parser.add_argument("--reindex", action="store_true", help="REINDEX 重建索引")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM 碎片整理")
    parser.add_argument("--all", action="store_true", help="analyze + reindex + vacuum")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    args = parser.parse_args()

    db_path: Path = args.db.resolve()
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}", file=sys.stderr)
        return 1

    # 没指定任何动作 → 默认 --check
    if not any([args.check, args.analyze, args.reindex, args.vacuum, args.all]):
        args.check = True

    print(f"🗄  数据库: {db_path.relative_to(PROJECT_ROOT)}")
    size_before = get_size(db_path)
    print(f"  当前大小: {human_size(size_before)}")

    # 状态检查 (总是打印, 即使没 --check)
    status = check_status(db_path)
    if "error" in status:
        print(f"❌ {status['error']}", file=sys.stderr)
        return 1
    print(f"  page_size: {status['page_size']}B, pages: {status['page_count']:,}")
    print(
        f"  空闲页: {status['freelist_pages']:,} ({status['freelist_pct']:.1f}%)"
        + ("  ⚠️  碎片较多, 建议 VACUUM" if status["freelist_pct"] > 10 else "")
    )
    print(f"  索引: {status['index_count']} 个")
    print(f"  完整性: {status['integrity']}")
    if status["integrity"] != "ok":
        print("❌ 数据库损坏, 请从备份恢复!", file=sys.stderr)
        return 2
    if args.check:
        return 0

    # 决定执行哪些
    do_analyze = args.analyze or args.all
    do_reindex = args.reindex or args.all
    do_vacuum = args.vacuum or args.all

    if do_vacuum and not args.yes:
        need = size_before * 2
        print(
            f"\n⚠️  VACUUM 需要约 {human_size(need)} 临时空间, 会锁库 ~{size_before // (50 * 1024 * 1024)}s"
        )
        if not confirm("确认执行 VACUUM?"):
            print("已取消")
            return 0
    if (do_reindex or do_vacuum) and not args.yes:
        if not confirm("确认执行 REINDEX?"):
            print("已取消")
            return 0

    print()
    if do_analyze:
        t = run_analyze(db_path)
        print(f"  ✓ ANALYZE   ({t:.2f}s)")
    if do_reindex:
        t = run_reindex(db_path)
        print(f"  ✓ REINDEX   ({t:.2f}s)")
    if do_vacuum:
        t = run_vacuum(db_path)
        print(f"  ✓ VACUUM    ({t:.2f}s)")

    size_after = get_size(db_path)
    diff = size_after - size_before
    sign = "+" if diff > 0 else ""
    print(f"\n  优化后: {human_size(size_after)} ({sign}{human_size(diff)})")
    print("✅ 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
