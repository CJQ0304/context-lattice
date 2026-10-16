#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收敛矩阵验收器（官方 `convergence-matrix-tool` 的等效实现）。

⚠️ 本文件**不是官方工具**。它按官方记录的 CLI 形态与硬卡项实现，目的是：
   ① 在没有官方工具的环境里先拿到等效验收证据；
   ② 把规格抽成标准 spec 文件，**官方工具一旦到位，用同一条命令跑同一个 spec 即可**，
      不需要改任何夹具。

用法（与官方记录一致的形态）：
    python convergence_matrix.py --spec <convergence_spec.json> --audit <判据源码.py>

官方记录会硬卡的六项，本实现逐条对齐：
    未分类 / 规则未声明 covers / 孤儿格（逃逸面）/ 规则重叠 / 格均规则数 > 1 / 规格与实现漂移
退出码：0 = 收敛；1 = 未收敛（附明细）；2 = 用法或规格错误。
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()


import argparse
import json
import os
import subprocess
import sys
import tempfile

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


class SpecError(Exception):
    pass


def load_spec(path):
    if not os.path.exists(path):
        raise SpecError("找不到规格文件：%s" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except Exception as exc:                                   # noqa: BLE001
        raise SpecError("规格文件不是合法 JSON：%s" % exc)
    for key in ("dimensions", "rules", "entry"):
        if key not in spec:
            raise SpecError("规格缺少必需字段：%s" % key)
    if not spec["rules"]:
        raise SpecError("规格里没有任何规则")
    return spec


def run_case(audit_path, argv, fixture_path, cwd):
    p = subprocess.run([sys.executable, audit_path] + argv, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=cwd)
    return p.returncode, p.stdout, p.stderr


# 「解释器连源码都没打开」的特征串。出现它说明问题在验收器自身（路径/调用姿势），
# 不是被判据拦下的夹具 —— 必须报成 SPEC ERROR，绝不能混进「规格与实现漂移」。
_LAUNCH_FAIL = ("can't open file", "No such file or directory", "Permission denied",
                "Errno 2", "Errno 13")


def main():
    ap = argparse.ArgumentParser(prog="convergence_matrix.py",
                                 description="收敛矩阵验收器（等效实现，非官方工具）")
    ap.add_argument("--spec", required=True, help="规格文件（convergence_spec.json）")
    ap.add_argument("--audit", required=True, help="判据源码路径（要验收的检查器）")
    ap.add_argument("--verbose", action="store_true", help="打印每个夹具的实跑结果")
    args = ap.parse_args()

    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print("SPEC ERROR %s" % exc)
        return EXIT_USAGE
    # 判据源码路径必须先绝对化：下面会把 cwd 切到临时目录造夹具，
    # 相对路径到那时就指错地方了（表现为解释器 exit 2，极易被误读成「夹具全部漂移」）。
    audit_abs = os.path.abspath(args.audit)
    if not os.path.exists(audit_abs):
        print("SPEC ERROR 找不到判据源码：%s" % args.audit)
        return EXIT_USAGE
    args.audit = audit_abs

    row_types = list(spec["dimensions"]["row_type"])
    bad_kinds = list(spec["dimensions"]["bad_kind"])
    entry = list(spec["entry"])

    problems = []

    # ---- 硬卡 1：规则未声明 covers ----
    for r in spec["rules"]:
        if not r.get("covers"):
            problems.append("规则 %s 未声明 covers（无法证明它管哪些格）" % r["id"])
        if not r.get("cases"):
            problems.append("规则 %s 没有任何夹具（无法证明它真拦得住）" % r["id"])

    # ---- 硬卡 2/3：覆盖非法维度取值 / 未分类 / 孤儿格 ----
    cell_rules = {}
    for r in spec["rules"]:
        for cell in r.get("covers", []):
            if len(cell) != 2:
                problems.append("规则 %s 的 covers 项不是 [行类型, 坏法]：%r" % (r["id"], cell))
                continue
            row, kind = cell
            if row not in row_types:
                problems.append("规则 %s 声明了未知行类型：%s" % (r["id"], row))
            if kind not in bad_kinds:
                problems.append("规则 %s 声明了未知坏法：%s" % (r["id"], kind))
            cell_rules.setdefault((row, kind), []).append(r["id"])

    missing_kinds = [k for k in bad_kinds if not any(kk == k for _r, kk in cell_rules)]
    missing_rows = [r for r in row_types if not any(rr == r for rr, _k in cell_rules)]
    if missing_kinds:
        problems.append("未分类（有坏法没有任何规则接管）：%s" % missing_kinds)
    if missing_rows:
        problems.append("孤儿格（有行类型没有任何规则接管）：%s" % missing_rows)

    # ---- 硬卡 4/5：规则重叠 / 格均规则数 ----
    overlaps = {c: v for c, v in cell_rules.items() if len(v) > 1}
    if overlaps:
        problems.append("规则重叠（同一格被多条规则覆盖）：%s"
                        % ["%s×%s→%s" % (c[0], c[1], v) for c, v in list(overlaps.items())[:5]])
    per_cell = sum(len(v) for v in cell_rules.values()) / float(len(cell_rules)) if cell_rules else 0.0
    if per_cell > 1.0:
        problems.append("格均规则数 %.2f > 1（一个格配多条规则 = 无法判定谁负责）" % per_cell)
    if len(spec["rules"]) > len(cell_rules):
        problems.append("规则数 %d 多于格数 %d（多出来的一定是补丁，不是在补通用规则）"
                        % (len(spec["rules"]), len(cell_rules)))

    # ---- 硬卡 6：规格与实现漂移（逐夹具实跑判据源码）----
    # fail fast：矩阵结构本身就不成立时，跑夹具没有意义（也省掉几百次进程启动）
    drift = []
    launch_fail = []
    case_total = 0
    case_pass = 0
    if not problems:
        tmp = os.path.join(tempfile.gettempdir(), "cl_convergence")
        os.makedirs(tmp, exist_ok=True)
        for r in spec["rules"]:
            for i, case in enumerate(r.get("cases", [])):
                case_total += 1
                fx = os.path.join(tmp, "%s_%d.ctx" % (r["id"], i))
                if "raw_hex" in case:
                    with open(fx, "wb") as fh:
                        fh.write(bytes.fromhex(case["raw_hex"]))
                else:
                    with open(fx, "w", encoding="utf-8", newline="") as fh:
                        fh.write(case["content"])
                argv = [a.replace("<fixture>", fx) for a in entry]
                rc, out, err = run_case(args.audit, argv, fx, tmp)
                ok = True
                detail = ""
                if rc != spec["expect_exit_on_bad"]:
                    ok, detail = False, "退出码 %d（期望 %d）" % (rc, spec["expect_exit_on_bad"])
                elif case["expect_contains"] not in err:
                    ok, detail = False, "错误信息未含 %r（实测 %r）" % (case["expect_contains"], err.strip()[:70])
                elif "Traceback" in err:
                    ok, detail = False, "裸露 traceback"
                if ok:
                    case_pass += 1
                else:
                    drift.append("%s 夹具#%d：%s" % (r["id"], i, detail))
                    if any(sig in err for sig in _LAUNCH_FAIL):
                        launch_fail.append("%s 夹具#%d" % (r["id"], i))
                if args.verbose:
                    print("  [%s] %s#%d %s" % ("ok" if ok else "RED", r["id"], i, detail))
        # 全部夹具都「连源码都没打开」→ 是验收器自己调错了，不是判据漂移。
        # 把这两件事分开报，否则修的人会去改夹具，越改越远。
        if launch_fail and len(launch_fail) == case_total:
            print("SPEC ERROR 判据源码根本无法启动（%d/%d 个夹具均如此）：%s"
                  % (len(launch_fail), case_total, args.audit))
            print("           很可能是路径问题，与夹具/规格无关；请核对 --audit 指向的 .py 是否存在且可读。")
            return EXIT_USAGE
        if drift:
            problems.append("规格与实现漂移（%d/%d 个夹具未按规格失败）：%s"
                            % (len(drift), case_total, drift[:5]))

    # ---- 矩阵打印 ----
    print("=" * 78)
    print("收敛矩阵（等效实现，非官方工具）  规格 v%s" % spec.get("spec_version"))
    print("=" * 78)
    print("  列号对应下列坏法：")
    for i, k in enumerate(bad_kinds, start=1):
        print("    %2d = %s" % (i, k))
    print()
    print("%-9s" % "行类型＼坏法" + "".join("%3d" % i for i in range(1, len(bad_kinds) + 1)))
    for row in row_types:
        line = "%-12s" % row
        for k in bad_kinds:
            line += "%3s" % ("■" if (row, k) in cell_rules else "·")
        print(line)
    print("  ■ = 有规则接管   · = 空格（不允许存在）")
    print()
    total_cases = sum(len(r.get("cases", [])) for r in spec["rules"])
    if case_total:
        case_note = "%d 个（实跑通过 %d）" % (case_total, case_pass)
    else:
        case_note = "%d 个（未执行：矩阵结构性问题优先，fail fast）" % total_cases
    print("规则 %d 条 / 覆盖格 %d 格 / 夹具 %s" % (len(spec["rules"]), len(cell_rules), case_note))
    print("格均规则数 %.2f  |  每规则均摊格数 %.2f  |  行类型 %d 坏法 %d"
          % (per_cell,
             sum(len(r["covers"]) for r in spec["rules"]) / float(len(spec["rules"])),
             len(row_types), len(bad_kinds)))

    if problems:
        print("\n未收敛：")
        for p in problems:
            print("  ✗ %s" % p)
        print("\nRESULT: FAIL   (EXIT 1)")
        return EXIT_FAIL
    print("\n未分类 0 · 孤儿格 0 · 规则重叠 0 · 未声明 covers 0 · 规格与实现漂移 0")
    print("RESULT: PASS   (EXIT 0)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
