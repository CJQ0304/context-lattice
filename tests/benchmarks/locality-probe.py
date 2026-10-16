# -*- coding: utf-8 -*-
"""局部性实测：模型做一件事，真正要看的那块占全部素材的多少。

不依赖任何外部服务，只用标准库 + tiktoken（缺失时退化为字符数）。
"""
import os, sys, json

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    ENC = None


def tk(s):
    return len(ENC.encode(s)) if ENC else len(s)


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根
PKG = ROOT
sys.path.insert(0, os.path.join(ROOT, "tests"))
from pkg_boundary import package_files                      # noqa: E402

TEXT_EXT = (".md", ".py", ".json", ".ctx", ".txt", ".yaml", ".yml")


def collect(base):
    """语料 = **交付包边界内**的文本文件（不含 tests/ docs/）。

    用同一边界是为了让「全量」这个分母稳定：仓库里加开发件不该改变已发布的实测数据。
    """
    out = []
    for rel, p in package_files(base):
        if not rel.endswith(TEXT_EXT):
            continue
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        out.append((rel, tk(t), len(t)))
    return sorted(out)


files = collect(PKG)
total = sum(f[1] for f in files)

print("=" * 74)
print("语料：context-lattice/ 交付包全部文本文件")
print("=" * 74)
print("%-52s %8s %8s" % ("文件", "token", "字符"))
print("-" * 74)
for rel, t, c in files:
    print("%-52s %8d %8d" % (rel, t, c))
print("-" * 74)
print("%-52s %8d" % ("合计（全贴进窗口 = 200k 场景里的「原始」）", total))
print()

# 索引层：技能 index 只存 key 层 T/P 名 + 域 + rev + 日期 + 计数
# 这里用「所有 md 的一级/二级标题」近似「索引能提供的导航粒度」
idx_lines = []
for rel, t, c in files:
    p = os.path.join(ROOT, rel)
    try:
        for line in open(p, encoding="utf-8", errors="replace"):
            s = line.strip()
            if s.startswith("#"):
                idx_lines.append(s)
    except Exception:
        pass
index_text = "\n".join(idx_lines)
index_tok = tk(index_text)

# 三个代表性任务的工作集（按真实改动需要读什么来定）
# 路径口径 = 仓库根相对路径（与 collect() 的输出一致）
TASKS = {
    "T1 给 ctx.py 加一个子命令": [
        "scripts/ctx.py",
        "references/store-format.md",
        "references/split-rules.md",
    ],
    "T2 改 SKILL.md 的 description": [
        "SKILL.md",
        "README.md",
    ],
    "T3 写一条新的拆分规则": [
        "references/split-rules.md",
        "references/store-format.md",
    ],
}
byrel = {f[0]: f[1] for f in files}

print("=" * 74)
print("任务工作集（模型真正必须看到的那一块）")
print("=" * 74)
print("%-34s %10s %10s %10s" % ("任务", "工作集tok", "全量tok", "占比"))
print("-" * 74)
for name, fl in TASKS.items():
    ws = sum(byrel.get(f, 0) for f in fl)
    print("%-34s %10d %10d %9.1f%%" % (name, ws, total, 100.0 * ws / total))
print()

print("=" * 74)
print("两种做法的窗口占用对比")
print("=" * 74)
print("索引层开销（导航用，只读一次）= %d token，占全量 %.1f%%" % (index_tok, 100.0 * index_tok / total))
print()
print("%-34s %12s %12s %8s" % ("任务", "全贴原文", "索引+工作集", "净省"))
print("-" * 74)
for name, fl in TASKS.items():
    ws = sum(byrel.get(f, 0) for f in fl)
    naive = total
    smart = index_tok + ws
    print("%-34s %12d %12d %7.1f%%" % (name, naive, smart, 100.0 * (1 - smart / float(naive))))
print()

# 结论：压缩率 = 1 - (索引 + 工作集) / 原始
big = "T1 给 ctx.py 加一个子命令"
ws = sum(byrel.get(f, 0) for f in TASKS[big])
print("=" * 74)
print("对照：如果任务需要读「全部」素材（工作集 = 100%）")
print("  索引 + 全量 = %d + %d = %d  →  净省 %.1f%%（负值 = 反而更贵）"
      % (index_tok, total, index_tok + total, 100.0 * (1 - (index_tok + total) / float(total))))
print("=" * 74)
