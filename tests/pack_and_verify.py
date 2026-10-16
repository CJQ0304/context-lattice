#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 + 平台视角模拟解析（五层）+ 负向样本证红。

运行（需要 PyYAML）：
    python tests/pack_and_verify.py

输出位置：环境变量 `CTX_DIST` 指定的目录；未设置则用系统临时目录下的
`context-lattice-dist/`。产物为 `context-lattice-<version>.zip`。

**技能包不含开发件**：`tests/`、`docs/`、`.git*`、`CONTRIBUTING.md` 只在仓库里存在，
不进 zip（平台只支持两级目录，顶层白名单为 references/scripts/templates/examples）。

五层：
  ① 落盘：把 zip 逐条目真实写到磁盘再回读比对字节
  ② 路径：.. / 绝对路径 / 盘符 / 目录深度 / 顶层形态
  ③ 编码：非 ASCII 条目必须置 UTF-8 标志位（否则平台按 cp437 解出乱码名）
  ④ 元数据：真 YAML 解析器读 frontmatter，校验必填字段 / 版本三处一致 / 长度约束
  ⑤ 内容：逐个读正文，可解码、非空、有标题结构

负向样本：每层都配一个「该失败」的夹具，实跑必须看到它变红。
"""

import hashlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))          # tests/
PKG = os.path.dirname(_HERE)                                # 仓库根（= 技能包根）
DIST = os.environ.get("CTX_DIST") or os.path.join(tempfile.gettempdir(),
                                                  "context-lattice-dist")

sys.path.insert(0, _HERE)
from pkg_boundary import package_files                    # noqa: E402

# 唯一真相在 tests/pkg_boundary.py，与悬空引用检查器共用
pkg_files = package_files

_skill_text = open(os.path.join(PKG, "SKILL.md"), encoding="utf-8").read()
_VERSION = re.search(r"^version:\s*(\S+)", _skill_text, re.M).group(1)
ZIP_NAME = "context-lattice-%s.zip" % _VERSION
FIXED_TIME = (2026, 10, 15, 0, 0, 0)

STATS = {"checks": 0, "failed": 0}
FAILS = []


def check(cond, msg):
    STATS["checks"] += 1
    if not cond:
        STATS["failed"] += 1
        FAILS.append(msg)
    return bool(cond)


def build_zip(src_dir, out_zip):
    """根布局打包：固定时间戳、名字排序稳定、非 ASCII 名自动置 UTF-8 标志位。"""
    os.makedirs(os.path.dirname(out_zip), exist_ok=True)
    entries = pkg_files(src_dir)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, full in entries:
            zi = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(full, "rb") as fh:
                zf.writestr(zi, fh.read())
    return out_zip


# ---------------------------------------------------------------------------
# 五层检查
# ---------------------------------------------------------------------------

def layer1_extract(zip_path):
    """① 落盘：逐条目真实写盘并回读比对字节。"""
    problems = []
    tmp = os.path.join(tempfile.gettempdir(), "cl_pack_extract")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = os.path.join(tmp, info.filename.replace("/", os.sep))
            try:
                data = zf.read(info)
            except Exception as exc:                          # noqa: BLE001
                problems.append("条目 %s 无法读取：%s" % (info.filename, exc))
                continue
            os.makedirs(os.path.dirname(target) or tmp, exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
            with open(target, "rb") as fh:
                if fh.read() != data:
                    problems.append("条目 %s 回读字节不一致" % info.filename)
    return problems


def layer2_paths(zip_path):
    """② 路径：穿越 / 绝对路径 / 盘符 / 深度 / 顶层形态。"""
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    for n in names:
        if ".." in n.split("/"):
            problems.append("条目名含 `..` 穿越：%s" % n)
        if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
            problems.append("条目名为绝对路径/盘符：%s" % n)
        if n.count("/") > 1:
            problems.append("目录深度超过 1 级（平台只支持两级目录）：%s" % n)
    if "SKILL.md" not in names:
        problems.append("顶层缺少 SKILL.md（包根必须直接放 SKILL.md）")
    tops = {n.split("/")[0] for n in names if "/" in n}
    allowed_tops = {"references", "scripts", "templates", "examples"}
    for t in tops:
        if t not in allowed_tops:
            problems.append("出现未预期的顶层目录：%s" % t)
    return problems, names


def layer3_encoding(zip_path):
    """③ 编码：非 ASCII 条目名必须带 UTF-8 标志位。"""
    problems = []
    non_ascii = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            try:
                info.filename.encode("ascii")
                continue
            except UnicodeEncodeError:
                pass
            non_ascii += 1
            if not (info.flag_bits & 0x800):
                problems.append("非 ASCII 条目名未置 UTF-8 标志位（平台会解成乱码）：%s" % info.filename)
    return problems, non_ascii


REQUIRED = ("description", "description_zh", "description_en", "version", "author")
ALLOWED_CATEGORIES = {"coding", "finance", "office", "content", "writing", "productivity"}


def layer4_metadata(src_dir):
    """④ 元数据：真 YAML 解析器读 frontmatter。"""
    problems = []
    try:
        import yaml
    except ImportError:
        return ["未安装 PyYAML，本层未执行"], None

    skill = os.path.join(src_dir, "SKILL.md")
    with open(skill, encoding="utf-8") as fh:
        text = fh.read()
    if not text.startswith("---\n"):
        return ["SKILL.md 未以 `---` 开头"], None
    end = text.find("\n---\n", 3)
    if end < 0:
        return ["frontmatter `---` 未成对闭合"], None
    raw = text[4:end + 1]
    try:
        data = yaml.safe_load(raw)
    except Exception as exc:                                  # noqa: BLE001
        return ["frontmatter 不是合法 YAML：%s" % exc], None
    if not isinstance(data, dict):
        return ["frontmatter 顶层不是映射"], None

    for f in REQUIRED:
        if f not in data or data[f] in (None, ""):
            problems.append("必填字段缺失或为空：%s" % f)
    if not re.match(r"^\d+\.\d+\.\d+$", str(data.get("version", ""))):
        problems.append("version 不是严格 x.y.z：%r" % data.get("version"))
    if data.get("name") != os.path.basename(src_dir):
        problems.append("name(%r) 与目录名(%r) 不一致" % (data.get("name"), os.path.basename(src_dir)))
    dn_en = str(data.get("display_name_en", ""))
    if len(dn_en) > 60:
        problems.append("display_name_en 长度 %d 超过 60" % len(dn_en))
    if data.get("category") not in ALLOWED_CATEGORIES:
        problems.append("category 取值不在英文小写白名单内：%r" % data.get("category"))
    tools = data.get("allowed-tools")
    if not tools or not str(tools).strip():
        problems.append("allowed-tools 为空值（写了等于没写）")
    for key in data:
        if "_" in key and "-" not in key and key in {"user_invocable", "disable_model_invocation"}:
            problems.append("连字符写法不统一，出现下划线变体：%s" % key)

    # 版本三处一致：SKILL.md / README / CHANGELOG
    readme_path = os.path.join(src_dir, "README.md")
    if not os.path.exists(readme_path):
        problems.append("包内缺 README.md（无法核对版本一致性）")
    else:
        with open(readme_path, encoding="utf-8") as fh:
            readme = fh.read()
        m = re.search(r"版本：`(\d+\.\d+\.\d+)`", readme)
        if not m:
            problems.append("README 未找到 `版本：`x.y.z`` 行")
        elif m.group(1) != str(data["version"]):
            problems.append("README 版本 %s 与 SKILL.md 版本 %s 不一致" % (m.group(1), data["version"]))

    clog_path = os.path.join(src_dir, "CHANGELOG.md")
    if not os.path.exists(clog_path):
        problems.append("包内缺 CHANGELOG.md（无法核对版本一致性）")
    else:
        with open(clog_path, encoding="utf-8") as fh:
            clog = fh.read()
        m2 = re.search(r"^## (\d+\.\d+\.\d+)", clog, re.M)
        if not m2:
            problems.append("CHANGELOG 未找到 `## x.y.z` 版本标题")
        elif m2.group(1) != str(data["version"]):
            problems.append("CHANGELOG 版本 %s 与 SKILL.md 版本 %s 不一致" % (m2.group(1), data["version"]))

    # 引号包裹检查：任何取值含半角冒号且未加引号的字段都是平台 Go-YAML 的雷
    for line in raw.split("\n"):
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if not val or val.startswith('"') or val.startswith("'"):
            continue
        if ": " in val or val.endswith(":"):
            problems.append("字段 %s 的值含裸半角冒号且未加引号" % key.strip())
    return problems, data


def layer5_content(src_dir):
    """⑤ 内容：可解码、非空、有结构。只检查会随包分发的文件。"""
    problems = []
    count = 0
    for rel, full in pkg_files(src_dir):
        count += 1
        with open(full, "rb") as fh:
            blob = fh.read()
            if not blob:
                problems.append("%s 是空文件" % rel)
                continue
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError as exc:
                problems.append("%s 不是合法 UTF-8：%s" % (rel, exc))
                continue
            if "\ufffd" in text:
                problems.append("%s 含替换字符 U+FFFD" % rel)
            if rel.endswith(".md") and not re.search(r"^#", text, re.M):
                problems.append("%s 没有任何标题结构" % rel)
            if rel.endswith(".py") and "def main(" not in text and "__main__" in text:
                problems.append("%s 有 __main__ 但没有 main() 入口" % rel)
    return problems, count


# ---------------------------------------------------------------------------
# 负向夹具
# ---------------------------------------------------------------------------

def _patch_zip_clear_utf8_flag(path):
    """把 zip 里所有 UTF-8 名字标志位清掉，模拟平台按 cp437 解码的坏包。"""
    with open(path, "rb") as fh:
        raw = bytearray(fh.read())
    # 本地文件头 PK\x03\x04 的通用位标志在第 6-7 字节；中央目录 PK\x01\x02 在第 8-9 字节
    # UTF-8 名字标志是 bit 11（0x0800），即高字节的 0x08
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while True:
            i = raw.find(sig, start)
            if i < 0:
                break
            raw[i + off + 1] &= ~0x08
            start = i + 4
    with open(path, "wb") as fh:
        fh.write(bytes(raw))


def build_negative_zips():
    d = os.path.join(tempfile.gettempdir(), "cl_neg_zips")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    made = {}

    def make(name, entries):
        p = os.path.join(d, name)
        with zipfile.ZipFile(p, "w") as zf:
            for arc, blob in entries:
                zf.writestr(arc, blob)
        made[name] = p
        return p

    good_skill = open(os.path.join(PKG, "SKILL.md"), encoding="utf-8").read().encode("utf-8")

    make("n1_depth.zip", [("SKILL.md", good_skill), ("examples/demo/x.md", b"# x\n")])
    make("n2_traversal.zip", [("SKILL.md", good_skill), ("../evil.txt", b"x")])
    make("n4_badcolon.zip", [("SKILL.md",
         ("---\nname: x\ndescription: 生成: 报告\ndescription_zh: a\ndescription_en: b\n"
          "version: 1.0.0\nauthor: c\n---\n\n# x\n").encode("utf-8"))])
    make("n5_missing.zip", [("SKILL.md",
         ("---\nname: x\ndescription: a\nversion: 1.0.0\nauthor: c\n---\n\n# x\n").encode("utf-8"))])
    make("n6_empty.zip", [("SKILL.md", b""), ("references/a.md", b"# a\n")])

    p3 = make("n3_noflag.zip", [("SKILL.md", good_skill), ("references/\u4e2d\u6587\u7528\u4f8b.md", b"# a\n")])
    _patch_zip_clear_utf8_flag(p3)
    return made


# ---------------------------------------------------------------------------

def report(title, problems, note=""):
    if problems:
        print("  [RED]  %s" % title)
        for p in problems[:6]:
            print("         - %s" % p)
        if len(problems) > 6:
            print("         ... 另有 %d 条" % (len(problems) - 6))
    else:
        print("  [ok]   %s%s" % (title, (" " + note) if note else ""))
    return not problems


def main():
    print("=" * 66)
    print("打包 + 平台视角模拟解析")
    print("=" * 66)

    zip_path = build_zip(PKG, os.path.join(DIST, ZIP_NAME))
    size = os.path.getsize(zip_path)
    print("打包：%s（%d 字节，固定时间戳 %s）" % (zip_path, size, FIXED_TIME))

    print("\n--- 正向：真实交付包 ---")
    p1 = layer1_extract(zip_path)
    report("① 落盘（逐条目写盘并回读比对）", p1)

    p2, names = layer2_paths(zip_path)
    report("② 路径与目录深度（共 %d 条目）" % len(names), p2)

    p3, non_ascii = layer3_encoding(zip_path)
    report("③ 条目名编码标志位（非 ASCII 条目 %d 个）" % non_ascii, p3)

    p4, meta = layer4_metadata(PKG)
    report("④ 元数据（真 YAML 解析 frontmatter）", p4,
           note="version=%s category=%s" % (meta.get("version"), meta.get("category")) if meta else "")

    p5, nfiles = layer5_content(PKG)
    report("⑤ 内容（%d 个文件逐个解码）" % nfiles, p5)

    for label, probs in (("①", p1), ("②", p2), ("③", p3), ("④", p4), ("⑤", p5)):
        check(not probs, "正向包在第 %s 层不通过：%s" % (label, probs[:2]))

    # 包内与源码字节一致
    with zipfile.ZipFile(zip_path) as zf:
        mismatch = []
        for info in zf.infolist():
            src = os.path.join(PKG, info.filename.replace("/", os.sep))
            if not os.path.exists(src):
                mismatch.append("包内 %s 在源码目录不存在" % info.filename)
                continue
            if hashlib.sha256(zf.read(info)).hexdigest() != hashlib.sha256(open(src, "rb").read()).hexdigest():
                mismatch.append("包内 %s 与源码字节不一致" % info.filename)
    check(not mismatch, "包内文件必须与源码字节一致：%s" % mismatch[:3])
    report("⑥ 包内 ↔ 源码字节一致（%d 条目）" % len(names), mismatch)

    # 自包含：抽取后能独立跑通（不依赖源码目录）
    tmp = os.path.join(tempfile.gettempdir(), "cl_pack_extract")
    selfcheck = subprocess.run(
        [sys.executable, os.path.join(tmp, "scripts", "ctx.py"),
         "validate", "-f", os.path.join(tmp, "templates", "skeleton.ctx")],
        capture_output=True, text=True, encoding="utf-8", cwd=tempfile.gettempdir())
    rc = selfcheck.returncode
    check(rc == 0, "抽取后的包应能独立跑通（退出码 %d，stderr=%s）" % (rc, selfcheck.stderr.strip()[:80]))
    report("⑦ 自包含（抽取后独立跑通）", [] if rc == 0 else ["退出码 %d：%s" % (rc, selfcheck.stderr.strip()[:60])])

    # 负向样本
    print("\n--- 负向：坏包必须变红 ---")
    neg = build_negative_zips()
    expect = {
        "n1_depth.zip": ("②", layer2_paths),
        "n2_traversal.zip": ("②", layer2_paths),
        "n4_badcolon.zip": ("④", None),
        "n5_missing.zip": ("④", None),
        "n6_empty.zip": ("⑤", None),
    }
    for name, (layer, fn) in expect.items():
        path = neg[name]
        ex = os.path.join(tempfile.gettempdir(), "cl_neg_src_%s" % name)
        if fn is layer2_paths:
            probs, _ = layer2_paths(path)
        else:
            shutil.rmtree(ex, ignore_errors=True)
            with zipfile.ZipFile(path) as zf:
                zf.extractall(ex)
            if layer == "④":
                probs, _ = layer4_metadata(ex)
            else:
                probs, _ = layer5_content(ex)
        ok = report("%s 应被第 %s 层拦下" % (name, layer), probs)
        check(not ok, "负向样本 %s 未被拦下（第 %s 层判绿）" % (name, layer))

    # ③ 的负向需要单独跑编码层
    probs3, _ = layer3_encoding(neg["n3_noflag.zip"])
    ok = report("n3_noflag.zip 应被第 ③ 层拦下", probs3)
    check(not ok, "负向样本 n3_noflag.zip 未被编码层拦下")

    print("\n" + "=" * 66)
    print("检查项 %d | 失败 %d" % (STATS["checks"], STATS["failed"]))
    if FAILS:
        for f in FAILS:
            print("  ✗ %s" % f)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
