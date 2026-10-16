# -*- coding: utf-8 -*-
"""事件节点冒烟：写入 → 视图 → 取回 → 漂移 → 老化保护 → 门控。"""
import os, shutil, subprocess, sys

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根
CTX = os.path.join(ROOT, "scripts", "ctx.py")
T = os.path.join(os.environ.get("TEMP", "/tmp"), "ev_smoke")


def run(*a, expect=0, stdin=None):
    p = subprocess.run([PY, CTX] + list(a), input=stdin, capture_output=True, text=True)
    flag = "ok " if p.returncode == expect else "!! "
    print("%sEXIT=%d  ctx.py %s" % (flag, p.returncode, " ".join(a)))
    for line in (p.stdout or "").splitlines():
        print("      | %s" % line)
    for line in (p.stderr or "").splitlines():
        print("      ! %s" % line)
    if p.returncode != expect:
        sys.exit("退出码不符：期望 %d 实测 %d" % (expect, p.returncode))
    return p


shutil.rmtree(T, ignore_errors=True)
os.makedirs(T)
src = os.path.join(T, "sample.py")
with open(src, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("def f():\n    return 42\n")

print("=" * 62)
print("① 建库 + 登记事件节点（库外指针）")
run("init", "-f", os.path.join(T, "s.ctx"), "--date", "2026-10-16", "--top", "事件节点冒烟",
    "--stdin", stdin="T 引擎重构\nP 拆出内核\nR 保留单写入者不变 [约束]\n")
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", src, "--summary", "旧版内核实现")
print("--- 库文件 ---")
print(open(os.path.join(T, "s.ctx"), encoding="utf-8").read())

print("=" * 62)
print("② 视图只给摘要与指针（原文不进窗口）")
run("read", "-f", os.path.join(T, "s.ctx"))
run("validate", "-f", os.path.join(T, "s.ctx"))
run("tree", "-f", os.path.join(T, "s.ctx"))
run("stats", "-f", os.path.join(T, "s.ctx"))

print("=" * 62)
print("③ 显式取回原文 + 字节级一致")
run("read", "-f", os.path.join(T, "s.ctx"), "--fetch")
got = os.path.join(T, "got.py")
run("fetch", "-f", os.path.join(T, "s.ctx"), "--summary", "旧版内核实现", "--out", got)
print("      cmp      = %s" % ("IDENTICAL" if open(src, "rb").read() == open(got, "rb").read() else "DIFFER!"))
run("fetch", "-f", os.path.join(T, "s.ctx"), "--check")

print("=" * 62)
print("④ 漂移检测：改了原文之后必须报出来且退出码非零")
with open(src, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("def f():\n    return 43\n")
run("fetch", "-f", os.path.join(T, "s.ctx"), "--check", expect=1)
run("validate", "-f", os.path.join(T, "s.ctx"))

print("=" * 62)
print("⑤ 幂等：同一事件节点重复登记不得产生新版本")
# 第 ④ 步改过源文件，所以这里第一次登记会把指纹同步到当前内容（这是正确行为：
# 指针跟着现实走，且 NOTE 里报出来了），第二次起必须完全无变化。
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", src, "--summary", "旧版内核实现")
before = open(os.path.join(T, "s.ctx"), encoding="utf-8").read()
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", src, "--summary", "旧版内核实现")
after = open(os.path.join(T, "s.ctx"), encoding="utf-8").read()
print("      重复登记后库文件字节未变 = %s" % ("YES" if before == after else "NO!"))
if before != after:
    sys.exit("幂等被破坏：重复登记改动了库文件")

print("=" * 62)
print("⑥ 老化折叠不得动事件节点")
run("prune", "-f", os.path.join(T, "s.ctx"), "--aging", "--days", "0", "--today", "2026-12-31", "--force")
print("--- 折叠后库文件 ---")
print(open(os.path.join(T, "s.ctx"), encoding="utf-8").read())

print("=" * 62)
print("⑦ --embed：正文复制进库，库自包含")
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", src, "--summary", "内嵌版内核", "--embed")
run("fetch", "-f", os.path.join(T, "s.ctx"), "--summary", "内嵌版内核", "--check")
print("      atoms 目录 = %s" % sorted(os.listdir(os.path.join(T, "s.ctx.atoms"))))

print("=" * 62)
print("⑧ 负向：摘要含竖线 / src 不存在 / 仅指针节点在源文件消失后必须报缺失")
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", src, "--summary", "带|竖线", expect=1)
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", os.path.join(T, "不存在.py"), "--summary", "缺源", expect=1)

solo = os.path.join(T, "solo.txt")
with open(solo, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("only-pointer\n")
run("event", "-f", os.path.join(T, "s.ctx"), "--topic", "引擎重构", "--purpose", "拆出内核",
    "--src", solo, "--summary", "仅指针节点")
os.remove(solo)
os.remove(src)
print("-- 源文件已全部删除：仅指针节点必须 MISS（退出码 1），内嵌节点必须仍然通过 --")
run("fetch", "-f", os.path.join(T, "s.ctx"), "--summary", "仅指针节点", "--check", expect=1)
run("fetch", "-f", os.path.join(T, "s.ctx"), "--summary", "内嵌版内核", "--check")

print("=" * 62)
print("⑨ 门控 plan")
run("plan", "--break-even")
run("plan", "--root", ROOT,
    "--work", "scripts/ctx.py,references/store-format.md")

print("=" * 62)
print("冒烟全部通过")
