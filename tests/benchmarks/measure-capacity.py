#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
容量测量：一个 .ctx 存储到底占多少字符 / 多少 token，各视图各占多少。

用途：回答「300k 窗口下，这个技能能装多少」——必须用 token 说话，不能用字符外推。

被测对象：ctx.py（交付包内的脚本，未做任何改动）
素材：合成规模库（用于测「视图机制的 token 开销」这一机制量，不代表任何真实项目内容）
锚点：同时输出真实的 A 组产物（12 轮真实对话的产物）作为对照

运行：python measure-capacity.py
"""
import os
import subprocess
import sys
import tempfile

PY = sys.executable
_HERE = os.path.dirname(os.path.abspath(__file__))          # tests/benchmarks/
_TESTS = os.path.dirname(_HERE)                             # tests/
PKG = os.path.dirname(_TESTS)                               # 仓库根（= 技能包根）
CTX = os.path.join(PKG, "scripts", "ctx.py")
REAL = os.path.join(_TESTS, "behavior", "raw", "arm-a-store.ctx")

TOPICS = ["品牌定位", "目标人群", "价格策略", "竞品分析", "视觉识别", "包装设计",
          "产品配方", "供应链", "门店选址", "会员体系", "内容营销", "社群运营",
          "联名计划", "季节限定", "外卖渠道", "私域流量", "客服标准", "退换政策",
          "品控流程", "成本核算", "定价模型", "SKU 规划", "上新节奏", "宣传物料",
          "摄影风格", "文案调性", "KOL 合作", "投放渠道", "数据看板", "复盘机制",
          "团队分工", "排期管理", "法务合规", "商标注册", "财务预算", "仓储物流",
          "设备采购", "培训体系", "考核指标", "风险预案"]

PURPOSES = ["确定方案", "收敛选项", "确认边界", "落地执行"]

REQS = ["优先考虑成本可控", "必须在下季度前完成", "不得使用未经审核的素材",
        "保留旧方案以便追溯", "以用户反馈为准", "由项目负责人最终拍板",
        "对外口径统一由市场部出", "涉及费用的需先报批", "不引入第三方依赖",
        "所有变更需留版本记录", "交付物同时提供源文件", "上线前需过一次完整验收",
        "口径以本仓库为准", "不接收口头需求"]

TAGS = ["约束", "约束", "偏好", "待办", ""]


def build_delta():
    lines = []
    for i, t in enumerate(TOPICS):
        lines.append("T %s" % t)
        for j in range(3):
            lines.append("P %s" % PURPOSES[(i + j) % len(PURPOSES)])
            for k in range(3):
                req = REQS[(i * 3 + j * 3 + k) % len(REQS)]
                tag = TAGS[(i + j + k) % len(TAGS)]
                lines.append("R %s%s" % (req, (" [%s]" % tag) if tag else ""))
    return "\n".join(lines) + "\n"


def run(args, stdin=None):
    r = subprocess.run([PY, CTX] + args, input=stdin, capture_output=True,
                       text=True, encoding="utf-8")
    return r.stdout, r.returncode


def tokenize(text):
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None
    return len(enc.encode(text))


def measure(path, label, raw_chars):
    full, _ = run(["read", "-f", path])
    lv2, _ = run(["read", "-f", path, "--level", "2"])
    lv1, _ = run(["read", "-f", path, "--level", "1"])
    topic, _ = run(["read", "-f", path, "--topic", "T1"])
    budget, _ = run(["read", "-f", path, "--budget", "400"])

    s, rc = run(["stats", "-f", path, "--raw-chars", str(raw_chars)])
    nodes = ""
    for line in s.splitlines():
        if line.startswith("nodes"):
            nodes = line.strip()

    views = [("全量 read", full), ("--level 2", lv2), ("--level 1", lv1),
             ("--topic T1", topic), ("--budget 400", budget)]

    print("=" * 74)
    print("%s    (%s)" % (label, nodes))
    print("-" * 74)
    print("%-14s %8s %10s %10s %12s" % ("视图", "字符", "token", "t/节点", "占全量"))
    total_tok = tokenize(full)
    for name, text in views:
        n = len(text)
        tk = tokenize(text)
        if tk is None:
            print("%-14s %8d %10s" % (name, n, "n/a"))
        else:
            print("%-14s %8d %10d %10.1f %11.1f%%"
                  % (name, n, tk, tk / max(1, count_nodes(path)[0]), 100.0 * tk / total_tok))
    print()
    return full, total_tok


def count_nodes(path):
    text = open(path, encoding="utf-8").read()
    t = sum(1 for l in text.splitlines() if l.startswith("T"))
    p = sum(1 for l in text.splitlines() if l.lstrip().startswith("P"))
    r = sum(1 for l in text.splitlines() if l.lstrip().startswith("R"))
    return t + p + r, (t, p, r)


d = os.path.join(tempfile.gettempdir(), "cl_capacity")
os.makedirs(d, exist_ok=True)
big = os.path.join(d, "big.ctx")
if os.path.exists(big):
    os.remove(big)

delta = build_delta()
out, rc = run(["init", "-f", big, "--date", "2026-10-16", "--top", "合成规模库", "--stdin"], delta)
if rc != 0:
    print("建库失败 rc=%d\n%s" % (rc, out))
    sys.exit(1)
v, _ = run(["validate", "-f", big])
print("规模库 validate rc=%d  %s" % (rc, v.strip().splitlines()[0] if v.strip() else ""))

print()
print("### 锚点：真实产物（12 轮真实对话 → 5T/10P/15R）")
rc_nodes, rp = count_nodes(REAL)
rt = open(REAL, encoding="utf-8").read()
rtk = tokenize(rt)
print("  字符 %d / 行 %d / 节点 %d%s / token %s"
      % (len(rt), len(rt.splitlines()), rc_nodes,
         " (T%d P%d R%d)" % rp, rtk if rtk is not None else "n/a"))
if rtk:
    print("  → 每节点 %.1f token，每行 %.1f token" % (rtk / rc_nodes, rtk / len(rt.splitlines())))
print()

print("### 规模库：合成 %d 行" % len(delta.splitlines()))
big_nodes, bp = count_nodes(big)
bt = open(big, encoding="utf-8").read()
btk = tokenize(bt)
print("  字符 %d / 行 %d / 节点 %d (T%d P%d R%d) / token %s"
      % (len(bt), len(bt.splitlines()), big_nodes, bp[0], bp[1], bp[2],
         btk if btk is not None else "n/a"))
if btk:
    print("  → 每节点 %.1f token，每行 %.1f token" % (btk / big_nodes, btk / len(bt.splitlines())))
print()

measure(big, "规模库各视图", len(bt))
