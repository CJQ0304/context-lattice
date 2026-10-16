"""负向注入：把 ctx.py 的「标准流固定 UTF-8」兜底去掉，确认套件 15 会红。

对应缺陷：非 UTF-8 本地编码（英文版 Windows = cp1252）下，只要标准流接了管道或
被重定向，输出侧中文一打印就 UnicodeEncodeError、输入侧 --stdin 把 UTF-8 字节
按本地编码解（cp1252 报错 / cp936 静默乱码落库）。修复见 `_ensure_utf8_console()`。

两类坏法各一条：
  ① 把 `_ensure_utf8_console()` 变成空操作（等于回到修复前）
  ② 只修输出、不修输入（三个流里漏掉 stdin）—— 输入侧是更隐蔽的那半

判据：注入后套件 15 必须出现失败；未注入时必须 0 失败（正控）。
**两臂都跑**，否则无法区分「断言真的有效」与「套件本来就永远红」。

用法（在仓库根）：python tests/injection/inject-console-encoding.py
退出码：0 = 注入被捕获且正控干净 / 1 = 假证据。
"""

# 控制台输出固定 UTF-8（实现唯一在 scripts/ctx.py，本行只是转发；见 tests/_console.py）
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _console import ensure_utf8 as _ensure_utf8
_ensure_utf8()

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))           # tests/injection/
TESTS = os.path.dirname(HERE)                               # tests/
sys.path.insert(0, TESTS)
import test_all as T                                        # noqa: E402

MAIN_GUARD = 'if __name__ == "__main__":'


def patched_ctx(mode, target):
    """把一份「坏掉」的 ctx.py 写到 target，返回是否成功。

    注入手法刻意做小：只在 `__main__` 守卫**之前**插一行重新绑定。
    这样不改动 `_ensure_utf8_console` 本身的结构 —— 注入的是「它没被调用」这件事，
    而不是把被测代码改得面目全非（后者会让人分不清红是注入造成的还是改坏的）。
    """
    with open(T.CTX, "r", encoding="utf-8") as fh:
        src = fh.read()
    if MAIN_GUARD not in src or "_ensure_utf8_console" not in src:
        return False
    if mode == "noop":
        inject = "_ensure_utf8_console = lambda: None  # 注入①：整体失效\n"
    elif mode == "stdin_only":
        # 注入②：保留 stdout/stderr 的修复，只让 stdin 回到原来的样子
        inject = ("_ensure_utf8_console_orig = _ensure_utf8_console\n"
                  "def _ensure_utf8_console():  # 注入②：漏掉输入侧\n"
                  "    _s = (sys.stdout, sys.stderr)\n"
                  "    for _x in _s:\n"
                  "        _x.reconfigure(encoding='utf-8', errors='backslashreplace')\n")
    else:
        raise ValueError(mode)
    src = src.replace(MAIN_GUARD, inject + MAIN_GUARD, 1)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src)
    return True


def run_suite(ctx_path):
    """跑套件 15，返回失败消息列表。"""
    T.FAILURES[:] = []
    T.STATS["asserts"] = 0
    T.STATS["failed"] = 0
    T.CTX = ctx_path
    os.makedirs(T.WORK, exist_ok=True)
    T.t_console_streams()
    return list(T.FAILURES), T.STATS["asserts"]


def main():
    real_ctx = T.CTX
    sandbox = tempfile.mkdtemp(prefix="cl-inject-console-")
    try:
        # 正控：没注入时必须干净。少了这一臂，「套件永远红」会被误读成「断言有效」。
        fails, n = run_suite(real_ctx)
        print("--- 正控：未注入 ---")
        print("  断言 %d · 失败 %d" % (n, len(fails)))
        for f in fails:
            print("  意外失败 %s" % f[:120])
        control_ok = not fails

        hits = {}
        for idx, mode in ((1, "noop"), (2, "stdin_only")):
            target = os.path.join(sandbox, "ctx_%d.py" % idx)
            if not patched_ctx(mode, target):
                print("注入 %d 失败：没找到锚点，未做任何改动（注入不生效 ≠ 断言有效）" % idx)
                return 1
            label = "① 整体失效" if mode == "noop" else "② 只修输出、漏掉 stdin"
            fails, n = run_suite(target)
            print("--- 注入 %s ---" % label)
            print("  断言 %d · 失败 %d" % (n, len(fails)))
            for f in fails[:3]:
                print("  命中 %s" % f[:130])
            hits[label] = fails
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        T.CTX = real_ctx

    print("=" * 62)
    ok1 = len(hits["① 整体失效"]) > 0
    ok2 = len(hits["② 只修输出、漏掉 stdin"]) > 0
    print("正控（未注入必须 0 失败）= %s"
          % ("干净" if control_ok else "脏！先怀疑断言本身"))
    print("注入是否被捕获 = %s（%d/2）"
          % ("YES（套件 15 有效）" if (ok1 and ok2) else "NO！（假证据）",
             int(ok1) + int(ok2)))
    print("=" * 62)
    return 0 if (control_ok and ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
