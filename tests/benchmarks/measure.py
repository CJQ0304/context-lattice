#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测量各视图的真实字符量，用于回填文档里的效率对照表（不估、只实测）。"""
import os
import subprocess
import sys
import tempfile

PY = sys.executable
_HERE = os.path.dirname(os.path.abspath(__file__))          # tests/benchmarks/
PKG = os.path.dirname(os.path.dirname(_HERE))               # 仓库根（= 技能包根）
CTX = os.path.join(PKG, "scripts", "ctx.py")

DELTA = """T 品牌定位
P 确定目标人群
R 25-35 岁新中产 [约束]
R 注重健康养生 [偏好]
P 确定价格带 [进行中]
R 客单价 18-28 元 [约束]
T 竞品分析 [已结]
P 头部品牌盘点
R 喜茶 / 茶颜悦色
T 视觉元素
P 确定主色
R 青瓷绿 + 米白
"""


def run(args, stdin=None):
    return subprocess.run([PY, CTX] + args, input=stdin, capture_output=True,
                          text=True, encoding="utf-8").stdout


d = os.path.join(tempfile.gettempdir(), "cl_measure")
os.makedirs(d, exist_ok=True)
f = os.path.join(d, "s.ctx")
if os.path.exists(f):
    os.remove(f)
run(["init", "-f", f, "--date", "2026-10-15", "--top", "新中式养生茶饮品牌作业", "--stdin"], DELTA)

RAW = 4200
rows = []
full = run(["read", "-f", f])
rows.append(("全量 read（level 3）", len(full)))
rows.append(("骨架层 --level 2", len(run(["read", "-f", f, "--level", "2"]))))
rows.append(("索引层 --level 1", len(run(["read", "-f", f, "--level", "1"]))))
rows.append(("单事物 --topic T1", len(run(["read", "-f", f, "--topic", "T1"]))))
rows.append(("预算裁剪 --budget 120", len(run(["read", "-f", f, "--budget", "120"]))))

print("原始对话字符数（调用方提供）：%d" % RAW)
print("%-26s %8s %10s" % ("视图", "字符数", "压缩率"))
for name, n in rows:
    print("%-26s %8d %9.1f%%" % (name, n, (1 - n / RAW) * 100))
print()
print(run(["stats", "-f", f, "--raw-chars", str(RAW)]))
