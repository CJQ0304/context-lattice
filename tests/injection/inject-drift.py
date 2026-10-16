"""负向注入：把 STALE-SRC 的 drift 分支短路掉，确认新断言会变红。

如果注入后断言仍然全绿，说明那个断言测的不是这段代码 —— 那就是假证据。

用法（在仓库根）：python tests/injection/inject-drift.py
退出码：0 = 注入被捕获（断言有效）/ 1 = 断言是假证据。
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

CTX = T.CTX
raw = open(CTX, encoding="utf-8").read()
old = '            if src_state == "drift":\n                n_stale += 1'
assert raw.count(old) == 1, "锚点必须唯一，实测出现 %d 次" % raw.count(old)
injected = raw.replace(old, '            if src_state == "drift" and False:\n                n_stale += 1')

tmp = os.path.join(tempfile.gettempdir(), "_ctx_drift_injected.py")
open(tmp, "w", encoding="utf-8", newline="\n").write(injected)

T.CTX = tmp
shutil.rmtree(T.WORK, ignore_errors=True)
os.makedirs(T.WORK, exist_ok=True)
T.t_event_nodes()

print("=" * 62)
print("注入后：断言 %d · 失败 %d" % (T.STATS["asserts"], T.STATS["failed"]))
for f in T.FAILURES:
    print("  命中 %s" % f)
print("=" * 62)
hit = any("STALE-SRC" in f for f in T.FAILURES)
print("注入是否被断言捕获 = %s" % ("YES（断言有效）" if hit else "NO！（断言是假证据）"))

os.remove(tmp)
sys.exit(0 if hit else 1)
