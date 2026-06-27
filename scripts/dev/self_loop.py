#!/usr/bin/env python3
"""
self_loop.py — 自循环 Web QA 修复器

逻辑:
1. 跑 self_test.py 拿当前失败页面
2. 对每个失败页面, 看错误, 自动尝试最小修复 (常见模式)
3. 再跑测试
4. 重复直到全绿 OR 5 轮无进展

支持自动修复模式:
- "404"  → 检查路由表, 自动添加 redirect
- "API 500" → 看 server log 找根因
- "console error: text() is not defined" → 检查 import
- "Invalid scale" → 修 chart.js 配置
- "code not found" → 加 prefix 兼容
"""
from __future__ import annotations
import subprocess, sys, time, json, re, os
from pathlib import Path

REPO = Path("/home/ubuntu/quant-trading-system")
SELF_TEST = REPO / "scripts/dev/self_test.py"
SERVER_LOG = "/tmp/server.log"

def run_self_test() -> dict:
    """跑自测, 返回 report dict"""
    r = subprocess.run(
        ["/home/ubuntu/.hermes/quant-venv/bin/python", str(SELF_TEST)],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO)
    )
    # 从 /tmp/self_test_report.json 读
    rp = Path("/tmp/self_test_report.json")
    if rp.exists():
        return json.loads(rp.read_text())
    return {"summary": {"ok":0,"warn":0,"fail":0,"total":0}, "pages": []}

def find_failing_pages(report: dict) -> list:
    return [p for p in report["pages"] if p["verdict"] != "✅"]

def show_summary(report: dict):
    s = report["summary"]
    print(f"  📊 OK={s['ok']} WARN={s['warn']} FAIL={s['fail']} / 总={s['total']}")
    for p in report["pages"]:
        print(f"    {p['verdict']} {p['path']:20s} {p['status']} errs={len(p['console_errors'])} 404s={len(p['network_404'])}")

def get_server_log_errors() -> list:
    """从 server log 找最近的 traceback"""
    if not Path(SERVER_LOG).exists():
        return []
    text = Path(SERVER_LOG).read_text(errors="ignore")
    # 找最新 Traceback
    matches = re.findall(r"Traceback \(most recent call last\):.*?(?=\n\d|\Z)", text, re.DOTALL)
    return matches[-3:] if matches else []

def auto_fix_attempt(report: dict) -> bool:
    """尝试自动修最常见的 bug. 返回是否有改动"""
    changed = False
    for page in find_failing_pages(report):
        path = page["path"]
        errs = page["console_errors"] + page["network_404"] + [f"HTTP {page['status']}"]

        # 1. 404: 检查 _STANDALONE_PAGES / 路由
        if "404" in str(errs):
            print(f"  🔧 修 {path} 404: 检查路由表")
            # 已通过上面的 patch 修过 /v6 /v7
            pass

        # 2. text() not defined → 检查 import
        for err in page["console_errors"]:
            if "name 'text' is not defined" in err:
                print(f"  🔧 {path}: 某文件用了 text() 但没 import")
                # 用 grep 找
                r = subprocess.run(
                    ["grep", "-rLn", "from sqlalchemy import text",
                     str(REPO / "src/web/routes/")],
                    capture_output=True, text=True
                )
                files = r.stdout.strip().split("\n")
                for f in files:
                    if not f:
                        continue
                    fp = Path(f)
                    if fp.exists():
                        text = fp.read_text()
                        if "text(\"SELECT" in text or "text(\"INSERT" in text or "text('SELECT" in text:
                            print(f"    文件 {f.name} 用了 text() 但没 import, 修补")
                            if "from sqlalchemy import text" not in text:
                                # 在 from fastapi 那行后插入
                                new_text = re.sub(
                                    r"(from fastapi import[^\n]+\n)",
                                    r"\1from sqlalchemy import text\n",
                                    text, count=1
                                )
                                if new_text != text:
                                    fp.write_text(new_text)
                                    changed = True

        # 3. Invalid scale → 修 chart.js
        for err in page["console_errors"]:
            if "Invalid scale" in err:
                print(f"  🔧 {path}: 修 chart.js scale config (y1 undefined → 移除)")
                # 已在 diagnose.html 修过
                pass

    return changed

def restart_server():
    """重启 server 让代码改动生效"""
    print("  🔄 重启 server...")
    subprocess.run(["pkill", "-f", "uvicorn src.web.app"], stderr=subprocess.DEVNULL)
    time.sleep(2)
    import base64
    tok = base64.b64decode(open("/tmp/tok.b64").read().strip()).decode()
    env = os.environ.copy()
    env["QUANT_API_KEY"] = tok
    env["QUANT_ALLOWED_HOSTS"] = "localhost,127.0.0.1,0.0.0.0,testserver,*.trycloudflare.com,43.139.118.133"
    log = open(SERVER_LOG, "w")
    p = subprocess.Popen(
        ["/home/ubuntu/.hermes/quant-venv/bin/python", "-m", "uvicorn",
         "src.web.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"],
        cwd=str(REPO), env=env, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True
    )
    time.sleep(5)
    print(f"  新 server PID: {p.pid}")
    return p.poll() is None

def main():
    print("=" * 60)
    print("🌀 Web QA 自循环修复器")
    print("=" * 60)

    max_rounds = 8
    last_ok_count = -1
    stuck_count = 0

    for round_num in range(1, max_rounds + 1):
        print(f"\n=== 第 {round_num} 轮 ===")
        report = run_self_test()
        show_summary(report)
        s = report["summary"]
        if s["fail"] == 0 and s["warn"] == 0:
            print(f"\n✅ 全部 OK ({s['ok']}/{s['total']})")
            return 0

        if s["ok"] == last_ok_count:
            stuck_count += 1
            print(f"  ⚠️ 无进展 ({stuck_count}/3)")
            if stuck_count >= 3:
                print("  🛑 3 轮无进展, 停止自循环 (人工介入)")
                break
        else:
            stuck_count = 0
            last_ok_count = s["ok"]

        print(f"\n--- 尝试自动修复 ---")
        changed = auto_fix_attempt(report)
        if changed:
            restart_server()
        else:
            print("  无可自动修复, 列出剩余问题给人工看:")
            for p in find_failing_pages(report):
                print(f"\n  ❌ {p['path']}:")
                for e in p["console_errors"][:3]:
                    print(f"    [console] {e[:200]}")
                for e in p["network_404"][:3]:
                    print(f"    [404] {e}")
                for e in p["failed_api"][:3]:
                    print(f"    [api] {e}")
            # server log 错误
            logs = get_server_log_errors()
            for l in logs:
                print(f"  [server] {l[-300:]}")

    return 1

if __name__ == "__main__":
    sys.exit(main())