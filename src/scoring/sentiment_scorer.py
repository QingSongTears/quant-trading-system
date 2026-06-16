"""
情绪面/题材热度评分器 (权重 10%)
================================

基于 A-stock-data 技能 API 的情绪与题材分析。

数据源:
  - 东财个股新闻 (§5.1): 新闻情感关键词匹配
  - 同花顺热点题材 (§3.1): 题材归因 reason tags
  - 东财全球资讯 (§5.3): 市场整体情绪

子指标 (每项 0-3 分):
  1. 新闻情感 (0-3): 近期新闻的正负面倾向
  2. 题材热度 (0-3): 是否属于当前热门题材
  3. 市场情绪 (0-3): 大盘情绪指标
  4. 关注度变化 (0-3): 新闻量是否在增加
  5. 公告情绪 (0-3): 公告标题的情感倾向
  6. 题材持续性 (0-3): 题材已持续天数
  7. 情绪综合 (0-3): 综合情绪评分
"""
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional
import json
import requests
from datetime import date, timedelta


class SentimentScorer:
    """情绪面评分器 — API驱动"""

    def __init__(self, engine=None):
        # engine 参数保持接口统一，情绪面通过 HTTP API 获取数据，不依赖数据库
        self._sentiment_cache = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })

    # ============================================================
    #  API 端点
    # ============================================================
    def _fetch_dongcai_news(self, code: str, limit: int = 10) -> list:
        """
        东财个股新闻 §5.1
        GET https://search-api-web.eastmoney.com/search/jsonp
        参数: cb=custom, param=内嵌JSON字符串(紧凑格式)
        返回: result.cmsArticleWebOld → 文章列表 [{title, content, date, mediaName, url}]

        注意: 东财搜索API近期可能返回仅 passportWeb 类型，
        此时降级返回空列表，评分 fallback 到中性分。
        """
        try:
            param_data = {
                "uid": "",
                "keyword": code,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": limit,
                        "preTag": "",
                        "postTag": "",
                    }
                },
            }
            param_str = json.dumps(param_data, separators=(",", ":"), ensure_ascii=False)
            resp = self._session.get(
                "https://search-api-web.eastmoney.com/search/jsonp",
                params={"cb": "jQuery_news", "param": param_str},
                headers={"Referer": "https://so.eastmoney.com/"},
                timeout=10,
            )
            text = resp.text

            # 通用 JSONP 解析: 取第一个 ( 到最后一个 ) 之间的内容
            json_str = text[text.index("(") + 1 : text.rindex(")")]
            data = json.loads(json_str)

            # result.cmsArticleWebOld 直接是列表
            articles = data.get("result", {}).get("cmsArticleWebOld", []) or []
            result = []
            for a in articles[:limit]:
                result.append({
                    "title": a.get("title", ""),
                    "content": a.get("content", ""),
                    "time": a.get("date", ""),
                    "source": a.get("mediaName", ""),
                    "url": a.get("url", ""),
                })
            return result
        except Exception:
            return []

    def _fetch_hot_topics(self, date_str: str) -> list:
        """
        同花顺热点题材 §3.1
        GET http://zx.10jqka.com.cn/event/api/getharden/date/{date}/.../
        参数: date=YYYYMMDD(如20250613), charset=GBK
        返回: {errocode:0, data:[{code,name,reason,zhangfu,...}]}
        reason 是核心字段：人工运营题材标签

        同花顺接口零鉴权，73ms 响应，稳定可用
        """
        cache_key = f"hot_topics_{date_str}"
        if cache_key in self._sentiment_cache:
            return self._sentiment_cache[cache_key]

        try:
            url = (
                f"http://zx.10jqka.com.cn/event/api/getharden/"
                f"date/{date_str}/orderby/date/orderway/desc/charset/GBK/"
            )
            resp = self._session.get(url, timeout=10)
            resp.encoding = "gbk"
            data = resp.json()
            if data.get("errocode") == 0:
                result = data.get("data", [])
                self._sentiment_cache[cache_key] = result
                return result
            self._sentiment_cache[cache_key] = []
            return []
        except Exception:
            self._sentiment_cache[cache_key] = []
            return []

    # ============================================================
    #  评分逻辑
    # ============================================================
    POSITIVE_KW = [
        "增长", "利好", "突破", "中标", "回购", "增持", "涨停",
        "新高", "超预期", "大涨", "涨停板", "扭亏", "预增",
        "分红", "重组", "资产注入", "股权激励", "定增",
    ]
    NEGATIVE_KW = [
        "下跌", "亏损", "减持", "立案", "处罚", "退市",
        "ST", "风险", "诉讼", "跌停", "暴跌", "预亏",
        "违规", "监管", "问询", "冻结", "爆雷", "踩雷",
    ]

    def _score_news_sentiment(self, code: str) -> int:
        """
        新闻情感分析 (0-3)
        基于东财个股新闻标题/内容的关键词正负面匹配
        API 不可用或返回空时降级为中性分 1
        """
        news = self._fetch_dongcai_news(code)
        if not news:
            return 1

        pos = 0
        neg = 0

        for article in news:
            title = article.get("title", "")
            content = article.get("content", "")
            text = f"{title} {content}"

            has_pos = any(kw in text for kw in self.POSITIVE_KW)
            has_neg = any(kw in text for kw in self.NEGATIVE_KW)

            if has_pos and not has_neg:
                pos += 1
            elif has_neg and not has_pos:
                neg += 1
            elif has_pos and has_neg:
                pos += 1
                neg += 1

        if pos > neg * 2:
            return 3
        elif pos > neg:
            return 2
        elif neg > pos * 2:
            return 0
        return 1

    def _score_topic_heat(self, code: str, date_str: Optional[str] = None) -> int:
        """
        题材热度 (0-3)
        检查股票 code 是否出现在当日同花顺热点题材中
        API 不可用时返回中性分 1
        """
        if date_str is None:
            date_str = date.today().strftime("%Y%m%d")

        topics = self._fetch_hot_topics(date_str)
        if not topics:
            return 1

        # 检查 code 是否出现在任一热点题材的 stock code 中
        for topic in topics:
            topic_code = topic.get("code", "")
            if topic_code == code:
                return 3

        return 1

    def _score_market_sentiment(self, date_str: Optional[str] = None) -> int:
        """
        大盘情绪 (0-3)
        基于当日热点题材数量判断市场整体活跃度
        >=20 个热点 → 3, >=10 → 2, >=5 → 1, <5 → 0
        API 不可用时返回中性分 1
        """
        if date_str is None:
            date_str = date.today().strftime("%Y%m%d")

        topics = self._fetch_hot_topics(date_str)
        if not topics:
            return 1

        count = len(topics)
        if count >= 20:
            return 3
        elif count >= 10:
            return 2
        elif count >= 5:
            return 1
        return 0

    def _score_attention_change(self, code: str) -> int:
        """关注度变化 — 需API"""
        return 1  # stub

    def _score_announcement_sentiment(self, code: str) -> int:
        """公告情绪 — 从 announcements 表获取"""
        return 1  # stub

    def _score_topic_durability(self, code: str) -> int:
        """题材持续性 — 需API"""
        return 1  # stub

    def _score_sentiment_composite(self, subs: dict) -> int:
        avg = np.mean(list(subs.values())[:5])
        if avg >= 2.0:
            return 3
        elif avg >= 1.5:
            return 2
        elif avg >= 1.0:
            return 1
        return 0

    # ============================================================
    #  主入口
    # ============================================================
    def score(self, code: str, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        if as_of_date is None:
            as_of_date = date.today().strftime("%Y%m%d")

        sub_scores = {
            "news_sentiment": self._score_news_sentiment(code),
            "topic_heat": self._score_topic_heat(code, as_of_date),
            "market_sentiment": self._score_market_sentiment(as_of_date),
            "attention_change": self._score_attention_change(code),
            "announcement_sentiment": self._score_announcement_sentiment(code),
            "topic_durability": self._score_topic_durability(code),
        }
        sub_scores["sentiment_composite"] = self._score_sentiment_composite(sub_scores)

        total = sum(sub_scores.values())
        return {
            "code": code,
            "as_of_date": as_of_date,
            "total": total,
            "weighted": round(total / 21 * 20, 1),
            "sub_scores": sub_scores,
            "error": None,
        }

    def batch_score(
        self, codes: List[str], as_of_date: Optional[str] = None, verbose: bool = False
    ) -> "pd.DataFrame":
        import pandas as pd
        results = []
        for i, code in enumerate(codes):
            r = self.score(code, as_of_date)
            if r["error"] is None:
                results.append({
                    "code": code,
                    "as_of_date": r["as_of_date"],
                    "total": r["total"],
                    "weighted": r["weighted"],
                    **{f"sent_{k}": v for k, v in r["sub_scores"].items()},
                })
            if verbose and (i + 1) % 50 == 0:
                print(f"  ... sentiment {i + 1}/{len(codes)}")
        return pd.DataFrame(results)
