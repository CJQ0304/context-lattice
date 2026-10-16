"""负向注入：把 _kind() 调用点退回旧的「非 E 即 R」写法，确认新断言会红。

用法（在仓库根）：python tests/injection/inject-kind.py
退出码：0 = 4 处断言全部被触发（断言有效）/ 1 = 断言是假证据。
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

raw = open(T.CTX, encoding="utf-8").read()
old = "_kind(it) =="
n = raw.count(old)
assert n >= 6, "调用点应不少于 6 处，实测 %d" % n
injected = raw.replace(old, '(it.get("kind") or "R") ==')

tmp = os.path.join(tempfile.gettempdir(), "_ctx_kind_injected.py")
open(tmp, "w", encoding="utf-8", newline="\n").write(injected)

T.CTX = tmp
shutil.rmtree(T.WORK, ignore_errors=True)
os.makedirs(T.WORK, exist_ok=True)
T.t_event_nodes()

hit = [f for f in T.FAILURES if "遇到未知 kind" in f]
print("=" * 62)
print("注入 %d 处调用点（退回「非 E 即 R」）" % n)
print("断言 %d · 失败 %d · 其中新增断言命中 %d" % (T.STATS["asserts"], T.STATS["failed"], len(hit)))
for f in hit:
    print("  命中 %s" % f[:96])
print("=" * 62)
print("注入是否被捕获 = %s" % ("YES（断言有效）" if len(hit) == 4 else "NO！（断言是假证据）"))

os.remove(tmp)
sys.exit(0 if len(hit) == 4 else 1)
