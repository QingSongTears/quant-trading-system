"""
参数调优面板 — Flask 后端
=========================
提供 API: POST /backtest 接收权重配置，运行回测并返回结果。
"""
import sys, json, time, threading
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder=str(PROJECT_ROOT))
CORS(app)

# ── 全局数据（启动时加载） ──
records = []
dim_cols = []


def load_data():
    global records, dim_cols
    json_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    if not json_path.exists():
        print(f"⚠️  {json_path} 不存在, 先运行 scripts/export_7d_scores.py")
        return
    with open(json_path) as f:
        records = json.load(f)
    dim_cols = [
        "tech_weighted", "fundam_weighted", "fund_weighted",
        "institutional_weighted", "sentiment_weighted",
        "news_event_weighted", "chip_weighted",
    ]
    print(f"✅ 数据加载: {len(records)} 条, {len(dim_cols)} 维")


def spearman(x, y):
    n = len(x)
    if n < 10: return float('nan')
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    # handle ties: use raw rank
    from scipy.stats import rankdata
    rx = rankdata(x)
    ry = rankdata(y)
    d = rx - ry
    return 1 - (6 * np.sum(d**2)) / (n * (n**2 - 1))


def compute_ic(scores, ret_key):
    """计算 Spearman IC"""
    sx, sy = [], []
    for i, r in enumerate(records):
        ret = r.get(ret_key)
        if ret is not None and not (isinstance(ret, float) and np.isnan(ret)):
            if not np.isnan(scores[i]):
                sx.append(scores[i])
                sy.append(ret)
    if len(sx) < 50:
        return {"ic": None, "n": len(sx)}
    try:
        ic = spearman(np.array(sx), np.array(sy))
        return {"ic": round(float(ic), 4) if not np.isnan(ic) else None, "n": len(sx)}
    except:
        return {"ic": None, "n": len(sx)}


def quintile(scores, ret_key):
    """五分位分析"""
    arr = []
    for i, r in enumerate(records):
        ret = r.get(ret_key)
        if ret is not None and not (isinstance(ret, float) and np.isnan(ret)):
            arr.append({"s": scores[i], "r": ret})
    if len(arr) < 100:
        return []
    arr.sort(key=lambda x: x["s"])
    n = len(arr)
    qsize = n // 5
    result = []
    for q in range(5):
        start = q * qsize
        end = n if q == 4 else (q + 1) * qsize
        sl = arr[start:end]
        mean_r = np.mean([x["r"] for x in sl])
        wins = sum(1 for x in sl if x["r"] > 0)
        result.append({
            "q": q + 1,
            "mean": round(float(mean_r), 2),
            "winRate": round(wins / len(sl) * 100, 1),
            "n": len(sl),
        })
    return result


def run_backtest(weights):
    """核心回测: 给定权重 → 计算各组合IC"""
    combos = [
        {"name": "技术+基本面", "ids": ["tech_weighted", "fundam_weighted"]},
        {"name": "纯技术面", "ids": ["tech_weighted"]},
        {"name": "Tech+Fund+Inst", "ids": ["tech_weighted", "fundam_weighted", "institutional_weighted"]},
        {"name": "三因子联盟", "ids": ["tech_weighted", "fundam_weighted", "fund_weighted"]},
        {"name": "六维全开", "ids": ["tech_weighted", "fundam_weighted", "fund_weighted",
                                "institutional_weighted", "sentiment_weighted", "news_event_weighted"]},
        {"name": "七维全开", "ids": dim_cols},
        {"name": "Tech+Chip", "ids": ["tech_weighted", "chip_weighted"]},
        {"name": "技术+情绪", "ids": ["tech_weighted", "sentiment_weighted"]},
    ]

    # 预计算加权分数
    combo_scores = {}
    for combo in combos:
        total_w = sum(weights.get(did, 0) for did in combo["ids"])
        if total_w == 0:
            combo_scores[combo["name"]] = np.zeros(len(records))
            continue
        scores = np.zeros(len(records))
        for did in combo["ids"]:
            w = weights.get(did, 0)
            if w == 0:
                continue
            for i, r in enumerate(records):
                scores[i] += (r.get(did, 0) or 0) * w
        scores /= total_w
        combo_scores[combo["name"]] = scores

    # 计算IC
    results = []
    for combo in combos:
        sc = combo_scores[combo["name"]]
        ic60 = compute_ic(sc, "ret_60d")
        ic40 = compute_ic(sc, "ret_40d")
        ic20 = compute_ic(sc, "ret_20d")
        results.append({
            "name": combo["name"],
            "ic20": ic20["ic"], "ic40": ic40["ic"], "ic60": ic60["ic"],
            "n": ic60["n"],
        })

    results.sort(key=lambda x: x["ic60"] if x["ic60"] is not None else -99, reverse=True)

    # 最优组合五分位
    best = results[0]
    qs = quintile(combo_scores[best["name"]], "ret_60d")
    spread = round(qs[-1]["mean"] - qs[0]["mean"], 2) if qs else None

    # 各维度独立IC
    dim_ics = {}
    for did in dim_cols:
        sc = np.array([(r.get(did, 0) or 0) for r in records])
        ic = compute_ic(sc, "ret_60d")
        dim_ics[did] = ic["ic"]

    return {
        "combos": results,
        "bestCombo": best["name"],
        "quintile": qs,
        "spread": spread,
        "dimICs": dim_ics,
    }


# ── API ──
@app.route("/")
def index():
    return send_from_directory(str(PROJECT_ROOT / "output"), "tuning_panel.html")


@app.route("/api/status")
def status():
    return jsonify({"loaded": len(records), "dims": dim_cols})


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    if not records:
        return jsonify({"error": "数据未加载"}), 500

    data = request.get_json()
    weights_raw = data.get("weights", {})

    # 解析权重
    weights = {}
    for did in dim_cols:
        try:
            w = float(weights_raw.get(did, 0))
            weights[did] = max(0, min(2.0, w))
        except:
            weights[did] = 0

    t0 = time.time()
    result = run_backtest(weights)
    result["elapsed"] = round(time.time() - t0, 2)
    result["weights"] = weights
    return jsonify(result)


# ── 启动 ──
if __name__ == "__main__":
    load_data()
    app.run(host="0.0.0.0", port=8081, debug=False)
