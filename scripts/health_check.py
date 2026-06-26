"""
health_check.py — 系统健康检查脚本

检查项:
1. 数据库: 文件存在 / 可连接 / 关键表非空 / 行数
2. 数据目录: market_data CSV 数量 / 大小
3. 磁盘: 关键目录剩余空间 (warn < 5GB)
4. Web 服务: HTTP 200 on /api/health (可选, 探测 localhost:5054)
5. 日志: 今日日志存在 / 大小 < 50MB
6. Python 环境: 关键依赖版本

输出:
- 控制台彩色表格 (✅ / ⚠️ / ❌)
- 退出码: 0=全绿, 1=有警告, 2=有错误

用法:
    python scripts/health_check.py
    python scripts/health_check.py --json           # 仅 JSON 输出 (CI 用)
    python scripts/health_check.py --no-network     # 跳过网络探测
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_DIR = PROJECT_ROOT / "database"
MARKET_DATA_DIR = PROJECT_ROOT / "market_data"
LOGS_DIR = PROJECT_ROOT / "logs"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5054

# ANSI 颜色 (Windows 10+ / *nix 都支持 VT)
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "error"
    detail: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def icon(self) -> str:
        return {"ok": "✅", "warn": "⚠️ ", "error": "❌"}.get(self.status, "?")

    @property
    def color(self) -> str:
        return {"ok": GREEN, "warn": YELLOW, "error": RED}.get(self.status, "")


def human_size(n: int) -> str:
    """字节 → 人类可读 (KiB/MiB/GiB)"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def free_disk(path: Path) -> int:
    """跨平台磁盘剩余空间 (Windows / *nix)"""
    try:
        if sys.platform == "win32":
            import ctypes

            free_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(  # type: ignore[attr-defined]
                ctypes.c_wchar_p(str(path)), None, None, ctypes.pointer(free_bytes)
            )
            return int(free_bytes.value)
        else:
            stat = __import__("os").statvfs(path)  # type: ignore[attr-defined]
            return stat.f_bavail * stat.f_frsize
    except Exception:
        return -1


# ============================================================
# 检查项
# ============================================================
def check_database() -> CheckResult:
    db_path = DATABASE_DIR / "quant.db"
    if not db_path.exists():
        return CheckResult("数据库", "error", f"缺失: {db_path.name}")
    size = db_path.stat().st_size
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            # 关键表
            key_tables = ["stock_basic", "daily_price", "backtest_result"]
            missing = [t for t in key_tables if t not in tables]
            if missing:
                return CheckResult(
                    "数据库", "warn", f"缺表: {missing}", {"size": size, "tables": len(tables)}
                )
            # 总行数
            total = 0
            for t in ["daily_price", "stock_basic", "backtest_result"]:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {t}")  # nosec - 表名白名单
                    total += cur.fetchone()[0]
                except sqlite3.OperationalError:
                    pass
        finally:
            conn.close()
        return CheckResult(
            "数据库",
            "ok",
            f"{human_size(size)}, {len(tables)} 表, {total:,} 行",
            {"size": size, "tables": len(tables), "rows": total},
        )
    except Exception as e:
        return CheckResult("数据库", "error", f"无法读取: {e}")


def check_market_data() -> CheckResult:
    if not MARKET_DATA_DIR.exists():
        return CheckResult("市场数据", "warn", "目录不存在")
    csvs = list(MARKET_DATA_DIR.glob("*.csv"))
    if not csvs:
        return CheckResult("市场数据", "warn", "无 CSV 文件")
    total_size = sum(p.stat().st_size for p in csvs)
    # 检查最近 7 天是否有数据更新
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=7)
    fresh = [p for p in csvs if datetime.fromtimestamp(p.stat().st_mtime) > cutoff]
    detail = f"{len(csvs)} 个 CSV, {human_size(total_size)}, 7日内更新 {len(fresh)} 个"
    status = "ok" if fresh else "warn"
    return CheckResult("市场数据", status, detail, {"count": len(csvs), "size": total_size})


