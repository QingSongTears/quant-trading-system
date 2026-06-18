"""
参数调优面板 — Flask 后端 v2 (支持暂停/继续/停止)
==================================================
API:
  POST /api/backtest        启动回测 → {task_id}
  GET  /api/backtest/status 查询状态 → {status, progress, result?}
  POST /api/backtest/pause  暂停
  POST /api/backtest/resume 继续
  POST /api/backtest/stop   停止
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

# ── 全局数据 ──
records = []
dim_cols = []

# ── 任务管理 ──
class BacktestTask:
    def __init__(self):
        self.status = "idle"       # idle | running | paused | stopped | done | error
        self.progress = 0
        self.weights = {}
        self.result = None
        self.error = None
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def start(self, weights):
        with self._lock:
            if self.status == "running":
                return False, "已有回测在运行"
            self.status = "running"
            self.progress = 0
            self.weights = weights
            self.result = None
            self.error = None
            self._pause_event.clear()
            self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True, "已启动"

    def pause(self):
        with self._lock:
            if self.status != "running":
                return False, "没有正在运行的回测"
            self.status = "paused"
            self._pause_event.set()
        return True, "已暂停"

    def resume(self):
        with self._lock:
            if self.status != "paused":
                return False, "没有暂停中的回测"
            self.status = "running"
            self._pause_event.clear()
        return True, "已继续"

    def stop(self):
        with self._lock:
            if self.status not in ("running", "paused"):
                return False, "没有可停止的回测"
            self._stop_event.set()
            self._pause_event.clear()  # 如果暂停中, 先解除暂停再停止
            self.status = "stopped"
        return True, "已停止"

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "progress": self.progress,
                "weights": self.weights,
                "result": self.result,
                "error": self.error,
            }

    def _check_pause(self):
        """检查是否需要暂停, 如果需要则阻塞等待"""
        if self._pause_event.is_set():
            while self._pause_event.is_set() and not self._stop_event.is_set():
                time.sleep(0.2)
        return not self._stop_event.is_set()

    def _run(self):
        try:
            if not self._check_pause(): return

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

            w = self.weights
            self.progress = 10

            # Phase 1: 计算加权分数
            combo_scores = {}
            for ci, combo in enumerate(combos):
                if not self._check_pause(): return
                if self._stop_event.is_set(): return

                total_w = sum(w.get(did, 0) for did in combo["ids"])
                if total_w == 0:
                    combo_scores[combo["name"]] = np.zeros(len(records))
                else:
                    scores = np.zeros(len(records))
                    for did in combo["ids"]:
                        wv = w.get(did, 0)
                        if wv == 0: continue
                        for i, r in enumerate(records):
                            scores[i] += (r.get(did, 0) or 0) * wv
                    scores /= total_w
                    combo_scores[combo["name"]] = scores
                self.progress = 10 + int(30 * (ci+1) / len(combos))

            if not self._check_pause(): return
            if self._stop_event.is_set(): return
            self.progress = 45

            # Phase 2: 计算IC
            results = []
            for ci, combo in enumerate(combos):
                if not self._check_pause(): return
                if self._stop_event.is_set(): return

                sc = combo_scores[combo["name"]]
                ic60 = self._compute_ic(sc, "ret_60d")
                ic40 = self._compute_ic(sc, "ret_40d")
                ic20 = self._compute_ic(sc, "ret_20d")
                results.append({"name": combo["name"], "ic20": ic60["ic"], "ic40": ic40["ic"], "ic60": ic60["ic"], "n": ic60["n"]})
                self.progress = 45 + int(35 * (ci+1) / len(combos))

            results.sort(key=lambda x: x["ic60"] if x["ic60"] is not None else -99, reverse=True)

            if not self._check_pause(): return
            if self._stop_event.is_set(): return
            self.progress = 85

            # Phase 3: 五分位 + 维度IC
            best = results[0]
            qs = self._quintile(combo_scores[best["name"]], "ret_60d")
            spread = round(qs[-1]["mean"] - qs[0]["mean"], 2) if qs else None

            dim_ics = {}
            for ci, did in enumerate(dim_cols):
                if self._stop_event.is_set(): return
                sc = np.array([(r.get(did, 0) or 0) for r in records])
                dim_ics[did] = self._compute_ic(sc, "ret_60d")["ic"]
                self.progress = 85 + int(15 * (ci+1) / len(dim_cols))

            self.progress = 100
            with self._lock:
                self.result = {
                    "combos": results, "bestCombo": best["name"],
                    "quintile": qs, "spread": spread, "dimICs": dim_ics,
                }
                self.status = "done"

        except Exception as e:
            with self._lock:
                self.error = str(e)
                self.status = "error"

    @staticmethod
    def _compute_ic(scores, ret_key):
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
            from scipy.stats import rankdata
            rx = rankdata(np.array(sx))
            ry = rankdata(np.array(sy))
            d = rx - ry
            n = len(d)
            ic = 1 - (6 * np.sum(d**2)) / (n * (n**2 - 1))
            return {"ic": round(float(ic), 4) if not np.isnan(ic) else None, "n": n}
        except:
            return {"ic": None, "n": len(sx)}

    @staticmethod
    def _quintile(scores, ret_key):
        arr = []
        for i, r in enumerate(records):
            ret = r.get(ret_key)
            if ret is not None and not (isinstance(ret, float) and np.isnan(ret)):
                arr.append({"s": scores[i], "r": ret})
        if len(arr) < 100: return []
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
            result.append({"q": q+1, "mean": round(float(mean_r), 2), "winRate": round(wins/len(sl)*100, 1), "n": len(sl)})
        return result


task = BacktestTask()


def load_data():
    global records, dim_cols
    json_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    if not json_path.exists():
        print(f"⚠️  {json_path} 不存在")
        return
    with open(json_path) as f:
        records = json.load(f)
    dim_cols = [
        "tech_weighted", "fundam_weighted", "fund_weighted",
        "institutional_weighted", "sentiment_weighted",
        "news_event_weighted", "chip_weighted",
    ]
    print(f"✅ 数据加载: {len(records)} 条, {len(dim_cols)} 维")


# ── API ──
@app.route("/")
def index():
    return send_from_directory(str(PROJECT_ROOT / "output"), "tuning_panel.html")


@app.route("/api/status")
def api_status():
    return jsonify({"loaded": len(records), "dims": dim_cols, "task": task.snapshot()})


@app.route("/api/backtest", methods=["POST"])
def api_start():
    data = request.get_json()
    weights_raw = data.get("weights", {})
    weights = {}
    for did in dim_cols:
        try:
            w = float(weights_raw.get(did, 0))
            weights[did] = max(0, min(2.0, w))
        except:
            weights[did] = 0

    ok, msg = task.start(weights)
    return jsonify({"ok": ok, "msg": msg, "task": task.snapshot()})


@app.route("/api/backtest/status")
def api_task_status():
    return jsonify(task.snapshot())


@app.route("/api/backtest/pause", methods=["POST"])
def api_pause():
    ok, msg = task.pause()
    return jsonify({"ok": ok, "msg": msg, "task": task.snapshot()})


@app.route("/api/backtest/resume", methods=["POST"])
def api_resume():
    ok, msg = task.resume()
    return jsonify({"ok": ok, "msg": msg, "task": task.snapshot()})


@app.route("/api/backtest/stop", methods=["POST"])
def api_stop():
    ok, msg = task.stop()
    return jsonify({"ok": ok, "msg": msg, "task": task.snapshot()})


@app.route("/api/stock/<code>")
def api_stock(code):
    """查询单只股票的历史评分与收益"""
    code = str(code).zfill(6)
    stock_data = [r for r in records if r.get("code") == code]
    if not stock_data:
        return jsonify({"error": f"未找到股票 {code}"}), 404

    # 按日期排序
    stock_data.sort(key=lambda x: x.get("as_of_date", ""))

    # 分离评分序列和收益序列
    dates = [r["as_of_date"] for r in stock_data]
    scores = {
        "技术面": [r.get("tech_weighted") for r in stock_data],
        "基本面": [r.get("fundam_weighted") for r in stock_data],
        "资金面": [r.get("fund_weighted") for r in stock_data],
        "机构面": [r.get("institutional_weighted") for r in stock_data],
        "情绪面": [r.get("sentiment_weighted") for r in stock_data],
        "新闻面": [r.get("news_event_weighted") for r in stock_data],
        "筹码面": [r.get("chip_weighted") for r in stock_data],
    }
    returns = {
        "20日": [r.get("ret_20d") for r in stock_data],
        "40日": [r.get("ret_40d") for r in stock_data],
        "60日": [r.get("ret_60d") for r in stock_data],
    }

    # 计算综合评分
    combined = []
    for r in stock_data:
        vals = [r.get(d) or 0 for d in dim_cols]
        combined.append(round(sum(vals)/len(vals), 1))

    return jsonify({
        "code": code,
        "dates": dates,
        "scores": scores,
        "returns": returns,
        "combined": combined,
    })


@app.route("/api/stock/<code>/kline")
def api_kline(code):
    """查询单只股票的日K线数据"""
    code = str(code).zfill(6)
    import sqlite3
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume "
        "FROM daily_price WHERE code=? ORDER BY trade_date",
        (code,)
    ).fetchall()
    conn.close()

    if not rows:
        return jsonify({"error": f"未找到K线 {code}"}), 404

    # 最近120天
    recent = rows[-120:]
    return jsonify({
        "code": code,
        "dates": [r["trade_date"] for r in recent],
        "open": [r["open"] for r in recent],
        "high": [r["high"] for r in recent],
        "low": [r["low"] for r in recent],
        "close": [r["close"] for r in recent],
        "volume": [r["volume"] for r in recent],
    })


if __name__ == "__main__":
    load_data()
    app.run(host="0.0.0.0", port=8081, debug=False)
