"""
audit_api_endpoints.py — 抓所有 HTML 模板调用的 /api/ 路径, 对比 api.py 已注册的端点
2026-06-26 Ardot 落地收尾 Phase 4

输出: 未注册端点列表 (前端调但后端没注册)
"""
from pathlib import Path
import re
import sys
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = ROOT / "src" / "web" / "templates"
API_FILE = ROOT / "src" / "web" / "routes" / "api.py"


def extract_api_paths_from_templates() -> dict[str, list[str]]:
    """抓所有模板里 fetch('/api/...') 的路径, 以及所在文件名
    同时抓 '/api/xxx' + 字符串拼接 (如 '/api/stock/' + code)"""
    paths = defaultdict(list)
    # 匹配引号内的 /api/ 路径
    pattern = re.compile(r"""['"`](/api/[a-zA-Z0-9_/\-]+)['"`]""")
    for html in TPL_DIR.glob("*.html"):
        text = html.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            path = m.group(1).split('?')[0]
            paths[path].append(html.name)
    return paths


def extract_registered_endpoints() -> set[str]:
    """从 api.py 抓所有已注册的 /api/ 端点
    注意: router 内部路径不带 /api 前缀 (FastAPI 挂载时统一加)
    """
    text = API_FILE.read_text(encoding="utf-8")
    # 匹配 @router.get("/xxx"), @router.post("/xxx"), @router.put, @router.delete
    pattern = re.compile(r"""@router\.(get|post|put|delete|patch)\(\s*['"`]([^'"`]+)['"`]""")
    endpoints = set()
    for m in pattern.finditer(text):
        path = m.group(2)
        # 把内部路径转换成 /api 路径, 与 HTML 调用方一致
        if path.startswith("/"):
            endpoints.add("/api" + path)
    return endpoints


def main():
    print("🔍 Phase 4: 端点体检")
    print("=" * 60)

    api_paths = extract_api_paths_from_templates()
    registered = extract_registered_endpoints()

    # 把所有调用的路径归一化 (去掉前缀 /api)
    called = set(api_paths.keys())

    missing = called - registered
    unused = registered - called

    print(f"\n📊 统计:")
    print(f"  HTML 调用的 API 端点: {len(called)} 个")
    print(f"  api.py 已注册端点: {len(registered)} 个")

    # 智能匹配: 处理路径参数 (/{code} 等)
    # 如果注册的端点能匹配调用路径 (按段对齐), 视为已注册
    def normalize_for_compare(path: str) -> str:
        """去掉末尾 / 和路径参数占位, 用于比较"""
        return path.rstrip("/")

    def path_matches(registered_path: str, called_path: str) -> bool:
        """检查 registered_path 是否能匹配 called_path
        - /api/stock/{code} 可以匹配 /api/stock/600519
        - 但注册的 /api/stock (没有参数) 不会匹配 /api/stock/600519
        """
        rp = registered_path.rstrip("/").split("/")
        cp = called_path.rstrip("/").split("/")
        if len(rp) != len(cp):
            return False
        for a, b in zip(rp, cp):
            if a.startswith("{") and a.endswith("}"):
                continue  # 路径参数, 任意匹配
            if a != b:
                return False
        return True

    # 真正 missing: 调用了但没有任何注册端点能匹配
    actually_missing = set()
    for c in called:
        if any(path_matches(r, c) for r in registered):
            continue
        actually_missing.add(c)

    if actually_missing:
        print(f"\n❌ 未注册 (前端调用但后端没有, 含路径参数分析): {len(actually_missing)} 个")
        for p in sorted(actually_missing):
            files = api_paths[p]
            print(f"   - {p}")
            for f in sorted(set(files)):
                print(f"       ↑ {f}")
    else:
        print(f"\n✅ 所有调用的端点都已注册 (含路径参数)")

    if unused:
        print(f"\n⚠️ 已注册但未被模板使用 (可能是 API-only): {len(unused)} 个")
        for p in sorted(unused):
            print(f"   - {p}")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
