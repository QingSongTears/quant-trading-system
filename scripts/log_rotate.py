"""
log_rotate.py — 日志轮转 / 清理脚本

策略:
- 按天压缩 (gzip): logs/quant_20260624.log → logs/archive/quant_20260624.log.gz
- 保留最近 N 天原始日志, 其余压缩归档
- 总磁盘占用上限: 500MB (超出按 mtime 删最早的)
- 锁文件: .logrotate.lock 防并发

用法:
    python scripts/log_rotate.py                    # 默认保留 7 天原始
    python scripts/log_rotate.py --keep 3 --max 200 # 保留 3 天, 总 200MB
    python scripts/log_rotate.py --dry-run          # 仅预览, 不动文件
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = PROJECT_ROOT / "logs"
ARCHIVE_DIR = LOGS_DIR / "archive"
LOCK_FILE = LOGS_DIR / ".logrotate.lock"


class LogFile(NamedTuple):
    path: Path
    size: int
    mtime: float
    age_days: int


def acquire_lock(timeout: float = 5.0) -> bool:
    """简单文件锁, 防多进程并发"""
    if LOCK_FILE.exists():
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
            if age > 60:  # 锁超过 60s 视为残留
                LOCK_FILE.unlink(missing_ok=True)
            else:
                return False
        except OSError:
            return False
    try:
        LOCK_FILE.write_text(f"{time.time():.0f}\n", encoding="utf-8")
        return True
    except OSError:
        return False


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def scan_logs(keep_days: int) -> list[LogFile]:
    """扫描 logs/*.log, 按 mtime 分组"""
    if not LOGS_DIR.exists():
        return []
    out: list[LogFile] = []
    now = time.time()
    for p in LOGS_DIR.glob("*.log"):
        if not p.is_file():
            continue
        st = p.stat()
        age_days = int((now - st.st_mtime) / 86400)
        out.append(LogFile(p, st.st_size, st.st_mtime, age_days))
    return sorted(out, key=lambda x: x.mtime, reverse=True)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


def compress_file(src: Path) -> Path:
    """gzip 压缩单文件 → archive/ 目录"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARCHIVE_DIR / f"{src.name}.gz"
    if dst.exists():
        dst.unlink()
    with src.open("rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    return dst


def cleanup_by_total(archive: list[Path], max_total_mb: int) -> list[Path]:
    """
    超出 max_total_mb 时, 按 mtime 删最早归档
    返回被删除的文件
    """
    max_bytes = max_total_mb * 1024 * 1024
    archive_sorted = sorted(archive, key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in archive_sorted)
    deleted: list[Path] = []
    while total > max_bytes and archive_sorted:
        victim = archive_sorted.pop(0)
        size = victim.stat().st_size
        try:
            victim.unlink()
            total -= size
            deleted.append(victim)
        except OSError:
            pass
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="日志轮转 / 归档")
    parser.add_argument("--keep", type=int, default=7, help="保留最近 N 天原始日志 (默认 7)")
    parser.add_argument("--max", type=int, default=500, help="归档总大小上限 MB (默认 500)")
    parser.add_argument(
        "--dry-run", action="store_true", help="仅预览, 不实际执行"
    )
    args = parser.parse_args()

    if not acquire_lock():
        print("❌ 锁文件存在, 另一进程正在运行或上次异常退出", file=sys.stderr)
        return 1
    try:
        return _run(args)
    finally:
        release_lock()


def _run(args: argparse.Namespace) -> int:
    logs = scan_logs(args.keep)
    if not logs:
        print("ℹ️  无日志文件")
        return 0

    fresh = [l for l in logs if l.age_days < args.keep]
    to_compress = [l for l in logs if l.age_days >= args.keep]

    total_fresh = sum(l.size for l in fresh)
    print(f"📂 日志目录: {LOGS_DIR.relative_to(PROJECT_ROOT)}")
    print(f"  ✓ 保留: {len(fresh)} 个 ({human_size(total_fresh)})")
    print(f"  → 待压缩: {len(to_compress)} 个")
    if args.dry_run:
        for l in to_compress:
            print(f"    [DRY] {l.path.name} ({human_size(l.size)}, {l.age_days}d)")
    else:
        for l in to_compress:
            try:
                dst = compress_file(l.path)
                l.path.unlink()
                print(
                    f"  ✓ 压缩 {l.path.name} → {dst.relative_to(PROJECT_ROOT)}"
                    f" ({human_size(l.size)} → {human_size(dst.stat().st_size)})"
                )
            except OSError as e:
                print(f"  ! 失败 {l.path.name}: {e}", file=sys.stderr)

    # 总大小控制
    if ARCHIVE_DIR.exists():
        archives = list(ARCHIVE_DIR.glob("*.gz"))
        total_arch = sum(p.stat().st_size for p in archives)
        print(
            f"  📦 归档目录: {len(archives)} 个, 总 {human_size(total_arch)}"
            f" / 上限 {args.max} MB"
        )
        if total_arch > args.max * 1024 * 1024 and not args.dry_run:
            deleted = cleanup_by_total(archives, args.max)
            for d in deleted:
                print(f"  🗑  删除超限归档: {d.name}")
        elif total_arch > args.max * 1024 * 1024 and args.dry_run:
            print(f"    [DRY] 将清理超限归档")

    return 0


if __name__ == "__main__":
    sys.exit(main())
