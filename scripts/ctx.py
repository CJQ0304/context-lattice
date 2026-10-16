#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ctx.py —— 上下文双层键压缩存储（Context Lattice）唯一读写内核。

设计要点（与 references/store-format.md 严格对应）：
  * 存储格式 = 读取格式：同一个 .ctx 文件既是权威存储，也是喂给模型读的视图。
  * 两级键：T（事物，上层键） / P（目的，下层键） / R（要求·描述，叶子链）。
  * 十字链表的落盘归一化：同层兄弟「行序即 next」，父子「缩进即 down」。
  * 本脚本是唯一写入者：模型只读不写，避免手改导致结构损坏。
  * 纯标准库、零第三方依赖、固定输出、稳定排序、受控错误码、原子写。

退出码：0 成功 / 1 受控失败（数据或输入错，中文提示，绝不裸 traceback）/ 2 用法错误。
"""

import argparse
import hashlib
import json
import os
import re
import sys
import threading
from datetime import date as _Date

FMT_VERSION = "1"

# —— 三个层级的行首标记（与 templates/delta.txt、SKILL.md 逐字对齐，勿单独改）——
SIGIL_TOPIC = "T"
SIGIL_PURPOSE = "P"
SIGIL_REQUEST = "R"

MAX_NAME = 200
MAX_TOP = 500
MAX_TAG = 12

# —— 作用域（记忆的作用半径）：决定「这条内容该在多大的范围里生效」——
# 语义与判定程序见 references/lifecycle.md，勿在此单独改。
SCOPE_DEFAULT = None                     # 未声明作用域 = 兼容旧文件，不做任何推断
SCOPE_KINDS = ("user", "proj", "task")   # 持久度由大到小
MAX_SCOPE = 64
RE_SCOPE = re.compile(r"^(?:user|proj|task)(?:[:/][A-Za-z0-9_.\-]{1,48})?$")

# —— 生命周期保留标记（复用行尾标记语法，未改格式）——
#   钉住 = 永不自动老化；长期 = 超期不删但列为升格候选；暂态 = 超期即折叠
TAG_PINNED = "钉住"
TAG_LONG = "长期"
TAG_TRANSIENT = "暂态"
LIFECYCLE_TAGS = (TAG_PINNED, TAG_LONG, TAG_TRANSIENT)

# —— 事件节点（原子节点）：一整块**不可拆分**的素材 ——
# 机制与判据见 references/store-format.md 的「事件节点」一节（本包不含单独的
# event-nodes.md —— 那些规则与行类型语法同处一节，避免同一份约束有两个出处）。
#
# 它解决的是：有些素材天生压不动（要逐字改的代码、要逐行核的数据）。把这类
# 素材拆成 T/P/R 会既丢原文又没换来压缩。于是给它一个单行落盘形态 ——
# **只有摘要 + 来源指针 + 字节数 + 哈希，原文不复制进库**（库外指针为默认，
# 只有 --embed 才把正文复制到库旁）。
#
# `E` 这个行首标记**本身就是原子性标记**，所以不再占用行尾标记位 ——
# 钉住 / 长期 / 暂态 仍可正常标在事件节点上，不存在互斥冲突。
#
# 语法（缩进 4 格，与 R 行同级，挂在某个 P 下）：
#     E <摘要> | src:<路径> | bytes:<字节数> | sha256:<16 位十六进制>
SIGIL_EVENT = "E"
MAX_SUMMARY = 200
MAX_SRC = 400
EVENT_SHA_LEN = 16
# 摘要按**非贪婪**取，因此「摘要里出现 ` | src:`」会造成误切 → 写入侧禁止摘要含半角竖线。
# src 用贪婪取，故路径里允许空格（Windows 路径常见）。bytes 与 sha256 锚定在行尾，无歧义。
RE_EVENT_META = re.compile(
    r"^E (.+?) \| src:(.+) \| bytes:(\d+) \| sha256:([0-9a-f]{%d})$" % EVENT_SHA_LEN
)
EMBED_DIR_SUFFIX = ".atoms"        # --embed 时正文落在 <库文件>.atoms/<sha16><扩展名>
MAX_ATOM_BYTES = 64 * 1024 * 1024  # 内嵌正文上限，防止把库变成大文件仓

# 叶子条目的种类白名单。新增行类型（例如未来的 F 行）必须同时登记到这里，
# 否则 _kind() 会在第一次渲染/计数/导出时就把它挡下来 —— 这是刻意的。
LEAF_KINDS = ("R", "E")

INDENT_TOPIC = 0
INDENT_PURPOSE = 2
INDENT_REQUEST = 4

# view 标记由 cmd_read 拼装，形态是 levelN 与 part 用 `+` 连接（如 level2+part，
# part = 视图被裁剪到某个子树）。注意与头部 scope:（记忆作用域）是两回事。
# 字符集必须含 `+` —— 少了它，带层级的局部视图将无法回灌。
#
# `| E<n>` 是可选段（事件节点计数）：老库没有它 → E=0，保持可读；
# 一旦库里出现事件节点就必须声明，否则「有 E 行但头部不认」会被判为结构错误。
# 顺序固定：计数 → E → scope → view。
RE_HEADER = re.compile(
    r"^#CTX (\d+) \| (\d{4}-\d{2}-\d{2}) \| rev (\d+) \| T(\d+) P(\d+) R(\d+)"
    r"(?: \| E(\d+))?"
    r"(?: \| scope:([A-Za-z0-9_:.\-]+))?"
    r"(?: \| view:[a-z0-9.+]+)?$"
)
RE_TOP = re.compile(r"^top: (.+)$")
RE_TOPIC = re.compile(r"^T(\d+) (.+)$")
RE_PURPOSE = re.compile(r"^  P(\d+)\.(\d+) (.+)$")
RE_REQUEST = re.compile(r"^    R (.+)$")
RE_TAG = re.compile(r" \[([^\[\]]+)\]$")

CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# —— 索引文件（index 子命令的输出）的行语法 ——
IDX_VERSION = "1"
IDX_HEADER_PREFIX = "#IDX"
RE_IDX_HEADER = re.compile(r"^#IDX (\d+) \| (\d{4}-\d{2}-\d{2}) \| stores (\d+) \| T(\d+) P(\d+)$")
RE_IDX_STORE = re.compile(r"^S ([A-Za-z0-9_:.\-]+) \| (.+) \| rev (\d+) \| date (\d{4}-\d{2}-\d{2})"
                          r" \| T(\d+) P(\d+)( \| mismatch)?$")
RE_IDX_TOPIC = re.compile(r"^  T(\d+) (.+)$")
RE_IDX_PURPOSE = re.compile(r"^    P(\d+)\.(\d+) (.+)$")
# 索引里的事件节点：只放摘要与指针，**不放正文**（正文由 fetch 按需取回）
RE_IDX_EVENT = re.compile(
    r"^      E (.+?) \| src:(.+) \| bytes:(\d+) \| sha256:([0-9a-f]{%d})$" % EVENT_SHA_LEN
)

EXIT_OK = 0
EXIT_DATA = 1
EXIT_USAGE = 2


class CtxError(Exception):
    """受控失败：带中文说明，直接展示给用户，不打印 traceback。"""


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def _split_tag(rest):
    """把行尾 ' [xxx]' 拆成 (正文, 标记或 None)。正文本身不动。"""
    m = RE_TAG.search(rest)
    if m:
        return rest[: m.start()].rstrip(), m.group(1)
    return rest, None


def _show(text, limit=42):
    """把不可信文本净化成单行可安全打印的形式（错误信息里引用用户输入时必用）。"""
    out = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch < " " or ch == "\x7f":
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    s = "".join(out)
    return s if len(s) <= limit else s[:limit] + "…"


def _check_text(text, where, limit=MAX_NAME):
    if not text:
        raise CtxError("%s：名称为空" % where)
    if CTRL_RE.search(text):
        raise CtxError("%s：名称含控制字符（制表符请改用空格）" % where)
    if "\ufffd" in text:
        raise CtxError("%s：名称含替换字符 U+FFFD，源文本编码已损坏" % where)
    if "[" in text or "]" in text:
        raise CtxError("%s：名称含半角方括号，与行尾标记语法冲突（如需方括号请改用全角［］）" % where)
    if len(text) > limit:
        raise CtxError("%s：名称长度 %d 超过上限 %d" % (where, len(text), limit))


def _check_top(text, where):
    _check_text(text, where, limit=MAX_TOP)


def _check_tag(tag, where):
    if tag is None:
        return
    if CTRL_RE.search(tag):
        raise CtxError("%s：标记含控制字符" % where)
    if len(tag) > MAX_TAG:
        raise CtxError("%s：行尾标记长度 %d 超过上限 %d（` [%s...]`）"
                       % (where, len(tag), MAX_TAG, _show(tag, 6)))


def _check_summary(text, where):
    """事件节点的摘要：与 R 行同样禁止方括号，另外**禁止半角竖线**。

    竖线是事件行元信息的分隔符（` | src:`），摘要里出现它会把元信息切错位；
    这与「名称里不能有方括号」是同一条设计：与语法冲突的字符改用全角。
    """
    _check_text(text, where, limit=MAX_SUMMARY)
    if not _norm(text):
        raise CtxError("%s：事件摘要只有空白，等于没有摘要（写一个能认出这块素材的名字）" % where)
    if "|" in text:
        raise CtxError("%s：事件摘要不能含半角竖线 `|`（它与 ` | src:` 分隔符冲突；需要竖线请改用全角｜）"
                       % where)


def _check_src(text, where):
    if not text:
        raise CtxError("%s：事件行的 src 路径为空" % where)
    if CTRL_RE.search(text):
        raise CtxError("%s：事件行的 src 路径含控制字符" % where)
    if len(text) > MAX_SRC:
        raise CtxError("%s：事件行的 src 路径长度 %d 超过上限 %d" % (where, len(text), MAX_SRC))


def _check_scope(scope, where):
    """作用域必须是 user / proj / task，可带限定名（proj:demo、task:2026-10-15-x）。"""
    if scope is None:
        return
    if CTRL_RE.search(scope) or "\ufffd" in scope:
        raise CtxError("%s：作用域含控制字符或替换字符" % where)
    if len(scope) > MAX_SCOPE:
        raise CtxError("%s：作用域长度 %d 超过上限 %d" % (where, len(scope), MAX_SCOPE))
    if not RE_SCOPE.match(scope):
        raise CtxError(
            "%s：作用域 %r 非法；必须是 %s 之一，或加限定名（如 proj:demo / task:2026-10-15-brand），"
            "限定名只允许字母数字与 _ . -"
            % (where, _show(scope, 24), " / ".join(SCOPE_KINDS)))


def scope_kind(scope):
    """取作用域的种类部分（proj:demo -> proj）；未声明返回 None。"""
    if not scope:
        return None
    return re.split(r"[:/]", scope, maxsplit=1)[0]


def parse(text):
    """解析 .ctx 文本 → (meta, top_text, topics)。任何结构问题抛 CtxError。"""
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()

    if not lines:
        raise CtxError("文件为空：不是合法的 .ctx 存储（请用 `ctx.py init` 生成头部）")

    # 头部 = 第一个非注释行；允许文件开头带 // 说明横幅
    head_idx = 0
    while head_idx < len(lines) and lines[head_idx].strip().startswith("//"):
        head_idx += 1
    if head_idx >= len(lines):
        raise CtxError("文件只有注释、没有头部：不是合法的 .ctx 存储")

    head = RE_HEADER.match(lines[head_idx])
    if not head:
        if lines[head_idx].startswith("#CTX"):
            raise CtxError(
                "第 %d 行头部格式非法，应为 `#CTX 1 | YYYY-MM-DD | rev N | Ta Pb Rc`"
                "（可选追加 `| scope:user` 与 `| view:level2+part`，顺序固定）：%s"
                % (head_idx + 1, _show(lines[head_idx], 80)))
        raise CtxError("第 %d 行不是 #CTX 头部：文件不是合法的 .ctx 存储" % (head_idx + 1))

    if head.group(1) != FMT_VERSION:
        raise CtxError("第 %d 行：格式版本 %s 不受支持（本内核只认版本 %s）"
                       % (head_idx + 1, head.group(1), FMT_VERSION))

    meta = {"version": FMT_VERSION, "date": head.group(2), "rev": int(head.group(3)),
            "events": int(head.group(7) or 0), "scope": head.group(8)}
    _check_scope(meta["scope"], "第 %d 行头部" % (head_idx + 1))

    top_text = None
    topics = []
    cur_topic = None
    cur_purpose = None
    seen_topic_ids = set()
    seen_purpose_ids = set()

    for idx in range(head_idx + 1, len(lines)):
        raw = lines[idx]
        lineno = idx + 1
        where = "第 %d 行" % lineno

        if raw.strip() == "":
            raise CtxError("%s：正文不允许空行" % where)

        if raw.strip().startswith("//"):
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        body = raw[indent:]

        if indent == INDENT_TOPIC:
            m_top = RE_TOP.match(body)
            if m_top:
                if top_text is not None:
                    raise CtxError("%s：top: 总目标重复出现" % where)
                if cur_topic is not None:
                    raise CtxError("%s：top: 总目标必须紧跟在头部之后" % where)
                top_text, tag = _split_tag(m_top.group(1))
                _check_top(top_text, where)
                _check_tag(tag, where)
                continue

            m_t = RE_TOPIC.match(body)
            if m_t:
                tid = int(m_t.group(1))
                if tid in seen_topic_ids:
                    raise CtxError("%s：事物编号 T%d 重复" % (where, tid))
                name, tag = _split_tag(m_t.group(2))
                _check_text(name, where)
                _check_tag(tag, where)
                cur_topic = {"id": "T%d" % tid, "num": tid, "name": name, "tag": tag, "purposes": []}
                seen_topic_ids.add(tid)
                topics.append(cur_topic)
                cur_purpose = None
                continue

            if re.match(r"^T\d+ *$", body):
                raise CtxError("%s：名称为空" % where)
            raise CtxError("%s：无法识别的行（顶层只允许 `top: ...` 与 `T<n> 名称`）：%s" % (where, raw[:60]))

        if indent == INDENT_PURPOSE:
            m_p = RE_PURPOSE.match(raw)
            if not m_p:
                if re.match(r"^ {2}P\d+\.\d+ *$", raw):
                    raise CtxError("%s：名称为空" % where)
                raise CtxError("%s：缩进 2 格的行必须是 `  P<n>.<m> 名称`" % where)
            if cur_topic is None:
                raise CtxError("%s：目的行之前没有事物行（层级跳跃）" % where)
            pid = "%s.%s" % (m_p.group(1), m_p.group(2))
            if pid in seen_purpose_ids:
                raise CtxError("%s：目的编号 P%s 重复" % (where, pid))
            name, tag = _split_tag(m_p.group(3))
            _check_text(name, where)
            _check_tag(tag, where)
            cur_purpose = {"id": "P" + pid, "num": pid, "name": name, "tag": tag, "items": []}
            seen_purpose_ids.add(pid)
            cur_topic["purposes"].append(cur_purpose)
            continue

        if indent == INDENT_REQUEST:
            m_r = RE_REQUEST.match(raw)
            if m_r:
                if cur_purpose is None:
                    raise CtxError("%s：要求行之前没有目的行（层级跳跃）" % where)
                text, tag = _split_tag(m_r.group(1))
                _check_text(text, where)
                _check_tag(tag, where)
                cur_purpose["items"].append({"kind": "R", "text": text, "tag": tag})
                continue
            text, tag = _split_tag(raw[indent:])
            m_e = RE_EVENT_META.match(text)
            if m_e:
                if cur_purpose is None:
                    raise CtxError("%s：事件行之前没有目的行（层级跳跃）" % where)
                summary = m_e.group(1)
                _check_summary(summary, where)
                _check_src(m_e.group(2), where)
                _check_tag(tag, where)
                cur_purpose["items"].append({
                    "kind": "E", "text": summary, "tag": tag,
                    "src": m_e.group(2), "bytes": int(m_e.group(3)), "sha": m_e.group(4),
                })
                continue
            if raw[indent:].startswith(SIGIL_EVENT + " "):
                raise CtxError(
                    "%s：事件行格式非法，应为 `    %s 摘要 | src:路径 | bytes:字节数 | sha256:%s`"
                    "（四段齐全、顺序固定、用 ` | ` 分隔）：%s"
                    % (where, SIGIL_EVENT, "0" * EVENT_SHA_LEN, _show(raw[indent:], 70)))
            raise CtxError("%s：缩进 4 格的行必须是 `    %s 内容`（要求）或 `    %s ...`（事件节点）"
                           % (where, SIGIL_REQUEST, SIGIL_EVENT))

        raise CtxError(
            "%s：缩进错位（应为 0/2/4 格，实测 %d 格）" % (where, indent)
        )

    if not topics:
        raise CtxError("存储里没有任何事物节点")

    n_t, n_p, n_r, n_e = _counts4(topics)
    if (n_t, n_p, n_r) != (int(head.group(4)), int(head.group(5)), int(head.group(6))):
        raise CtxError(
            "头部计数与实际不符：头部 T%d P%d R%d，实际 T%d P%d R%d"
            % (int(head.group(4)), int(head.group(5)), int(head.group(6)), n_t, n_p, n_r)
        )
    if n_e != meta["events"]:
        declared = "未声明" if head.group(7) is None else "声明 %d" % meta["events"]
        raise CtxError(
            "事件节点计数不符：头部%s，实际 %d 条 `%s` 行"
            "（有事件节点时头部必须写 ` | E%d`，位置在 R 计数之后、scope 之前）"
            % (declared, n_e, SIGIL_EVENT, n_e)
        )

    return meta, top_text, topics


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def render(meta, top_text, topics):
    """把结构渲染回 .ctx 文本（行尾统一 \n，无 BOM，确定性）。"""
    n_t, n_p, n_r, n_e = _counts4(topics)

    scope_seg = "" if not meta.get("scope") else " | scope:%s" % meta["scope"]
    e_seg = "" if not n_e else " | E%d" % n_e
    out = ["#CTX %s | %s | rev %d | T%d P%d R%d%s%s"
           % (meta["version"], meta["date"], meta["rev"], n_t, n_p, n_r, e_seg, scope_seg)]
    if top_text:
        out.append("top: %s" % top_text)

    for t in topics:
        out.append("T%d %s%s" % (t["num"], t["name"], _fmt_tag(t["tag"])))
        for p in t["purposes"]:
            out.append("  P%s %s%s" % (p["num"], p["name"], _fmt_tag(p["tag"])))
            for it in p["items"]:
                out.append("    " + _fmt_item(it))
    return "\n".join(out) + "\n"


def _kind(it):
    """叶子的种类。**未知种类立刻报错，绝不默认当 R。**

    原先各处都写成 `if it.get("kind") == "E": ... else: 当 R`。这种兜底在**只有两种
    叶子时**完全正确，但它把「不认识的东西」和「R 行」混为一谈 —— 将来新增第三种
    行类型时，渲染、计数、内容指纹、导出、老化折叠会**全部静默**把它当成 R：
    改它的内容被判 no-op、折叠时被删掉，而且一个错误都不报。

    这是真实任务测试里两个独立执行者各自指出的同一处结构性风险。修法就是这四行：
    把「缺省 = R」换成「未知 = 报错」。CtxError 而非 assert，是为了让它在被当库用
    （--import 模块）的场景下也能被捕获成一条普通错误。
    """
    k = it.get("kind")
    if k is None:
        k = "R"        # 兼容内部构造条目时省略 kind 的写法（parse 始终显式写入）
    if k not in LEAF_KINDS:
        raise CtxError("内部错误：未知的叶子种类 %r（只支持 %s）。"
                       "这是代码缺陷而非数据问题 —— 新增行类型时必须同步所有分派点。"
                       % (k, " / ".join(LEAF_KINDS)))
    return k


def _fmt_item(it):
    """把一个叶子条目渲染成一行（R = 文本要求 / E = 事件节点）。"""
    if _kind(it) == "E":
        return "%s %s | src:%s | bytes:%d | sha256:%s%s" % (
            SIGIL_EVENT, it["text"], it["src"], it["bytes"], it["sha"], _fmt_tag(it["tag"]))
    return "%s %s%s" % (SIGIL_REQUEST, it["text"], _fmt_tag(it["tag"]))


def _fmt_tag(tag):
    return "" if tag is None else " [%s]" % tag


def _counts4(topics):
    """(T, P, R, E)：E = 事件节点数，与 R 分开计。

    刻意不保留一个只返回前三项的包装函数 —— 那会是一段没有任何调用点的代码，
    而「没人跑过的分支」正是本项目反复踩到的坑。
    """
    n_t = len(topics)
    n_p = sum(len(t["purposes"]) for t in topics)
    items = [it for t in topics for p in t["purposes"] for it in p["items"]]
    return (n_t, n_p,
            sum(1 for it in items if _kind(it) == "R"),
            sum(1 for it in items if _kind(it) == "E"))


def _iter_events(topics):
    """按 (事物, 目的, 条目) 顺序产出所有事件节点。"""
    for t in topics:
        for p in t["purposes"]:
            for it in p["items"]:
                if it.get("kind") == "E":
                    yield t, p, it


def _body_key(top_text, topics, scope=None):
    """内容指纹（不含 rev）：用于判断一次写操作是否真的改变了内容。

    无实际改动时不应写盘、不应递增 rev —— 否则等于谎报「有过一次修改」。
    作用域算内容的一部分：它变了就是真的变了（关系到这条记忆在多大范围生效）。
    """
    parts = [top_text or "", "scope=%s" % (scope or "")]
    for t in topics:
        parts.append("T%d|%s|%s" % (t["num"], t["name"], t["tag"]))
        for p in t["purposes"]:
            parts.append("P%s|%s|%s" % (p["num"], p["name"], p["tag"]))
            for it in p["items"]:
                if _kind(it) == "E":
                    # 事件节点的指针与哈希都算内容的一部分：改了其中一个就是真的变了
                    # （换源文件、原文件被改写 → 哈希变），必须递增 rev，不能算 no-op。
                    parts.append("E|%s|%s|%s|%s|%s"
                                 % (it["text"], it["src"], it["bytes"], it["sha"], it["tag"]))
                else:
                    parts.append("R|%s|%s" % (it["text"], it["tag"]))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 文件读写（原子）
# --------------------------------------------------------------------------

def load(path):
    if not os.path.exists(path):
        raise CtxError("找不到存储文件：%s（先用 `ctx.py init --file %s` 创建）" % (path, path))
    if os.path.isdir(path):
        raise CtxError("路径是目录不是文件：%s" % path)
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        raise CtxError("文件不是合法 UTF-8：%s（.ctx 必须为 UTF-8 无 BOM）" % path)
    except OSError as exc:
        raise CtxError("无法读取文件 %s：%s" % (path, exc.strerror or exc))
    if text.startswith("\ufeff"):
        raise CtxError("文件带 UTF-8 BOM，请去除后重试：%s" % path)
    text = text.replace("\r\n", "\n").replace("\r", "\n")   # CRLF / 裸 CR 归一化
    return parse(text)


def save(path, meta, top_text, topics):
    text = render(meta, top_text, topics)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(directory):
        raise CtxError("目标目录不存在：%s" % directory)
    # 原子写：临时文件名必须唯一（带 pid + 线程号）。
    # 不能用固定的 path + ".tmp"：Windows 的 os.replace 走 MoveFileEx(MOVEFILE_REPLACE_EXISTING)，
    # 语义是「先删目标、再搬源」。两个写入者共用一个临时名时，后者会把前者刚放好的目标删掉，
    # 又因为源已被前者搬走而搬不回来 —— 结果是目标文件凭空消失（比写坏更糟）。
    # 唯一临时名让每次替换都「源确实在」，最坏只是最后写入者胜出（丢更新），绝不会丢文件。
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise CtxError("无法写入文件 %s：%s" % (path, exc.strerror or exc))


# --------------------------------------------------------------------------
# 增量归并（add）
# --------------------------------------------------------------------------

def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _find_topic(topics, name):
    target = _norm(name)
    for t in topics:
        if _norm(t["name"]) == target:
            return t
    return None


def _find_purpose(topic, name):
    target = _norm(name)
    for p in topic["purposes"]:
        if _norm(p["name"]) == target:
            return p
    return None


def apply_delta(topics, delta_lines):
    """把 T/P/R 增量并入结构，返回 (统计, 提示列表)。确定性、幂等。

    标记（` [xxx]`）对三个层级都可**更新**：同文本 + 新标记 = 改标记，不是重复。
    否则 `[已废]` / `[已升级]` 这类审计标记在首次写入之后将永远改不了，
    文档承诺的留痕机制就落不了地。
    """
    stat = {"t_new": 0, "p_new": 0, "r_new": 0, "r_dup": 0,
            "t_retag": 0, "p_retag": 0, "r_retag": 0,
            "e_new": 0, "e_dup": 0, "e_upd": 0, "e_retag": 0}
    notes = []
    cur_topic = None
    cur_purpose = None

    for raw in delta_lines:
        line = raw.rstrip("\n").rstrip("\r")
        if line.strip() == "":
            continue
        if line.startswith("#") or line.startswith("//"):
            continue
        body = line.strip()

        m = re.match(r"^(%s|%s|%s|%s)(?:[ \t]+(.*))?$"
                     % (SIGIL_TOPIC, SIGIL_PURPOSE, SIGIL_REQUEST, SIGIL_EVENT), body)
        if not m:
            hint = ""
            if re.match(r"^[%s%s%s][0-9]" % (SIGIL_TOPIC, SIGIL_PURPOSE, SIGIL_REQUEST), body):
                hint = "（标记后必须紧跟一个空格：写 `%s 品牌定位`，不要写 `%s1 品牌定位`）" \
                       % (SIGIL_TOPIC, SIGIL_TOPIC)
            elif not body:
                hint = "（空行不计入增量）"
            raise CtxError("增量行无法识别：`%s`%s；行首标记只能是 %s（事物）/ %s（目的）/ %s（要求）/ %s（事件节点）"
                           % (_show(body), hint, SIGIL_TOPIC, SIGIL_PURPOSE, SIGIL_REQUEST, SIGIL_EVENT))

        sigil, rest = m.group(1), (m.group(2) or "")
        text, tag = _split_tag(rest)

        if sigil == SIGIL_TOPIC:
            if not text:
                raise CtxError("`%s` 行缺少事物名称" % SIGIL_TOPIC)
            _check_text(text, "增量 %s 行" % SIGIL_TOPIC)
            _check_tag(tag, "增量 %s 行" % SIGIL_TOPIC)
            topic = _find_topic(topics, text)
            if topic is None:
                topic = {"id": "T%d" % (len(topics) + 1), "num": _next_topic_num(topics),
                         "name": _norm(text), "tag": tag, "purposes": []}
                topics.append(topic)
                stat["t_new"] += 1
            elif tag is not None and topic["tag"] != tag:
                notes.append("改标记：%s「%s」 %s -> %s"
                             % (topic["id"], topic["name"], topic["tag"] or "无", tag))
                topic["tag"] = tag
                stat["t_retag"] += 1
            cur_topic = topic
            cur_purpose = None
            continue

        if sigil == SIGIL_PURPOSE:
            if not text:
                raise CtxError("`%s` 行缺少目的名称" % SIGIL_PURPOSE)
            _check_text(text, "增量 %s 行" % SIGIL_PURPOSE)
            _check_tag(tag, "增量 %s 行" % SIGIL_PURPOSE)
            if cur_topic is None:
                raise CtxError("`%s` 行之前没有 `%s` 行，无法确定它挂在哪个事物下"
                               % (SIGIL_PURPOSE, SIGIL_TOPIC))
            purpose = _find_purpose(cur_topic, text)
            if purpose is None:
                purpose = {"id": "P%s" % _next_purpose_num(cur_topic), "num": _next_purpose_num(cur_topic),
                           "name": _norm(text), "tag": tag, "items": []}
                cur_topic["purposes"].append(purpose)
                stat["p_new"] += 1
            elif tag is not None and purpose["tag"] != tag:
                notes.append("改标记：%s「%s」 %s -> %s"
                             % (purpose["id"], purpose["name"], purpose["tag"] or "无", tag))
                purpose["tag"] = tag
                stat["p_retag"] += 1
            cur_purpose = purpose
            continue

        if sigil == SIGIL_EVENT:
            # 事件节点增量行：`E 摘要 | src:路径 | bytes:N | sha256:H [标记]`
            m_e = RE_EVENT_META.match("%s %s" % (SIGIL_EVENT, text))
            if not m_e:
                raise CtxError(
                    "增量事件行格式非法，应为 `%s 摘要 | src:路径 | bytes:字节数 | sha256:%s`"
                    "（四段齐全、顺序固定、用 ` | ` 分隔）：%s"
                    % (SIGIL_EVENT, "0" * EVENT_SHA_LEN, _show(body, 70)))
            if cur_purpose is None:
                raise CtxError("`%s` 行之前没有 `%s` 行，无法确定它挂在哪个目的下"
                               % (SIGIL_EVENT, SIGIL_PURPOSE))
            summary, src = m_e.group(1), m_e.group(2)
            nbytes, sha = int(m_e.group(3)), m_e.group(4)
            _check_summary(summary, "增量 %s 行" % SIGIL_EVENT)
            _check_src(src, "增量 %s 行" % SIGIL_EVENT)
            _check_tag(tag, "增量 %s 行" % SIGIL_EVENT)
            norm_summary = _norm(summary)
            hit = None
            for it in cur_purpose["items"]:
                if it.get("kind") == "E" and _norm(it["text"]) == norm_summary and it["src"] == src:
                    hit = it
                    break
            if hit is not None:
                if hit["sha"] != sha or hit["bytes"] != nbytes:
                    notes.append("事件节点指纹变化：%s / %s「%s」sha %s -> %s"
                                 % (cur_topic["id"], cur_purpose["id"], norm_summary, hit["sha"], sha))
                    hit["sha"], hit["bytes"] = sha, nbytes
                    stat["e_upd"] += 1
                elif tag is not None and hit["tag"] != tag:
                    notes.append("改标记：%s / %s / E %s %s -> %s"
                                 % (cur_topic["id"], cur_purpose["id"], norm_summary,
                                    hit["tag"] or "无", tag))
                    hit["tag"] = tag
                    stat["e_retag"] += 1
                else:
                    stat["e_dup"] += 1
                    notes.append("重复跳过：%s / %s / E %s"
                                 % (cur_topic["id"], cur_purpose["id"], norm_summary))
                continue
            cur_purpose["items"].append({
                "kind": "E", "text": norm_summary, "tag": tag,
                "src": src, "bytes": nbytes, "sha": sha})
            stat["e_new"] += 1
            continue

        # SIGIL_REQUEST
        if not text:
            raise CtxError("`%s` 行缺少内容" % SIGIL_REQUEST)
        _check_text(text, "增量 %s 行" % SIGIL_REQUEST)
        _check_tag(tag, "增量 %s 行" % SIGIL_REQUEST)
        if cur_purpose is None:
            raise CtxError("`%s` 行之前没有 `%s` 行，无法确定它挂在哪个目的下"
                           % (SIGIL_REQUEST, SIGIL_PURPOSE))
        norm_text = _norm(text)
        hit = None
        for it in cur_purpose["items"]:
            if it.get("kind") != "E" and _norm(it["text"]) == norm_text:
                hit = it
                break
        if hit is not None:
            if tag is not None and hit["tag"] != tag:
                notes.append("改标记：%s / %s「%s」 %s -> %s"
                             % (cur_topic["id"], cur_purpose["id"], norm_text, hit["tag"] or "无", tag))
                hit["tag"] = tag
                stat["r_retag"] += 1
            else:
                stat["r_dup"] += 1
                notes.append("重复跳过：%s / %s / %s" % (cur_topic["id"], cur_purpose["id"], norm_text))
            continue
        cur_purpose["items"].append({"kind": "R", "text": norm_text, "tag": tag})
        stat["r_new"] += 1

    return stat, notes


def _next_topic_num(topics):
    used = {t["num"] for t in topics}
    n = 1
    while n in used:
        n += 1
    return n


def _next_purpose_num(topic):
    used = set()
    for p in topic["purposes"]:
        try:
            used.add(int(p["num"].split(".")[1]))
        except (ValueError, IndexError):
            continue
    n = 1
    while n in used:
        n += 1
    return "%d.%d" % (topic["num"], n)


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def cmd_init(args):
    if os.path.exists(args.file) and not args.force:
        raise CtxError("目标已存在：%s（要覆盖请显式加 --force）" % args.file)
    _check_scope(args.scope, "--scope")
    meta = {"version": FMT_VERSION, "date": args.date, "rev": 0, "scope": args.scope}
    topics = []
    top_text = args.top
    if args.stdin:
        delta = _read_stdin().split("\n")
        _stat, notes = apply_delta(topics, delta)
        for note in notes:
            print("NOTE %s" % note)
    if not topics:
        raise CtxError("init 未产生任何事物节点：请用 --stdin 传入至少一个 `T 名称` 行")
    save(args.file, meta, top_text, topics)
    print("OK init %s rev=0 %s | scope=%s"
          % (args.file, _fmt_counts(topics), meta["scope"] or "未声明"))
    return EXIT_OK


def _fmt_counts(topics):
    """统一的节点计数摘要：没有事件节点时不显示 E，避免污染既有输出的可读性。"""
    n_t, n_p, n_r, n_e = _counts4(topics)
    return "T%d P%d R%d%s" % (n_t, n_p, n_r, "" if not n_e else " E%d" % n_e)


def _fmt_stat(stat):
    """输出用的增量统计。只在非零时才追加，避免污染常规输出。"""
    text = "+%dT +%dP +%dR" % (stat["t_new"], stat["p_new"], stat["r_new"])
    if stat.get("e_new"):
        text += " +%dE" % stat["e_new"]
    text += " (dup %d" % (stat["r_dup"] + stat.get("e_dup", 0))
    retag = stat.get("t_retag", 0) + stat.get("p_retag", 0) + stat.get("r_retag", 0) \
        + stat.get("e_retag", 0)
    if retag:
        text += ", retag %d" % retag
    if stat.get("e_upd"):
        text += ", upd %d" % stat["e_upd"]
    return text + ")"


def cmd_add(args):
    meta, top_text, topics = load(args.file)

    # ---- 1. 作用域：显式给出才动，且 `-` 是「清除已声明作用域」的唯一写法 ----
    scope_before = meta.get("scope")
    scope_arg = getattr(args, "scope", None)
    if scope_arg is not None:
        if scope_arg == "":
            raise CtxError("--scope 不能为空字符串；要清除已声明的作用域请显式写 `--scope -`")
        new_scope = None if scope_arg == "-" else scope_arg
        _check_scope(new_scope, "--scope")
    else:
        new_scope = scope_before
    scope_changed = (new_scope != scope_before)

    # ---- 2. 增量：三种来源 ----
    if args.topic or args.purpose or args.item:
        if not args.topic or not args.purpose:
            raise CtxError("用参数式写入时必须同时给出 --topic 与 --purpose（--item 可选）")
        if args.tag and not args.item:
            raise CtxError("--tag 只作用于 --item；要给事物或目的打标记请改用 --stdin 行内标记")
        delta = ["%s %s" % (SIGIL_TOPIC, args.topic), "%s %s" % (SIGIL_PURPOSE, args.purpose)]
        if args.item:
            delta.append("%s %s%s" % (SIGIL_REQUEST, args.item, "" if not args.tag else " [%s]" % args.tag))
        has_delta = True
    elif args.stdin:
        delta = _read_stdin().split("\n")
        has_delta = True
    elif scope_arg is not None:
        delta, has_delta = [], False
    else:
        raise CtxError("add 需要输入：--topic/--purpose[/--item]，或 --stdin 读取 T/P/R 增量，或 --scope 改作用域")

    if scope_arg is not None and not scope_changed:
        print("NOTE scope: 已是 %s，未变化" % (scope_before or "未声明"))

    before_key = _body_key(top_text, topics, scope_before)
    stat, notes = apply_delta(topics, delta)
    # 「一行都没认出来」才是输入错误；纯重复（r_dup>0）不算错误，只算没变化。
    if has_delta and sum(stat.values()) == 0:
        raise CtxError("增量内容为空：没有可并入的新增 T/P/R 行（原文件未改动）")
    if scope_changed:
        meta["scope"] = new_scope
        print("NOTE scope: %s -> %s" % (scope_before or "未声明", new_scope or "未声明"))
    for note in notes:
        print("NOTE %s" % note)

    counts = _fmt_counts(topics)
    if _body_key(top_text, topics, meta.get("scope")) == before_key:
        print("OK added %s | 内容未变化，未写盘 rev=%d | now %s"
              % (_fmt_stat(stat), meta["rev"], counts))
        return EXIT_OK
    meta["rev"] += 1
    save(args.file, meta, top_text, topics)
    print("OK added %s rev=%d | now %s | scope=%s"
          % (_fmt_stat(stat), meta["rev"], counts, meta.get("scope") or "未声明"))
    return EXIT_OK


def _select(topics, topic_sel, purpose_sel):
    if purpose_sel:
        want = _norm(purpose_sel)
        out = []
        for t in topics:
            for p in t["purposes"]:
                if _norm(p["id"]) == want or _norm(p["name"]) == want:
                    out.append((t, [p]))
        if not out:
            raise CtxError("找不到目的：%s（可用 P<事物号>.<序号> 或目的名称）" % _show(purpose_sel))
        return out
    if topic_sel:
        want = _norm(topic_sel)
        for t in topics:
            if _norm(t["id"]) == want or _norm(t["name"]) == want:
                return [(t, t["purposes"])]
        raise CtxError("找不到事物：%s（可用 T<序号> 或事物名称）" % _show(topic_sel))
    return [(t, t["purposes"]) for t in topics]


def cmd_read(args):
    meta, top_text, topics = load(args.file)
    if args.level not in (1, 2, 3):
        raise CtxError("--level 只能是 1（仅事物）/ 2（事物+目的）/ 3（全量）")
    if args.budget is not None and args.budget < 0:
        raise CtxError("--budget 不能为负数（实测 %d）；不裁剪请直接省略该参数" % args.budget)

    if args.fetch:
        # --fetch 在裁剪**之前**处理：它按完整结构定位事件节点，与 --level 无关。
        return _read_fetch(meta, topics, args)

    if args.level == 1 or args.level == 2:
        topics = [{"id": t["id"], "num": t["num"], "name": t["name"], "tag": t["tag"],
                   "purposes": [] if args.level == 1 else
                   [{"id": p["id"], "num": p["num"], "name": p["name"], "tag": p["tag"], "items": []}
                    for p in t["purposes"]]} for t in topics]

    picked = _select(topics, args.topic, args.purpose)

    view_marker = None
    if args.level != 3:
        view_marker = "level%d" % args.level
    if args.topic or args.purpose:
        # 注意：这里表示「视图被裁剪到某个子树」，与头部 scope:（记忆作用域）无关，
        # 故刻意不叫 scope，避免两个同名字段含义打架。
        view_marker = (view_marker + "+" if view_marker else "") + "part"

    body = []
    for t, ps in picked:
        body.append("T%d %s%s" % (t["num"], t["name"], _fmt_tag(t["tag"])))
        for p in ps:
            body.append("  P%s %s%s" % (p["num"], p["name"], _fmt_tag(p["tag"])))
            for it in p["items"]:
                body.append("    " + _fmt_item(it))

    if args.budget and args.budget > 0:
        body = _apply_budget(body, args.budget)

    # 头部计数 = 本视图最终实际包含的内容计数（保证视图可原样回灌为合法存储）
    n_t, n_p, n_r, n_e = _count_lines(body)

    lines = []
    if not args.no_header:
        lines.append("#CTX %s | %s | rev %d | T%d P%d R%d%s%s%s"
                     % (meta["version"], meta["date"], meta["rev"], n_t, n_p, n_r,
                        "" if not n_e else " | E%d" % n_e,
                        "" if not meta.get("scope") else " | scope:%s" % meta["scope"],
                        "" if not view_marker else " | view:%s" % view_marker))
        if top_text:
            lines.append("top: %s" % top_text)
    lines.extend(body)

    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


def _count_lines(lines):
    """从已渲染的行统计 T/P/R/E 数量（不依赖内存结构，保证视图头部自洽）。"""
    n_t = sum(1 for ln in lines if re.match(r"^T\d+ ", ln))
    n_p = sum(1 for ln in lines if re.match(r"^ {2}P\d+\.\d+ ", ln))
    n_r = sum(1 for ln in lines if ln.startswith("    " + SIGIL_REQUEST + " "))
    n_e = sum(1 for ln in lines if ln.startswith("    " + SIGIL_EVENT + " "))
    return n_t, n_p, n_r, n_e


def _apply_budget(lines, budget):
    """预算裁剪：key 层（头部/top/T/P）与事件节点永不裁；R 层按原行序就地丢弃。

    事件节点只有一行、是回原文的唯一把手，裁掉它等于把内容变成不可达，
    所以它与 key 层一样受保护。
    """
    total_leaf = sum(1 for ln in lines if ln.startswith("    " + SIGIL_REQUEST + " "))
    kept = []
    used = 0
    dropped = 0
    for ln in lines:
        cost = len(ln) + 1
        if ln.startswith("    " + SIGIL_REQUEST + " "):
            if used + cost > budget:
                dropped += 1
                continue
            kept.append(ln)
            used += cost
        else:
            kept.append(ln)
            used += cost
    if dropped:
        kept.append("// 已按预算裁剪 %s %d/%d 条" % (SIGIL_REQUEST, total_leaf - dropped, total_leaf))
    return kept


# --------------------------------------------------------------------------
# 事件节点（原子节点）：写入、取回与校验
# --------------------------------------------------------------------------
#
# 为什么需要它：「读完再压」之后，剩下的问题只有一类 —— 有些素材**天生压不动**。
# 要逐字改的代码、要逐行核的数据，拆成 T/P/R 会既丢原文、又没换来任何压缩。
# 于是给这类素材一个单行形态：只有摘要 + 来源指针 + 字节数 + 哈希，原文不进库。
#
# 它买到的是「重新找到它」的成本，不是读取成本 —— 原文该读还得读。
# 压缩买「读得更少」，事件节点买「找得更快」，两笔账不要混。

def _sha256(data):
    return hashlib.sha256(data).hexdigest()[:EVENT_SHA_LEN]


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _embed_dir(store_path):
    return os.path.abspath(store_path) + EMBED_DIR_SUFFIX


def _embed_path(store_path, sha, src):
    """内嵌正文的落点：<库文件>.atoms/<sha16><扩展名>（内容寻址，同哈希只存一份）。"""
    ext = os.path.splitext(src or "")[1]
    if not re.match(r"^\.[A-Za-z]{1,10}$", ext or ""):
        ext = ""
    return os.path.join(_embed_dir(store_path), sha + ext)


def _relativize(store_path, src):
    """库被整体搬走之后指针仍然有效：优先存相对库文件的路径，否则存绝对路径。

    统一用 `/` 分隔，保证同一输入在任何平台上落盘字节一致（确定性）。
    """
    ap = os.path.abspath(src)
    base = os.path.dirname(os.path.abspath(store_path))
    try:
        rel = os.path.relpath(ap, base)
    except ValueError:
        return ap.replace("\\", "/")      # Windows 跨盘符：relpath 会抛 ValueError
    if rel.startswith(".."):
        return ap.replace("\\", "/")
    return rel.replace("\\", "/")


def _resolve_src(store_path, src):
    if os.path.isabs(src):
        return os.path.normpath(src)
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(store_path)), src))


def _load_atom(store_path, it):
    """取回事件节点的原文。返回 (data 或 None, status, 描述, src_state)。

    status    —— 针对**取回的内容**：ok（字节数与哈希都相符）/ drift / missing
    src_state —— 针对**来源指针**：ok / drift / missing

    两者刻意分开，因为它们是两件事：
      * 内嵌副本存在时，源文件被改写**不该**让取回失败 —— 节点是自包含的；
      * 但那意味着**指针已不再指向同一内容** —— 这件事必须单独报出来，
        否则「可校验」会退化成「只要有一份副本就永远绿灯」，等于没有校验。
    """
    emb = _embed_path(store_path, it["sha"], it["src"])
    src_path = _resolve_src(store_path, it["src"])
    if not os.path.isfile(src_path):
        src_state = "missing"
    else:
        raw = _read_bytes(src_path)
        src_state = "ok" if (len(raw) == it["bytes"] and _sha256(raw) == it["sha"]) else "drift"

    if os.path.isfile(emb):
        data = _read_bytes(emb)
        desc = "embed:%s" % os.path.relpath(
            emb, os.path.dirname(os.path.abspath(store_path))).replace("\\", "/")
    else:
        if src_state == "missing":
            return None, "missing", "src:%s" % it["src"], src_state
        data = _read_bytes(src_path)
        desc = "src:%s" % it["src"]

    if len(data) != it["bytes"] or _sha256(data) != it["sha"]:
        return data, "drift", desc, src_state
    return data, "ok", desc, src_state


def _gather_events(topics, topic_sel, purpose_sel):
    for t, ps in _select(topics, topic_sel, purpose_sel):
        for p in ps:
            for it in p["items"]:
                if it.get("kind") == "E":
                    yield t, p, it


def _do_fetch(store_path, topics, topic_sel, purpose_sel, out_path, check_only):
    """取回事件节点原文并逐个校验。有漂移或缺失 → 非零退出码（可被验收脚本断言）。"""
    hits = list(_gather_events(topics, topic_sel, purpose_sel))
    if not hits:
        raise CtxError("选定范围内没有事件节点（事件节点用 `event` 子命令登记）")
    if out_path and len(hits) != 1:
        raise CtxError("--out 只允许在命中恰好 1 个事件节点时使用（本次命中 %d 个）；"
                       "要批量取回请省略 --out 打到标准输出" % len(hits))

    n_ok = n_drift = n_missing = n_stale = 0
    payloads = []
    for t, p, it in hits:
        data, status, desc, src_state = _load_atom(store_path, it)
        head = "==== %s / %s | E %s | %s | bytes:%d | sha256:%s"
        if status == "missing":
            n_missing += 1
            print("MISS %s / %s | E %s | 原文找不到（%s）" % (t["id"], p["id"], it["text"], desc))
            continue
        if status == "drift":
            n_drift += 1
            print("DRIFT %s / %s | E %s | 内容与登记哈希不符：登记 %s，实测 %s"
                  % (t["id"], p["id"], it["text"], it["sha"], _sha256(data)))
        else:
            n_ok += 1
            # 内嵌副本让取回成功，但这不代表指针还指得准 —— 指针状态单独报，且不改退出码。
            if src_state == "drift":
                n_stale += 1
                print("STALE-SRC %s / %s | E %s | 取回用的是内嵌副本，但来源文件已被改写（%s）："
                      "指针已不再指向同一内容" % (t["id"], p["id"], it["text"], it["src"]))
            elif src_state == "missing":
                n_stale += 1
                print("STALE-SRC %s / %s | E %s | 取回用的是内嵌副本，来源文件已不存在（%s）"
                      % (t["id"], p["id"], it["text"], it["src"]))
        if not check_only:
            payloads.append((head % (t["id"], p["id"], it["text"], desc, it["bytes"], it["sha"]), data))

    if out_path:
        try:
            with open(out_path, "wb") as fh:
                fh.write(payloads[0][1])
        except OSError as exc:
            raise CtxError("无法写入 %s：%s" % (out_path, exc.strerror or exc))
        print("OK fetched -> %s (%d 字节，sha256:%s)"
              % (out_path, len(payloads[0][1]), _sha256(payloads[0][1])))
    elif not check_only:
        out = sys.stdout
        for head, data in payloads:
            out.write(head + "\n")
            try:
                out.write(data.decode("utf-8"))
            except UnicodeDecodeError:
                out.write("// 该事件节点不是 UTF-8 文本（%d 字节），已按十六进制前 256 字节显示\n" % len(data))
                out.write(data[:256].hex() + "\n")
                print("NOTE 非文本内容请改用 `fetch --out <文件>` 取回原字节")
            if not data.endswith(b"\n"):
                out.write("\n")

    print("OK fetch 命中 %d | 校验通过 %d · 漂移 %d · 缺失 %d · 指针失效 %d%s"
          % (len(hits), n_ok, n_drift, n_missing, n_stale,
             "；仅校验（未输出内容）" if check_only else ""))
    return EXIT_DATA if (n_drift or n_missing) else EXIT_OK


def _read_fetch(meta, topics, args):
    """read --fetch：把所选范围内事件节点的原文取回并逐个校验哈希。

    刻意**不与视图混排**：视图输出必须能原样回灌成合法存储（这是已经验证过的
    round-trip 不变量），而正文不是合法的 .ctx 行。所以 --fetch 只出正文块，
    块头以 `====` 起首 —— 任何合法 .ctx 行都不会这样开头，误灌时会显式报错，
    而不是静默把库写坏。
    """
    return _do_fetch(args.file, topics, args.topic, args.purpose,
                     getattr(args, "out", None), getattr(args, "check", False))


def cmd_fetch(args):
    meta, _top, topics = load(args.file)
    if args.summary is not None:
        want = _norm(args.summary)
        topics = [{"id": t["id"], "num": t["num"], "name": t["name"], "tag": t["tag"],
                   "purposes": [{"id": p["id"], "num": p["num"], "name": p["name"], "tag": p["tag"],
                                 "items": [it for it in p["items"]
                                           if it.get("kind") == "E" and _norm(it["text"]) == want]}
                                for p in t["purposes"]]}
                  for t in topics]
        topics = [t for t in topics if any(p["items"] for p in t["purposes"])]
        if not topics:
            raise CtxError("找不到摘要为「%s」的事件节点（--summary 是精确匹配，按归一化空白比较）"
                           % _show(args.summary, 40))
    return _do_fetch(args.file, topics, args.topic, args.purpose, args.out, args.check)


def cmd_event(args):
    """登记一个事件节点（原子节点）：把一整块压不动的素材挂到某个目的下。"""
    meta, top_text, topics = load(args.file)
    if not args.topic or not args.purpose:
        raise CtxError("event 必须同时给出 --topic 与 --purpose（事件节点挂在某个目的下）")
    if not args.src:
        raise CtxError("event 必须给出 --src <原文路径>（默认库外指针；要复制进库加 --embed）")

    src_abs = os.path.abspath(args.src)
    if not os.path.isfile(src_abs):
        raise CtxError(
            "--src 不存在或不是普通文件：%s"
            "（事件节点必须能取回原文，所以来源要真实存在；只想记一条文字约定请用 add 写 R 行）"
            % _show(args.src, 80))

    data = _read_bytes(src_abs)
    nbytes = len(data)
    sha = _sha256(data)
    summary = args.summary if args.summary is not None else os.path.basename(src_abs)
    _check_summary(summary, "--summary")
    tag = args.tag
    _check_tag(tag, "--tag")

    stored_src = _relativize(args.file, src_abs)

    if args.embed:
        if nbytes > MAX_ATOM_BYTES:
            raise CtxError("--embed 拒绝：原文件 %d 字节超过上限 %d 字节；"
                           "这类大块请改用默认的库外指针（库里占一行，原文留原地）"
                           % (nbytes, MAX_ATOM_BYTES))
        dst = _embed_path(args.file, sha, stored_src)
        directory = os.path.dirname(dst)
        if not os.path.isdir(directory):
            try:
                os.makedirs(directory)
            except OSError as exc:
                raise CtxError("无法创建内嵌目录 %s：%s" % (directory, exc.strerror or exc))
        if not os.path.isfile(dst):
            try:
                with open(dst, "wb") as fh:
                    fh.write(data)
            except OSError as exc:
                raise CtxError("无法写入内嵌正文 %s：%s" % (dst, exc.strerror or exc))
        print("NOTE embed: 正文已复制到 %s（内容寻址，同哈希只存一份）"
              % os.path.relpath(dst, os.path.dirname(os.path.abspath(args.file))).replace("\\", "/"))

    delta = [
        "%s %s" % (SIGIL_TOPIC, args.topic),
        "%s %s" % (SIGIL_PURPOSE, args.purpose),
        "%s %s | src:%s | bytes:%d | sha256:%s%s"
        % (SIGIL_EVENT, summary, stored_src, nbytes, sha, _fmt_tag(tag)),
    ]

    before_key = _body_key(top_text, topics, meta.get("scope"))
    stat, notes = apply_delta(topics, delta)
    for note in notes:
        print("NOTE %s" % note)

    n_t, n_p, n_r, n_e = _counts4(topics)
    if _body_key(top_text, topics, meta.get("scope")) == before_key:
        print("OK event %s | 内容未变化，未写盘 rev=%d | now T%d P%d R%d E%d"
              % (_fmt_stat(stat), meta["rev"], n_t, n_p, n_r, n_e))
        return EXIT_OK
    meta["rev"] += 1
    save(args.file, meta, top_text, topics)
    print("OK event %s rev=%d | now T%d P%d R%d E%d | src=%s | bytes=%d | sha256=%s"
          % (_fmt_stat(stat), meta["rev"], n_t, n_p, n_r, n_e, stored_src, nbytes, sha))
    return EXIT_OK


# --------------------------------------------------------------------------
# 门控（plan）：该压进库，还是该整块当一个事件节点
# --------------------------------------------------------------------------
#
# 净省 = 1 − (索引层 I + 任务工作集 W) / 原始素材 T
#   库级断点：W/T < 1 − I/T  → 用局部视图；否则建库是负收益，整块当事件节点。
#   块级断点：k > 1/(1−r)    → 「读完再压」口径下，读过的块就压存（r→1 者除外）。
#
# 这里只用标准库：装了 tiktoken 就按 token 精确计，没装就退化为字符口径。
# **比例是量纲无关的**，所以判定结论不因换口径而改变。

def _make_counter():
    try:
        import tiktoken                                    # 可选加速，不是依赖
        enc = tiktoken.get_encoding("cl100k_base")
        return "token(cl100k_base)", (lambda s: len(enc.encode(s)))
    except Exception:
        return "字符（未装 tiktoken；比例判定不受影响）", (lambda s: len(s))


_PLAN_TEXT_EXT = (".md", ".py", ".json", ".ctx", ".txt", ".yaml", ".yml",
                  ".js", ".ts", ".html", ".css", ".go", ".rs", ".java", ".c", ".h")


def _plan_walk(root):
    found = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns
                        if d not in (".git", "__pycache__", ".workbuddy", "node_modules"))
        for fn in sorted(fns):
            if fn.endswith(_PLAN_TEXT_EXT):
                found.append(os.path.join(dp, fn))
    return sorted(found)


def _plan_nav_units(files, count):
    """索引导航层的近似口径：只数「标题 / def / class / 键名」这类导航行。

    真实 I 应在**已建库**上由 index 实测；这里针对「还没有库」的原始语料，
    用导航行代替键层名。口径是近似的，但两侧用同一把尺，比例仍可用。
    """
    buf = []
    for p in files:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith("#") or s.startswith("def ") \
                            or s.startswith("class ") or s.startswith("func "):
                        buf.append(s)
        except OSError:
            continue
    return count("\n".join(buf))


def cmd_plan(args):
    if args.break_even:
        print("块级断点（读完再压口径）：k > 1 / (1 - r)     r = 压缩后 / 原始")
        print("%10s %12s   %s" % ("压缩比 r", "需要复用 k≥", "典型素材"))
        print("-" * 60)
        import math
        for r, note in ((0.05, "摘要/结论"), (0.10, "讨论记录"), (0.20, "会议纪要"),
                        (0.30, "需求文档"), (0.50, "半压缩"), (0.70, "偏原文"),
                        (0.80, "接近原文"), (0.90, "代码片段"), (0.95, "几乎压不动"),
                        (0.99, "必须逐字看的对象")):
            print("%9.2f %12d   %s" % (r, int(math.floor(1.0 / (1.0 - r))) + 1, note))
        print("-" * 60)
        print("r → 1 时 k → ∞：**必须逐字看到的东西，压缩永远不划算**。")
        print("推论：读完再压之所以划算，是因为原料读取成本本来就要付；")
        print("      所以规则退化为「读过的块就压存」，不需要预估 k，也不需要观测 k。")
        if not args.root:
            return EXIT_OK
        print()

    if not args.root:
        raise CtxError("plan 需要 --root <语料目录>"
                       "（只看块级断点表请加 --break-even 并省略 --root）")
    if not os.path.isdir(args.root):
        raise CtxError("--root 不是已存在的目录：%s" % args.root)

    label, count = _make_counter()
    root = os.path.abspath(args.root)
    files = _plan_walk(root)
    if not files:
        raise CtxError("%s 下没有可统计的文本文件" % root)

    per_file = []
    for p in files:
        try:
            per_file.append((p, count(open(p, "r", encoding="utf-8", errors="replace").read())))
        except OSError:
            continue
    T = sum(t for _, t in per_file)
    if T <= 0:
        raise CtxError("%s 下的文本文件合计为 0，无法判定" % root)
    I = _plan_nav_units(files, count)

    work = []
    if args.work:
        want = set()
        for part in args.work.split(","):
            part = part.strip()
            if part:
                want.add(os.path.normpath(part if os.path.isabs(part) else os.path.join(root, part)))
        work = [(p, t) for p, t in per_file if os.path.normpath(p) in want]
        missing = sorted(want - {os.path.normpath(p) for p, _ in per_file})
    else:
        missing = []
    W = sum(t for _, t in work)

    ratio = W / float(T)
    break_even = 1.0 - (I / float(T))
    use_store = ratio <= break_even

    print("=" * 70)
    print("读取源替换门控判定（口径：%s）" % label)
    print("=" * 70)
    print("语料根            : %s" % root)
    print("文本文件数        : %d" % len(per_file))
    print("全量 T            : %d" % T)
    print("索引导航层 I      : %d  (占 T 的 %.1f%%)" % (I, 100.0 * I / T))
    print("库级断点 1−I/T    : %.1f%%   ← 工作集超过它就不该建库" % (100.0 * break_even))
    print("-" * 70)
    if not args.work:
        print("未给 --work：只报告语料规模与库级断点。")
        print("=" * 70)
        return EXIT_OK
    print("工作集 W          : %d  (占 T 的 %.1f%%)" % (W, 100.0 * ratio))
    for m in missing:
        print("NOTE 清单里没找到的文件（判定按实际命中的 %d 个算）：%s"
              % (len(work), _show(os.path.relpath(m, root).replace("\\", "/"), 60)))
    print("全贴原文          : %d" % T)
    print("索引 + 工作集     : %d" % (I + W))
    delta = 100.0 * (1.0 - (I + W) / float(T))
    print("净省              : %+.1f%%" % delta)
    print("-" * 70)
    if use_store:
        print("判定：STORE —— 工作集 %.1f%% ≤ 断点 %.1f%%，用局部视图（索引+按需取支）。"
              % (100.0 * ratio, 100.0 * break_even))
    else:
        print("判定：INLINE —— 工作集 %.1f%% > 断点 %.1f%%，建库是负收益（净省 %+.1f%%）。"
              % (100.0 * ratio, 100.0 * break_even, delta))
        print("      此时应把整块当成**一个完整事件节点**处理（登记指针，不拆键）。")
    print()
    print("块级复核（库级判定不替代它）：")
    print("  %-56s %10s %8s" % ("文件", "规模", "占比"))
    for p, t in sorted(work, key=lambda x: -x[1]):
        print("  %-56s %10d %7.1f%%"
              % (os.path.relpath(p, root).replace("\\", "/"), t, 100.0 * t / T))
    print("  —— 凡「必须逐字操作」的块（要改的代码 / 要核的数据）一律登记事件节点，")
    print("     与占比无关；只有能压到 r<0.5 的块才拆键压存。")
    print("=" * 70)
    return EXIT_OK


def cmd_tree(args):
    meta, top_text, topics = load(args.file)
    picked = _select(topics, args.topic, args.purpose)
    if top_text:
        print("◆ %s" % top_text)
    for t, ps in picked:
        tag = "" if t["tag"] is None else " [%s]" % t["tag"]
        print("├─ %s %s%s" % (t["id"], t["name"], tag))
        for p in ps:
            ptag = "" if p["tag"] is None else " [%s]" % p["tag"]
            print("│  ├─ %s %s%s" % (p["id"], p["name"], ptag))
            for it in p["items"]:
                itag = "" if it["tag"] is None else " [%s]" % it["tag"]
                if _kind(it) == "E":
                    print("│  │  ◆ E %s%s  ← %s (%d 字节, sha %s)"
                          % (it["text"], itag, it["src"], it["bytes"], it["sha"]))
                else:
                    print("│  │  · %s%s" % (it["text"], itag))
    return EXIT_OK


def cmd_validate(args):
    meta, top_text, topics = load(args.file)
    n_t, n_p, n_r, n_e = _counts4(topics)
    if n_e:
        n_bytes = sum(it["bytes"] for _t, _p, it in _iter_events(topics))
        bad = []
        stale = []
        for t, p, it in _iter_events(topics):
            _data, status, _desc, src_state = _load_atom(args.file, it)
            if status != "ok":
                bad.append("%s / %s / E %s（%s）" % (t["id"], p["id"], it["text"],
                                                    "漂移" if status == "drift" else "原文缺失"))
            elif src_state != "ok":
                stale.append("%s / %s / E %s（%s）" % (t["id"], p["id"], it["text"],
                                                      "来源已改写" if src_state == "drift" else "来源已不存在"))
        print("OK valid %s | rev %d | T%d P%d R%d E%d | 事件节点指向 %d 字节原文"
              % (args.file, meta["rev"], n_t, n_p, n_r, n_e, n_bytes))
        for b in bad:
            print("WARN 事件节点无法校验：%s" % b)
        for s in stale:
            print("WARN 事件节点的取回内容可用，但来源指针已失效：%s" % s)
        if bad or stale:
            print("NOTE 结构合法；上述问题是**内容层**的，校验只读，不修改任何文件")
            print("NOTE 要逐条确认请用 `fetch --check`（漂移或缺失会返回非零退出码）")
    else:
        print("OK valid %s | rev %d | T%d P%d R%d" % (args.file, meta["rev"], n_t, n_p, n_r))
    return EXIT_OK


def cmd_stats(args):
    meta, top_text, topics = load(args.file)
    n_t, n_p, n_r, n_e = _counts4(topics)
    text = render(meta, top_text, topics)
    chars = len(text)
    lines = text.count("\n")
    total = n_t + n_p + n_r + n_e
    print("nodes  T=%d P=%d R=%d E=%d  total=%d" % (n_t, n_p, n_r, n_e, total))
    print("size   chars=%d lines=%d" % (chars, lines))
    if n_e:
        # 事件节点的价值口径：库内只花多少，换回多少字节原文。
        ref_bytes = sum(it["bytes"] for _t, _p, it in _iter_events(topics))
        line_chars = sum(len(_fmt_item(it)) + 5 for _t, _p, it in _iter_events(topics))
        print("atoms  %d 个事件节点引用 %d 字节原文，库内只占 %d 字符"
              % (n_e, ref_bytes, line_chars))
        if ref_bytes and line_chars:
            print("       = 库内每 1 字符指向 %.1f 字节原文（原文不进窗口，取回才付费）"
                  % (ref_bytes / float(line_chars)))
    if args.raw_chars is not None:
        if args.raw_chars <= 0:
            raise CtxError("--raw-chars 必须为正整数")
        ratio = 1.0 - (chars / float(args.raw_chars))
        print("raw    chars=%d" % args.raw_chars)
        print("ratio  %.1f%% (1 - store/raw)" % (ratio * 100.0))
    return EXIT_OK


def _check_date(text, where):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text or ""):
        raise CtxError("%s：日期必须写成 YYYY-MM-DD（实测 %r）" % (where, _show(text or "", 24)))
    try:
        _Date(int(text[:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        raise CtxError("%s：日期不存在：%s" % (where, text))


def _date_span(from_date, to_date):
    """返回 to - from 的天数。两个日期都由调用方保证已校验过格式。"""
    a = _Date(int(from_date[:4]), int(from_date[5:7]), int(from_date[8:10]))
    b = _Date(int(to_date[:4]), int(to_date[5:7]), int(to_date[8:10]))
    return (b - a).days


def _emit_promote(path, candidates):
    """把升格候选写成一份标准增量，可直接喂给目标作用域的库。

    刻意只做「搬运内容」这一件确定性的事：不删源库、不猜目标路径。
    跨库动作必须由调用方显式执行，避免内核产生看不见的跨文件副作用。
    """
    lines = [
        "// 由 ctx.py prune --aging --emit-promote 生成的升格增量",
        "// 里面是标记为 [%s] 且已超过老化阈值的条目 —— 它们看起来值得在更持久的作用域里生效。" % TAG_LONG,
        "// 用法：ctx.py add --file <目标库路径> --stdin < 本文件",
        "// 注意：本文件只搬运内容，源库副本不会被自动删除；请在目标库确认无误后再自行处理源库那份。",
    ]
    for t, p, it in candidates:
        lines.append("%s %s" % (SIGIL_TOPIC, t["name"]))
        lines.append("%s %s" % (SIGIL_PURPOSE, p["name"]))
        lines.append("%s %s [%s]" % (SIGIL_REQUEST, it["text"], TAG_LONG))
    directory = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(directory):
        raise CtxError("--emit-promote 的目标目录不存在：%s" % directory)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        raise CtxError("无法写入 %s：%s" % (path, exc.strerror or exc))
    print("OK emit-promote -> %s（%d 个升格候选）" % (path, len(candidates)))


def cmd_prune(args):
    meta, top_text, topics = load(args.file)
    if not (args.drop_stale or args.collapse_done or args.compact or args.aging):
        raise CtxError("prune 需要至少一个动作：--drop-stale / --collapse-done / --compact / --aging")
    if not args.force:
        raise CtxError("prune 会不可逆地删除已有内容，必须显式加 --force 确认")

    before_key = _body_key(top_text, topics, meta.get("scope"))
    removed_r = 0
    removed_e = 0
    removed_p = 0
    removed_t = 0
    aged_r = 0
    pinned_r = 0
    untagged_r = 0
    kept_atom = 0
    candidates = []

    if args.drop_stale:
        # 唯一允许删除事件节点的动作：它得被显式标成 [已废]。这是「显式处置」而非自动老化。
        for t in topics:
            for p in t["purposes"]:
                keep = [it for it in p["items"] if it["tag"] != "已废"]
                gone = [it for it in p["items"] if it["tag"] == "已废"]
                removed_r += sum(1 for it in gone if _kind(it) == "R")
                removed_e += sum(1 for it in gone if _kind(it) == "E")
                p["items"] = keep

    if args.collapse_done:
        # 保留键位（T 行与 P 行都在），只清空**要求层**——日后仍能看出「这事做过」。
        # 事件节点不随之消失：原文不可再生，折叠掉它等于把这条路堵死。
        for t in topics:
            if t["tag"] == "已结":
                for p in t["purposes"]:
                    # 保留的判据写「是 E」而不是「不是 R」：后者会在第三种叶子出现时
                    # 把它一并删掉（折叠是不可逆的）。_kind() 会在未知种类上先报错，
                    # 所以这里的白名单是安全的 —— 新增行类型时必须在这里显式表态。
                    kept = [it for it in p["items"] if _kind(it) == "E"]
                    removed_r += len(p["items"]) - len(kept)
                    if len(kept) != len(p["items"]):
                        kept_atom += len(kept)
                    p["items"] = kept
                t["tag"] = "已结·已折叠"
        if kept_atom:
            print("NOTE 折叠保留了 %d 个事件节点（原文不可再生，要处置请显式标 [已废] 后 --drop-stale）"
                  % kept_atom)

    if args.compact:
        for i, t in enumerate(topics, start=1):
            t["num"] = i
            t["id"] = "T%d" % i
            for j, p in enumerate(t["purposes"], start=1):
                p["num"] = "%d.%d" % (i, j)
                p["id"] = "P%s" % p["num"]

    if args.aging:
        # 天数不能由脚本猜：给不出 --today 就拒绝执行，否则同一命令不同天跑出不同结果。
        if not args.today:
            raise CtxError("--aging 必须显式给出 --today YYYY-MM-DD（日期不能由脚本猜，否则结果不可复现）")
        _check_date(args.today, "--today")
        if args.days is None:
            raise CtxError("--aging 必须显式给出 --days N（老化阈值天数）")
        if args.days < 0:
            raise CtxError("--days 不能为负数（实测 %d）" % args.days)
        age = _date_span(meta["date"], args.today)
        if age < 0:
            raise CtxError("--today（%s）早于存储日期（%s），时间倒流：请核对参数" % (args.today, meta["date"]))
        over = age >= args.days
        for t in topics:
            for p in t["purposes"]:
                kept = []
                for it in p["items"]:
                    if it.get("kind") == "E":
                        # 事件节点**不参与**老化折叠：它是回原文的唯一把手，
                        # 折掉它等于让那块素材彻底不可达。只允许显式标 [已废] 后删除。
                        kept_atom += 1
                        kept.append(it)
                        continue
                    tag = it["tag"]
                    if tag == TAG_PINNED:
                        pinned_r += 1
                        kept.append(it)
                    elif tag == TAG_TRANSIENT and over:
                        aged_r += 1
                        removed_r += 1
                    else:
                        if tag == TAG_LONG and over:
                            candidates.append((t, p, it))
                        if tag is None:
                            untagged_r += 1
                        kept.append(it)
                p["items"] = kept
        print("AGE date=%s today=%s days=%d over=%s | 距 %d 天"
              % (meta["date"], args.today, args.days, "yes" if over else "no", age))
        print("AGE 折叠暂态 %d 条 · 钉住跳过 %d 条 · 无标记保留 %d 条 · 升格候选 %d 条 · 事件节点另存 %d 个"
              % (aged_r, pinned_r, untagged_r, len(candidates), kept_atom))
        if not over:
            print("NOTE 未达老化阈值（%d 天 < %d 天）：本次没有折叠任何 [%s] 条目"
                  % (age, args.days, TAG_TRANSIENT))
        for t, p, it in candidates:
            print("PROMOTE-CANDIDATE %s / %s / %s" % (t["id"], p["id"], it["text"]))
        if args.emit_promote:
            if candidates:
                _emit_promote(args.emit_promote, candidates)
            else:
                print("NOTE 没有升格候选，未生成引入文件：%s" % args.emit_promote)

    n_t, n_p, n_r, n_e = _counts4(topics)
    if _body_key(top_text, topics, meta.get("scope")) == before_key:
        print("OK pruned no-op | 内容未变化，未写盘 rev=%d | now T%d P%d R%d E%d"
              % (meta["rev"], n_t, n_p, n_r, n_e))
        return EXIT_OK
    meta["rev"] += 1
    save(args.file, meta, top_text, topics)
    print("OK pruned -%dT -%dP -%dR%s rev=%d | now T%d P%d R%d E%d"
          % (removed_t, removed_p, removed_r,
             "" if not removed_e else " -%dE" % removed_e,
             meta["rev"], n_t, n_p, n_r, n_e))
    return EXIT_OK


def _export_item(it):
    """导出用条目表示。事件节点带出指针与指纹，仍然**不带正文**。"""
    if _kind(it) == "E":
        return {"kind": "E", "text": it["text"], "tag": it["tag"],
                "src": it["src"], "bytes": it["bytes"], "sha256": it["sha"]}
    return {"kind": "R", "text": it["text"], "tag": it["tag"]}


def cmd_export(args):
    meta, top_text, topics = load(args.file)
    if args.format == "json":
        data = {
            "format": "context-lattice",
            "version": int(meta["version"]),
            "date": meta["date"],
            "rev": meta["rev"],
            "scope": meta.get("scope"),
            "top": top_text,
            "topics": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "tag": t["tag"],
                    "purposes": [
                        {
                            "id": p["id"],
                            "name": p["name"],
                            "tag": p["tag"],
                            "items": [_export_item(it) for it in p["items"]],
                        }
                        for p in t["purposes"]
                    ],
                }
                for t in topics
            ],
        }
        out = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    elif args.format == "md":
        rows = []
        if top_text:
            rows.append("# %s" % top_text)
            rows.append("")
        for t in topics:
            ttag = "" if t["tag"] is None else " `%s`" % t["tag"]
            rows.append("## %s %s%s" % (t["id"], t["name"], ttag))
            rows.append("")
            for p in t["purposes"]:
                ptag = "" if p["tag"] is None else " `%s`" % p["tag"]
                rows.append("- **%s %s**%s" % (p["id"], p["name"], ptag))
                for it in p["items"]:
                    itag = "" if it["tag"] is None else " `%s`" % it["tag"]
                    if it.get("kind") == "E":
                        rows.append("  - ◆ E %s%s ← `%s` (%d 字节, sha256:%s)"
                                    % (it["text"], itag, it["src"], it["bytes"], it["sha"]))
                    else:
                        rows.append("  - %s%s" % (it["text"], itag))
            rows.append("")
        out = "\n".join(rows).rstrip("\n") + "\n"
    else:
        raise CtxError("--format 只支持 json 或 md")

    if args.out:
        directory = os.path.dirname(os.path.abspath(args.out)) or "."
        if not os.path.isdir(directory):
            raise CtxError("导出目录不存在：%s" % directory)
        try:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
        except OSError as exc:
            raise CtxError("无法写入 %s：%s" % (args.out, exc.strerror or exc))
        print("OK export %s -> %s (%d chars)" % (args.format, args.out, len(out)))
    else:
        sys.stdout.write(out)
    return EXIT_OK


# --------------------------------------------------------------------------
# 跨库索引与定位（记忆的「全库找得到」那一半）
# --------------------------------------------------------------------------

def _iter_stores(root):
    """递归找 .ctx（跳过隐藏目录与临时文件），返回排序后的路径列表。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if fn.endswith(".ctx") and not fn.endswith(".tmp"):
                found.append(os.path.join(dirpath, fn))
    return sorted(found)


