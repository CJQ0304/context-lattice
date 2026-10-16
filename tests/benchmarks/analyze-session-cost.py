#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量一个真实 agent 会话的上下文构成：到底是谁把窗口占满的。

素材：WorkBuddy 的会话转录 jsonl（真实会话，非构造）
输出：按「内容来源」分桶的 token 统计 —— 回答「省哪一块最有效」

用法：python analyze-session-cost.py <transcript.jsonl>
"""
import json
import sys

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    ENC = None


def tok(text):
    if ENC is None:
        return len(text)
    return len(ENC.encode(text))


def blocks_of(o):
    c = o.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    if isinstance(c, list):
        return [b for b in c if isinstance(b, dict)]
    return []


def text_of(b):
    t = b.get("type")
    if t in ("input_text", "text"):
        return b.get("text", "")
    if t == "tool_use":
        return json.dumps(b.get("input", {}), ensure_ascii=False)
    if t == "tool_result":
        c = b.get("content")
        if isinstance(c, list):
            return "".join(x.get("text", "") for x in c if isinstance(x, dict))
        return str(c or "")
    return json.dumps(b, ensure_ascii=False)


def main(path):
    buckets = {}          # 内容来源 -> token
    calls = {}            # 工具名 -> (次数, token)
    n_msg = 0
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "message":
            continue
        n_msg += 1
        role = o.get("role", "?")
        for b in blocks_of(o):
            t = b.get("type")
            text = text_of(b)
            n = tok(text)
            if t in ("input_text", "text"):
                key = ("用户输入" if role == "user" else "助手正文")
                # 剥掉宿主注入的 system-reminder 块，单独计
                if role == "user" and "system-reminder" in text:
                    key = "用户输入(含宿主注入)"
                buckets[key] = buckets.get(key, 0) + n
            elif t == "tool_use":
                name = str(b.get("name", "?"))
                buckets["助手·工具调用参数"] = buckets.get("助手·工具调用参数", 0) + n
                c, s = calls.get(name, (0, 0))
                calls[name] = (c + 1, s + n)
            elif t == "tool_result":
                buckets["工具结果"] = buckets.get("工具结果", 0) + n
            else:
                buckets["其他(" + str(t) + ")"] = buckets.get("其他(" + str(t) + ")", 0) + n

    total = sum(buckets.values())
    print("会话消息数: %d   合计 token: %d" % (n_msg, total))
    print()
    print("%-24s %10s %8s" % ("内容来源", "token", "占比"))
    print("-" * 46)
    for k, v in sorted(buckets.items(), key=lambda x: -x[1]):
        print("%-24s %10d %7.1f%%" % (k, v, 100.0 * v / max(1, total)))
    print()
    print("工具调用明细（按 token 降序，前 15）")
    print("%-34s %6s %10s %8s" % ("工具", "次数", "token", "占工具总量"))
    print("-" * 62)
    tool_total = sum(s for _, s in calls.values())
    for name, (c, s) in sorted(calls.items(), key=lambda x: -x[1][1])[:15]:
        print("%-34s %6d %10d %7.1f%%" % (name, c, s, 100.0 * s / max(1, tool_total)))
    print()
    print("工具调用参数合计 = %d token（%.1f%%）" % (tool_total, 100.0 * tool_total / max(1, total)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
