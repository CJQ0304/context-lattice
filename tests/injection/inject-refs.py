"""负向注入：在交付包副本里插一条悬空引用，确认「交付包自洽」套件会红。

用法（在仓库根）：python tests/injection/inject-refs.py
退出码：0 = 注入被捕获（检查器有效）/ 1 = 假证据。
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))           # tests/injection/
TESTS = os.path.dirname(HERE)                               # tests/
sys.path.insert(0, TESTS)
import test_all as T                                        # noqa: E402

copy = os.path.join(tempfile.gettempdir(), "_pkg_copy")
shutil.rmtree(copy, ignore_errors=True)
shutil.copytree(T.PKG, copy,
                ignore=shutil.ignore_patterns(".git", "__pycache__"))
target = os.path.join(copy, "references", "split-rules.md")
text = open(target, encoding="utf-8").read()
open(target, "w", encoding="utf-8", newline="\n").write(
    text + "\n详见 references/missing-file.md 与 templates/no-such.txt 的说明。\n")

T.PKG = copy
shutil.rmtree(T.WORK, ignore_errors=True)
os.makedirs(T.WORK, exist_ok=True)
T.t_refs()

hit = [f for f in T.FAILURES if "悬空引用" in f]
print("=" * 62)
print("断言 %d · 失败 %d" % (T.STATS["asserts"], T.STATS["failed"]))
for f in hit:
    print("  命中 %s" % f[:150])
print("注入是否被捕获 = %s" % ("YES（检查器有效）" if hit and "missing-file.md" in hit[0]
                              and "no-such.txt" in hit[0] else "NO！（假证据）"))
print("=" * 62)

shutil.rmtree(copy, ignore_errors=True)
sys.exit(0 if hit else 1)