def _scope_mismatch(root, path, scope):
    """目录暗示的作用域与头部声明不一致 → True。

    只是**提示**，不是错误：目录约定属于宿主适配层，内核不强制。
    能发现「文件被放错地方」比静默读错记忆更安全。
    """
    if not scope:
        return False
    rel = os.path.relpath(path, root).replace("\\", "/")
    parts = rel.split("/")
    if len(parts) < 2:
        return False                     # 直接放在 root 下，没有目录提示可用
    kind = scope_kind(scope)
    return parts[0] in SCOPE_KINDS and parts[0] != kind


def _scan_stores(root):
    """扫描 root 下所有可解析的库，返回 (entries, skipped)。

    entries: [(abspath, meta, top_text, topics)]，已按「作用域持久度 → 限定名 → 相对路径」排序。
    skipped: [(abspath, 受控错误信息)] —— 坏库只跳过并上报，绝不中断整次扫描。
    """
    entries, skipped = [], []
    for p in _iter_stores(root):
        try:
            meta, top_text, topics = load(p)
        except CtxError as exc:
            skipped.append((p, str(exc)))
            continue
        entries.append((p, meta, top_text, topics))

    def key(e):
        p, meta, _t, _ts = e
        kind = scope_kind(meta.get("scope"))
        order = SCOPE_KINDS.index(kind) if kind in SCOPE_KINDS else len(SCOPE_KINDS)
        return (order, meta.get("scope") or "", os.path.relpath(p, root).replace("\\", "/"))

    entries.sort(key=key)
    return entries, skipped


