"""
Ardot 设计稿相关页面:
- /ardot-specs, /ardot-specs/{slug} — Spec Markdown 浏览器
- /console — AI QuantX 风格 7-tab 控制台
- /fund-flow-report — 资金面融合回测

注: 共享 core_pages.py 的 router 和 templates
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from .core_pages import (
    SPECS_DIR,
    _get_global_context,
    get_templates,
)

router = APIRouter()
templates = get_templates()


# ============================================================
# Ardot 设计 Spec 浏览器 (2026-06-26 新增)
# 列出并渲染 output/ardot_specs/*.md, 用于在没有 Ardot MCP 时
# 也能查阅已规划的页面设计规格
# ============================================================
@router.get("/ardot-specs", response_class=HTMLResponse)
async def ardot_specs_index(request: Request):
    """列出所有 Ardot 设计 spec"""
    from pathlib import Path
    specs = []
    if SPECS_DIR.exists():
        for md in sorted(SPECS_DIR.glob("*.md")):
            title = md.stem
            if title == "README":
                continue
            # 从文件名推断标题: 01_v5_tuning_detailed → V5 调参详细
            num, name = title.split("_", 1)
            specs.append({
                "filename": md.name,
                "num": num,
                "slug": name,
                "title": name.replace("_", " ").title(),
                "size_kb": round(md.stat().st_size / 1024, 1),
            })
    ctx = _get_global_context()
    ctx.update({
        "specs": specs,
        "readme_exists": (SPECS_DIR / "README.md").exists(),
    })
    return templates.TemplateResponse(request, "ardot_specs_index.html", ctx)


@router.get("/ardot-specs/{slug}", response_class=HTMLResponse)
async def ardot_specs_view(request: Request, slug: str):
    """查看单个 spec 的 Markdown 渲染"""
    from pathlib import Path
    import re as _re
    from fastapi.responses import HTMLResponse as _HTML
    # 防止路径穿越
    if "/" in slug or ".." in slug:
        raise HTTPException(400, "invalid slug")
    # slug 形如 "v5_tuning_detailed"，文件名是 "01_v5_tuning_detailed.md"
    # 先尝试直接匹配，再尝试加数字前缀
    target = SPECS_DIR / f"{slug}.md"
    if not target.exists():
        for prefix in ("01_", "02_", "03_", "04_", "05_", "06_", "07_", "08_", "09_", "10_"):
            candidate = SPECS_DIR / f"{prefix}{slug}.md"
            if candidate.exists():
                target = candidate
                break
    if not target.exists():
        raise HTTPException(404, f"spec not found: {slug}")
    raw = target.read_text(encoding="utf-8")
    # 极简 Markdown → HTML 渲染 (只处理标题/列表/表格/代码块)
    def render_md(md: str) -> str:
        out = []
        in_code = False
        in_table = False
        for line in md.splitlines():
            if line.startswith("```"):
                if in_code:
                    out.append("</pre>")
                    in_code = False
                else:
                    lang = line[3:].strip() or ""
                    out.append(f'<pre class="md-code" data-lang="{lang}">')
                    in_code = True
                continue
            if in_code:
                out.append(_re.sub(r'<', '&lt;', line))
                continue
            # 表格
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not in_table:
                    out.append('<table class="md-table">')
                    out.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr></thead><tbody>")
                    in_table = True
                else:
                    if all(set(c) <= set("-: ") for c in cells):
                        continue
                    out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
                continue
            elif in_table and not line.startswith("|"):
                out.append("</tbody></table>")
                in_table = False
            # 标题
            m = _re.match(r'^(#{1,6})\s+(.*)$', line)
            if m:
                lvl = len(m.group(1))
                out.append(f"<h{lvl}>{m.group(2)}</h{lvl}>")
                continue
            # 列表
            if line.startswith("- ") or line.startswith("* "):
                out.append(f"<li>{line[2:]}</li>")
                continue
            if line.startswith("✅") or line.startswith("⚠️") or line.startswith("⏳"):
                out.append(f'<div class="md-callout">{line}</div>')
                continue
            if line.strip():
                out.append(f"<p>{line}</p>")
        if in_table:
            out.append("</tbody></table>")
        return "\n".join(out)
    body = render_md(raw)
    return _HTML(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{slug} · Ardot Spec</title>
<style>
body{{background:#0F172A;color:#E2E8F0;font-family:'Fira Sans',system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:32px;line-height:1.6;}}
h1{{color:#F59E0B;border-bottom:2px solid #334155;padding-bottom:12px;font-size:32px;}}
h2{{color:#8B5CF6;font-size:24px;margin-top:32px;}}
h3,h4,h5,h6{{color:#3B82F6;font-size:18px;margin-top:24px;}}
.md-table{{width:100%;border-collapse:collapse;margin:16px 0;font-family:'Fira Code',monospace;font-size:13px;}}
.md-table th,.md-table td{{border:1px solid #334155;padding:6px 10px;text-align:left;}}
.md-table th{{background:#1E293B;color:#F1F5F9;}}
.md-table tr:nth-child(even){{background:#1E293B;}}
.md-code{{background:#0B1120;color:#22C55E;padding:12px;border-radius:6px;overflow-x:auto;font-family:'Fira Code',monospace;font-size:13px;}}
.md-callout{{background:#1E293B;border-left:4px solid #F59E0B;padding:12px;margin:12px 0;border-radius:4px;}}
p{{margin:8px 0;}}
li{{margin:4px 0;}}
a{{color:#3B82F6;}}
.back{{display:inline-block;margin-bottom:24px;color:#3B82F6;text-decoration:none;}}
.back:hover{{text-decoration:underline;}}
</style></head><body>
<a class="back" href="/ardot-specs">← 返回 spec 列表</a>
{body}
</body></html>"""
    )


@router.get("/console", response_class=HTMLResponse)
async def console_page(request: Request):
    """AI QuantX 风格统一控制台 — 7 tabs (总览/回测/对比/持仓/行情/风控/日志) A股涨红跌绿"""
    return templates.TemplateResponse(request, "console.html", _get_global_context())


@router.get("/fund-flow-report", response_class=HTMLResponse)
async def fund_flow_report_page(request: Request):
    """资金面融合回测 — 实时从 database/fund_flow_report_data.json 读"""
    from pathlib import Path
    import json

    PROJECT_ROOT = Path(__file__).resolve().parents[3]
    data_path = PROJECT_ROOT / "database" / "fund_flow_report_data.json"

    ctx = _get_global_context()
    ctx["data_available"] = False
    ctx["data"] = {"scenarios": [], "metrics": {}, "period": {}}
    ctx["baseline"] = {}
    ctx["best"] = {}
    ctx["best_name"] = "—"
    ctx["error"] = None

    if not data_path.exists():
        ctx["error"] = f"数据文件不存在: {data_path.name}"
    else:
        try:
            with open(data_path, encoding="utf-8") as f:
                payload = json.load(f)
            ctx["data_available"] = True
            ctx["data"] = payload
            ctx["baseline"] = next(
                (s for s in payload["scenarios"] if s["id"] == "v6_only"),
                payload["scenarios"][0] if payload["scenarios"] else {},
            )
            ctx["best"] = next(
                (s for s in payload["scenarios"] if s["id"] == payload.get("best_id")),
                payload["scenarios"][0] if payload["scenarios"] else {},
            )
            ctx["best_name"] = ctx["best"].get("name", "—")
        except Exception as e:
            ctx["error"] = f"数据解析失败: {e}"

    return templates.TemplateResponse(request, "fund-flow-report.html", ctx)

