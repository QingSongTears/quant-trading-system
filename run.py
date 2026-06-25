#!/usr/bin/env python3
"""
QuantTradingSystem - A股量化交易模型系统
==========================================

启动方式:
    python run.py              # 启动 Web 服务 (默认端口 5050)
    python run.py --port 8080  # 指定端口
    python run.py --download   # 仅下载数据（不启动 Web 服务）
    python run.py --check      # 检查数据库状态
    python run.py --health     # 数据质量全面检查

所有数据来源于可考证的公开数据接口 (AKShare / WeStock Data / Baostock)。
AI 生成的分析内容均标注「AI生成」。
"""
import sys
import os
import signal
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def _configure_stdio() -> None:
    """Windows 控制台/重定向默认 GBK 时，避免启动横幅里的中文和图标崩溃。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_configure_stdio()


def setup_logging(level: str = "INFO", file_log: bool = True):
    """初始化日志系统（loguru + stdlib bridge）"""
    try:
        from src.log import setup, bridge_stdlib_logging
        setup(
            log_dir="logs",
            level=level,
            console=True,
            file_log=file_log,
            project_root=str(PROJECT_ROOT),
        )
        bridge_stdlib_logging()
        return True
    except ImportError:
        # loguru 未安装时静默降级到 stdlib logging
        import logging
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
        return False


def check_database(auto_fix: bool = True):
    """检查数据库状态 (PR-fix Step 2 H: 启动自动修复)

    Args:
        auto_fix: True 时缺表自动 init_database() (Step 2 新增)

    Returns:
        bool: True=数据库就绪可启动 Web; False=需用户手动导入数据
    """
    from src.models.repository import DataRepository
    db_path = PROJECT_ROOT / "database" / "quant.db"
    repo = DataRepository()
    try:
        # 1. DB 文件不存在 → 尝试自动 init
        if not db_path.exists():
            print("\n📊 数据库状态检查")
            print("=" * 50)
            print(f"  ❌ 数据库文件不存在: {db_path}")
            print("=" * 50)
            if auto_fix:
                print("  🔧 自动初始化表结构 (create_all)...")
                try:
                    repo.init_database()
                    print("  ✅ 表结构已创建 (空库)")
                    print("\n💡 数据库已就绪 (无数据), 请选择:")
                    print("   python run.py --download       # 从 AKShare 拉数据")
                    print("   python scripts/build_db.py      # 从 market_data/ CSV 构建")
                except Exception as e:
                    print(f"  ❌ 自动 init 失败: {e}")
                    return False
                return False  # 仍需用户导入数据
            print("\n💡 修复: python run.py --init")
            return False

        # 2. 检查关键表是否存在 (stock_basic / daily_price)
        existing_tables = set(repo.list_tables())
        critical_tables = {"stock_basic", "daily_price"}
        missing = critical_tables - existing_tables
        if missing:
            print("\n📊 数据库状态检查")
            print("=" * 50)
            print(f"  ⚠️  缺失关键表: {missing}")
            print(f"  当前表数:   {len(existing_tables)}")
            print("=" * 50)
            if auto_fix:
                print("  🔧 自动创建缺失表 (create_all)...")
                try:
                    repo.init_database()
                    print("  ✅ 表结构已修复")
                except Exception as e:
                    print(f"  ❌ 自动 init 失败: {e}")
                    return False
            else:
                print("\n💡 修复: python run.py --init")
                return False

        # 3. 检查数据量
        coverage = repo.get_data_coverage()
        print("\n📊 数据库状态检查")
        print("=" * 50)
        print(f"  A股总数:     {coverage['total_stocks']}")
        print(f"  日线记录:     {coverage['total_records']:,}")
        date_range = coverage.get("date_range", {})
        print(f"  数据区间:     {date_range.get('start', 'N/A')} ~ {date_range.get('end', 'N/A')}")
        print(f"  数据库文件:   {db_path}")
        print("=" * 50)

        if coverage["total_records"] == 0:
            print("\n⚠️  数据库表结构已就绪但无数据，请选择导入方式:")
            print("   python run.py --download        # 从 AKShare 拉数据 (慢,需联网)")
            print("   python scripts/build_db.py       # 从 market_data/ CSV 构建 (快)")
            return False
        return True
    except Exception as e:
        print(f"\n❌ 数据库检查失败: {e}")
        return False


def run_health_check():
    """运行数据质量全面检查"""
    print("\n🔍 开始数据质量全面检查...\n")
    try:
        from src.data.validator import DataValidator
        v = DataValidator()
        report = v.run_all()
        v.print_report(report)

        # 如果有 pct_change 空值问题，自动回填
        pct_null = report.nulls.get("pct_change", {}).get("null_pct", 0)
        if pct_null > 50:
            print("\n🔧 自动回填 pct_change...")
            result = v.fill_pct_change()
            print(f"  {result['message']}")

        return len(report.summary) == 0
    except Exception as e:
        print(f"\n❌ 健康检查失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def init_data_from_csv():
    """PR-fix Step 2 H: 从 market_data/ CSV 构建数据库 (无网络,快速)

    调用 scripts/build_db.py 的核心逻辑 — 从已下载的 CSV 导入到 quant.db
    """
    print("\n📦 从 market_data/ CSV 构建数据库...")
    try:
        # 优先调用项目里已有的 build_db 脚本
        import subprocess
        script = PROJECT_ROOT / "scripts" / "build_db.py"
        if script.exists():
            print(f"   调用: python {script.name}")
            result = subprocess.run(
                [sys.executable, str(script), "--incremental"],
                cwd=str(PROJECT_ROOT),
                capture_output=True, text=True,
                timeout=600,
            )
            print(result.stdout[-2000:] if result.stdout else "")
            if result.returncode == 0:
                print("✅ CSV 数据导入完成")
                return True
            else:
                print(f"⚠️ build_db 退出码 {result.returncode}")
                print(result.stderr[-1000:] if result.stderr else "")
        # 备用:直接调 internal importer
        print("   (script 不存在, 直接调 importer)")
        from scripts.build_db import build_db
        build_db(incremental=True)
        print("✅ CSV 数据导入完成")
        return True
    except Exception as e:
        print(f"❌ CSV 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def start_param_server_subprocess(tuning_port: int = 8081):
    """PR-fix Step 3 A: 启动 param_server.py 作为子进程

    与 FastAPI 主服务并行运行,提供额外的调参/筛选面板 UI
    默认端口 8081,可通过 --tuning-port 修改

    Args:
        tuning_port: param_server 监听端口
    Returns:
        Popen: 子进程对象,失败时 None
    """
    import subprocess
    script = PROJECT_ROOT / "scripts" / "param_server.py"
    if not script.exists():
        print(f"⚠️  param_server.py 不存在: {script}")
        return None
    print(f"\n🎛️  启动 param_server 调参面板 (端口 {tuning_port})...")
    env = os.environ.copy()
    env["PORT"] = str(tuning_port)
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # 隐藏 cmd 窗口
        )
        # 等几秒让 server 起来
        import time
        time.sleep(3)
        if proc.poll() is None:
            print(f"  ✅ param_server 启动成功, PID={proc.pid}")
            print(f"     URL: http://localhost:{tuning_port}/")
            return proc
        else:
            print(f"  ❌ param_server 启动失败, exit code={proc.returncode}")
            return None
    except Exception as e:
        print(f"  ❌ param_server 启动异常: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="A股量化交易模型系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run.py                    # 启动 Web 服务
  python run.py --port 8080        # 指定端口
  python run.py --download         # 下载全市场数据
  python run.py --check            # 检查数据库状态
  python run.py --health           # 数据质量全面检查
  python run.py --init             # 初始化数据库表结构
        """
    )
    parser.add_argument("--port", type=int, default=5050, help="Web 服务端口 (默认: 5050)")
    # 安全:默认绑定 127.0.0.1 (仅本机访问),避免无认证服务暴露到公网
    # 如需公开访问,显式 --host=0.0.0.0 并配合 QUANT_API_KEY 环境变量 + 反向代理
    parser.add_argument("--host", type=str,
                        default=os.environ.get("BIND_HOST", "127.0.0.1"),
                        help="监听地址 (默认: 127.0.0.1, env BIND_HOST 可覆盖)")
    parser.add_argument("--download", action="store_true", help="下载全市场历史数据")
    parser.add_argument("--download-incr", action="store_true", help="增量更新数据")
    parser.add_argument("--check", action="store_true", help="检查数据库状态")
    parser.add_argument("--health", action="store_true", help="数据质量全面检查")
    parser.add_argument("--init", action="store_true", help="初始化数据库表结构")
    parser.add_argument("--init-data", action="store_true",
                        help="从 market_data/ CSV 导入数据 (无需网络,Step2 H 新增)")
    parser.add_argument("--check-only", action="store_true",
                        help="仅检查数据库,不启动 Web (Step2 H 新增, 用于 CI/Docker)")
    parser.add_argument("--with-tuning", action="store_true",
                        help="同时启动 param_server.py 调参面板 (Step3 A 新增, 默认端口 8081)")
    parser.add_argument("--tuning-port", type=int, default=8081,
                        help="param_server 端口 (默认: 8081, 需 --with-tuning)")
    parser.add_argument("--debug", action="store_true", help="调试模式 (log_level=DEBUG, 默认 reload=True)")
    parser.add_argument("--no-reload", action="store_true", help="禁用 auto-reload (生产用)")
    # 2026-06-25: 默认 reload=True (开发体验, 改代码自动重启)
    # 生产用 --no-reload 关闭

    args = parser.parse_args()

    # 确保目录存在
    (PROJECT_ROOT / "database").mkdir(exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    # 初始化日志系统（所有模式都启用）
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(level=log_level, file_log=not args.check and not args.health)

    # 仅检查数据库
    if args.check or args.check_only:
        ok = check_database(auto_fix=False)
        sys.exit(0 if ok else 1)

    # 数据质量全面检查
    if args.health:
        ok = run_health_check()
        sys.exit(0 if ok else 1)

    # 初始化数据库
    if args.init:
        print("🔧 初始化数据库表结构...")
        from src.models.repository import DataRepository
        repo = DataRepository()
        repo.init_database()
        print("✅ 数据库表结构初始化完成")
        return

    # 从 CSV 导入数据 (PR-fix Step 2 H 新增, 无需网络)
    if args.init_data:
        ok = init_data_from_csv()
        sys.exit(0 if ok else 1)

    # 仅下载数据
    if args.download or args.download_incr:
        print("\n📥 开始下载数据...")
        print(f"   数据源: AKShare (东方财富/新浪财经公开接口)")
        print(f"   模式:   {'全量下载' if args.download else '增量更新'}")
        print(f"   数据库: {PROJECT_ROOT / 'database' / 'quant.db'}\n")

        from src.data.downloader import run_download
        mode = "full" if args.download else "incremental"
        result = run_download(mode=mode)
        print(f"\n✅ 下载完成: {result}")
        return

    # 启动 Web 服务
    print(f"""
╔══════════════════════════════════════════════════╗
║   📊 QuantTradingSystem - A股量化交易模型系统     ║
║                                                  ║
║   数据源: AKShare / WeStock Data / Baostock      ║
║   启动地址: http://localhost:{args.port}              ║
║   API文档: http://localhost:{args.port}/docs (调试) ║
║   日志目录: {PROJECT_ROOT / 'logs'}  ║
║                                                  ║
║   按 Ctrl+C 停止服务                              ║
╚══════════════════════════════════════════════════╝
""")

    # 启动前检查数据库 (PR-fix Step 2 H: 自动修复缺表)
    ok = check_database(auto_fix=True)
    if not ok:
        print("\n❌ 数据库未就绪,无法启动 Web 服务")
        print("\n💡 推荐操作 (选一):")
        print("   python run.py --download      # 从 AKShare 下载 (慢,需联网)")
        print("   python run.py --init-data     # 从 market_data/ CSV 导入 (快,无需网络)")
        sys.exit(1)

    # 启动 param_server 调参面板 (PR-fix Step 3 A: --with-tuning)
    tuning_proc = None
    if args.with_tuning:
        tuning_proc = start_param_server_subprocess(tuning_port=args.tuning_port)
        if tuning_proc is None:
            print("⚠️  param_server 启动失败,继续只启动 FastAPI")
    else:
        print("\n💡 提示: --with-tuning 可同时启动 param_server 调参面板")

    # Graceful shutdown: 捕获 SIGINT/SIGTERM,清理所有子进程
    def _signal_handler(sig, frame):
        print("\n🛑 收到停止信号，正在优雅关闭...")
        if tuning_proc is not None and tuning_proc.poll() is None:
            print("   关闭 param_server 子进程...")
            tuning_proc.terminate()
            try:
                tuning_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tuning_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(f"\n📊 访问入口:")
    print(f"   - FastAPI 主服务:  http://localhost:{args.port}/")
    if tuning_proc is not None:
        print(f"   - param_server:    http://localhost:{args.tuning_port}/")
    print()

    import uvicorn
    reload_enabled = args.debug and not args.no_reload  # 2026-06-25: 默认 reload
    uvicorn.run(
        "src.web.app:create_app",
        host=args.host,
        port=args.port,
        reload=reload_enabled,
        factory=True,
        log_level="debug" if args.debug else "info",
    )
    if reload_enabled:
        print("\n🔄 Auto-reload 已启用, 修改代码会自动重启")
        print("   生产部署: python run.py --no-reload")


if __name__ == "__main__":
    main()
