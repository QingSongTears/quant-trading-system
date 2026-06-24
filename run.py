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


def check_database():
    """检查数据库状态"""
    from src.models.repository import DataRepository
    db_path = PROJECT_ROOT / "database" / "quant.db"
    repo = DataRepository()
    try:
        # 如果 DB 不存在,提示从 CSV 构建
        if not db_path.exists():
            print("\n📊 数据库状态检查")
            print("=" * 50)
            print(f"  ❌ 数据库文件不存在: {db_path}")
            print(f"  数据库文件:   {db_path}")
            print("=" * 50)
            print("\n💡 DB 文件已 gitignore, 需要从 market_data/ 下的 CSV 构建:")
            print("   python scripts/build_db.py")
            print("   (或 python scripts/build_db.py --incremental 仅增量)")
            return False

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
            print("\n⚠️  数据库为空，请先下载数据:")
            print("   python run.py --download")
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
    parser.add_argument("--debug", action="store_true", help="调试模式")

    args = parser.parse_args()

    # 确保目录存在
    (PROJECT_ROOT / "database").mkdir(exist_ok=True)
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)

    # 初始化日志系统（所有模式都启用）
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(level=log_level, file_log=not args.check and not args.health)

    # 仅检查数据库
    if args.check:
        check_database()
        return

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

    # 启动前检查数据库
    check_database()

    # Graceful shutdown: 捕获 SIGINT/SIGTERM，让 uvicorn 有机会清理资源
    def _signal_handler(sig, frame):
        print("\n🛑 收到停止信号，正在优雅关闭...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    import uvicorn
    uvicorn.run(
        "src.web.app:create_app",
        host=args.host,
        port=args.port,
        reload=args.debug,
        factory=True,
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
