#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟脚本：逐条打印 ctx.py 的各类视图与错误处理结果。

自包含：先建一个演示库，再把每种视图/错误路径的结果打印出来，
用来肉眼快速确认「脚本还活着、输出长什么样」。
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()

import os
import shutil
import subprocess
import sys

PY = sys.executable
_HERE = os.path.dirname(os.path.abspath(__file__))          # tests/
_ROOT = os.path.dirname(_HERE)                              # 仓库根（= 技能包根）
CTX = os.path.join(_ROOT, "scripts", "ctx.py")
_DEMO = os.path.join(os.environ.get("TEMP", "/tmp"), "cl")
S = os.path.join(_DEMO, "s.ctx")

DEMO_DELTA = """T 品牌定位
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
    p = subprocess.run([PY, CTX] + args, input=stdin, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def show(title, args, stdin=None):
    rc, out, err = run(args, stdin)
    print("=== %s (exit=%d) ===" % (title, rc))
    if out:
        print(out.rstrip())
    if err:
        print("[stderr] " + err.rstrip())
    print()


# 建演示库（自包含，不依赖任何预置文件）
shutil.rmtree(_DEMO, ignore_errors=True)
os.makedirs(_DEMO, exist_ok=True)
print("=== init 演示库 (exit=%d) ===" % run(
    ["init", "-f", S, "--date", "2026-10-15", "--top", "演示会话", "--stdin"],
    stdin=DEMO_DELTA)[0])
print()


show("read --level 1", ["read", "-f", S, "--level", "1", "--no-header"])
show("read --level 2", ["read", "-f", S, "--level", "2", "--no-header"])
show("read --topic T1", ["read", "-f", S, "--topic", "T1", "--no-header"])
show("read --topic 视觉元素（按名字）", ["read", "-f", S, "--topic", "视觉元素", "--no-header"])
show("read --purpose P1.1", ["read", "-f", S, "--purpose", "P1.1", "--no-header"])
show("read --budget 170", ["read", "-f", S, "--budget", "170"])

# 回灌验证：把裁剪后的视图写回文件，再 validate（视图必须是合法存储）
rc, out, err = run(["read", "-f", S, "--budget", "170"])
tmp_view = os.path.join(os.environ.get("TEMP", "/tmp"), "cl", "view.ctx")
with open(tmp_view, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(out)
show("回灌：validate 裁剪视图", ["validate", "-f", tmp_view])
rc, out2, err = run(["read", "-f", S, "--topic", "T1"])
with open(tmp_view, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(out2)
show("回灌：validate 单事物视图", ["validate", "-f", tmp_view])
rc, out3, err3 = run(["read", "-f", S, "--level", "1"])
with open(tmp_view, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(out3)
show("回灌：validate level1 视图", ["validate", "-f", tmp_view])
show("read --topic T99（不存在）", ["read", "-f", S, "--topic", "T99"])
show("tree", ["tree", "-f", S])
show("validate", ["validate", "-f", S])
show("stats --raw-chars 4200", ["stats", "-f", S, "--raw-chars", "4200"])
show("stats --raw-chars 0（非法）", ["stats", "-f", S, "--raw-chars", "0"])
show("export json", ["export", "-f", S, "--format", "json"])
show("export md", ["export", "-f", S, "--format", "md"])
show("找不到文件", ["validate", "-f", os.path.join(os.environ.get("TEMP", "/tmp"), "cl", "nope.ctx")])
show("prune 无动作", ["prune", "-f", S])
show("prune 无 --force", ["prune", "-f", S, "--drop-stale"])
show("add 非法增量行", ["add", "-f", S, "--stdin"], stdin="X 乱写的行\n")
show("add P 先于 T", ["add", "-f", S, "--stdin"], stdin="P 没挂靠\n")
show("add 缺 --purpose", ["add", "-f", S, "--topic", "品牌定位"])
show("无子命令", [])