def check_disk() -> CheckResult:
    free = free_disk(PROJECT_ROOT)
    if free < 0:
        return CheckResult("磁盘空间", "warn", "无法获取")
    if free < 5 * 1024**3:  # < 5GB
        return CheckResult(
            "磁盘空间", "warn", f"仅剩 {human_size(free)}", {"free": free}
        )
    return CheckResult("磁盘空间", "ok", f"剩 {human_size(free)}", {"free": free})


def check_logs() -> CheckResult:
    if not LOGS_DIR.exists():
        return CheckResult("日志", "warn", "目录不存在")
    today_prefix = date.today().strftime("%Y%m%d")
    today_logs = list(LOGS_DIR.glob(f"*{today_prefix}*.log"))
    if not today_logs:
        return CheckResult("日志", "warn", "今日无日志")
    big = [p for p in today_logs if p.stat().st_size > 50 * 1024**2]
    if big:
        return CheckResult(
            "日志", "warn", f"{len(big)} 个 > 50MB, 建议 rotate", {"big": [p.name for p in big]}
        )
    return CheckResult("日志", "ok", f"{len(today_logs)} 个今日文件")


def check_web(host: str, port: int, timeout: float = 2.0) -> CheckResult:
    """HTTP 探测 /api/health"""
    url = f"http://{host}:{port}/api/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "health_check/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec
            code = resp.getcode()
            body = resp.read(200).decode("utf-8", errors="replace")
            if 200 <= code < 300:
                return CheckResult("Web 服务", "ok", f"{code} {url}", {"body": body})
            return CheckResult("Web 服务", "warn", f"{code} {url}")
    except urllib.error.URLError as e:
        return CheckResult("Web 服务", "warn", f"连接失败: {e.reason}")
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return CheckResult("Web 服务", "warn", f"未运行 ({e})")
    except Exception as e:
        return CheckResult("Web 服务", "error", f"探测异常: {e}")


def check_dependencies() -> CheckResult:
    """关键依赖版本探测 (有则报告, 无则跳过)"""
    # 模块名 ≠ pip 包名, 这里用真实 import 名
    needed = ["fastapi", "pandas", "sqlalchemy", "akshare", "xgboost", "sklearn"]
    found: dict[str, str] = {}
    missing: list[str] = []
    for mod in needed:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            found[mod] = ver
        except ImportError:
            missing.append(mod)
    if missing:
        return CheckResult("依赖", "warn", f"缺: {missing}", {"found": found, "missing": missing})
    return CheckResult("依赖", "ok", f"{len(found)}/{len(needed)} 已装", {"found": found})


# ============================================================
# 渲染
# ============================================================
def render_console(results: list[CheckResult], no_color: bool = False) -> None:
    c = lambda x: x if no_color else x  # noqa: E731
    print(f"\n{BOLD}🩺 量化系统健康检查{RESET}".replace(BOLD, "" if no_color else BOLD))
    print(f"{GRAY}项目根: {PROJECT_ROOT}{RESET}".replace(GRAY, "" if no_color else GRAY))
    print()
    name_w = max(len(r.name) for r in results)
    for r in results:
        line = f"  {r.icon}  {r.name:<{name_w}}  {r.detail}"
        if not no_color:
            line = f"{r.color}{line}{RESET}"
        print(line)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="系统健康检查")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--no-network", action="store_true", help="跳过网络探测")
    parser.add_argument("--no-color", action="store_true", help="禁用颜色")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Web 探测 host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Web 探测 port")
    args = parser.parse_args()

    results: list[CheckResult] = [
        check_database(),
        check_market_data(),
        check_disk(),
        check_logs(),
        check_dependencies(),
    ]
    if not args.no_network:
        results.append(check_web(args.host, args.port))

    if args.json:
        print(
            json.dumps(
                [{"name": r.name, "status": r.status, "detail": r.detail, **r.meta} for r in results],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        render_console(results, no_color=args.no_color)

    # 退出码: 0=全绿, 1=有警告, 2=有错误
    has_error = any(r.status == "error" for r in results)
    has_warn = any(r.status == "warn" for r in results)
    if has_error:
        return 2
    if has_warn:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
