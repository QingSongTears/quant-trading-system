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
industry_map = {}  # code(6-digit) → industry_name
industry_ic_cache = None  # 缓存行业IC结果

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
                self.progress = 85 + int(8 * (ci+1) / len(dim_cols))

            # Phase 4: 行业暴露分析 — 按行业分组计算最佳组合IC
            industry_ics = _compute_industry_ics(combo_scores[best["name"]], best["name"])
            self.progress = 98

            self.progress = 100
            with self._lock:
                self.result = {
                    "combos": results, "bestCombo": best["name"],
                    "quintile": qs, "spread": spread, "dimICs": dim_ics,
                    "industryICs": industry_ics,
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
    global records, dim_cols, industry_map
    json_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    if not json_path.exists():
        print(f"⚠️  {json_path} 不存在")
        return
    with open(json_path) as f:
        records = json.load(f)
    dim_cols = [
        "tech_weighted", "fundam_weighted", "fund_weighted",
        "institutional_weighted", "lh_institutional_weighted",
        "sentiment_weighted", "news_event_weighted", "chip_weighted",
    ]
    # 加载行业映射 (stock_profile: code→industry)
    import sqlite3
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT code, industry FROM stock_profile").fetchall()
    conn.close()
    for r in rows:
        raw_code = r[0]
        industry = r[1] or "其他"
        # 去掉 sz/sh 前缀 → 6位代码
        code6 = raw_code.replace("sz","").replace("sh","")
        industry_map[code6] = industry
    print(f"✅ 数据加载: {len(records)} 条, {len(dim_cols)} 维, {len(industry_map)} 只含行业")


# ── API ──
@app.route("/")
def index():
    return send_from_directory(str(PROJECT_ROOT / "output"), "index.html")


@app.route("/tuning")
def tuning_panel():
    return send_from_directory(str(PROJECT_ROOT / "output"), "tuning_panel.html")


@app.route("/predict")
def predict_dashboard():
    return send_from_directory(str(PROJECT_ROOT / "output"), "predict_dashboard.html")


@app.route("/ic")
def ic_analysis():
    return send_from_directory(str(PROJECT_ROOT / "output"), "ic_analysis.html")


@app.route("/v6-compare")
def v6_compare():
    return send_from_directory(str(PROJECT_ROOT / "output"), "v6_compare.html")


@app.route("/bull-report")
def bull_report():
    return send_from_directory(str(PROJECT_ROOT / "output"), "bull_backtest_report.html")


@app.route("/dim-compare")
def dim_compare():
    return send_from_directory(str(PROJECT_ROOT / "output"), "dim_compare.html")


@app.route("/fund-flow-report")
def fund_flow_report():
    return send_from_directory(str(PROJECT_ROOT / "output"), "fund_flow_report.html")


@app.route("/backtest-view")
def backtest_view():
    return send_from_directory(str(PROJECT_ROOT / "output"), "backtest_view.html")


@app.route("/diagnose")
def diagnose_page():
    return send_from_directory(str(PROJECT_ROOT / "output"), "diagnose.html")


@app.route("/backtest-lab")
def backtest_lab():
    return send_from_directory(str(PROJECT_ROOT / "output"), "backtest_lab.html")


@app.route("/sector")
def sector_page():
    return send_from_directory(str(PROJECT_ROOT / "output"), "sector.html")


@app.route("/data-monitor")
def data_monitor():
    return send_from_directory(str(PROJECT_ROOT / "output"), "data_monitor.html")


@app.route("/screener")
def screener_page():
    return send_from_directory(str(PROJECT_ROOT / "output"), "screener.html")


@app.route("/portfolio")
def portfolio_page():
    return send_from_directory(str(PROJECT_ROOT / "output"), "portfolio.html")


# ── 股票搜索API ──
SEARCH_INDEX = None  # 懒加载

def _build_search_index():
    """构建搜索索引: code/name/拼音首字母"""
    global SEARCH_INDEX
    if SEARCH_INDEX is not None:
        return SEARCH_INDEX
    try:
        import sqlite3
        from pypinyin import lazy_pinyin, Style
        db = sqlite3.connect(str(PROJECT_ROOT / "database" / "quant.db"))
        cur = db.cursor()
        cur.execute("SELECT code, name FROM stock_basic WHERE name IS NOT NULL")
        rows = cur.fetchall()
        db.close()
        index = []
        for code, name in rows:
            code = code.strip()
            name = name.strip() if name else ""
            initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)) if name else ""
            full_py = "".join(lazy_pinyin(name)) if name else ""
            index.append({"code": code, "name": name, "initials": initials, "full_py": full_py})
        SEARCH_INDEX = index
        print(f"  [搜索索引] {len(index)} 只股票已加载")
    except Exception as e:
        print(f"  [搜索索引] 失败: {e}")
        SEARCH_INDEX = []
    return SEARCH_INDEX


@app.route("/api/stock/search")
def api_stock_search():
    """搜索股票: 支持代码/名称/拼音首字母模糊搜索"""
    q = request.args.get("q", "").strip().lower()
    if not q or len(q) < 1:
        return jsonify({"results": []})
    
    index = _build_search_index()
    results = []
    for item in index:
        # 代码匹配
        if q in item["code"]:
            results.append({"code": item["code"], "name": item["name"], "match": "code"})
        # 名称匹配
        elif q in item["name"].lower():
            results.append({"code": item["code"], "name": item["name"], "match": "name"})
        # 拼音首字母匹配
        elif q in item["initials"]:
            results.append({"code": item["code"], "name": item["name"], "match": "pinyin"})
        # 全拼匹配
        elif q in item["full_py"]:
            results.append({"code": item["code"], "name": item["name"], "match": "pinyin"})
        
        if len(results) >= 20:
            break
    
    return jsonify({"results": results})


@app.route("/api/status")
def api_status():
    return jsonify({"loaded": len(records), "dims": dim_cols, "task": task.snapshot()})


@app.route("/api/data/db-status")
def api_db_status():
    """数据库状态：各表行数、最新/最早日期"""
    import sqlite3
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    result = []
    for t in tables:
        name = t[0]
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM \"{name}\"").fetchone()
            count = row[0] if row else 0
            latest = None
            earliest = None
            # 尝试获取日期列的最新值
            for date_col in ['trade_date', 'date', 'as_of_date', 'created_at', 'report_date']:
                try:
                    r = conn.execute(f"SELECT MAX({date_col}) FROM \"{name}\"").fetchone()
                    if r and r[0]:
                        latest = r[0]
                        r2 = conn.execute(f"SELECT MIN({date_col}) FROM \"{name}\"").fetchone()
                        if r2 and r2[0]:
                            earliest = r2[0]
                        break
                except:
                    continue
            descriptions = {
                "daily_price": "日K线数据", "stock_basic": "股票基本信息",
                "tencent_quotes": "腾讯实时行情", "stock_profile": "股票档案",
                "fund_flow": "资金流向", "technical_indicators": "技术指标",
                "lhb_institutional": "龙虎榜机构", "margin_trading": "融资融券",
                "shareholder_count": "股东人数", "finance_summary": "财务摘要",
                "backtest_result": "回测结果", "strategy_config": "策略配置",
            }
            result.append({
                "name": name, "row_count": count,
                "latest_date": str(latest)[:10] if latest else None,
                "earliest_date": str(earliest)[:10] if earliest else None,
                "description": descriptions.get(name, ""),
            })
        except:
            pass
    conn.close()
    return jsonify({"tables": result})


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


