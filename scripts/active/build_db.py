#!/usr/bin/env python3
"""build_db.py shim (2026-06-28 #86)

兼容层 — 实际实现已拆到 build_db/ 包:
  - _schema.py      SQLite DDL
  - _core.py        核心 4 importers
  - _extended.py    扩展 5 importers
  - _finance.py     财务 2 importers
  - _research.py    研究 3 importers

用法不变:
    python scripts/active/build_db.py              # 全量
    python scripts/active/build_db.py --incremental
    python scripts/active/build_db.py --table daily_price
"""
from __future__ import annotations

# 直接 import package 然后调用 main
from build_db import main

if __name__ == "__main__":
    main()
