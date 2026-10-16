"""真实任务的行为层成本核算（数据来源：两臂自己报告的第 4 问清单）。

不重跑模型，只做机械核算：
  * 把「精读全文」的文件按真实 token 计入
  * 「部分读」按区间计入
  * 「仅检索」不计（grep 命中不等于把文件读进窗口）
再跑一次门控判定，看这个任务的工作集落在断点哪一侧。
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()

import os
import shutil
import subprocess
import sys
import tempfile

import tiktoken

HERE = os.path.dirname(os.path.abspath(__file__))           # tests/benchmarks/
sys.path.insert(0, os.path.dirname(HERE))                   # tests/
from pkg_boundary import corpus_files                   # noqa: E402

PKG = os.path.dirname(os.path.dirname(HERE))                # 仓库根（= 技能包根）
CTX = os.path.join(PKG, "scripts", "ctx.py")
PY = sys.executable
ENC = tiktoken.get_encoding("cl100k_base")


def tk(text):
    return len(ENC.encode(text))


def read(rel):
    return open(os.path.join(PKG, rel), encoding="utf-8", errors="replace").read()


# 臂 A 自报：精读全文
A_FULL = ["SKILL.md", "scripts/ctx.py", "references/store-format.md",
          "references/split-rules.md", "references/compression-strategy.md",
          "references/lifecycle.md", "CHANGELOG.md",
          "templates/skeleton.ctx", "templates/delta.txt"]
# 臂 A 自报：部分读 README.md L140–L228
A_PART = [("README.md", 140, 228)]

# 臂 B 自报：全部素材（其中 lifecycle、host-memory-adapter、start-prompt
# 标为「已列出/未逐行引用」，仍按读过计入 —— 这对 B 臂**有利**，宁可高估它）。
# **不含 `LICENSE`**：它是无扩展名的法律样板，`plan` 不把它算作可读素材，
# 语料分母也就不该含它 —— 否则会出现「同一屏里语料全量 86,489、门控全量 86,259」
# 这种一个脚本两个分母的错（实测踩过）。
B_FULL = ["SKILL.md", "README.md", "CHANGELOG.md",
          "scripts/ctx.py", "references/store-format.md", "references/split-rules.md",
          "references/compression-strategy.md", "references/degradation.md",
          "references/coverage-matrix.md", "references/lifecycle.md",
          "references/host-memory-adapter.md",
          "templates/skeleton.ctx", "templates/delta.txt", "templates/start-prompt.md"]


def cost_a():
    tot = sum(tk(read(f)) for f in A_FULL)
    for rel, lo, hi in A_PART:
        lines = read(rel).split("\n")
        tot += tk("\n".join(lines[lo - 1:hi]))
    return tot


def cost_b():
    return sum(tk(read(f)) for f in B_FULL)


def corpus_total():
    """语料全量 T：**交付包边界内、且 `plan` 会算进 T 的文本文件**。

    口径经过三次修正，三次都是「分母算错」：
      ① 第一版写成 endswith("")，恒真，把 .pyc 二进制也算进去 → 分母膨胀到 17 万。
      ② 仓库里新增 `tests/` `docs/` 之后，若仍按「整仓」计，同一份语料换一次
         目录布局就换一个 T，已发布的实测数据立刻不可复现 → 改为取交付包边界
         （定义在 `tests/pkg_boundary.py`，与打包器共用同一份）。
      ③ 又按扩展名自带了一套规则、并把无扩展名的 `LICENSE` 也计入 → 与同一屏下
         `plan` 报的 T 差 230 token（一个脚本、两个分母）。现在统一走
         `pkg_boundary.corpus_files`（唯一真相，三个测量脚本共用）。
    """
    return sum(tk(open(full, encoding="utf-8", errors="replace").read())
               for _, full in corpus_files(PKG))


def main():
    total = corpus_total()
    a, b = cost_a(), cost_b()
    print("=" * 70)
    print("真实任务：给存储格式新增一种行类型（F）")
    print("=" * 70)
    print("语料全量              : %6d token" % total)
    print("臂 A（告知有技能）读入 : %6d token  (%.1f%% 全量)" % (a, 100.0 * a / total))
    print("臂 B（不告知技能）读入 : %6d token  (%.1f%% 全量)" % (b, 100.0 * b / total))
    print("差值（A − B）         : %6d token  (%.1f%%)" % (a - b, 100.0 * (a - b) / b))

    print()
    print("门控判定（把 A 臂实际读的文件当工作集）:")
    # plan 是通用工具，不认识本仓库的 tests/ docs/。先把交付包摊平到临时目录
    # 再交给它，否则「语料全量」这个分母会从交付包变成整仓，与上面的数字对不上。
    corpus = os.path.join(tempfile.gettempdir(), "cl_rw_cost_corpus")
    shutil.rmtree(corpus, ignore_errors=True)
    for rel, full in corpus_files(PKG):
        dst = os.path.join(corpus, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(full, dst)
    work = ",".join(A_FULL)
    p = subprocess.run([PY, CTX, "plan", "--root", corpus, "--work", work],
                       capture_output=True, text=True, encoding="utf-8")
    for line in p.stdout.splitlines():
        if any(k in line for k in ("全量 T", "索引导航层", "库级断点", "工作集 W",
                                   "净省", "判定")):
            print("   " + line.strip())
    print()
    print("注：本任务是「给格式加一种行类型」，天然要读遍格式规范与全部实现分派点 ——")
    print("    工作集占全量的四分之三左右，落在断点以内，所以门控仍判 STORE；")
    print("    但净省只剩十几个百分点，已经贴近「建库不值」的边界。")
    print("    这道题因此属于技能的**不利区间**，拿它证明增益是不成立的。")


if __name__ == "__main__":
    main()