@app.route("/api/scan", methods=["POST"])
def api_scan():
    """参数网格扫描: 对每个维度按指定步长扫描，返回IC最优的Top 20组合"""
    data = request.get_json() or {}
    mode = data.get("mode", "fast")  # fast=0/1二分, medium=0/0.5/1三步, full=0/0.25/0.5/0.75/1五步
    
    step_map = {"fast": [0, 1.0], "medium": [0, 0.5, 1.0], "full": [0, 0.25, 0.5, 0.75, 1.0]}
    values = step_map.get(mode, step_map["fast"])
    
    # 只扫描用户指定的维度，默认全体
    selected_dims = data.get("dims", dim_cols)
    selected_dims = [d for d in selected_dims if d in dim_cols]
    
    if not selected_dims:
        return jsonify({"error": "无有效维度"}), 400
    
    # 生成所有组合 (笛卡尔积)
    from itertools import product
    all_combos = list(product(values, repeat=len(selected_dims)))
    total_combos = len(all_combos)
    
    if total_combos > 10000:
        return jsonify({"error": f"组合数{total_combos}过大，请选择更高步长或更少维度"}), 400
    
    results = []
    for combo_idx, combo_vals in enumerate(all_combos):
        # 构建权重字典
        w = {}
        for did in dim_cols:
            w[did] = 0
        for did, val in zip(selected_dims, combo_vals):
            w[did] = val
        
        # 计算加权分数
        total_w = sum(w.get(did, 0) for did in selected_dims)
        if total_w == 0:
            continue
        scores = np.zeros(len(records))
        for did in selected_dims:
            wv = w.get(did, 0)
            if wv == 0: continue
            for i, r in enumerate(records):
                scores[i] += (r.get(did, 0) or 0) * wv
        scores /= total_w
        
        # 计算IC
        ic = _calc_ic_fast(scores)
        if ic is not None:
            results.append({
                "weights": {did: w[did] for did in selected_dims},
                "ic": round(ic, 4),
                "n": len(records),
            })
    
    # 排序取 Top 20
    results.sort(key=lambda x: x["ic"] if x["ic"] is not None else -99, reverse=True)
    top20 = results[:20]
    
    # 维度重要性分析
    dim_labels = {
        "tech_weighted": "技术面 v3", "fundam_weighted": "基本面 v3",
        "fund_weighted": "资金面 v2.1", "institutional_weighted": "机构面 v2",
        "sentiment_weighted": "情绪面 v2", "news_event_weighted": "新闻面 v1",
        "chip_weighted": "筹码面 v1",
    }
    importance = {}
    for did in selected_dims:
        weights_vals = [r["weights"].get(did, 0) for r in top20]
        non_zero = sum(1 for wv in weights_vals if wv > 0)
        avg_w = np.mean(weights_vals)
        importance[did] = {
            "avgWeight": round(float(avg_w), 2),
            "nonZeroRate": round(non_zero/len(top20)*100, 1),
            "name": dim_labels.get(did, did),
        }
    
    return jsonify({
        "mode": mode,
        "totalCombos": total_combos,
        "topResults": top20,
        "dimImportance": importance,
        "bestIC": top20[0]["ic"] if top20 else None,
        "dimsScanned": selected_dims,
    })


def _calc_ic_fast(scores):
    """快速IC计算（与BacktestTask._compute_ic逻辑一致）"""
    sx, sy = [], []
    for i, r in enumerate(records):
        ret = r.get("ret_60d")
        if ret is not None and not (isinstance(ret, float) and np.isnan(ret)):
            if not np.isnan(scores[i]):
                sx.append(scores[i])
                sy.append(ret)
    if len(sx) < 50:
        return None
    try:
        from scipy.stats import rankdata
        rx = rankdata(np.array(sx))
        ry = rankdata(np.array(sy))
        d = rx - ry
        n = len(d)
        ic = 1 - (6 * np.sum(d**2)) / (n * (n**2 - 1))
        return float(ic) if not np.isnan(ic) else None
    except:
        return None


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
        "龙虎榜": [r.get("lh_institutional_weighted") or 0 for r in stock_data],
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


# ═══════════════════════════════════════════════════════════════
# 技术指标计算（纯Python，不依赖外部模块）
# ═══════════════════════════════════════════════════════════════

def _calc_ma(arr, period):
    """简单移动平均"""
    result = [None]*len(arr)
    for i in range(period-1, len(arr)):
        result[i] = round(sum(arr[i-period+1:i+1])/period, 4)
    return result

def _calc_ema(arr, period):
    """指数移动平均"""
    result = [None]*len(arr)
    if len(arr) < period: return result
    # 初始值用SMA
    sma = sum(arr[:period])/period
    result[period-1] = round(sma, 4)
    multiplier = 2/(period+1)
    for i in range(period, len(arr)):
        result[i] = round((arr[i]-result[i-1])*multiplier + result[i-1], 4)
    return result

def _calc_rsi(closes, period=14):
    """RSI序列"""
    result = [None]*len(closes)
    if len(closes) < period+1: return result
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i]-closes[i-1]
        gains.append(diff if diff>0 else 0)
        losses.append(-diff if diff<0 else 0)
    # 初始平均
    avg_gain = sum(gains[:period])/period
    avg_loss = sum(losses[:period])/period
    for i in range(period, len(gains)):
        if avg_loss == 0:
            result[i+1] = 100.0
        else:
            rs = avg_gain/avg_loss
            result[i+1] = round(100-100/(1+rs), 2)
        avg_gain = (avg_gain*(period-1)+gains[i])/period
        avg_loss = (avg_loss*(period-1)+losses[i])/period
    return result

