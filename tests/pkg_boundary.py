#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能包的边界：哪些路径会随包分发，哪些只在仓库里存在。

**这里是唯一真相。** 打包器（`pack_and_verify.py`）与悬空引用检查器
（`test_all.py` 套件 14）都从这里取边界，不许各写一份 —— 两份口径迟早会漂移，
而漂移的表现是「检查器说 PASS 但包里有问题」或反过来「包没问题检查器喊红」。

边界为什么必须存在（而不是「包 = 仓库根」）：
  1. **平台限制**：技能包顶层只允许 references / scripts / templates / examples，
     目录深度最多两级。`tests/` 里有三级路径，塞进去会被平台拒收。
  2. **检查器口径**：`tests/` 与 `docs/` 里**故意**存在长得像引用、实际不存在的路径串
     （正控探针 `references/definitely-missing.md`、坏包夹具条目名、负向注入用路径）。
     把它们纳入检查范围只会得到「设计如此」被误报成缺陷的假红。
"""

import os

# 只在仓库里存在的目录（不进技能包，也不进悬空引用检查范围）
REPO_ONLY_DIRS = frozenset({
    ".git", ".github", ".gitlab", "tests", "docs", "__pycache__",
    ".venv", "venv", "dist", "build", "node_modules", ".idea", ".vscode",
})

# 只在仓库里存在的文件（同上）
REPO_ONLY_FILES = frozenset({
    ".gitignore", ".gitattributes", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md",
})


def is_repo_only_dir(name):
    """目录名是否属于「只在仓库里」。"""
    return name in REPO_ONLY_DIRS


def is_repo_only_file(name):
    """文件名是否属于「只在仓库里」；`.pyc` 一律排除。"""
    return name in REPO_ONLY_FILES or name.endswith(".pyc")


def package_files(root):
    """按交付包边界遍历 root，返回 [(相对路径, 绝对路径), ...]，按相对路径排序。

    用途：打包器、语料测量脚本（`realworld-probe` / `locality-probe` / `rw-cost`）
    都要回答「这个包有多少素材」（T）这个问题。分母必须一致，否则同一份语料
    换一次目录布局就换一个 T，实测数据立刻不可复现。
    """
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not is_repo_only_dir(d))
        for fn in sorted(files):
            if is_repo_only_file(fn):
                continue
            full = os.path.join(dirpath, fn)
            out.append((os.path.relpath(full, root).replace(os.sep, "/"), full))
    return sorted(out)
