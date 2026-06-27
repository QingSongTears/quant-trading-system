#!/usr/bin/env python3
"""单文件测试：转换 dashboard.html 并打印 head 部分"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path("E:/work/work/quant-trading-system/scripts").resolve()))
from convert_standalone_to_jinja2 import convert

src = Path("E:/work/work/quant-trading-system/output/dashboard.html")
text = src.read_text(encoding="utf-8")
out = convert(text)

print("=" * 60)
print("ORIGINAL HEAD (first 8 lines):")
print("=" * 60)
for line in text.splitlines()[:8]:
    print(line)

print("\n" + "=" * 60)
print("CONVERTED HEAD (first 25 lines):")
print("=" * 60)
for line in out.splitlines()[:25]:
    print(line)

print("\n" + "=" * 60)
print("CONVERTED TAIL (last 8 lines):")
print("=" * 60)
for line in out.splitlines()[-8:]:
    print(line)