def _calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD: 返回 (macd_line, signal_line, histogram)"""
    ema_fast = _calc_ema(closes, fast)
    ema_slow = _calc_ema(closes, slow)
    macd_line = [None]*len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = round(ema_fast[i]-ema_slow[i], 4)
    # signal = EMA of macd_line
    valid_macd = [(i,v) for i,v in enumerate(macd_line) if v is not None]
    sig_line = [None]*len(closes)
    hist = [None]*len(closes)
    if len(valid_macd) >= signal:
        vals = [v for _,v in valid_macd]
        ema_sig = _calc_ema(vals, signal)
        for j, (idx,_) in enumerate(valid_macd):
            if ema_sig[j] is not None:
                sig_line[idx] = round(ema_sig[j], 4)
                hist[idx] = round(macd_line[idx]-ema_sig[j], 4)
    return macd_line, sig_line, hist

def _calc_bollinger(closes, period=20, std_dev=2.0):
    """布林带: 返回 (mid, upper, lower, position)"""
    mid = [None]*len(closes)
    upper = [None]*len(closes)
    lower = [None]*len(closes)
    pos = [None]*len(closes)
    for i in range(period-1, len(closes)):
        window = closes[i-period+1:i+1]
        m = sum(window)/period
        mid[i] = round(m, 4)
        variance = sum((x-m)**2 for x in window)/period
        std = variance**0.5
        upper[i] = round(m + std_dev*std, 4)
        lower[i] = round(m - std_dev*std, 4)
        if upper[i] != lower[i]:
            pos[i] = round((closes[i]-lower[i])/(upper[i]-lower[i]), 4)
    return mid, upper, lower, pos

def _calc_atr(highs, lows, closes, period=14):
    """ATR (Average True Range)"""
    n = len(closes)
    atr = [None]*n
    tr_values = [None]*n
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr_values[i] = max(hl, hc, lc)
    # SMA of first 'period' TRs
    tr_valid = [v for v in tr_values[1:period+1] if v is not None]
    if len(tr_valid) >= period:
        atr[period] = sum(tr_valid[:period])/period
        for i in range(period+1, n):
            if tr_values[i] is not None and atr[i-1] is not None:
                atr[i] = round((atr[i-1]*(period-1)+tr_values[i])/period, 4)
    return atr

# ═══════════════════════════════════════════════════════════════
# 交易策略定义
# ═══════════════════════════════════════════════════════════════

STRATEGY_REGISTRY = {
    "score_cross": {
        "name": "七维评分穿越",
        "desc": "综合评分≥阈值买入，<阈值卖出",
        "params": {"threshold": {"default": 8, "min": 4, "max": 16, "step": 0.5, "label": "信号阈值"}},
        "type": "score"
    },
    "ma_cross": {
        "name": "均线金叉",
        "desc": "MA5上穿MA20+放量买入，下穿或止损-8%卖出",
        "params": {
            "stop_loss": {"default": -8, "min": -15, "max": -3, "step": 1, "label": "止损%"},
            "take_profit": {"default": 15, "min": 5, "max": 30, "step": 1, "label": "止盈%"}
        },
        "type": "tech"
    },
    "oversold": {
        "name": "超卖反转",
        "desc": "RSI(14)≤30+BB下轨+阳线买入，RSI≥65或止损-5%卖出",
        "params": {
            "stop_loss": {"default": -5, "min": -12, "max": -2, "step": 1, "label": "止损%"},
            "rsi_entry": {"default": 30, "min": 20, "max": 40, "step": 2, "label": "RSI入场阈值"}
        },
        "type": "tech"
    },
    "trend_follow": {
        "name": "趋势跟踪",
        "desc": "EMA12>EMA26+MACD金叉买入，EMA12<EMA26或止损-10%卖出",
        "params": {
            "stop_loss": {"default": -10, "min": -15, "max": -3, "step": 1, "label": "止损%"},
            "take_profit": {"default": 20, "min": 8, "max": 40, "step": 1, "label": "止盈%"}
        },
        "type": "tech"
    },
    "bollinger": {
        "name": "布林突破",
        "desc": "收盘<下轨且回升买入，收盘>上轨或止损-5%卖出",
        "params": {
            "stop_loss": {"default": -5, "min": -12, "max": -2, "step": 1, "label": "止损%"},
            "bb_period": {"default": 20, "min": 10, "max": 30, "step": 5, "label": "布林周期"}
        },
        "type": "tech"
    },
    "bull_wave": {
        "name": "七维共振牛股",
        "desc": "技术+资金+情绪+消息+大盘+机构+板块 七维评分≥阈值买入",
        "params": {
            "threshold": {"default": 7, "min": 4, "max": 12, "step": 0.5, "label": "七维总分阈值"},
            "stop_loss": {"default": -8, "min": -15, "max": -3, "step": 1, "label": "止损%"}
        },
        "type": "combo"
    },
    "combo": {
        "name": "综合多信号",
        "desc": "七维评分≥阈值 AND (MA金叉 OR RSI超卖) 买入，评分<阈值-2 OR 止损-8%卖出",
        "params": {
            "threshold": {"default": 8, "min": 4, "max": 16, "step": 0.5, "label": "评分阈值"},
            "stop_loss": {"default": -8, "min": -15, "max": -3, "step": 1, "label": "止损%"}
        },
        "type": "combo"
    }
}


def _run_single_stock_backtest(code, strategy_id, params):
    """统一单股回测引擎：逐交易日检查信号，支持止损止盈"""
    import sqlite3
    db = PROJECT_ROOT / "database" / "quant.db"

    # 1. 拉取完整OHLCV
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume "
        "FROM daily_price WHERE code=? ORDER BY trade_date",
        (code,)
    ).fetchall()
    conn.close()

    if len(rows) < 60:
        return {"error": f"K线数据不足 ({len(rows)}条, 需≥60)"}

    dates_raw = [r["trade_date"] for r in rows]
    opens_raw = [r["open"] for r in rows]
    highs_raw = [r["high"] for r in rows]
    lows_raw = [r["low"] for r in rows]
    closes_raw = [r["close"] for r in rows]
    volumes_raw = [r["volume"] for r in rows]

    # 按日期区间切片
    start_date = params.get("start", "")
    end_date = params.get("end", "")
    slice_start = 0
    slice_end = len(dates_raw)
    if start_date:
        for i, d in enumerate(dates_raw):
            if d >= start_date:
                slice_start = max(0, i - 60)  # 预留60天计算指标
                break
    if end_date:
        for i, d in enumerate(dates_raw):
            if d > end_date:
                slice_end = i
                break

    dates = dates_raw[slice_start:slice_end]
    opens = opens_raw[slice_start:slice_end]
    highs = highs_raw[slice_start:slice_end]
    lows = lows_raw[slice_start:slice_end]
    closes = closes_raw[slice_start:slice_end]
    volumes = volumes_raw[slice_start:slice_end]
    n = len(dates)

    if n < 60:
        return {"error": f"区间K线数据不足 ({n}条, 需≥60)"}

    # 2. 计算技术指标
    ma5 = _calc_ma(closes, 5)
    ma10 = _calc_ma(closes, 10)
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    rsi14 = _calc_rsi(closes, 14)
    rsi6 = _calc_rsi(closes, 6)
    macd_l, macd_s, macd_h = _calc_macd(closes)
    bb_mid, bb_up, bb_lo, bb_pos = _calc_bollinger(closes)
    atr14 = _calc_atr(highs, lows, closes, 14)  # ATR用于动态仓位
    atr_pct = [round(atr14[i]/closes[i]*100, 2) if atr14[i] and closes[i] else None for i in range(n)]
    # 量比 (5日均量)
    vol_ma5 = _calc_ma(volumes, 5)

    # 3. 获取七维评分（按日期映射）
    stock_scores = [r2 for r2 in records if r2.get("code") == code]
    score_by_date = {}
    for s in stock_scores:
        d = s.get("as_of_date", "")
        vals = [s.get(did) or 0 for did in dim_cols]
        score_by_date[d] = round(sum(vals)/len(vals), 1)

    # 4. 策略参数
    strat = STRATEGY_REGISTRY.get(strategy_id, STRATEGY_REGISTRY["score_cross"])
    stop_loss = params.get("stop_loss", strat["params"].get("stop_loss", {}).get("default", -8) if "stop_loss" in strat.get("params",{}) else -8)
    take_profit = params.get("take_profit", strat["params"].get("take_profit", {}).get("default", 15) if "take_profit" in strat.get("params",{}) else 15)
    threshold = params.get("threshold", strat["params"].get("threshold", {}).get("default", 8) if "threshold" in strat.get("params",{}) else 8)
    rsi_entry = params.get("rsi_entry", 30)
    bb_period_param = int(params.get("bb_period", 20))

    # 5. 逐日回测
    initial_capital = int(params.get("initial_capital", 100000))
    cash = initial_capital
    shares = 0
    position = 0  # 0=空仓 1=持仓
    entry_price = 0
    entry_date = ""
    entry_idx = 0
    max_favorable = 0
    max_adverse = 0
    entry_reason = ""

    trades = []
    equity_curve = []
    bh_curve = []
    signal_dates = []  # 记录每天信号详情（用于对账）

    # 买入持有基准: 首日买入
    bh_shares = int(initial_capital / closes[0] / 100) * 100
    bh_cost = bh_shares * closes[0]
    bh_cash = initial_capital - bh_cost

    for i in range(n):
        if i < 60:  # 前60天无法计算所有指标，跳过
            equity_curve.append({"date": dates[i], "value": initial_capital})
            bh_curve.append({"date": dates[i], "value": round(bh_cash + bh_shares*closes[i], 2)})
            continue

        price = closes[i]
        signal_info = {"date": dates[i], "close": price}

        # ── 生成信号 ──
        buy_signal = False
        sell_signal = False
        sell_reason = ""

        if strategy_id == "score_cross":
            today_score = 0
            for dt_key in score_by_date:
                if dt_key <= dates[i]:
                    today_score = score_by_date[dt_key]
            signal_info["score"] = round(today_score, 1)
            if position == 0 and today_score >= threshold:
                buy_signal = True
                entry_reason = f"综合评分{today_score}≥阈值{threshold}"
            elif position == 1 and today_score < threshold:
                sell_signal = True
                sell_reason = f"评分{today_score}<阈值{threshold}"

        elif strategy_id == "ma_cross":
            signal_info["ma5"] = ma5[i]; signal_info["ma20"] = ma20[i]
            vol_ratio = volumes[i]/vol_ma5[i] if vol_ma5[i] and vol_ma5[i]>0 else 1
            signal_info["vol_ratio"] = round(vol_ratio, 2)
            if position == 0:
                if (ma5[i] and ma20[i] and ma5[i-1] and ma20[i-1]
                    and ma5[i-1] <= ma20[i-1] and ma5[i] > ma20[i] and vol_ratio >= 1.2):
                    buy_signal = True
                    entry_reason = f"MA5({ma5[i]:.2f})上穿MA20({ma20[i]:.2f}) 量比{vol_ratio:.1f}"
            elif position == 1:
                if ma5[i] and ma20[i] and ma5[i] < ma20[i]:
                    sell_signal = True
                    sell_reason = f"MA5({ma5[i]:.2f})下穿MA20({ma20[i]:.2f})"

        elif strategy_id == "oversold":
            signal_info["rsi14"] = rsi14[i]; signal_info["bb_pos"] = bb_pos[i]
            if position == 0:
                if (rsi14[i] and rsi14[i] <= rsi_entry and bb_pos[i] is not None and bb_pos[i] <= 0.08
                    and closes[i] > opens[i]):  # 阳线
                    buy_signal = True
                    entry_reason = f"RSI={rsi14[i]:.0f}≤{rsi_entry} BB位={bb_pos[i]:.2f} 阳线确认"
            elif position == 1:
                if rsi14[i] and rsi14[i] >= 65:
                    sell_signal = True
                    sell_reason = f"RSI={rsi14[i]:.0f}≥65 超买"

        elif strategy_id == "trend_follow":
            signal_info["ema12"] = ema12[i]; signal_info["ema26"] = ema26[i]
            macd_golden = (macd_h[i] is not None and macd_h[i-1] is not None
                          and macd_h[i-1] <= 0 and macd_h[i] > 0)
            if position == 0:
                if (ema12[i] and ema26[i] and ema12[i] > ema26[i] and macd_golden):
                    buy_signal = True
                    entry_reason = f"EMA12({ema12[i]:.2f})>EMA26({ema26[i]:.2f}) MACD金叉"
            elif position == 1:
                if ema12[i] and ema26[i] and ema12[i] < ema26[i]:
                    sell_signal = True
                    sell_reason = f"EMA12({ema12[i]:.2f})<EMA26({ema26[i]:.2f})"

        elif strategy_id == "bollinger":
            # 动态重算布林带
            bb_mid_i, bb_up_i, bb_lo_i, bb_pos_i = _calc_bollinger(closes[:i+1], bb_period_param, 2.0)
            signal_info["bb_lower"] = bb_lo_i[i]; signal_info["bb_upper"] = bb_up_i[i]
            if position == 0:
                if (bb_lo_i[i] and closes[i] < bb_lo_i[i]
                    and closes[i] > closes[i-1]):  # 回升
                    buy_signal = True
                    entry_reason = f"收盘{closes[i]:.2f}<下轨{bb_lo_i[i]:.2f} 回升确认"
            elif position == 1:
                if bb_up_i[i] and closes[i] > bb_up_i[i]:
                    sell_signal = True
                    sell_reason = f"收盘{closes[i]:.2f}>上轨{bb_up_i[i]:.2f}"

        elif strategy_id == "combo":
            today_score = 0
            for dt_key in score_by_date:
                if dt_key <= dates[i]:
                    today_score = score_by_date[dt_key]
            signal_info["score"] = round(today_score, 1)
            signal_info["rsi14"] = rsi14[i]
            signal_info["ma5"] = ma5[i]; signal_info["ma20"] = ma20[i]
            ma_golden = (ma5[i] and ma20[i] and ma5[i-1] and ma20[i-1]
                        and ma5[i-1] <= ma20[i-1] and ma5[i] > ma20[i])
            rsi_oversold = rsi14[i] and rsi14[i] <= 30
            if position == 0:
                if today_score >= threshold and (ma_golden or rsi_oversold):
                    buy_signal = True
                    reasons = []
                    if ma_golden: reasons.append("MA金叉")
                    if rsi_oversold: reasons.append(f"RSI={rsi14[i]:.0f}超卖")
                    entry_reason = f"评分{today_score}≥{threshold} AND ({'/'.join(reasons)})"
            elif position == 1:
                if today_score < threshold - 2:
                    sell_signal = True
                    sell_reason = f"评分{today_score}<{threshold-2}"

        elif strategy_id == "bull_wave":
            # 七维共振牛股 — 使用评分的加权和（模拟7维）
            today_score = 0
            for dt_key in score_by_date:
                if dt_key <= dates[i]:
                    today_score = score_by_date[dt_key]
            signal_info["score"] = round(today_score, 1)
            signal_info["rsi14"] = rsi14[i]
            bw_threshold = params.get("threshold", 7)
            bw_stop = params.get("stop_loss", -8)
            if position == 0 and today_score >= bw_threshold:
                buy_signal = True
                entry_reason = f"七维共振{today_score}≥{bw_threshold}"
            elif position == 1:
                pnl_pct = (price - entry_price) / entry_price * 100
                if pnl_pct <= bw_stop:
                    sell_signal = True
                    sell_reason = f"止损 {pnl_pct:.1f}%≤{bw_stop}%"
                elif today_score < bw_threshold - 10:
                    sell_signal = True
                    sell_reason = f"七维{today_score}<{bw_threshold-10}"

        # ── 止损止盈检查（所有tech策略共用）──
        if position == 1 and not sell_signal:
            pnl_pct = (price - entry_price) / entry_price * 100
            if stop_loss and pnl_pct <= stop_loss:
                sell_signal = True
                sell_reason = f"止损 {pnl_pct:.1f}%≤{stop_loss}%"
            elif take_profit and pnl_pct >= take_profit:
                sell_signal = True
                sell_reason = f"止盈 {pnl_pct:.1f}%≥{take_profit}%"
            # 跟踪最大盈利/回撤
            if pnl_pct > max_favorable: max_favorable = pnl_pct
            if pnl_pct < max_adverse: max_adverse = pnl_pct

        # ── 执行交易 ──
        if position == 0 and buy_signal:
            base_shares = int(cash / price / 100) * 100
            # 动态仓位: ATR越高→波动越大→仓位越小
            atr_pct_i = atr_pct[i] if i < len(atr_pct) else None
            if atr_pct_i and atr_pct_i > 0:
                mult = min(1.5, max(0.3, 2.5 / atr_pct_i))
                shares = int(base_shares * mult / 100) * 100
            else:
                shares = base_shares
            if shares > 0:
                cash -= shares * price
                position = 1
                entry_price = price
                entry_date = dates[i]
                entry_idx = i
                max_favorable = 0
                max_adverse = 0
                signal_info["action"] = "buy"
                signal_info["reason"] = entry_reason

        elif position == 1 and sell_signal:
            sell_value = shares * price
            cash += sell_value
            ret = round((price - entry_price) / entry_price * 100, 2)
            hold_days = i - entry_idx
            trades.append({
                "id": len(trades) + 1,
                "entryDate": entry_date,
                "entryPrice": round(entry_price, 2),
                "exitDate": dates[i],
                "exitPrice": round(price, 2),
                "returnPct": ret,
                "holdDays": hold_days,
                "reason": sell_reason,
                "entrySignal": entry_reason,
                "exitSignal": sell_reason,
                "maxFavorable": round(max_favorable, 2),
                "maxAdverse": round(max_adverse, 2),
            })
            shares = 0
            position = 0
            signal_info["action"] = "sell"
            signal_info["reason"] = sell_reason
            signal_info["return"] = ret

        # ── 权益曲线 ──
        total_value = cash + (shares * price if position == 1 else 0)
        equity_curve.append({"date": dates[i], "value": round(total_value, 2)})
        bh_curve.append({"date": dates[i], "value": round(bh_cash + bh_shares*price, 2)})
        signal_dates.append(signal_info)

    # ── 期末清仓 ──
    if position == 1:
        last_price = closes[-1]
        ret = round((last_price - entry_price) / entry_price * 100, 2)
        cash += shares * last_price
        trades.append({
            "id": len(trades) + 1,
            "entryDate": entry_date,
            "entryPrice": round(entry_price, 2),
            "exitDate": dates[-1],
            "exitPrice": round(last_price, 2),
            "returnPct": ret,
            "holdDays": n - 1 - entry_idx,
            "reason": "期末清仓",
            "entrySignal": entry_reason,
            "exitSignal": "回测结束",
            "maxFavorable": round(max_favorable, 2),
            "maxAdverse": round(max_adverse, 2),
        })

    # ── 统计 ──
    final_value = cash
    total_return = round((final_value - initial_capital) / initial_capital * 100, 2)
    bh_final = bh_cash + bh_shares * closes[-1]
    bh_return = round((bh_final - initial_capital) / initial_capital * 100, 2)

    win_trades = [t for t in trades if t["returnPct"] > 0]
    loss_trades = [t for t in trades if t["returnPct"] <= 0]
    total_trades = len(trades)
    win_rate = round(len(win_trades)/total_trades*100, 1) if total_trades > 0 else 0
    avg_return = round(np.mean([t["returnPct"] for t in trades]), 2) if trades else 0
    avg_hold = round(np.mean([t["holdDays"] for t in trades]), 0) if trades else 0
    total_wins = sum(t["returnPct"] for t in win_trades) if win_trades else 0
    total_losses = abs(sum(t["returnPct"] for t in loss_trades)) if loss_trades else 0
    profit_factor = round(total_wins/total_losses, 2) if total_losses > 0 else (99 if total_wins > 0 else 0)

    # 最大回撤
    eq_vals = [e["value"] for e in equity_curve]
    peak = eq_vals[0]
    max_dd = 0
    for v in eq_vals:
        if v > peak: peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd: max_dd = dd

    # 夏普比率
    if len(eq_vals) > 10:
        daily_r = [(eq_vals[j]-eq_vals[j-1])/eq_vals[j-1] for j in range(1, len(eq_vals))]
        mean_r = np.mean(daily_r)
        std_r = np.std(daily_r)
        sharpe = round((mean_r/std_r)*np.sqrt(252), 2) if std_r > 0 else 0
    else:
        sharpe = 0

    # 对账数据：最近100个交易日的OHLCV+指标
    verify_n = min(100, n)
    verify_prices = []
    verify_signals = []
    for i in range(n-verify_n, n):
        verify_prices.append({
            "date": dates[i],
            "open": round(opens[i], 2), "high": round(highs[i], 2),
            "low": round(lows[i], 2), "close": round(closes[i], 2),
            "volume": int(volumes[i]) if volumes[i] else 0,
        })
        verify_signals.append({
            "date": dates[i],
            "ma5": ma5[i], "ma20": ma20[i], "ma60": ma60[i],
            "ema12": ema12[i], "ema26": ema26[i],
            "rsi14": rsi14[i], "rsi6": rsi6[i],
            "macd": macd_l[i], "macd_signal": macd_s[i], "macd_hist": macd_h[i],
            "bb_mid": bb_mid[i], "bb_upper": bb_up[i], "bb_lower": bb_lo[i], "bb_pos": bb_pos[i],
        })

    return {
        "code": code,
        "strategy": strategy_id,
        "strategyName": strat["name"],
        "strategyDesc": strat["desc"],
        "params": params,
        "summary": {
            "totalReturn": total_return,
            "bhReturn": bh_return,
            "excessReturn": round(total_return - bh_return, 2),
            "maxDrawdown": round(max_dd, 2),
            "sharpe": sharpe,
            "totalTrades": total_trades,
            "winTrades": len(win_trades),
            "winRate": win_rate,
            "avgReturn": avg_return,
            "avgHoldDays": int(avg_hold),
            "profitFactor": profit_factor,
            "initialCapital": initial_capital,
            "finalValue": round(final_value, 2),
        },
        "trades": trades,
        "equityCurve": equity_curve,
        "bhCurve": bh_curve,
        "signalDates": signal_dates[60:],  # 跳过前60天（指标不全）
        "verification": {
            "prices": verify_prices,
            "indicators": verify_signals,
        }
    }


# ── 行业板块API ──

@app.route("/api/sector")
def api_sector():
    """行业板块分析: 按行业聚合评分、排名、成分股列表"""
    import numpy as np
    from collections import defaultdict

    # 按行业聚合最新评分
    # 获取每只股票最新一条记录
    latest = {}
    for r in records:
        code = r.get("code", "")
        date = r.get("as_of_date", "")
        if code not in latest or date > latest[code]["date"]:
            latest[code] = {"date": date, "record": r}

    # 按行业分组
    ind_stocks = defaultdict(list)
    ind_data = defaultdict(lambda: {
        "codes": [], "names": [], "scores": [],
        "dim_scores": defaultdict(list), "stock_count": 0
    })

    for code, data in latest.items():
        ind = industry_map.get(code, "其他")
        r = data["record"]
        vals = [r.get(d, 0) or 0 for d in dim_cols]
        avg = round(sum(vals) / len(vals), 2)

        ind_data[ind]["codes"].append(code)
        ind_data[ind]["names"].append(r.get("name", ""))
        ind_data[ind]["scores"].append(avg)
        ind_data[ind]["stock_count"] += 1
        for i, d in enumerate(dim_cols):
            ind_data[ind]["dim_scores"][d].append(vals[i])

    # 计算行业维度
    min_stocks = request.args.get("min_stocks", 3, type=int)
    sort_by = request.args.get("sort_by", "avg_score")

    sectors = []
    for ind, data in ind_data.items():
        if data["stock_count"] < min_stocks:
            continue
        avg_all = round(sum(data["scores"]) / len(data["scores"]), 2)
        dim_avg = {}
        for d in dim_cols:
            vals = data["dim_scores"][d]
            dim_avg[d] = round(sum(vals) / len(vals), 2) if vals else 0

        # 计算行业趋势（最近 vs 之前）
        trend = "up" if avg_all > 8 else ("down" if avg_all < 6 else "neutral")

        sectors.append({
            "industry": ind,
            "stock_count": data["stock_count"],
            "avg_score": avg_all,
            "dim_scores": dim_avg,
            "trend": trend,
            "top_stocks": sorted(
                [{"code": c, "name": n} for c, n in zip(data["codes"], data["names"])],
                key=lambda x: latest.get(x["code"], {}).get("record", {}).get("tech_weighted", 0) or 0,
                reverse=True
            )[:5],
        })

    # 排序
    sort_map = {
        "avg_score": lambda x: -x["avg_score"],
        "stock_count": lambda x: -x["stock_count"],
        "name": lambda x: x["industry"],
    }
    key_fn = sort_map.get(sort_by, sort_map["avg_score"])
    sectors.sort(key=key_fn)

    return jsonify({
        "total": len(sectors),
        "sectors": sectors,
        "dim_labels": dim_cols,
        "as_of_date": max((r.get("as_of_date", "") for r in records), default="")
    })


@app.route("/api/sector/<industry>")
def api_sector_detail(industry):
    """行业成分股详细数据"""
    from urllib.parse import unquote
    industry = unquote(industry)

    latest = {}
    for r in records:
        code = r.get("code", "")
        date = r.get("as_of_date", "")
        if code not in latest or date > latest[code]["date"]:
            latest[code] = {"date": date, "record": r}

    stocks = []
    for code, data in latest.items():
        ind = industry_map.get(code, "其他")
        if ind != industry:
            continue
        r = data["record"]
        vals = [r.get(d, 0) or 0 for d in dim_cols]
        avg = round(sum(vals) / len(vals), 2)
        stocks.append({
            "code": code,
            "name": r.get("name", ""),
            "avg_score": avg,
            "dim_scores": {d: round(r.get(d, 0) or 0, 2) for d in dim_cols},
            "ret_20d": r.get("ret_20d"),
            "ret_60d": r.get("ret_60d"),
        })

    stocks.sort(key=lambda x: -x["avg_score"])
    return jsonify({
        "industry": industry,
        "total": len(stocks),
        "stocks": stocks,
        "dim_labels": dim_cols,
    })


# ── 选股筛选API ──

@app.route("/api/stock/screener")
def api_stock_screener():
    """选股筛选: 多条件过滤"""
    import numpy as np

    # 获取每只股票最新记录
    latest = {}
    for r in records:
        code = r.get("code", "")
        date = r.get("as_of_date", "")
        if code not in latest or date > latest[code]["date"]:
            latest[code] = {"date": date, "record": r}

    # 筛选参数
    min_score = request.args.get("min_score", 0, type=float)
    max_score = request.args.get("max_score", 15, type=float)
    industry = request.args.get("industry", "")
    min_ret = request.args.get("min_ret", -100, type=float)
    tech_min = request.args.get("tech_min", 0, type=float)
    fund_min = request.args.get("fund_min", 0, type=float)
    inst_min = request.args.get("inst_min", 0, type=float)
    chip_min = request.args.get("chip_min", 0, type=float)
    sort_by = request.args.get("sort_by", "avg_score")
    limit = request.args.get("limit", 50, type=int)

    results = []
    for code, data in latest.items():
        r = data["record"]
        ind = industry_map.get(code, "其他")

        # 行业过滤
        if industry and industry != "全部" and ind != industry:
            continue

        vals = [r.get(d, 0) or 0 for d in dim_cols]
        avg = round(sum(vals) / len(vals), 2)

        if avg < min_score or avg > max_score:
            continue
        if r.get("ret_20d", 0) is not None and (r["ret_20d"] or 0) < min_ret:
            continue
        if (r.get("tech_weighted", 0) or 0) < tech_min:
            continue
        if (r.get("fund_weighted", 0) or 0) < fund_min:
            continue
        if (r.get("institutional_weighted", 0) or 0) < inst_min:
            continue
        if (r.get("chip_weighted", 0) or 0) < chip_min:
            continue

        results.append({
            "code": code,
            "name": r.get("name", ""),
            "industry": ind,
            "avg_score": avg,
            "dim_scores": {d: round(r.get(d, 0) or 0, 2) for d in dim_cols},
            "ret_20d": r.get("ret_20d"),
            "ret_60d": r.get("ret_60d"),
        })

    sort_map = {
        "avg_score": lambda x: -x["avg_score"],
        "ret_20d": lambda x: -(x["ret_20d"] or 0),
        "tech_weighted": lambda x: -(x["dim_scores"].get("tech_weighted", 0)),
    }
    key_fn = sort_map.get(sort_by, sort_map["avg_score"])
    results.sort(key=key_fn)
    results = results[:limit]

    return jsonify({"total": len(results), "stocks": results})


@app.route("/api/stock/<code>/backtest")
def api_stock_backtest(code):
    """单股策略回测: 支持6种策略，逐交易日模拟，详细交易记录+对账数据"""
    code = str(code).zfill(6)
    strategy_id = request.args.get("strategy", "score_cross")

    # 解析策略参数 + 区间参数
    params = {}
    for key in request.args:
        if key in ("strategy", "threshold"):
            try: params[key] = float(request.args.get(key))
            except: pass
        elif key.startswith("param_"):
            try: params[key[6:]] = float(request.args.get(key))
            except: pass

    # 兼容旧版 threshold 参数
    if "threshold" in params:
        params["threshold"] = params.pop("threshold") if "threshold" in params else float(request.args.get("threshold", 8))

    # 解析日期区间和初始资金
    params["start"] = request.args.get("start", "")
    params["end"] = request.args.get("end", "")
    try: params["initial_capital"] = float(request.args.get("capital", 100000))
    except: params["initial_capital"] = 100000

    if strategy_id not in STRATEGY_REGISTRY:
        return jsonify({"error": f"未知策略: {strategy_id}", "available": list(STRATEGY_REGISTRY.keys())}), 400

    result = _run_single_stock_backtest(code, strategy_id, params)
    if "error" in result:
        return jsonify(result), 404

    # ── 保存回测结果到数据库 ──
    try:
        import sqlite3, json, datetime
        db = PROJECT_ROOT / "database" / "quant.db"
        conn = sqlite3.connect(str(db))
        s = result.get("summary", {})
        now = datetime.datetime.now().isoformat()
        start_date = params.get("start", result.get("dates", [None])[0] or "2000-01-01")
        end_date = params.get("end", result.get("dates", [None])[-1] or "2099-12-31")
        if isinstance(start_date, str) and len(start_date) > 10: start_date = start_date[:10]
        if isinstance(end_date, str) and len(end_date) > 10: end_date = end_date[:10]

        # 查找或创建策略配置
        strat_name = STRATEGY_REGISTRY.get(strategy_id, {}).get("name", strategy_id)
        cur = conn.execute("SELECT id FROM strategy_config WHERE name=?", (strat_name,))
        row = cur.fetchone()
        if row:
            strategy_db_id = row[0]
        else:
            cur.execute(
                "INSERT INTO strategy_config (name, class_path, params, description, source, created_at) VALUES (?,?,?,?,?,?)",
                (strat_name, f"scripts.param_server.{strategy_id}", json.dumps(params),
                 STRATEGY_REGISTRY.get(strategy_id, {}).get("desc", ""), "param_server", now))
            strategy_db_id = cur.lastrowid

        equity_curve = json.dumps(result.get("equityCurve", []), ensure_ascii=False)
        trades_detail = json.dumps(result.get("trades", []), ensure_ascii=False)

        # 检查是否已存在相同记录（策略+代码+区间）
        cur.execute("""SELECT id FROM backtest_result
                       WHERE strategy_id=? AND stock_code=? AND start_date=? AND end_date=?
                       ORDER BY created_at DESC LIMIT 1""",
                    (strategy_db_id, code, start_date, end_date))
        existing = cur.fetchone()

        insert_sql = """INSERT INTO backtest_result
            (strategy_id, stock_code, stock_name, start_date, end_date,
             initial_capital, final_equity, total_return, annual_return,
             sharpe_ratio, max_drawdown, win_rate, profit_factor, total_trades,
             annual_volatility, benchmark_return, excess_return,
             equity_curve, trades_detail, created_at)
            VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?)"""

        insert_vals = (
            strategy_db_id, code, result.get("code", ""),
            start_date, end_date,
            s.get("initialCapital", 100000), s.get("finalValue", 100000),
            s.get("totalReturn"), s.get("annualReturn"), s.get("sharpe"),
            s.get("maxDrawdown"), s.get("winRate"), s.get("profitFactor", 0),
            s.get("totalTrades"), s.get("annualVolatility"),
            s.get("bhReturn"), s.get("excessReturn"),
            equity_curve, trades_detail, now
        )

        if existing:
            conn.execute(f"""UPDATE backtest_result SET
                total_return=?, annual_return=?, sharpe_ratio=?, max_drawdown=?,
                win_rate=?, profit_factor=?, total_trades=?, benchmark_return=?,
                excess_return=?, equity_curve=?, trades_detail=?, created_at=?
                WHERE id=?""",
                (s.get("totalReturn"), s.get("annualReturn"), s.get("sharpe"),
                 s.get("maxDrawdown"), s.get("winRate"), s.get("profitFactor", 0),
                 s.get("totalTrades"), s.get("bhReturn"), s.get("excessReturn"),
                 equity_curve, trades_detail, datetime.datetime.now().isoformat(),
                 existing[0]))
        else:
            conn.execute(insert_sql, insert_vals)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [保存回测结果] 失败: {e}")

    return jsonify(result)


@app.route("/api/strategies")
def api_strategies():
    """返回所有可用策略的元数据"""
    return jsonify(STRATEGY_REGISTRY)


@app.route("/api/backtest/history")
def api_backtest_history():
    """获取回测历史记录"""
    import sqlite3, json
    limit = request.args.get("limit", 50, type=int)
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT r.id, s.name as strategy_name, r.stock_code, r.stock_name,
               r.start_date, r.end_date, r.initial_capital,
               r.total_return, r.sharpe_ratio, r.max_drawdown,
               r.win_rate, r.total_trades, r.benchmark_return,
               r.excess_return, r.created_at
        FROM backtest_result r
        LEFT JOIN strategy_config s ON r.strategy_id = s.id
        WHERE r.total_return IS NOT NULL
        ORDER BY r.created_at DESC
        LIMIT ?
    """, (limit,))
    results = [dict(r) for r in rows]
    conn.close()
    # 处理日期格式
    for r in results:
        for k in ['created_at', 'start_date', 'end_date']:
            if r.get(k):
                r[k] = str(r[k])[:19]
    return jsonify({"total": len(results), "results": results})