def _report_skipped(root, skipped):
    """坏库统一报到 stderr，前缀 NOTE 以示「非致命」，不影响退出码。"""
    for p, reason in skipped:
        sys.stderr.write("NOTE 跳过无法解析的库：%s（%s）\n"
                         % (os.path.relpath(p, root).replace("\\", "/"), reason))


def cmd_index(args):
    if not os.path.isdir(args.root):
        raise CtxError("--root 不是已存在的目录：%s" % args.root)
    _check_date(args.date, "--date")
    if args.kind and args.kind not in SCOPE_KINDS:
        raise CtxError("--kind 只能是 %s 之一（实测 %s）" % (" / ".join(SCOPE_KINDS), _show(args.kind, 16)))
    root = os.path.abspath(args.root)

    entries, skipped = _scan_stores(root)
    if args.kind:
        entries = [e for e in entries if scope_kind(e[1].get("scope")) == args.kind]

    n_t = sum(len(e[3]) for e in entries)
    n_p = sum(sum(len(t["purposes"]) for t in e[3]) for e in entries)

    lines = ["#IDX %s | %s | stores %d | T%d P%d" % (IDX_VERSION, args.date, len(entries), n_t, n_p)]
    mismatched = 0
    for p, meta, _top, topics in entries:
        rel = os.path.relpath(p, root).replace("\\", "/")
        bad = _scope_mismatch(root, p, meta.get("scope"))
        if bad:
            mismatched += 1
        lines.append("S %s | %s | rev %d | date %s | T%d P%d%s"
                     % (meta.get("scope") or "-", rel, meta["rev"], meta["date"],
                        len(topics), sum(len(t["purposes"]) for t in topics),
                        " | mismatch" if bad else ""))
        for t in topics:
            lines.append("  T%d %s" % (t["num"], t["name"]))
            for pp in t["purposes"]:
                lines.append("    P%s %s" % (pp["num"], pp["name"]))
                for it in pp["items"]:
                    if it.get("kind") == "E":
                        # 事件节点只进「摘要 + 指针」——索引里出现正文就等于把库撑大
                        lines.append("      E %s | src:%s | bytes:%d | sha256:%s"
                                     % (it["text"], it["src"], it["bytes"], it["sha"]))
    text = "\n".join(lines) + "\n"

    _report_skipped(root, skipped)

    if args.out:
        directory = os.path.dirname(os.path.abspath(args.out)) or "."
        if not os.path.isdir(directory):
            raise CtxError("--out 的目标目录不存在：%s" % directory)
        old = None
        if os.path.exists(args.out):
            try:
                with open(args.out, "r", encoding="utf-8", newline="") as fh:
                    old = fh.read()
            except OSError:
                old = None
        if old == text:
            print("OK index -> %s | 索引未变化，未写盘 | stores %d T%d P%d"
                  % (args.out, len(entries), n_t, n_p))
        else:
            try:
                with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            except OSError as exc:
                raise CtxError("无法写入 %s：%s" % (args.out, exc.strerror or exc))
            print("OK index -> %s | stores %d T%d P%d" % (args.out, len(entries), n_t, n_p))
    else:
        sys.stdout.write(text)

    if mismatched:
        sys.stderr.write("NOTE 有 %d 个库的作用域声明与所在目录不一致（已标 mismatch）\n" % mismatched)
    sys.stderr.write("NOTE 本次扫描 %d 个库，跳过 %d 个（只统计条目，不影响退出码）\n"
                     % (len(entries) + len(skipped), len(skipped)))
    return EXIT_OK


