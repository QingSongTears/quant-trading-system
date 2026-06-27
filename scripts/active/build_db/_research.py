"""build_db._research — 研报 / 新闻 / 热股 importer

- research_report : 研报
- em_global_news  : 财经新闻 (东方财富)
- ths_hot_reason  : 同花顺热股
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def import_research_report(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """研报 ← raw/reference/research_report.csv"""
    import csv as _csv
    csv_path = data_dir / "raw" / "reference" / "research_report.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in _csv.DictReader(f):
            code = r.get("code", "").strip()
            code = code.zfill(6) if code.isdigit() else code
            rows.append((
                code,
                r.get("date", "")[:10] if r.get("date") else None,
                r.get("rating") or None,
                r.get("rating_change") or None,
                r.get("title") or None,
                r.get("author") or None,
                r.get("institution") or None,
                r.get("url") or None,
            ))
    cur.executemany(
        "INSERT OR IGNORE INTO research_report "
        "(code, date, rating, rating_change, title, author, institution, url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT research_report: {len(rows)} 行")
    return len(rows)


def import_em_global_news(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """财经新闻 ← raw/reference/em_global_news.csv

    CSV 字段: time,title,summary,source,url (time 是 'YYYY-MM-DD HH:MM:SS')
    DB 字段:  date,title,url,summary,source (date 是 'YYYY-MM-DD')

    2026-06-25 修复: 原来读 r.get("date") 拿到空 → date 全 NULL; 现改读 r["time"][:10]
    """
    import csv as _csv
    csv_path = data_dir / "raw" / "reference" / "em_global_news.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    rows = []
    for r in _csv.DictReader(open(csv_path, "r", encoding="utf-8-sig", newline="")):
        # CSV 列名是 time, 取前 10 字符作为 date
        time_val = r.get("time") or ""
        date_val = time_val[:10] if time_val else None
        if not date_val or len(date_val) != 10:
            continue  # 跳过空日期或格式异常的行
        rows.append((
            date_val,
            r.get("title") or None,
            r.get("url") or None,
            r.get("summary") or None,
            r.get("source") or None,
        ))
    if full:
        cur.execute("DELETE FROM em_global_news")
    cur.executemany(
        "INSERT OR IGNORE INTO em_global_news "
        "(date, title, url, summary, source) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT em_global_news: {len(rows)} 行")
    return len(rows)


def import_ths_hot_reason(cur: sqlite3.Cursor, data_dir: Path, full: bool) -> int:
    """同花顺热股 ← raw/reference/ths_hot_reason.csv"""
    import csv as _csv
    csv_path = data_dir / "raw" / "reference" / "ths_hot_reason.csv"
    if not csv_path.exists():
        print(f"   ⚠️ {csv_path.name} 不存在, 跳过")
        return 0
    def _f(s):
        if not s or s == "nan":
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in _csv.DictReader(f):
            code_raw = r.get("code", "").strip()
            code = code_raw.zfill(6) if code_raw.isdigit() else code_raw
            rows.append((
                r.get("date", "")[:10] if r.get("date") else None,
                int(r["rank"]) if r.get("rank", "").isdigit() else None,
                code, r.get("name") or None,
                _f(r.get("hot_value")), r.get("concept") or None,
                r.get("reason") or None, _f(r.get("change_pct")),
            ))
    cur.executemany(
        "INSERT OR IGNORE INTO ths_hot_reason "
        "(date, rank, code, name, hot_value, concept, reason, change_pct) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"   ✅ INSERT ths_hot_reason: {len(rows)} 行")
    return len(rows)