@app.route("/api/backtest/history/<int:result_id>")
def api_backtest_detail(result_id):
    """获取单条回测详情（含净值曲线和交易明细）"""
    import sqlite3, json
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT r.*, s.name as strategy_name
        FROM backtest_result r
        LEFT JOIN strategy_config s ON r.strategy_id = s.id
        WHERE r.id = ?
    """, (result_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "记录不存在"}), 404
    r = dict(row)
    # 解析JSON字段
    for key in ['equity_curve', 'trades_detail', 'monthly_returns', 'cost_config']:
        if r.get(key) and isinstance(r[key], str):
            try: r[key] = json.loads(r[key])
            except: pass
    for k in ['created_at', 'start_date', 'end_date']:
        if r.get(k): r[k] = str(r[k])[:19]
    return jsonify(r)


@app.route("/api/stock/<code>/kline/<period>")
def api_kline(code, period="day"):
    """K线查询: 优先网络实时数据, 断网降级到本地DB"""
    code = str(code).zfill(6)

    # ── 1. 尝试网络实时数据 ──
    remote_data = _fetch_kline_remote(code, period)
    if remote_data is not None:
        remote_data["_source"] = "网络实时"
        return jsonify(remote_data)

    # ── 2. 降级到本地 DB ──
    local_data = _fetch_kline_local(code, period)
    if local_data is not None:
        local_data["_source"] = "本地DB"
        return jsonify(local_data)

    return jsonify({"error": f"未找到K线 {code}"}), 404


def _fetch_kline_remote(code, period="day"):
    """从东财 push2 API 获取K线"""
    import requests
    market = 1 if code.startswith("6") else 0
    secid = f"{market}.{code}"
    klt_map = {"day": 101, "week": 102, "month": 103}
    klt = klt_map.get(period, 101)
    limit = 120

    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt={klt}&fqt=1&end=20500101&lmt={limit}"
    )
    try:
        resp = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://quote.eastmoney.com/",
        })
        data = resp.json()
        if not data or not data.get("data") or not data["data"].get("klines"):
            return None

        klines = data["data"]["klines"]
        dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
        for line in klines[-limit:]:
            parts = line.split(",")
            if len(parts) < 6: continue
            dates.append(parts[0])
            opens.append(float(parts[1]))
            closes.append(float(parts[2]))
            highs.append(float(parts[3]))
            lows.append(float(parts[4]))
            volumes.append(float(parts[5]))

        return {
            "code": code, "period": period,
            "dates": dates, "open": opens, "high": highs,
            "low": lows, "close": closes, "volume": volumes,
        }
    except Exception:
        return None


def _fetch_kline_local(code, period="day"):
    """从本地 DB 获取K线 (日线直出, 周月线聚合)"""
    import sqlite3, pandas as pd
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, volume "
        "FROM daily_price WHERE code=? ORDER BY trade_date",
        (code,)
    ).fetchall()
    conn.close()

    if not rows: return None

    if period == "day":
        recent = rows[-120:]
        return {
            "code": code, "period": "day",
            "dates": [r["trade_date"] for r in recent],
            "open": [r["open"] for r in recent], "high": [r["high"] for r in recent],
            "low": [r["low"] for r in recent], "close": [r["close"] for r in recent],
            "volume": [r["volume"] for r in recent],
        }

    df = pd.DataFrame(rows, columns=["date","open","high","low","close","volume"])
    df["date"] = pd.to_datetime(df["date"])

    if period == "week":
        df["period"] = df["date"].dt.isocalendar().year.astype(str) + "-W" + df["date"].dt.isocalendar().week.astype(str).str.zfill(2)
    else:
        df["period"] = df["date"].dt.strftime("%Y-%m")

    grouped = df.groupby("period").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"), date=("date", "last")
    ).reset_index(drop=True)

    recent = grouped.tail(120)
    return {
        "code": code, "period": period,
        "dates": [d.strftime("%Y-%m-%d") for d in recent["date"]],
        "open": recent["open"].tolist(), "high": recent["high"].tolist(),
        "low": recent["low"].tolist(), "close": recent["close"].tolist(),
        "volume": recent["volume"].tolist(),
    }


def _compute_industry_ics(scores, combo_name):
    """按行业计算IC: 分组统计各行业的评分有效性"""
    if not industry_map:
        return []

    # 按行业聚合评分和收益
    industry_data = {}  # industry → {"scores":[], "returns":[]}
    for i, r in enumerate(records):
        code = r.get("code", "")
        ind = industry_map.get(code, "其他")
        ret = r.get("ret_60d")
        sc = scores[i]
        if ret is not None and not (isinstance(ret, float) and np.isnan(ret)):
            if not np.isnan(sc):
                if ind not in industry_data:
                    industry_data[ind] = {"scores": [], "returns": [], "codes": set()}
                industry_data[ind]["scores"].append(sc)
                industry_data[ind]["returns"].append(ret)
                industry_data[ind]["codes"].add(code)

    # 计算每个行业的IC
    results = []
    for ind, data in industry_data.items():
        if len(data["scores"]) < 20:
            continue
        try:
            from scipy.stats import rankdata
            sx = np.array(data["scores"])
            sy = np.array(data["returns"])
            rx = rankdata(sx)
            ry = rankdata(sy)
            d = rx - ry
            n = len(d)
            ic = 1 - (6 * np.sum(d**2)) / (n * (n**2 - 1))
            avg_score = round(float(np.mean(sx)), 1)
            avg_ret = round(float(np.mean(sy)), 2)
            results.append({
                "industry": ind,
                "ic": round(float(ic), 4) if not np.isnan(ic) else None,
                "n": len(data["codes"]),
                "samples": n,
                "avgScore": avg_score,
                "avgRet": avg_ret,
            })
        except:
            pass

    results.sort(key=lambda x: x["ic"] if x["ic"] is not None else -99, reverse=True)
    return results




# ── 8维预测API ──
PRED_MODEL_PATH = PROJECT_ROOT / "data" / "bull_8d_monthly_result.json"

def _load_pred_model():
    """加载预测模型权重"""
    if not PRED_MODEL_PATH.exists():
        return None
    with open(PRED_MODEL_PATH) as f:
        return json.load(f)

@app.route("/api/predict/<code>")
def api_predict(code):
    """预测单只股票上涨概率，支持历史日期回溯"""
    code = str(code).zfill(6)
    date_param = request.args.get("date", "")  # YYYY-MM
    model = _load_pred_model()
    if model is None:
        return jsonify({"error": "预测模型未找到"}), 404
    
    scores_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    if not scores_path.exists():
        return jsonify({"error": "分数数据未找到"}), 404
    
    with open(scores_path) as f:
        all_scores = json.load(f)
    
    # 找该股票指定日期的分数
    stock_scores = [s for s in all_scores if s['code'] == code]
    if not stock_scores:
        return jsonify({"error": f"未找到股票 {code} 的分数"}), 404
    
    if date_param:
        # 历史回溯：找指定月份的最新一条
        candidates = [s for s in stock_scores if s.get('year_month', '') <= date_param]
        if not candidates:
            return jsonify({"error": f"未找到 {code} 在 {date_param} 前的分数"}), 404
        latest = max(candidates, key=lambda x: x['as_of_date'])
    else:
        latest = max(stock_scores, key=lambda x: x['as_of_date'])
    
    # 计算预测概率
    dim_cols = list(model['logistic_coef'].keys())
    coef = np.array([model['logistic_coef'][c] for c in dim_cols])
    intercept = model['intercept']
    
    x = np.array([[latest.get(c, 0) or 0 for c in dim_cols]])
    logit = x @ coef + intercept
    proba = float((1 / (1 + np.exp(-logit))).item())
    
    signal = "买入" if proba >= 0.55 else ("回避" if proba < 0.4 else "中性")
    
    result = {
        "code": code,
        "pred_month": latest.get('year_month', latest['as_of_date'][:7]),
        "as_of_date": latest['as_of_date'],
        "pred_proba_up": round(proba, 4),
        "signal": signal,
        "dim_scores": {c.replace('_weighted',''): latest.get(c, 0) or 0 for c in dim_cols},
        "auc": model['auc'],
        "ret_20d": latest.get('ret_20d'),
        "ret_60d": latest.get('ret_60d'),
    }
    
    # ── 保存到数据库 ──
    try:
        import sqlite3
        db = PROJECT_ROOT / "database" / "quant.db"
        conn = sqlite3.connect(str(db))
        pred_month = result["pred_month"]
        # 检查是否已存在
        cur = conn.execute("SELECT id FROM prediction_record WHERE code=? AND pred_month=?",
                          (code, pred_month))
        existing = cur.fetchone()
        dim_scores_json = json.dumps(result["dim_scores"], ensure_ascii=False)
        coef_json = json.dumps({k: float(v) for k, v in zip(dim_cols, coef)}, ensure_ascii=False)
        if existing:
            conn.execute("""UPDATE prediction_record SET
                pred_proba=?, signal=?, dim_scores=?, logistic_coef=?,
                actual_return_20d=?, actual_return_60d=?
                WHERE id=?""",
                (result["pred_proba_up"], signal, dim_scores_json, coef_json,
                 result.get("ret_20d"), result.get("ret_60d"), existing[0]))
        else:
            conn.execute("""INSERT INTO prediction_record
                (code, stock_name, pred_month, as_of_date, pred_proba, signal,
                 auc, dim_scores, logistic_coef, actual_return_20d, actual_return_60d)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (code, latest.get("name", ""), pred_month, latest["as_of_date"],
                 result["pred_proba_up"], signal, model['auc'],
                 dim_scores_json, coef_json,
                 result.get("ret_20d"), result.get("ret_60d")))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  [保存预测] 失败: {e}")
    
    return jsonify(result)