def cmd_find(args):
    if not os.path.isdir(args.root):
        raise CtxError("--root 不是已存在的目录：%s" % args.root)
    if not args.topic and not args.purpose:
        raise CtxError("find 需要至少一个条件：--topic 或 --purpose")
    if args.kind and args.kind not in SCOPE_KINDS:
        raise CtxError("--kind 只能是 %s 之一（实测 %s）" % (" / ".join(SCOPE_KINDS), _show(args.kind, 16)))
    root = os.path.abspath(args.root)

    def matches(name, node_id, want):
        """--exact 要求相等；默认是「编号相等 或 名称包含关键词」。

        名称包含对 ASCII 关键词走大小写不敏感（英文键名写法常有大小写差异），
        对中文等非 ASCII 关键词只做原样子串判定（中文没有大小写，lower() 也无副作用）。
        """
        n, w = _norm(name), _norm(want)
        if args.exact:
            return n == w or _norm(node_id) == w
        if _norm(node_id) == w:
            return True
        return w.lower() in n.lower()

    entries, skipped = _scan_stores(root)
    if args.kind:
        entries = [e for e in entries if scope_kind(e[1].get("scope")) == args.kind]

    hits = []
    for p, meta, _top, topics in entries:
        rel = os.path.relpath(p, root).replace("\\", "/")
        scope = meta.get("scope") or "-"
        for t in topics:
            t_ok = (not args.topic) or matches(t["name"], t["id"], args.topic)
            for pp in t["purposes"]:
                p_ok = (not args.purpose) or matches(pp["name"], pp["id"], args.purpose)
                if args.purpose:
                    if t_ok and p_ok:
                        hits.append((scope, rel, t, pp))
                else:
                    if t_ok:
                        hits.append((scope, rel, t, pp))

    for scope, rel, t, pp in hits:
        print("HIT %s | %s | %s %s | %s %s" % (scope, rel, t["id"], t["name"], pp["id"], pp["name"]))

    _report_skipped(root, skipped)
    cond = []
    if args.topic:
        cond.append("topic=%s" % _show(args.topic, 24))
    if args.purpose:
        cond.append("purpose=%s" % _show(args.purpose, 24))
    if args.kind:
        cond.append("kind=%s" % args.kind)
    print("OK find %d 命中（扫描 %d 个库，跳过 %d 个；条件 %s%s）"
          % (len(hits), len(entries) + len(skipped), len(skipped), " ".join(cond),
             "，精确匹配" if args.exact else ""))
    if not hits:
        print("NOTE 0 命中就是 0 命中：不要据此推断「库里没有相关内容以外的结论」")
    return EXIT_OK


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", "-f", required=True, help=".ctx 存储文件路径")

    p = argparse.ArgumentParser(
        prog="ctx.py",
        description="上下文双层键压缩存储（Context Lattice）内核：T 事物 / P 目的 / R 要求。",
    )
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", parents=[common], help="创建空存储（必须给出起始事物）")
    sp.add_argument("--top", default=None, help="会话总目标（可选单行）")
    sp.add_argument("--date", required=True, help="会话日期 YYYY-MM-DD（固定值，保证可复现）")
    sp.add_argument("--scope", default=None,
                    help="作用域：user / proj / task，可带限定名（proj:demo）。缺省=未声明")
    sp.add_argument("--stdin", action="store_true", help="从标准输入读 T/P/R 增量建库")
    sp.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add", parents=[common], help="并入本轮增量（幂等归并）")
    sp.add_argument("--topic", help="事物名称")
    sp.add_argument("--purpose", help="目的名称")
    sp.add_argument("--item", help="要求/描述内容")
    sp.add_argument("--tag", help="行尾标记，如 约束 / 待办 / 已废 / 钉住 / 长期 / 暂态")
    sp.add_argument("--scope", default=None,
                    help="改本库作用域（如 --scope proj）；写 `-` 表示清除已声明的作用域")
    sp.add_argument("--stdin", action="store_true", help="从标准输入读 T/P/R 增量")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("read", parents=[common], help="渲染读取视图（模型直接读这个）")
    sp.add_argument("--topic", help="只输出某个事物（T 编号或名称）")
    sp.add_argument("--purpose", help="只输出某个目的（P 编号或名称）")
    sp.add_argument("--level", type=int, default=3, help="1=仅事物 2=事物+目的 3=全量")
    sp.add_argument("--budget", type=int, default=0, help="字符预算，超出则裁剪 R 层")
    sp.add_argument("--fetch", action="store_true",
                    help="改为取回所选范围内事件节点的**原文**并校验哈希（默认视图只给摘要与指针）")
    sp.add_argument("--no-header", action="store_true", help="不输出头部与 top 行")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("event", parents=[common],
                        help="登记事件节点（原子节点）：整块压不动的素材只存指针，原文不进库")
    sp.add_argument("--topic", required=True, help="挂在哪个事物下")
    sp.add_argument("--purpose", required=True, help="挂在哪个目的下")
    sp.add_argument("--src", required=True, help="原文路径（必须真实存在；默认只存指针）")
    sp.add_argument("--summary", default=None, help="摘要，缺省用文件名")
    sp.add_argument("--tag", default=None, help="行尾标记：钉住 / 长期 / 暂态 / 已废 …")
    sp.add_argument("--embed", action="store_true",
                    help="把正文复制到 <库文件>.atoms/<sha>（库自包含，代价是库体变大）")
    sp.set_defaults(func=cmd_event)

    sp = sub.add_parser("fetch", parents=[common],
                        help="取回事件节点原文并校验哈希（漂移或缺失 → 退出码 1）")
    sp.add_argument("--topic", default=None, help="限定某个事物")
    sp.add_argument("--purpose", default=None, help="限定某个目的")
    sp.add_argument("--summary", default=None, help="按摘要精确定位（归一化空白后比较）")
    sp.add_argument("--out", default=None, help="写出到文件（要求恰好命中 1 个事件节点）")
    sp.add_argument("--check", action="store_true", help="只校验，不输出正文")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("tree", parents=[common], help="框线树形视图（给人看）")
    sp.add_argument("--topic")
    sp.add_argument("--purpose")
    sp.set_defaults(func=cmd_tree)

    sp = sub.add_parser("validate", parents=[common], help="结构校验（检查器）")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("stats", parents=[common], help="节点计数与压缩率")
    sp.add_argument("--raw-chars", type=int, default=None, help="原始对话字符数，用于算压缩率")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("prune", parents=[common], help="折叠、老化与降级（不可逆，需 --force）")
    sp.add_argument("--drop-stale", action="store_true", help="删除标记为 已废 的要求")
    sp.add_argument("--collapse-done", action="store_true", help="折叠标记为 已结 的事物的要求层")
    sp.add_argument("--compact", action="store_true", help="重排 T/P 编号（引用会失效）")
    sp.add_argument("--aging", action="store_true",
                    help="按生命周期标记老化：暂态超期折叠、长期超期列为升格候选、钉住永不动")
    sp.add_argument("--days", type=int, default=None, help="老化阈值天数（--aging 必给）")
    sp.add_argument("--today", default=None, help="今天 YYYY-MM-DD（--aging 必给，避免不可复现）")
    sp.add_argument("--emit-promote", default=None,
                    help="把升格候选写成一份标准增量文件，可直接喂给目标作用域的库")
    sp.add_argument("--force", action="store_true", help="确认执行不可逆删除")
    sp.set_defaults(func=cmd_prune)

    sp = sub.add_parser("export", parents=[common], help="导出 JSON 或 Markdown 供外部工具消费")
    sp.add_argument("--format", required=True, choices=["json", "md"])
    sp.add_argument("--out", default=None, help="输出路径，缺省写标准输出")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("index", help="跨库索引：只索引 key 层（T/P 名），供模型先读索引再取子树")
    sp.add_argument("--root", required=True, help="记忆根目录，递归扫描其中的 .ctx")
    sp.add_argument("--date", required=True, help="构建日期 YYYY-MM-DD（固定值，保证可复现）")
    sp.add_argument("--kind", default=None, help="只索引某类作用域：user / proj / task")
    sp.add_argument("--out", default=None, help="索引输出路径；缺省打印到标准输出")
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("find", help="跨库定位：在记忆根目录下找某个事物或目的（只读）")
    sp.add_argument("--root", required=True, help="记忆根目录，递归扫描其中的 .ctx")
    sp.add_argument("--topic", default=None, help="按事物找（编号或名称片段）")
    sp.add_argument("--purpose", default=None, help="按目的找（编号或名称片段）")
    sp.add_argument("--kind", default=None, help="只看某类作用域：user / proj / task")
    sp.add_argument("--exact", action="store_true", help="精确匹配（默认是包含匹配）")
    sp.set_defaults(func=cmd_find)

    sp = sub.add_parser("plan",
                        help="门控判定：该压进库用局部视图，还是该整块当一个事件节点（只读）")
    sp.add_argument("--root", default=None, help="语料根目录（只看断点表时可省略）")
    sp.add_argument("--work", default=None,
                    help="本任务的工作集文件清单（逗号分隔，相对 --root；省略则只报语料规模）")
    sp.add_argument("--break-even", action="store_true", help="打印块级断点表 k > 1/(1−r)")
    sp.set_defaults(func=cmd_plan)

    return p


