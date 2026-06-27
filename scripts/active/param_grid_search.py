"""
参数网格搜索脚本 — Issue #37 (T16)

三大搜索模式:
  grid      全枚举网格搜索 (精确但组合数<5000时适用)
  random    随机采样搜索 (适合大参数空间)
  bayesian  简单贝叶斯优化 (基于贪婪细化, 适合小参数空间)

用法:
    python scripts/param_grid_search.py ma_cross 000001 \\
        --start 2020-01-01 --end 2025-06-01 \\
        --param fast_period:3,5,10,20 slow_period:15,26,40 \\
        --mode grid --metric sharpe_ratio --workers 4 \\
        --export output/param_search_results.csv \\
        --heatmap output/param_heatmap.html
"""
import sys, json, itertools, argparse
from pathlib import Path
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.engine import BacktestEngine
from src.config import load_strategies


HEATMAP_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<title>参数搜索热力图</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
body {{ font-family: system-ui; max-width: 1000px; margin: 20px auto; padding: 0 20px; background: #f5f5f5; }}
h1,h2,h3 {{ color: #333; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; }}
th,td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right; font-size: 13px; }}
th {{ background: #4a90d9; color: #fff; text-align: center; }}
tr:nth-child(even) {{ background: #f9f9f9; }}
td.min {{ color: #c0392b; }}
td.mid {{ color: #e67e22; }}
td.max {{ color: #27ae60; }}
canvas {{ display: block; margin: 20px 0; }}
.summary {{ background: #fff; border-radius: 8px; padding: 16px; margin: 16px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
.tag-best {{ background: #27ae60; color: #fff; }}
.tag-ok {{ background: #3498db; color: #fff; }}
.tag-warn {{ background: #e67e22; color: #fff; }}
</style>
</head>
<body>
<h1>📊 参数搜索结果</h1>
<div class="summary">
<h3>搜索概要</h3>
<ul>
<li><strong>策略:</strong> {strategy_name}</li>
<li><strong>股票:</strong> {stock_code} {stock_name}</li>
<li><strong>区间:</strong> {start_date} ~ {end_date}</li>
<li><strong>搜索模式:</strong> {search_mode}</li>
<li><strong>参数组合数:</strong> {total_combos}</li>
<li><strong>排序指标:</strong> {metric}</li>
<li><strong>最优 {metric}:</strong> {best_value}</li>
</ul>
</div>

<h2>参数重要性 (Spearman 相关性)</h2>
<table><tr><th>参数</th><th>与 {metric} 相关性</th><th>说明</th></tr>
{param_importance_rows}
</table>

<h2>Top 20 参数组合</h2>
<table>
<tr><th>排名</th><th>参数</th>
{metric_headers}
</tr>
{top_rows}
</table>

<h2>参数空间热力图</h2>
{heatmap_canvases}

<h2>综合评分排名</h2>
<table>
<tr><th>排名</th><th>参数</th><th>综合评分</th>
{combo_metric_headers}
</tr>
{combo_top_rows}
</table>
</body></html>"""


def parse_param_spec(specs: List[str]) -> Dict[str, list]:
    """
    解析参数规格: "fast_period:3,5,10,20" → {"fast_period": [3,5,10,20]}
    支持 int 和 float 类型自动检测
    """
    param_grid = {}
    for spec in specs:
        if ":" not in spec:
            print(f"⚠️  参数格式错误: {spec} (应为 name:val1,val2,...)")
            continue
        name, vals_str = spec.split(":", 1)
        vals = []
        for v in vals_str.split(","):
            v = v.strip()
            if not v:
                continue
            try:
                if "." in v:
                    vals.append(float(v))
                else:
                    vals.append(int(v))
            except ValueError:
                vals.append(v)
        if vals:
            param_grid[name] = vals
    return param_grid


def get_strategy_metadata(strategy_name: str):
    """获取策略的元数据 (参数定义/描述)"""
    config = load_strategies()
    for s in config.get("strategies", []):
        if s["name"] == strategy_name:
            return s
    return None


def compute_param_importance(results: List[Dict], metric: str) -> List[Dict]:
    """
    计算每个参数与目标指标的相关性
    使用 Spearman 秩相关 (样本数>5时)
    """
    from scipy.stats import spearmanr

    if not results:
        return []

    # 收集所有参数键
    param_keys = set()
    for r in results:
        param_keys.update(r["params"].keys())

    importance = []
    for pk in sorted(param_keys):
        x_vals = []
        y_vals = []
        for r in results:
            v = r["params"].get(pk)
            m = r.get(metric)
            if v is not None and m is not None:
                try:
                    x_vals.append(float(v))
                    y_vals.append(float(m))
                except (ValueError, TypeError):
                    pass
        if len(x_vals) < 5:
            importance.append({"param": pk, "correlation": None, "note": "样本不足"})
            continue
        try:
            corr, pval = spearmanr(x_vals, y_vals)
            importance.append({
                "param": pk,
                "correlation": round(corr, 4),
                "p_value": round(pval, 4),
                "note": "显著" if pval < 0.05 else ("弱相关" if pval < 0.1 else "不显著"),
            })
        except Exception:
            importance.append({"param": pk, "correlation": None, "note": "计算失败"})

    importance.sort(key=lambda x: abs(x["correlation"] or 0), reverse=True)
    return importance


def generate_heatmap_html(results: List[Dict], param_grid: Dict, metric: str) -> Optional[str]:
    """生成参数搜索热力图 HTML (最多 2 个参数做散点图)"""
    import plotly.express as px

    if not results:
        return None

    df = pd.DataFrame([
        {**r["params"], metric: r.get(metric, 0)}
        for r in results
    ])

    param_keys = list(param_grid.keys())
    sections = []

    if len(param_keys) >= 1:
        # 参数 vs 指标 散点图
        figs_html = []
        for pk in param_keys[:3]:
            fig = px.scatter(
                df, x=pk, y=metric,
                title=f"{pk} vs {metric}",
                labels={pk: pk, metric: metric},
                trendline="lowess" if len(df) > 10 else None,
                opacity=0.7,
                width=600, height=400,
            )
            figs_html.append(fig.to_html(full_html=False, include_plotlyjs=False))

        sections.append('<div style="display:flex;flex-wrap:wrap;gap:20px">')
        for fh in figs_html:
            sections.append(f'<div>{fh}</div>')
        sections.append('</div>')

    if len(param_keys) >= 2:
        # 热力图: 前 2 个参数作为 X/Y
        x_key, y_key = param_keys[0], param_keys[1]
        pivot = df.pivot_table(
            values=metric,
            index=y_key, columns=x_key,
            aggfunc="mean"
        )
        if not pivot.empty and pivot.size > 1:
            fig = px.imshow(
                pivot,
                title=f"{metric} 参数热力图 ({x_key} x {y_key})",
                labels={"x": x_key, "y": y_key, "color": metric},
                aspect="auto",
                color_continuous_scale="RdYlGn",
                width=700, height=500,
            )
            sections.append(fig.to_html(full_html=False, include_plotlyjs=False))

    return "".join(sections)


def export_results_csv(results: List[Dict], output_path: str):
    """导出搜索结果为 CSV"""
    rows = []
    for r in results:
        row = {"排名": r.get("composite_rank", 0) if "composite_rank" in r else 0}
        row.update(r["params"])
        row.update({
            "综合评分": r.get("composite_score", ""),
            "总收益率%": r["total_return"],
            "年化收益率%": r.get("annual_return", ""),
            "夏普比率": r["sharpe_ratio"],
            "最大回撤%": r["max_drawdown"],
            "胜率%": r["win_rate"],
            "交易次数": r["total_trades"],
            "卡玛比率": r.get("calmar_ratio", ""),
        })
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"✅ 搜索结果导出: {output_path} ({len(rows)} 条)")


def print_top_results(results: List[Dict], param_grid: Dict, metric: str, top_n: int = 20):
    """打印 Top N 搜索结果"""
    param_keys = list(param_grid.keys())
    headers = ["排名"] + [f"综合评分" if "composite_score" in r else "" for r in results[:1]]
    print(f"\n{'='*100}")
    print(f"参数搜索 Top {top_n} (按 {metric} 排序)")
    print(f"{'='*100}")

    # Header
    header = f" {'排名':<4} "
    for pk in param_keys:
        header += f" {pk:<12}"
    header += f" {'总收益%':<8} {'年化%':<8} {'夏普':<7} {'回撤%':<7} {'胜率%':<6} {'交易':<5}"
    if "composite_score" in results[0]:
        header += " {'综合':<6}"
    print(header)
    print(f" {'-'*100}")

    for i, r in enumerate(results[:top_n]):
        line = f" {i+1:<3}. "
        for pk in param_keys:
            v = r["params"].get(pk, "")
            if isinstance(v, float):
                line += f" {v:<12.4f}"
            else:
                line += f" {str(v):<12}"
        line += (f" {r['total_return']:<8.2f} {r.get('annual_return', 0):<8.2f} "
                 f"{r['sharpe_ratio']:<7.2f} {r['max_drawdown']:<7.2f} "
                 f"{r['win_rate']:<6.1f} {r['total_trades']:<5}")
        if "composite_score" in r:
            line += f" {r['composite_score']:<6.4f}"
        print(line)

    print(f" {'-'*100}")
    print(f"最优组合: {results[0]['params']}")
    print(f"  {metric}={results[0].get(metric, 'N/A')}")


def generate_html_report(
    results: List[Dict], param_grid: Dict, metric: str,
    strategy_name: str, stock_code: str, stock_name: str,
    start_date: str, end_date: str, search_mode: str,
    output_path: str,
):
    """生成 HTML 报告"""
    import plotly
    best_value = results[0].get(metric, "N/A") if results else "N/A"
    total_combos = len(results)

    # 参数重要性
    importance = compute_param_importance(results, metric)
    param_importance_rows = "".join(
        f"<tr><td>{imp['param']}</td>"
        f"<td>{imp['correlation'] if imp['correlation'] is not None else '-'}</td>"
        f"<td>{imp['note']}</td></tr>"
        for imp in importance
    )

    # Top 20 表格
    param_keys = list(param_grid.keys())
    metric_headers = "".join(f"<th>{m}</th>" for m in ["总收益%", "年化%", "夏普", "回撤%", "胜率%", "交易", metric])
    top_rows = ""
    for i, r in enumerate(results[:20]):
        param_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        vals = [r.get("total_return", 0), r.get("annual_return", 0),
                r.get("sharpe_ratio", 0), r.get("max_drawdown", 0),
                r.get("win_rate", 0), r.get("total_trades", 0),
                r.get(metric, 0)]
        val_cells = "".join(f"<td class=\"{'max' if j == 6 else ''}\">{v}</td>" for j, v in enumerate(vals))
        top_rows += f"<tr><td>{i+1}</td><td style='text-align:left'>{param_str}</td>{val_cells}</tr>"

    # 热力图
    heatmap_canvases = generate_heatmap_html(results, param_grid, metric) or "<p>无法生成热力图</p>"

    # 综合评分排名 (Top 20)
    combo_metric_headers = "".join(f"<th>{m}</th>" for m in ["总收益%", "年化%", "夏普", "回撤%", "胜率%", "交易"])
    combo_top_rows = ""
    for i, r in enumerate(results[:20]):
        param_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
        comp = r.get("composite_score", "")
        vals = [r.get("total_return", 0), r.get("annual_return", 0),
                r.get("sharpe_ratio", 0), r.get("max_drawdown", 0),
                r.get("win_rate", 0), r.get("total_trades", 0)]
        val_cells = "".join(f"<td>{v}</td>" for v in vals)
        combo_top_rows += f"<tr><td>{i+1}</td><td style='text-align:left'>{param_str}</td><td>{comp}</td>{val_cells}</tr>"

    html = HEATMAP_TEMPLATE.format(
        strategy_name=strategy_name,
        stock_code=stock_code,
        stock_name=stock_name,
        start_date=start_date,
        end_date=end_date,
        search_mode=search_mode,
        total_combos=total_combos,
        metric=metric,
        best_value=best_value,
        param_importance_rows=param_importance_rows,
        metric_headers=metric_headers,
        top_rows=top_rows,
        heatmap_canvases=heatmap_canvases,
        combo_metric_headers=combo_metric_headers,
        combo_top_rows=combo_top_rows,
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 热力图报告: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="参数搜索 — 网格/随机/贝叶斯")
    parser.add_argument("strategy", help="策略名称")
    parser.add_argument("stock_code", help="股票代码")
    parser.add_argument("--start", default="2020-01-01", help="开始日期")
    parser.add_argument("--end", default="2025-06-01", help="结束日期")
    parser.add_argument("--capital", type=float, default=100000, help="初始资金")
    parser.add_argument("--param", nargs="+", required=True,
                        help="参数定义: name:val1,val2,... (如 fast_period:3,5,10,20)")
    parser.add_argument("--mode", default="grid",
                        choices=["grid", "random", "bayesian"],
                        help="搜索模式: grid=枚举, random=随机, bayesian=贝叶斯")
    parser.add_argument("--metric", default="sharpe_ratio",
                        choices=["sharpe_ratio", "total_return", "annual_return",
                                 "max_drawdown", "calmar_ratio"],
                        help="优化目标指标")
    parser.add_argument("--workers", type=int, default=4, help="并行线程数")
    parser.add_argument("--n-iter", type=int, default=100,
                        help="随机/贝叶斯模式采样次数")
    parser.add_argument("--n-initial", type=int, default=10,
                        help="贝叶斯模式初始采样次数")
    parser.add_argument("--top", type=int, default=20, help="显示 Top N")
    parser.add_argument("--export", help="导出 CSV 路径")
    parser.add_argument("--html", help="导出 HTML 报告路径")
    parser.add_argument("--multi-metric", nargs="+",
                        default=["sharpe_ratio:0.5", "total_return:0.3", "max_drawdown:0.2"],
                        help="多指标优化权重 (格式 metric:weight)")

    args = parser.parse_args()

    # 解析参数
    param_grid = parse_param_spec(args.param)
    if not param_grid:
        print("❌ 无有效参数定义")
        sys.exit(1)

    print(f"\n🚀 参数搜索:")
    print(f"   策略: {args.strategy}")
    print(f"   股票: {args.stock_code}")
    print(f"   区间: {args.start} ~ {args.end}")
    print(f"   模式: {args.mode}")
    print(f"   目标: {args.metric}")
    print(f"   参数: {param_grid}")

    engine = BacktestEngine()
    stock_name = ""
    try:
        from src.models.repository import DataRepository
        repo = DataRepository()
        stock_list = repo.get_stock_list()
        match = stock_list[stock_list["code"] == args.stock_code]
        if not match.empty:
            stock_name = match.iloc[0]["name"]
    except Exception:
        pass

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    # 执行搜索
    if args.mode == "grid":
        total_combos = 1
        for vals in param_grid.values():
            total_combos *= len(vals)
        print(f"   组合数: {total_combos}")

        results = engine.run_grid_search(
            strategy_class=None,  # 需动态加载
            stock_code=args.stock_code,
            start_date=start_date,
            end_date=end_date,
            param_grid=param_grid,
            metric=args.metric,
            max_workers=args.workers,
        )

    elif args.mode == "random":
        # 将 param_grid 转为 param_ranges 格式
        param_ranges = {}
        for k, vals in param_grid.items():
            if all(isinstance(v, int) for v in vals):
                param_ranges[k] = (min(vals), max(vals), "int")
            elif all(isinstance(v, float) for v in vals):
                param_ranges[k] = (min(vals), max(vals), "float")
            else:
                param_ranges[k] = (0, 0, "categorical")
                param_ranges[k] = (0, 0, "categorical")
                param_ranges[k] = (vals, vals, "categorical")

        # 修正: categorical 用列表
        for k, vals in param_grid.items():
            if any(isinstance(v, str) for v in vals):
                param_ranges[k] = (vals, vals, "categorical")

        results = engine.run_random_search(
            strategy_class=None,
            stock_code=args.stock_code,
            start_date=start_date,
            end_date=end_date,
            param_ranges=param_ranges,
            n_iter=args.n_iter,
            metric=args.metric,
            max_workers=args.workers,
        )

    else:  # bayesian
        param_ranges = {}
        for k, vals in param_grid.items():
            if all(isinstance(v, int) for v in vals):
                param_ranges[k] = (min(vals), max(vals), "int")
            elif all(isinstance(v, float) for v in vals):
                param_ranges[k] = (min(vals), max(vals), "float")
            else:
                param_ranges[k] = (vals, vals, "categorical")
        for k, vals in param_grid.items():
            if any(isinstance(v, str) for v in vals):
                param_ranges[k] = (vals, vals, "categorical")

        results = engine.run_bayesian_search(
            strategy_class=None,
            stock_code=args.stock_code,
            start_date=start_date,
            end_date=end_date,
            param_ranges=param_ranges,
            n_iter=args.n_iter,
            n_initial=args.n_initial,
            metric=args.metric,
            max_workers=args.workers,
        )

    # ⚠️ 动态加载策略 (因为上面传了 None)
    strategy_class = None
    meta = get_strategy_metadata(args.strategy)
    if meta:
        import importlib
        module_path, class_name = meta["class_path"].rsplit(".", 1)
        module = importlib.import_module(module_path)
        strategy_class = getattr(module, class_name)

    if strategy_class:
        # 重新用正确策略执行一次搜索
        engine = BacktestEngine()
        if args.mode == "grid":
            results = engine.run_grid_search(
                strategy_class=strategy_class,
                stock_code=args.stock_code,
                start_date=start_date,
                end_date=end_date,
                param_grid=param_grid,
                metric=args.metric,
                max_workers=args.workers,
            )
        elif args.mode == "random":
            param_ranges = {}
            for k, vals in param_grid.items():
                if all(isinstance(v, int) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "int")
                elif all(isinstance(v, float) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "float")
                else:
                    param_ranges[k] = (vals, vals, "categorical")
            for k, vals in param_grid.items():
                if any(isinstance(v, str) for v in vals):
                    param_ranges[k] = (vals, vals, "categorical")
            results = engine.run_random_search(
                strategy_class=strategy_class,
                stock_code=args.stock_code,
                start_date=start_date,
                end_date=end_date,
                param_ranges=param_ranges,
                n_iter=args.n_iter,
                metric=args.metric,
                max_workers=args.workers,
            )
        else:
            param_ranges = {}
            for k, vals in param_grid.items():
                if all(isinstance(v, int) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "int")
                elif all(isinstance(v, float) for v in vals):
                    param_ranges[k] = (min(vals), max(vals), "float")
                else:
                    param_ranges[k] = (vals, vals, "categorical")
            for k, vals in param_grid.items():
                if any(isinstance(v, str) for v in vals):
                    param_ranges[k] = (vals, vals, "categorical")
            results = engine.run_bayesian_search(
                strategy_class=strategy_class,
                stock_code=args.stock_code,
                start_date=start_date,
                end_date=end_date,
                param_ranges=param_ranges,
                n_iter=args.n_iter,
                n_initial=args.n_initial,
                metric=args.metric,
                max_workers=args.workers,
            )

    if not results:
        print("❌ 搜索无结果")
        sys.exit(1)

    # 多指标优化
    if args.multi_metric and len(args.multi_metric) > 1:
        results = engine.multi_metric_optimize(results, args.multi_metric)

    # 打印
    print_top_results(results, param_grid, args.metric, args.top)

    # 导出 CSV
    if args.export:
        export_results_csv(results, args.export)

    # 导出 HTML 报告
    if args.html:
        generate_html_report(
            results, param_grid, args.metric,
            args.strategy, args.stock_code, stock_name,
            args.start, args.end, args.mode,
            args.html,
        )


if __name__ == "__main__":
    main()