@app.route("/api/predict/history")
def api_predict_history():
    """获取预测历史记录"""
    import sqlite3
    code = request.args.get("code", "")
    limit = request.args.get("limit", 50, type=int)
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    if code:
        rows = conn.execute("""
            SELECT * FROM prediction_record
            WHERE code=? ORDER BY pred_month DESC LIMIT ?
        """, (code, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM prediction_record
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        for k in ['dim_scores', 'logistic_coef']:
            if d.get(k) and isinstance(d[k], str):
                try: d[k] = json.loads(d[k])
                except: pass
        for k in ['created_at', 'as_of_date']:
            if d.get(k): d[k] = str(d[k])[:19]
        results.append(d)
    conn.close()
    return jsonify({"total": len(results), "results": results})


@app.route("/api/predict/verify")
def api_predict_verify():
    """验证预测准确率：对比预测概率与实际收益，更新 verified 标记"""
    import sqlite3
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    
    # 加载评分数据（含实际收益）
    scores_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    with open(scores_path) as f:
        all_scores = json.load(f)
    
    # 建立 code+year_month → actual_return 映射
    ret_map = {}
    for s in all_scores:
        key = (s['code'], s.get('year_month', ''))
        if key not in ret_map or s['as_of_date'] > ret_map[key].get('date', ''):
            ret_map[key] = {
                'ret_20d': s.get('ret_20d'),
                'ret_60d': s.get('ret_60d'),
                'date': s.get('as_of_date', ''),
            }
    
    # 更新未验证的记录
    rows = conn.execute("SELECT id, code, pred_month FROM prediction_record WHERE verified=0").fetchall()
    updated = 0
    for r in rows:
        key = (r['code'], r['pred_month'])
        if key in ret_map:
            data = ret_map[key]
            conn.execute("""UPDATE prediction_record SET
                actual_return_20d=?, actual_return_60d=?, verified=1
                WHERE id=?""",
                (data['ret_20d'], data['ret_60d'], r['id']))
            updated += 1
    conn.commit()
    conn.close()
    return jsonify({"updated": updated, "total_pending": len(rows)})


@app.route("/api/predict/stats")
def api_predict_stats():
    """预测效果统计：命中率、分组表现"""
    import sqlite3
    db = PROJECT_ROOT / "database" / "quant.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    
    # 已验证的记录
    rows = conn.execute("""
        SELECT pred_proba, signal, actual_return_20d, actual_return_60d
        FROM prediction_record
        WHERE verified=1 AND actual_return_20d IS NOT NULL
    """).fetchall()
    
    total = len(rows)
    if total == 0:
        return jsonify({"total": 0, "message": "暂无已验证预测记录，请先调用 /api/predict/verify"})
    
    # 整体命中率（预测涨→实际涨）
    hits = sum(1 for r in rows if (r['pred_proba'] >= 0.55 and (r['actual_return_20d'] or 0) > 0)
                                or (r['pred_proba'] < 0.4 and (r['actual_return_20d'] or 0) < 0))
    # 分组统计
    bins = {"<0.4": [], "0.4-0.55": [], ">=0.55": []}
    for r in rows:
        p = r['pred_proba']
        if p < 0.4: bins["<0.4"].append(r['actual_return_20d'] or 0)
        elif p >= 0.55: bins[">=0.55"].append(r['actual_return_20d'] or 0)
        else: bins["0.4-0.55"].append(r['actual_return_20d'] or 0)
    
    stats = []
    for label, vals in bins.items():
        if vals:
            avg_ret = sum(vals) / len(vals)
            up_rate = sum(1 for v in vals if v > 0) / len(vals) * 100
            stats.append({"bin": label, "count": len(vals), "avg_return": round(avg_ret, 2), "up_rate": round(up_rate, 1)})
    
    conn.close()
    return jsonify({
        "total": total,
        "hit_rate": round(hits / total * 100, 1) if total else 0,
        "bin_stats": stats,
    })

@app.route("/api/predict/batch")
def api_predict_batch():
    """批量预测（返回全部股票预测结果）"""
    model = _load_pred_model()
    if model is None:
        return jsonify({"error": "预测模型未找到"}), 404
    
    scores_path = PROJECT_ROOT / "data" / "all_7d_scores.json"
    if not scores_path.exists():
        return jsonify({"error": "分数数据未找到"}), 404
    
    with open(scores_path) as f:
        scores = json.load(f)
    
    dim_cols = list(model['logistic_coef'].keys())
    coef = np.array([model['logistic_coef'][c] for c in dim_cols])
    intercept = model['intercept']
    
    results = []
    for s in scores:
        x = np.array([[s.get(c, 0) for c in dim_cols]])
        logit = x @ coef + intercept
        proba = float((1 / (1 + np.exp(-logit))).item())
        signal = "买入" if proba >= 0.55 else ("回避" if proba < 0.4 else "中性")
        results.append({
            "code": s['code'],
            "as_of_date": s['as_of_date'],
            "pred_proba_up": round(proba, 4),
            "signal": signal
        })
    
    return jsonify({"count": len(results), "results": results})

if __name__ == "__main__":
    load_data()
    app.run(host="0.0.0.0", port=8081, debug=False, threaded=True)
