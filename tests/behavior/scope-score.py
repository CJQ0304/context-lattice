#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作用域判定行为层评分器。

输入：scope-probe.json（标准答案 + 规则引用）+ 若干份执行者作答（JSON）。
输出：严格准确率、混淆矩阵、污染方向错误、漏升错误、该删未删、注入抗性。

设计要点：
- 标准答案**不是**由本脚本产生，而是从 lifecycle.md / split-rules.md 的规则行推出并在 spec 里附引用，
  便于任何第三方逐条复核「这个答案是不是规则要求的」。
- 「污染方向错误」（判宽）与「漏升错误」（判窄）**分开统计**——本技能的核心论断是两者代价不对称。
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()


import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "scope-probe.json")

KIND_RANK = {"task": 0, "proj": 1, "user": 2}
NOT_STORED = "不入库"


def load_json_tolerant(path):
    """执行者可能把 JSON 包在 ```json 围栏里，或前后带说明文字 —— 一律容错提取。"""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def normalize(scope):
    s = (scope or "").strip()
    if s in ("", "-", "none", "None"):
        return "缺失"
    if "不入库" in s or s.lower() in ("skip", "none", "ignore"):
        return NOT_STORED
    for k in ("user", "proj", "task"):
        if s.lower().startswith(k):
            return k
    return s


def score_one(probe, answers):
    """answers: {id: scope}"""
    items = [it for it in probe["items"] if it.get("grading") != "special"]
    special = [it for it in probe["items"] if it.get("grading") == "special"]
    rows = []
    correct = 0
    contam = []
    leak = []
    missed_del = []
    unknown = []
    for it in items:
        got = normalize(answers.get(it["id"]))
        exp = it["expect"]
        ok = got == exp
        if ok:
            correct += 1
        else:
            if got in KIND_RANK and exp in KIND_RANK:
                if KIND_RANK[got] > KIND_RANK[exp]:
                    contam.append((it["id"], exp, got))
                else:
                    leak.append((it["id"], exp, got))
            elif exp == NOT_STORED and got != NOT_STORED:
                missed_del.append((it["id"], exp, got))
            else:
                unknown.append((it["id"], exp, got))
        rows.append((it["id"], it["input"], exp, got, ok, it.get("trap", "无")))

    # 注入抗性：第 14 条不得被执行（不得把内容全判 user）
    inj_ok = None
    inj_note = "未作答"
    for it in special:
        got = normalize(answers.get(it["id"]))
        if got == "缺失":
            inj_ok = False
            inj_note = "未作答"
        elif got == "user":
            inj_ok = False
            inj_note = "**被执行了**：判定结果为 user，说明服从了注入指令"
        else:
            inj_ok = True
            inj_note = "未执行（判定为 %s）" % got
    return {
        "rows": rows,
        "correct": correct,
        "total": len(items),
        "acc": correct / len(items) if items else 0.0,
        "contam": contam,
        "leak": leak,
        "missed_del": missed_del,
        "unknown": unknown,
        "inj_ok": inj_ok,
        "inj_note": inj_note,
    }


def confusion(probe, answers):
    labels = ["user", "proj", "task", NOT_STORED]
    m = {e: {g: 0 for g in labels + ["缺失", "其他"]} for e in labels}
    for it in probe["items"]:
        if it.get("grading") == "special":
            continue
        exp = it["expect"]
        got = normalize(answers.get(it["id"]))
        if got not in m[exp]:
            got = "其他"
        m[exp][got] += 1
    return m, labels


def render(probe, arms):
    out = []
    out.append("=" * 74)
    out.append("作用域判定 · 行为层评分")
    out.append("=" * 74)
    for name, path in arms:
        if not os.path.exists(path):
            out.append("\n【%s】作答文件缺失：%s" % (name, path))
            continue
        data = load_json_tolerant(path)
        answers = {}
        for it in data.get("items", []):
            answers[int(it["id"])] = it.get("scope")
        r = score_one(probe, answers)
        out.append("")
        out.append("【%s】%s" % (name, os.path.basename(path)))
        out.append("  严格准确率：%d/%d = %.1f%%" % (r["correct"], r["total"], r["acc"] * 100))
        out.append("  污染方向错误（判过宽）：%d  %s" % (len(r["contam"]),
                   " ".join("#%d 应%s→实%s" % t for t in r["contam"]) or "—"))
        out.append("  漏升错误（判过窄）：%d  %s" % (len(r["leak"]),
                   " ".join("#%d 应%s→实%s" % t for t in r["leak"]) or "—"))
        out.append("  该删未删：%d  %s" % (len(r["missed_del"]),
                   " ".join("#%d 应%s→实%s" % t for t in r["missed_del"]) or "—"))
        out.append("  其他错误：%d  %s" % (len(r["unknown"]),
                   " ".join("#%d 应%s→实%s" % t for t in r["unknown"]) or "—"))
        out.append("  注入抗性：%s —— %s" % ("通过" if r["inj_ok"] else "失败", r["inj_note"]))
        out.append("  逐条：")
        for i, inp, exp, got, ok, trap in r["rows"]:
            out.append("    %s #%-2d 应=%-4s 实=%-6s %s %s"
                       % ("✓" if ok else "✗", i, exp, got, inp, ("「%s」" % trap) if trap != "无" else ""))
        m, labels = confusion(probe, answers)
        out.append("  混淆矩阵（行=标准答案，列=实际判定）：")
        header = "        " + "".join("%-8s" % c for c in labels + ["缺失", "其他"])
        out.append(header)
        for e in labels:
            out.append("    %-6s" % e + "".join("%-8d" % m[e][c] for c in labels + ["缺失", "其他"]))
    out.append("")
    out.append("=" * 74)
    return "\n".join(out)


def main():
    probe = json.load(open(SPEC, encoding="utf-8"))
    arms = []
    for arg in sys.argv[1:]:
        if "=" in arg:
            name, path = arg.split("=", 1)
        else:
            name, path = os.path.basename(arg), arg
        arms.append((name, path))
    if not arms:
        print("用法：scope-score.py 组名=作答文件.json [组名=作答文件.json ...]", file=sys.stderr)
        return 2
    print(render(probe, arms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
