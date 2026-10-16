"""负向注入：把「套件清单」弄坏，确认验收器的两条自检断言会红。

两类坏法各一条（对应缺陷账第 6 处）：
  ① 存在**未登记**进 `SUITES` 的套件函数（曾经真的发生过：第 14 套件漏登记，
     摘要行报「套件 13」而明细表 14 行 —— 数字骗人，但没有一条断言会红）
  ② 套件函数**重号**（清单里同一个函数出现两次）

用法（在仓库根）：python tests/injection/inject-suite-registry.py
退出码：0 = 两类注入都被捕获（自检断言有效）/ 1 = 假证据。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))           # tests/injection/
TESTS = os.path.dirname(HERE)                               # tests/
sys.path.insert(0, TESTS)
import test_all as T                                        # noqa: E402

os.makedirs(T.WORK, exist_ok=True)


def run(label):
    T.FAILURES[:] = []
    T.STATS["failed"] = 0
    T.STATS["asserts"] = 0
    T.t_contract()
    hits = [f for f in T.FAILURES if "验收器自检" in f]
    print("--- %s ---" % label)
    print("  断言 %d · 失败 %d" % (T.STATS["asserts"], T.STATS["failed"]))
    for h in hits:
        print("  命中 %s" % h[:130])
    return hits


# ① 孤儿套件：函数存在于模块里，却不在 SUITES 清单中
T.t_orphan_probe = lambda: None
hit1 = run("① 存在未登记进 SUITES 的套件函数")
del T.t_orphan_probe

# ② 重号：把清单第一项复制一份追加到末尾
T.SUITES.append(T.SUITES[0])
hit2 = run("② 套件函数重号")
T.SUITES.pop()

print("=" * 62)
ok1 = any("未登记进 SUITES" in h for h in hit1)
ok2 = any("重号" in h for h in hit2)
print("注入是否被捕获 = %s（%d/2）" % ("YES（自检断言有效）" if (ok1 and ok2) else "NO！（假证据）",
                                     int(ok1) + int(ok2)))
print("=" * 62)
sys.exit(0 if (ok1 and ok2) else 1)
