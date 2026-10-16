# -*- coding: utf-8 -*-
"""把 tests/ 下可跑件的 stdout/stderr 固定到 UTF-8。

**为什么需要**：本项目所有可读输出都是中文。Windows 控制台下 Python 走 PEP 528 的
UTF-8 通道本来没问题，但只要输出被重定向或走管道，编码就退回系统本地编码
（英文版 Windows = cp1252），中文一打印就抛 UnicodeEncodeError ——
CI 的 Windows runner 正是这种情况。

**为什么不在这里再写一遍实现**：与 `scripts/ctx.py` 里的 `_ensure_utf8_console()`
是同一件事。ctx.py 是交付包内唯一写入者、必须自包含，不能反向依赖 tests/，
所以实现留在它那里，这里只做转发 —— 全项目只有一处真相。
副作用也正好是想要的：**如果谁把 ctx.py 里的兜底删掉，整个测试套件自己就会先崩**，
而不是静默退化成「没测到」。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def ensure_utf8():
    """转发到 scripts/ctx.py 的实现（唯一真相）。"""
    import ctx
    ctx._ensure_utf8_console()
