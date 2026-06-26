"""
backup_db.py — 量化系统数据库备份脚本

支持:
- SQLite 在线热备份 (sqlite3 backup API, 无需停服务)
- 保留最近 N 份, 自动清理
- 输出 backup_info.json (含 SHA256, 大小, 时间)

用法:
    python scripts/backup_db.py                    # 默认备份 database/quant.db
    python scripts/backup_db.py --db database/quant.db --keep 7
    python scripts/backup_db.py --db database/quant.db --output backups/

跨平台: Windows / Linux / macOS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "quant.db"
DEFAULT_OUT = PROJECT_ROOT / "backups"


def calc_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """流式计算文件 SHA256（不一次性读入内存，避免大文件爆内存）"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def backup_sqlite(src: Path, dst: Path) -> dict:
    """
    用 sqlite3 官方 backup API 做热备份（无需停服务）
    比 file copy 安全: 避免复制过程中 WAL flush 导致的不一致
    """
    if not src.exists():
        raise FileNotFoundError(f"数据库不存在: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 在线热备份 (源 db 可同时被读写)
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    size = dst.stat().st_size
    return {
        "path": str(dst.relative_to(PROJECT_ROOT)),
        "size_bytes": size,
        "size_human": f"{size / 1024 / 1024:.1f} MB",
        "sha256": calc_sha256(dst),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def cleanup_old(backup_dir: Path, db_name: str, keep: int) -> list[str]:
    """
    保留最近 N 份备份（按时间戳文件名排序），其余删除
    返回被删除的文件名列表
    """
    pattern = f"{db_name}.*.bak"
    files = sorted(backup_dir.glob(pattern), key=lambda p: p.name, reverse=True)
    deleted = []
    for old in files[keep:]:
        try:
            old.unlink()
            deleted.append(old.name)
        except OSError as e:
            print(f"  ! 删除失败 {old.name}: {e}", file=sys.stderr)
    return deleted


def write_manifest(backup_dir: Path, info: dict, db_path: Path) -> None:
    """
    写 / 更新 backup_info.json (追加式, 最多保留 50 条历史)
    """
    manifest = backup_dir / "backup_info.json"
    history: list[dict] = []
    if manifest.exists():
        try:
            history = json.loads(manifest.read_text(encoding="utf-8")).get("history", [])
        except (json.JSONDecodeError, OSError):
            history = []
    history.insert(0, {**info, "source": str(db_path.relative_to(PROJECT_ROOT))})
    history = history[:50]
    payload = {
        "last_backup_at": info["created_at"],
        "total_backups": len(history),
        "history": history,
    }
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SQLite 数据库热备份（在线，不中断服务）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  python scripts/backup_db.py --keep 7",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help="源数据库路径 (默认: database/quant.db)"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUT, help="备份输出目录 (默认: backups/)"
    )
    parser.add_argument(
        "--keep", type=int, default=7, help="保留最近 N 份 (默认: 7)"
    )
    args = parser.parse_args()

    db_path: Path = args.db.resolve()
    out_dir: Path = args.output.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    db_name = db_path.stem  # e.g. "quant"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = out_dir / f"{db_name}.{ts}.bak"

    print(f"📦 备份 {db_path.name} → {dst.relative_to(PROJECT_ROOT)}")
    try:
        info = backup_sqlite(db_path, dst)
    except Exception as e:
        print(f"❌ 备份失败: {e}", file=sys.stderr)
        return 1

    print(f"  ✓ 大小: {info['size_human']}")
    print(f"  ✓ SHA256: {info['sha256'][:16]}…")

    deleted = cleanup_old(out_dir, db_name, args.keep)
    if deleted:
        print(f"  🗑  清理旧备份: {len(deleted)} 份 ({deleted[0]}…)")

    write_manifest(out_dir, info, db_path)
    print(f"  📋 清单: {(out_dir / 'backup_info.json').relative_to(PROJECT_ROOT)}")
    print("✅ 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
