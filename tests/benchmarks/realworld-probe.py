"""真实场景测试：在真实语料上把「读取源替换」完整跑一遍，量出真实数据。

与 locality-probe.py 的区别（都是为了防「作者挑有利场景」）：
  * 语料 = **真实交付包**的 13 个文件，不是合成的规模库
  * 全程走**真实 CLI**（init / add / event / index / read / plan / fetch）
  * 基线是**机械量**：语料全文 token 之和，不由作者挑选
  * 工作集给**两个口径**（保守=整文件，乐观=按结构切片命中），交出区间而不是一个数
  * 事件节点与门控（本轮新增）全链路真跑

工作集口径的两处披露：
  1. 键层由作者手写（模拟模型读完后的压存），因此**索引层大小有主观成分**。
     为了让它可被质疑，脚本额外做一次「体积无关性」验证：把文件内容翻倍，
     索引层必须**一字不涨**——这是 I 只随节点数涨的机械证据。
  2. 乐观口径的切片由**结构边界**机械切（markdown 标题 / python def），
     关键词命中即入选，不靠人工挑段。切片规则写在 pick() 里，可复核。
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import tiktoken

HERE = os.path.dirname(os.path.abspath(__file__))           # tests/benchmarks/
WS = os.path.dirname(HERE)                                  # tests/
PKG = os.path.dirname(WS)                                   # 仓库根（= 技能包根）
sys.path.insert(0, WS)
from pkg_boundary import corpus_files                   # noqa: E402

CTX = os.path.join(PKG, "scripts", "ctx.py")
TMP = os.path.join(tempfile.gettempdir(), "cl_rw_probe")
PY = sys.executable
DATE = "2026-10-16"

ENC = tiktoken.get_encoding("cl100k_base")

# 体积无关性判据的上界：E 行元数据（`bytes:` 位数 + `sha256:` 十六进制串的分词分组）
# 允许的 token 波动上限。它是**常数**、与正文体积无关；12 远超实测波动（本轮 3），
# 又远低于「正文进了视图」时的万级量级。不要改回「按位数差推算」——那是错的模型。
VOL_META_TOL = 12


def tk(text):
    return len(ENC.encode(text))


def sh(*args, stdin=None):
    p = subprocess.run([PY, CTX] + list(args), cwd=PKG, input=stdin,
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode not in (0,):
        raise SystemExit("命令失败 rc=%d：ctx.py %s\n%s" % (p.returncode, " ".join(args), p.stderr))
    return p.stdout


# --------------------------------------------------------------------------
# 真实语料：交付包的实际文件
# --------------------------------------------------------------------------

# 语料 = **交付包边界内**、且按 `plan` 同一张扩展名表能算进 T 的文本文件。
# 这里不再手抄一份文件名清单 —— 手抄的那份迟早和实际包漂移，
# 而分母漂移会让已发布的实测数据悄悄失效。口径由 `pkg_boundary.corpus_files`
# 定义（唯一真相，三个测量脚本共用）。
FILES = [rel for rel, _ in corpus_files(PKG)]

# 按真实职责分组（模拟模型读完后的压存：读过的块就压存）
GROUPS = [
    ("交付说明", "对外可读的入口文档", ["SKILL.md", "README.md", "CHANGELOG.md"]),
    ("存储格式与规则", "格式规范与拆分规则", ["references/store-format.md", "references/split-rules.md"]),
    ("记忆与生命周期", "作用域、老化、宿主适配", ["references/lifecycle.md",
                                                "references/host-memory-adapter.md"]),
    ("质量与降级", "覆盖矩阵、降级路径、压缩策略", ["references/compression-strategy.md",
                                                   "references/coverage-matrix.md",
                                                   "references/degradation.md"]),
    ("内核实现", "唯一写入者：解析、渲染、子命令", ["scripts/ctx.py"]),
    ("模板", "骨架与增量模板", ["templates/delta.txt", "templates/skeleton.ctx",
                                "templates/start-prompt.md"]),
]

# 键层：真实的约定（模拟模型从素材里抽出的可压缩结论）
KEYS = [
    ("存储形态", "格式与不变量", [
        ("键树挂事件节点，不是纯链表", None),
        ("E 行 = 摘要+指针+字节数+内容哈希，正文不进库", "钉住"),
        ("15 条结构不变量，坏文件拒载", None),
        ("行首标记只有 T / P / R / E 四种", None),
    ]),
    ("读取源替换", "用紧凑投影替代原文进窗口", [
        ("库级断点 = 1 - I/T，实测约 95%", None),
        ("块级断点 k > 1/(1-r)，读完再压口径", None),
        ("read 默认只回摘要，原文须显式 --fetch", "钉住"),
        ("read 严守只读：升格只依据写入侧显式信号", "钉住"),
    ]),
    ("工程纪律", "改这个包必须先过的关", [
        ("唯一写入者是 ctx.py，纯标准库", None),
        ("改交付包必须同步升版本 + 重打包 + 重跑验收", "钉住"),
        ("负向夹具每类坏法各配一条", None),
    ]),
]


def read_bytes(rel):
    with open(os.path.join(PKG, rel), "rb") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# 结构切片器（乐观口径用）：按真实结构边界切，不人工挑段
# --------------------------------------------------------------------------

def sections(rel):
    """把文件按结构边界切成 (标题, 文本) 列表。"""
    text = open(os.path.join(PKG, rel), encoding="utf-8").read()
    lines = text.split("\n")
    out = []
    if rel.endswith(".py"):
        cur, title = [], "模块头"
        for ln in lines:
            if ln.startswith(("def ", "class ")) and cur:
                out.append((title, "\n".join(cur)))
                cur, title = [ln], ln.split("(")[0]
            else:
                if ln.startswith(("def ", "class ")):
                    title = ln.split("(")[0]
                cur.append(ln)
        out.append((title, "\n".join(cur)))
    else:
        cur, title = [], "文首"
        for ln in lines:
            if ln.startswith("#") and cur:
                out.append((title, "\n".join(cur)))
                cur, title = [ln], ln.strip("# ").strip()
            elif ln.startswith("#"):
                title = ln.strip("# ").strip()
            cur.append(ln)
        out.append((title, "\n".join(cur)))
    return [(t, b) for t, b in out if b.strip()]


def pick(rel, keywords):
    """返回命中的切片与它们的 token 和。"""
    hits = []
    for title, body in sections(rel):
        blob = (title + "\n" + body).lower()
        if any(k.lower() in blob for k in keywords):
            hits.append((title, tk(body) if body else 0, len(body)))
    return hits


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    lib = os.path.join(TMP, "repo.ctx")

    # —— 1. 建库：真实语料 ——
    body = "\n".join("T %s\nP %s\nR %s%s\n" % (
        g[0], g[1],
        "；".join(os.path.basename(f) for f in g[2]),
        "")
        for g in GROUPS)
    sh("init", "-f", lib, "--date", DATE, "--top",
       "context-lattice 交付包 / 读取源替换的真实语料", "--stdin", stdin=body)

    # 键层：把真实约定写进键树（模型语义标注 → 脚本落盘）
    key_lines = []
    for t, p, rs in KEYS:
        key_lines.append("T %s\nP %s" % (t, p))
        for text, tag in rs:
            key_lines.append("R %s%s" % (text, "" if tag is None else " [%s]" % tag))
    sh("add", "-f", lib, "--stdin", stdin="\n".join(key_lines) + "\n")

    # 事件节点：读过的块就压存（本轮定下的规则）。整文件作为一个原子块。
    for t, p, files in GROUPS:
        for rel in files:
            sh("event", "-f", lib, "--topic", t, "--purpose", p,
               "--src", os.path.join(PKG, rel), "--summary", "原文·%s" % rel)

    print("=" * 74)
    print("一、库的形态（真实语料建库）")
    print("=" * 74)
    print(sh("stats", "-f", lib).rstrip())
    print()
    print("头部：%s" % open(lib, encoding="utf-8").read().split("\n")[0])
    print("库文件字节数 = %d" % os.path.getsize(lib))

    # —— 2. 索引层 ——
    sh("index", "--root", TMP, "--date", DATE, "--out", os.path.join(TMP, "index.txt"))
    idx = open(os.path.join(TMP, "index.txt"), encoding="utf-8").read()
    i_tok = tk(idx)
    print()
    print("=" * 74)
    print("二、索引层（read --level 1 的等价物）")
    print("=" * 74)
    print("索引 token = %d  字符 = %d" % (i_tok, len(idx)))

    # —— 3. 语料全量（机械基线）——
    total = 0
    per_file = {}
    for rel in FILES:
        n = tk(read_bytes(rel).decode("utf-8"))
        per_file[rel] = n
        total += n
    print("语料全量 token = %d（%d 个文件，机械量）" % (total, len(FILES)))

    # 技能自身开销
    self_cost = tk(open(os.path.join(PKG, "SKILL.md"), encoding="utf-8").read())
    print("技能自身 SKILL.md token = %d" % self_cost)

    # —— 4. 三个真实任务 ——
    TASKS = [
        ("T1 给 ctx.py 加一个子命令",
         ["scripts/ctx.py", "references/store-format.md"],
         {"scripts/ctx.py": ["def cmd_event", "def parse", "RE_EVENT", "def cmd_read",
                             "_check_summary", "def _load_atom"],
          "references/store-format.md": ["E 行", "事件节点", "不变量"]}),
        ("T2 改 SKILL.md 的 description",
         ["SKILL.md"],
         {"SKILL.md": ["description", "name:", "触发", "何时"]}),
        ("T3 新增一条拆分规则",
         ["references/split-rules.md"],
         {"references/split-rules.md": ["规则", "拆分"]}),
    ]

    print()
    print("=" * 74)
    print("三、三个真实任务的读取账")
    print("=" * 74)
    print("%-26s %9s %9s %8s %8s %8s" % ("任务", "W保守", "W乐观", "全量", "净省保守", "净省乐观"))
    print("-" * 74)
    rowdata = []
    for name, cons_files, picks in TASKS:
        w_cons = sum(per_file[f] for f in cons_files)
        w_opt = 0
        detail = []
        for rel, kws in picks.items():
            hits = pick(rel, kws)
            got = sum(h[1] for h in hits)
            if got == 0:
                raise SystemExit("切片器 0 命中，口径不可信：%s / %s" % (rel, kws))
            if got > per_file[rel]:
                raise SystemExit("切片超过整文件，口径不可信：%s %d > %d"
                                 % (rel, got, per_file[rel]))
            w_opt += got
            detail.append("%s: %d/%d 段命中(%d)" % (rel, len(hits),
                                                    len(sections(rel)), got))
        save_c = 1.0 - (i_tok + w_cons) / float(total)
        save_o = 1.0 - (i_tok + w_opt) / float(total)
        print("%-26s %9d %9d %8d %7.1f%% %7.1f%%"
              % (name, w_cons, w_opt, total, save_c * 100, save_o * 100))
        rowdata.append({"task": name, "w_cons": w_cons, "w_opt": w_opt,
                        "save_cons": save_c, "save_opt": save_o, "detail": detail})

    # —— 5. 门控判定（真实 CLI）——
    # 先把「交付包」摊到一个干净目录再交给 plan：plan 是通用工具，只按
    # .git/__pycache__ 之类通用规则排除，它不认识本仓库的 tests/ docs/。
    # 不摊平就会出现「同一份报告两个分母」（三、用 14 文件，四、用整仓）。
    print()
    print("=" * 74)
    print("四、门控判定 plan（脚本算，不让模型估）")
    print("=" * 74)
    corpus = os.path.join(TMP, "corpus")
    shutil.rmtree(corpus, ignore_errors=True)
    for rel in FILES:
        dst = os.path.join(corpus, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(PKG, rel), dst)
    print("（语料已摊平到临时目录，共 %d 个文件，与第二节同一分母）" % len(FILES))
    cons_all = ",".join(TASKS[0][1])
    print("--- 局部任务（T1 工作集）---")
    plan_local = sh("plan", "--root", corpus, "--work", cons_all)
    print(plan_local.rstrip())
    print()
    print("--- 反例：任务需要反复用到全部素材（每次的工作集都接近全量）---")
    print(sh("plan", "--root", corpus, "--work", ",".join(FILES)).rstrip())
    print()
    print("注：plan **没有** --reuse 参数 —— 新口径下不需要预估复用次数 k，")
    print("    规则退化为「读过的块就压存」，参数直接消失了。")

    # 导航层 token（plan 的 I）**从实际输出解析**，不许手抄。
    # 旧版这里是硬编码的一个常数，而同一次运行里 plan 明明打印了另一个值 ——
    # 机器可读摘要与人读输出自相矛盾，正是本项目反复处理的那一类错。
    nav_tok = None
    for line in plan_local.splitlines():
        if "索引导航层" in line and ":" in line:
            digits = re.findall(r"\d+", line.split(":")[-1].split("(")[0])
            if digits:
                nav_tok = int(digits[0])
            break
    print("导航层 I（从 plan 输出解析）= %s" % nav_tok)

    # —— 6. 事件节点全链路：索引只放摘要、取回字节一致 ——
    print()
    print("=" * 74)
    print("五、事件节点全链路（登记 → 索引只放摘要 → 取回校验）")
    print("=" * 74)
    lines = idx.splitlines()
    e_idx = [ln for ln in lines if " E " in ln or ln.strip().startswith("E ")]
    print("索引中的事件节点行数 = %d（应与登记的 14 个文件对应）" % len(e_idx))
    body_in_index = any("def cmd_event" in ln for ln in lines)
    print("索引里是否出现正文（必须 NO）= %s" % ("YES!" if body_in_index else "NO"))

    rel = "scripts/ctx.py"
    out_path = os.path.join(TMP, "got_ctx.py")
    print(sh("fetch", "-f", lib, "--summary", "原文·%s" % rel, "--out", out_path).rstrip())
    a = hashlib.sha256(read_bytes(rel)).hexdigest()
    b = hashlib.sha256(open(out_path, "rb").read()).hexdigest()
    print("取回与原件 sha256 一致 = %s" % ("YES" if a == b else "NO!"))
    print(sh("fetch", "-f", lib, "--summary", "原文·%s" % rel, "--check").rstrip())

    # —— 7. 体积无关性：正文翻倍，索引必须一字不涨 ——
    # 控制变量：两个源文件同目录、**文件名等长**、摘要相同。
    # 否则 src 指针本身的字符长度会混进来，测出来的是路径长度差而不是体积无关性。
    print()
    print("=" * 74)
    print("六、体积无关性验证：正文体积翻倍，视图必须一字不涨")
    print("=" * 74)
    with open(os.path.join(PKG, "scripts/ctx.py"), encoding="utf-8") as fh:
        content = fh.read()
    small = os.path.join(TMP, "twinA.py")
    big = os.path.join(TMP, "twinB.py")
    with open(small, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    with open(big, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content + "\n" + content)          # 正文翻倍，节点数不变
    views = []
    for tag, path in (("A", small), ("B", big)):
        lb = os.path.join(TMP, "vol%s.ctx" % tag)
        sh("init", "-f", lb, "--date", DATE, "--top", "体积无关性", "--stdin",
           stdin="T 内核\nP 实现\n")
        sh("event", "-f", lb, "--topic", "内核", "--purpose", "实现",
           "--src", path, "--summary", "内核原文")
        views.append(sh("read", "-f", lb))
    t1, t2 = tk(views[0]), tk(views[1])
    sz1, sz2 = os.path.getsize(big), os.path.getsize(small)
    delta = t1 - t2
    # 判据是「只有 E 行那一行不同 + 差值有界」，**不是**「差值 = bytes 位数差」。
    # 后一个模型是错的，本轮实测把它打掉了：两个孪生文件内容不同 → `sha256:` 十六进制串的
    # 分词分组不同（实测 `b4b97bfa…` 切 3 个 token、`21841ba5…` 切 6 个），差值**可正可负**，
    # 与 bytes 位数没有关系；用位数差当上界会得到假 NO。两条新判据都机械可核：
    #   ① 结构：两个视图**只有 1 行**不同，且那行是 E 行 —— 正文若进了视图，会是成千上万行不同；
    #   ② 上界：|差值| ≤ VOL_META_TOL，且该上界只由 E 行的元数据字段决定，与正文体积无关。
    lines_a, lines_b = views[0].splitlines(), views[1].splitlines()
    diff_rows = [i for i, (x, y) in enumerate(zip(lines_a, lines_b)) if x != y]
    same_shape = len(lines_a) == len(lines_b)
    only_e_line = (same_shape and len(diff_rows) == 1
                   and lines_a[diff_rows[0]].strip().startswith("E "))
    ok = only_e_line and abs(delta) <= VOL_META_TOL
    print("正文体积：%d 字节(翻倍) vs %d 字节 → 差 %.0f%%" % (sz1, sz2, 100.0 * (sz1 - sz2) / sz2))
    print("视图差异行数：%d / 共 %d 行（必须恰好 1 行，且是 E 行）" % (len(diff_rows), len(lines_a)))
    print("视图 token：%d vs %d → 差 %d（|差| 必须 ≤ %d）" % (t1, t2, delta, VOL_META_TOL))
    print("体积无关性成立 = %s" % ("YES" if ok else "NO!"))
    print("口径注：与**正文体积**无关（正文不进视图）；相关的只有节点数、摘要与指针的字符长度、")
    print("        以及 E 行元数据本身的分词 —— `bytes:` 位数与 `sha256:` 十六进制串的切分。")
    print("        两项都是**常数级**的，且不确定方向；正文若真进视图，差值会是数万 token。")

    # —— 汇总 ——
    summary = {
        "corpus_files": len(FILES),
        "total_token": total,
        "index_token": i_tok,
        "index_ratio": i_tok / float(total),
        "skill_self_token": self_cost,
        "tasks": rowdata,
        "event_nodes": len(FILES),
        "volume_invariance": ok,
        "volume_delta_token": delta,
        "volume_diff_rows": len(diff_rows),
        "nav_layer_token_plan": nav_tok,
        "nav_note": "index_token=库的索引层(49 节点，粗粒度)；plan 的 I=%s 是对语料目录"
                    "现算的细粒度导航层(按每个结构单元)。索引做多细是取舍，不是定值。"
                    % nav_tok,
    }
    with open(os.path.join(TMP, "RESULT-realworld.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print()
    print("RESULT -> %s" % os.path.join(TMP, "RESULT-realworld.json"))


if __name__ == "__main__":
    main()