def _ensure_utf8_console():
    """把三个标准流固定到 UTF-8，避免中文在非 UTF-8 环境里崩掉或**被静默读坏**。

    起因是一个真实缺陷：Windows 控制台下 Python 走 PEP 528 的 UTF-8 通道，本来没问题；
    但只要标准流接了管道或被重定向，编码就退回系统本地编码（英文版 Windows 是
    cp1252，中文版是 cp936），于是

    - **输出侧**：中文一打印就抛 UnicodeEncodeError，`read` / `index` / `plan` 等命令
      整体失败，只回一句「未预期的失败」；
    - **输入侧**：`add --stdin < delta.txt` 把 UTF-8 字节按本地编码解 —— 在 cp1252 上
      报 UnicodeDecodeError，在 cp936 上更糟：解成乱码**不报错**，乱码直接写进库。
      后者意味着「同输入 → 同存储」这条确定性在每个平台上并不成立。

    而「管道进、管道出」恰恰是本工具的主要用法（喂给下游程序或模型），CI 的
    Windows runner 也是这种情况。所以三个流统一按 UTF-8 处理 —— 这也是本格式
    自身的编码（.ctx、导出的 JSON/MD、随包的 delta.txt 模板全是 UTF-8）。

    错误处理器分两种，不是随手写的：
    - 输出用 `backslashreplace`：不可编码的字符要留**可见且可逆**的痕迹，不静默丢字。
    - 输入保持 `strict`：输入解错会**写坏库**，宁可当场报错也不静默掺入替换字符
      （替换字符 U+FFFD 正是本项目明令禁止落盘的东西）。报错措辞见 `_read_stdin`。

    reconfigure 自 Python 3.7 起可用；若流已被包装成没有该方法的对象，就保持原样不动。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass


def _read_stdin():
    """读标准输入；解不出来时给可行动的报错，而不是裸的 UnicodeDecodeError。

    管道里传的增量一律按 UTF-8 解读。Windows 上常见的 cp936/GBK 文本要先转码 ——
    这句话必须出现在报错里，否则用户只会看到「未预期的失败」而无从下手。
    """
    try:
        return sys.stdin.read()
    except UnicodeDecodeError as exc:
        raise CtxError(
            "标准输入不是合法 UTF-8（%s）。本工具的增量输入一律按 UTF-8 解读，"
            "请确认管道里传的是 UTF-8 文本；Windows 上的 cp936/GBK 文本需先转成 UTF-8"
            "（例如用记事本另存为 UTF-8，或先跑 `iconv -f GBK -t UTF-8`）" % exc)


def main(argv):
    _ensure_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except CtxError as exc:
        sys.stderr.write("ERROR %s\n" % exc)
        return EXIT_DATA
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        sys.stderr.write("ERROR 已中断\n")
        return EXIT_DATA
    except Exception as exc:  # 兜底：不裸 traceback
        sys.stderr.write("ERROR 未预期的失败（%s）：%s\n" % (type(exc).__name__, exc))
        return EXIT_DATA


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
