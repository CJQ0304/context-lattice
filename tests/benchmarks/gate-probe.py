# -*- coding: utf-8 -*-
"""门控原型（**历史件：早于 1.2.0 的 `ctx.py plan`**）。

分工原则：脚本负责一切可机械计算的量（T / I / W / 断点 / 判定）；
模型只负责语义标注（压缩比 r、预计复用次数 k）。—— 能算的不要让模型估。

**口径差异（保留本件就是为了留痕）**：本原型按旧口径把 `--reuse k` 作为输入，
块级断点为 `k > (1+r)/(1−r)`。随包的 `ctx.py plan` 已把 k 消掉
（理由见 `CHANGELOG.md` 的 1.2.0「设计决策：把 k 这个参数消掉」），
断点为 `k > 1/(1−r)`，因此 `plan` **没有** `--reuse` 参数。
两者共用同一把尺算 T/I/W，所以判定结论可交叉验证。

用法:
    python gate-probe.py --root . --work "scripts/ctx.py,references/store-format.md"
    python gate-probe.py --break-even-table
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()

import os
import sys
import argparse

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    ENC = None

TEXT_EXT = (".md", ".py", ".json", ".ctx", ".txt", ".yaml", ".yml",
            ".js", ".html", ".css", ".ts", ".tsx", ".go", ".rs", ".java")


def tk(s):
    return len(ENC.encode(s)) if ENC else len(s)


def tokenize_file(p):
    try:
        return tk(open(p, encoding="utf-8", errors="replace").read())
    except Exception:
        return 0


def walk_text(base):
    out = []
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in (".git", "__pycache__", ".workbuddy", "node_modules")]
        for fn in fns:
            if fn.endswith(TEXT_EXT):
                out.append(os.path.join(dp, fn))
    return sorted(out)


def nav_index_tokens(base):
    """索引导航层的近似口径：只数「标题/键名」这类导航行的 token。

    注意：这是近似口径。真实的 I 应当由 ctx.py index 在**已建库**上实测；
    本脚本用在「还没有库」的原始语料上，故用标题行代替键层名。
    """
    buf = []
    for p in walk_text(base):
        try:
            for line in open(p, encoding="utf-8", errors="replace"):
                s = line.strip()
                if s.startswith("#") or s.startswith("def ") or s.startswith("class "):
                    buf.append(s)
        except Exception:
            pass
    return tk("\n".join(buf))


def break_even_table():
    print("块级断点（读完再压口径）：k > 1 / (1 - r)     （r = 压缩后 / 原始）")
    print("含义：原文本来就要读，压存不额外付读取费；k 是预计还要复用几次。")
    print("-" * 68)
    print("%10s %14s   %s" % ("压缩比 r", "需要复用 k≥", "典型素材"))
    print("-" * 68)
    rows = [(0.05, "摘要/结论"), (0.10, "讨论记录"), (0.20, "会议纪要"),
            (0.30, "需求文档"), (0.50, "半压缩"), (0.70, "偏原文"),
            (0.80, "接近原文"), (0.90, "代码片段"), (0.99, "必须逐字看的对象")]
    import math
    for r, note in rows:
        k = 1.0 / (1.0 - r)
        need = int(math.floor(k)) + 1
        print("%9.2f %14d   %s" % (r, need, note))
    print("-" * 68)
    print("r → 1 时 k → ∞：**必须逐字看到的东西，压缩永远不划算**。")
    print()
    print("对照 旧口径（先 INLINE 一次再补建库）k > (1+r)/(1-r)，门槛明显更高：")
    for r, _ in rows[:8]:
        old = int(math.floor((1.0 + r) / (1.0 - r))) + 1
        new = int(math.floor(1.0 / (1.0 - r))) + 1
        print("  r=%.2f   旧 k≥%-3d  新 k≥%-3d" % (r, old, new))
    print()
    print("结论：**读完再压等于把原料读取成本从决策里删掉** → 规则退化为「读过的块就压存」，")
    print("      不需要预估 k，也不需要观测 k。")


def parse_work(arg, root):
    """支持逗号分隔的相对/绝对路径。"""
    items = []
    for part in arg.split(","):
        part = part.strip()
        if not part:
            continue
        p = part if os.path.isabs(part) else os.path.join(root, part)
        items.append(os.path.normpath(p))
    return items


def main():
    ap = argparse.ArgumentParser(
        description="读取源替换门控：判定该压缩还是该当原子节点")
    ap.add_argument("--root", help="语料根目录")
    ap.add_argument("--work", help="工作集文件清单（逗号分隔，相对 --root）")
    ap.add_argument("--reuse", type=int, default=1, help="预计复用次数 k（默认 1）")
    ap.add_argument("--break-even-table", action="store_true", help="打印块级断点表")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出判定")
    args = ap.parse_args()

    if args.break_even_table:
        break_even_table()
        return 0

    if not args.root:
        ap.error("需要 --root（或用 --break-even-table）")

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print("错误：--root 不是目录：%s" % root, file=sys.stderr)
        return 2

    files = walk_text(root)
    if not files:
        print("错误：%s 下没有可统计的文本文件" % root, file=sys.stderr)
        return 2

    per_file = [(f, tokenize_file(f)) for f in files]
    T = sum(t for _, t in per_file)
    I = nav_index_tokens(root)

    if args.work:
        ws = set(parse_work(args.work, root))
        hit = [(f, t) for f, t in per_file if os.path.normpath(f) in ws]
        missing = sorted(ws - {os.path.normpath(f) for f, _ in per_file})
        W = sum(t for _, t in hit)
    else:
        hit, missing, W = [], [], 0

    ratio = (W / float(T)) if T else 0.0
    break_even = 1.0 - (I / float(T)) if T else 0.0
    use_store = ratio <= break_even

    print("=" * 70)
    print("读取源替换门控判定")
    print("=" * 70)
    print("语料根           : %s" % root)
    print("文件数           : %d" % len(files))
    print("全量 T           : %d token" % T)
    print("索引导航层 I     : %d token  (占 T 的 %.1f%%)" % (I, 100.0 * I / T))
    print("库级断点 1−I/T   : %.1f%%   ← 工作集超过这个比例就不该建库" % (100.0 * break_even))
    print("-" * 70)
    if args.work:
        print("工作集 W         : %d token  (占 T 的 %.1f%%)" % (W, 100.0 * ratio))
        print("预计复用次数 k   : %d" % args.reuse)
        if missing:
            print("!! 清单里没找到的文件（判定按实际命中算）:")
            for m in missing:
                print("   - %s" % m)
        print("-" * 70)
        naive = T
        smart = I + W
        delta = 100.0 * (1.0 - smart / float(naive)) if naive else 0.0
        print("全贴原文         : %d token" % naive)
        print("索引 + 工作集    : %d token" % smart)
        print("净省             : %+.1f%%" % delta)
        print("-" * 70)
        if use_store:
            print("判定：STORE —— 工作集 %.1f%% ≤ 断点 %.1f%%，用局部视图。"
                  % (100.0 * ratio, 100.0 * break_even))
        else:
            print("判定：INLINE —— 工作集 %.1f%% > 断点 %.1f%%，建库是负收益（净省 %+.1f%%）。"
                  % (100.0 * ratio, 100.0 * break_even, delta))
            print("      此时应把整块当**一个完整事件节点**直接处理，不要拆键。")
        print()
        print("块级复核（每块单独判，库级判定不替代它）：")
        print("  %-52s %9s %10s" % ("文件", "token", "占比"))
        for f, t in sorted(hit, key=lambda x: -x[1]):
            rel = os.path.relpath(f, root).replace("\\", "/")
            print("  %-52s %9d %9.1f%%" % (rel, t, 100.0 * t / float(T) if T else 0.0))
        print("  —— 其中凡「必须逐字操作」的块（要改的代码 / 要分析的数据），")
        print("     无论占比多少一律走事件节点；只有能压到 r<0.5 且复用 k≥4 的块才拆键。")
    else:
        print("未给 --work：只报告语料规模与库级断点。")

    if args.json:
        import json
        print()
        print(json.dumps({
            "root": root, "files": len(files), "T": T, "I": I,
            "break_even": round(break_even, 4), "W": W, "ratio": round(ratio, 4),
            "reuse": args.reuse, "verdict": "STORE" if use_store else "INLINE",
        }, ensure_ascii=False, indent=2))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
